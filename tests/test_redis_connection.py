"""Exercise Redis authentication using the real driver and a RESP2-only peer."""
import socket
import socketserver
import threading
from contextlib import ExitStack

import pytest
import redis

from baseskiff.adapters import adapter_for
from baseskiff.models import Connection
from baseskiff.service import DatabaseService
from tests.ssh_test_server import TestSSHServer


class _RESP2Peer:
    """A minimal Redis wire peer which rejects Redis 6's HELLO command.

    Password-only mode models a legacy requirepass server. The optional named
    user models ACL authentication over RESP2, which newer servers also support.
    """

    def __init__(self, username="", password="", failure_code=""):
        self.username = username
        self.password = password
        self.failure_code = failure_code
        self.commands = []
        self.sockets = []
        peer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                peer.sockets.append(self.request)
                authenticated = not peer.password
                try:
                    while True:
                        header = self.rfile.readline()
                        if not header:
                            return
                        if not header.startswith(b"*"):
                            raise ValueError("Expected a RESP array")
                        args = []
                        for _ in range(int(header[1:])):
                            size = self.rfile.readline()
                            if not size.startswith(b"$"):
                                raise ValueError("Expected a bulk argument")
                            args.append(self.rfile.read(int(size[1:])).decode())
                            if self.rfile.read(2) != b"\r\n":
                                raise ValueError("Expected an argument terminator")
                        peer.commands.append(args)
                        command = args[0].upper()
                        if peer.failure_code and command in {"HELLO", "AUTH"}:
                            # Some Redis proxies echo rejected arguments in errors.
                            response = (f"-{peer.failure_code} credential rejected: {peer.password}\r\n").encode()
                        elif command == "HELLO":
                            # This is the failure observed with redis-py 8 defaults.
                            response = b"-ERR unknown command 'HELLO'\r\n"
                        elif command == "AUTH":
                            expected = [peer.username, peer.password] if peer.username else [peer.password]
                            authenticated = args[1:] == expected
                            response = b"+OK\r\n" if authenticated else b"-WRONGPASS invalid credentials\r\n"
                        elif not authenticated:
                            response = b"-NOAUTH Authentication required\r\n"
                        elif command == "PING":
                            response = b"+PONG\r\n"
                        elif command == "SELECT":
                            response = b"+OK\r\n"
                        elif command == "CLIENT":
                            # Old Redis accepts no CLIENT SETINFO; redis-py ignores
                            # that optional metadata failure and proceeds.
                            response = b"-ERR unknown subcommand 'SETINFO'\r\n"
                        else:
                            response = b"-ERR unsupported command\r\n"
                        self.wfile.write(response)
                        self.wfile.flush()
                except (EOFError, OSError):
                    return

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.host, self.port = self.server.server_address

    def __enter__(self):
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        for stream in self.sockets:
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            stream.close()
        self.server.server_close()
        self.thread.join(1)


@pytest.mark.parametrize("route", ["direct", "ssh"])
@pytest.mark.parametrize("username,password", [
    pytest.param("", "only-test-password", id="legacy-password-only"),
    pytest.param("operator", "only-test-password", id="acl-user"),
    pytest.param("", "", id="no-authentication"),
])
def test_redis_connects_to_resp2_peer_without_hello(route, username, password):
    with ExitStack() as stack:
        peer = stack.enter_context(_RESP2Peer(username, password))
        values = dict(name="Redis compatibility", kind="Redis", host=peer.host,
                      port=peer.port, database="2", username=username, password=password)
        ssh = stack.enter_context(TestSSHServer()) if route == "ssh" else None
        if ssh:
            connection = ssh.connection(peer.host, peer.port, database="2",
                                        username=username, password=password)
        else:
            connection = Connection(**values)

        assert adapter_for(connection).test() == "Connected to Redis successfully."
        commands = [args[0].upper() for args in peer.commands]
        assert "HELLO" not in commands
        assert "PING" in commands
        assert ["SELECT", "2"] in peer.commands
        auth_commands = [args for args in peer.commands if args[0].upper() == "AUTH"]
        expected_auth = ["AUTH", username, password] if username else ["AUTH", password]
        assert auth_commands == ([expected_auth] if password else [])
        if ssh:
            assert ssh.destinations == [(peer.host, peer.port)]


@pytest.mark.parametrize("route", ["direct", "ssh"])
@pytest.mark.parametrize("failure_code,error_type", [
    ("WRONGPASS", redis.AuthenticationError), ("ERR", redis.ResponseError),
])
def test_echoed_auth_secret_is_hidden_from_connection_errors_and_history(tmp_path, route, failure_code, error_type):
    password = "only-test-password"
    service = DatabaseService(tmp_path)
    with ExitStack() as stack:
        peer = stack.enter_context(_RESP2Peer(password=password, failure_code=failure_code))
        ssh = stack.enter_context(TestSSHServer()) if route == "ssh" else None
        if ssh:
            connection = ssh.connection(peer.host, peer.port, password=password)
        else:
            connection = Connection(name="Redis failure", kind="Redis", host=peer.host,
                                    port=peer.port, password=password)
        saved = service.save_connection(connection)
        with pytest.raises(error_type) as tested:
            service.test_connection(saved)
        assert type(tested.value) is error_type
        assert "credential rejected" in str(tested.value)
        assert password not in str(tested.value)
        with pytest.raises(error_type) as executed:
            service.execute(saved.id, "PING")
        assert type(executed.value) is error_type
        assert password not in str(executed.value)
        history = service.list_history()[0]
        assert not history.success
        assert "credential rejected" in history.error
        assert password not in history.error
