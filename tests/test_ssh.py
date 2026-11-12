"""Exercise actual SSH authentication and forwarding over loopback sockets."""
import base64
import hashlib
import os
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import paramiko
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from baseskiff.models import Connection
from baseskiff.ssh import fingerprint, ssh_endpoint, validate_ssh
from tests.ssh_test_server import EchoServer, TestSSHServer


def _recv(sock, length):
    result = b""
    while len(result) < length:
        data = sock.recv(length - len(result))
        if not data:
            break
        result += data
    return result


def _eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_disabled_tunnel_does_not_connect():
    with ssh_endpoint(Connection(name="local")) as endpoint:
        assert endpoint is None


def test_password_forward_handles_server_first_protocol_and_closes_all_sockets():
    with EchoServer(greeting=b"database ready") as echo, TestSSHServer() as ssh:
        with ssh_endpoint(ssh.connection(echo.host, echo.port)) as endpoint:
            direct_port, proxy_port = endpoint.port, endpoint.proxy_port
            assert endpoint.host == "127.0.0.1"
            client = socket.create_connection((endpoint.host, direct_port), timeout=3)
            assert _recv(client, 14) == b"database ready"
            client.sendall(b"actual SSH bytes")
            assert _recv(client, 16) == b"actual SSH bytes"
        assert client.recv(1) == b""
        client.close()
        for port in (direct_port, proxy_port):
            with pytest.raises(OSError):
                socket.create_connection(("127.0.0.1", port), timeout=0.2)
        assert ssh.auth_attempts == [("password", "ssh-user")]
        assert ssh.destinations == [(echo.host, echo.port)]


@pytest.mark.parametrize("protocol", ["socks", "http"])
def test_proxy_transfers_real_bytes_to_remote_hostname(protocol):
    with EchoServer() as echo, TestSSHServer() as ssh:
        with ssh_endpoint(ssh.connection("localhost", echo.port)) as endpoint:
            with socket.create_connection((endpoint.host, endpoint.proxy_port), timeout=3) as client:
                if protocol == "socks":
                    client.sendall(b"\x05\x01\x00")
                    assert _recv(client, 2) == b"\x05\x00"
                    client.sendall(b"\x05\x01\x00\x03\x09localhost" + echo.port.to_bytes(2, "big"))
                    assert _recv(client, 10)[:2] == b"\x05\x00"
                else:
                    client.sendall(f"CONNECT localhost:{echo.port} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
                    assert client.recv(1024).startswith(b"HTTP/1.1 200")
                client.sendall(b"forwarded through proxy")
                assert _recv(client, 23) == b"forwarded through proxy"
        assert ssh.destinations == [("localhost", echo.port)]


@pytest.mark.parametrize("protocol", ["socks", "http"])
def test_proxy_refuses_other_destination(protocol):
    with EchoServer() as echo, TestSSHServer() as ssh:
        with ssh_endpoint(ssh.connection(echo.host, echo.port)) as endpoint:
            with socket.create_connection((endpoint.host, endpoint.proxy_port), timeout=3) as client:
                if protocol == "socks":
                    client.sendall(b"\x05\x01\x00")
                    assert _recv(client, 2) == b"\x05\x00"
                    client.sendall(b"\x05\x01\x00\x03\x0bexample.com" + echo.port.to_bytes(2, "big"))
                    assert _recv(client, 10)[1] == 2
                else:
                    client.sendall(f"CONNECT example.com:{echo.port} HTTP/1.1\r\n\r\n".encode())
                    assert client.recv(1024).startswith(b"HTTP/1.1 403")
        assert ssh.destinations == []


@pytest.mark.parametrize("encrypted", [False, True])
@pytest.mark.parametrize("kind", ["RSA", "Ed25519"])
def test_private_key_authentication_reads_real_key_file(tmp_path, encrypted, kind):
    path = tmp_path / "private_key"
    passphrase = "encrypted passphrase" if encrypted else ""
    if kind == "RSA":
        key = paramiko.RSAKey.generate(2048)
        key.write_private_key_file(str(path), password=passphrase or None)
    else:
        private = ed25519.Ed25519PrivateKey.generate()
        encryption = (serialization.BestAvailableEncryption(passphrase.encode()) if encrypted
                      else serialization.NoEncryption())
        path.write_bytes(private.private_bytes(serialization.Encoding.PEM,
                                              serialization.PrivateFormat.OpenSSH, encryption))
        key = paramiko.Ed25519Key.from_private_key_file(str(path), password=passphrase or None)
    with EchoServer() as echo, TestSSHServer(allowed_key=key) as ssh:
        connection = ssh.connection(echo.host, echo.port, ssh_auth_method="key", ssh_private_key=str(path),
                                    ssh_passphrase=passphrase, ssh_password="deliberately wrong")
        with ssh_endpoint(connection) as endpoint:
            with socket.create_connection((endpoint.host, endpoint.port), timeout=3) as client:
                client.sendall(b"key authenticated")
                assert _recv(client, 17) == b"key authenticated"
        assert ssh.auth_attempts == [("publickey", "ssh-user")]


def test_agent_authentication_uses_a_real_isolated_ssh_agent(tmp_path, monkeypatch):
    if os.name == "nt" or not shutil.which("ssh-agent") or not shutil.which("ssh-add"):
        pytest.skip("OpenSSH ssh-agent and ssh-add are required for the real agent test")
    key = paramiko.RSAKey.generate(2048)
    key_path = tmp_path / "agent_key"
    key.write_private_key_file(str(key_path))
    key_path.chmod(0o600)
    # AF_UNIX paths are capped at around 100 bytes; pytest's temp path can be longer.
    with TemporaryDirectory(prefix="bs-agent-", dir="/tmp") as short_directory:
        agent_socket = Path(short_directory) / "agent.sock"
        agent = subprocess.Popen(["ssh-agent", "-D", "-a", str(agent_socket)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            assert _eventually(agent_socket.exists), "isolated ssh-agent did not start"
            monkeypatch.setenv("SSH_AUTH_SOCK", str(agent_socket))
            environment = dict(os.environ, SSH_ASKPASS_REQUIRE="never")
            subprocess.run(["ssh-add", str(key_path)], env=environment, stdin=subprocess.DEVNULL,
                           capture_output=True, check=True, timeout=5)
            with EchoServer() as echo, TestSSHServer(allowed_key=key) as ssh:
                with ssh_endpoint(ssh.connection(echo.host, echo.port, ssh_auth_method="agent")) as endpoint:
                    with socket.create_connection((endpoint.host, endpoint.port), timeout=3) as client:
                        client.sendall(b"agent authenticated")
                        assert _recv(client, 19) == b"agent authenticated"
                assert ssh.auth_attempts == [("publickey", "ssh-user")]
        finally:
            agent.terminate()
            agent.wait(timeout=5)


def test_wrong_private_key_passphrase_reports_clear_error_without_authentication(tmp_path):
    key = paramiko.RSAKey.generate(2048)
    path = tmp_path / "encrypted_key"
    key.write_private_key_file(str(path), password="right password")
    with TestSSHServer(allowed_key=key) as ssh:
        connection = ssh.connection("localhost", 1234, ssh_auth_method="key", ssh_private_key=str(path),
                                    ssh_passphrase="wrong password")
        with pytest.raises(ValueError, match="(?i)private key"):
            with ssh_endpoint(connection):
                pytest.fail("invalid encrypted key authenticated")
        assert ssh.auth_attempts == []


def test_agent_mode_does_not_fall_back_to_default_private_key_file(tmp_path, monkeypatch):
    key = paramiko.RSAKey.generate(2048)
    path = tmp_path / "default_rsa_key"
    key.write_private_key_file(str(path))
    expanduser = os.path.expanduser
    # Redirect only the disk identity lookup; no user files or home directory change.
    monkeypatch.setattr(os.path, "expanduser", lambda value:
                        str(path) if value == "~/.ssh/id_rsa" else expanduser(value))
    monkeypatch.setenv("SSH_AUTH_SOCK", str(tmp_path / "absent-agent.sock"))
    with TestSSHServer(allowed_key=key) as ssh:
        connection = ssh.connection("localhost", 1234, ssh_auth_method="agent")
        with pytest.raises(paramiko.SSHException, match="(?i)authentication"):
            with ssh_endpoint(connection):
                pytest.fail("Agent mode used an identity file from disk")
        assert ssh.auth_attempts == []


def test_unknown_host_key_is_rejected_before_authentication(tmp_path):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("")
    with TestSSHServer() as ssh:
        connection = ssh.connection("localhost", 1234, ssh_host_key="", ssh_known_hosts=str(known_hosts))
        with pytest.raises(Exception, match="SHA256:") as error:
            with ssh_endpoint(connection):
                pytest.fail("unknown host was accepted")
        assert ssh.host_key_fingerprint in str(error.value)
        assert ssh.auth_attempts == []


def test_wrong_pin_is_rejected_before_authentication():
    wrong_key = paramiko.RSAKey.generate(2048)
    wrong_pin = "SHA256:" + base64.b64encode(hashlib.sha256(wrong_key.asbytes()).digest()).decode().rstrip("=")
    with TestSSHServer() as ssh:
        connection = ssh.connection("localhost", 1234, ssh_host_key=wrong_pin)
        with pytest.raises(Exception, match="(?i)(fingerprint|host.key)"):
            with ssh_endpoint(connection):
                pytest.fail("wrong pin was accepted")
        assert ssh.auth_attempts == []


def test_known_host_key_is_accepted_and_changed_key_is_rejected(tmp_path):
    with TestSSHServer() as ssh:
        known_hosts = tmp_path / "known_hosts"
        known_hosts.write_text(f"[127.0.0.1]:{ssh.port} {ssh.host_key.get_name()} {ssh.host_key.get_base64()}\n")
        connection = ssh.connection("localhost", 1234, ssh_host_key="", ssh_known_hosts=str(known_hosts))
        with ssh_endpoint(connection):
            pass
        key = paramiko.RSAKey.generate(2048)
        known_hosts.write_text(f"[127.0.0.1]:{ssh.port} {key.get_name()} {key.get_base64()}\n")
        with pytest.raises(Exception, match="(?i)(host.key|fingerprint)"):
            with ssh_endpoint(connection):
                pytest.fail("changed host key was accepted")
        assert len(ssh.auth_attempts) == 1


def test_explicit_pin_takes_precedence_over_existing_known_host(tmp_path):
    with TestSSHServer() as ssh:
        known_hosts = tmp_path / "known_hosts"
        wrong_key = paramiko.RSAKey.generate(2048)
        known_hosts.write_text(f"[127.0.0.1]:{ssh.port} {wrong_key.get_name()} {wrong_key.get_base64()}\n")
        with ssh_endpoint(ssh.connection("localhost", 1234, ssh_known_hosts=str(known_hosts))):
            pass
        assert ssh.auth_attempts == [("password", "ssh-user")]


def test_bad_password_closes_ssh_transport():
    with TestSSHServer() as ssh:
        connection = ssh.connection("localhost", 1234, ssh_password="wrong password")
        with pytest.raises(Exception, match="(?i)authentication"):
            with ssh_endpoint(connection):
                pytest.fail("wrong password authenticated")
        assert _eventually(lambda: ssh.transports and not ssh.transports[0].is_active())


def test_context_exit_closes_clients_that_have_not_sent_proxy_handshake():
    previous_threads = set(threading.enumerate())
    with TestSSHServer() as ssh:
        with ssh_endpoint(ssh.connection("localhost", 1234)) as endpoint:
            incoming = socket.create_connection((endpoint.host, endpoint.proxy_port), timeout=3)
        try:
            assert incoming.recv(1) == b""
        except ConnectionResetError:
            pass
        incoming.close()
        assert not [thread for thread in threading.enumerate()
                    if thread not in previous_threads and thread.name == "BaseSkiff SSH"]


def test_context_exit_promptly_closes_many_partial_proxy_handshakes():
    previous_threads = set(threading.enumerate())
    clients = []
    try:
        with TestSSHServer() as ssh:
            with ssh_endpoint(ssh.connection("localhost", 1234)) as endpoint:
                for index in range(12):
                    client = socket.create_connection((endpoint.host, endpoint.proxy_port), timeout=3)
                    client.sendall(b"\x05\x01" if index % 2 else b"CONNECT incomplete")
                    clients.append(client)
                closing_started = time.monotonic()
            assert time.monotonic() - closing_started < 2
            assert not [thread for thread in threading.enumerate()
                        if thread not in previous_threads and thread.name == "BaseSkiff SSH"]
    finally:
        for client in clients:
            client.close()


@pytest.mark.parametrize("changes", [
    {"kind": "SQLite"}, {"ssh_host": ""}, {"ssh_username": ""}, {"ssh_port": 0},
    {"ssh_port": 65536}, {"port": -1}, {"port": 65536}, {"ssh_auth_method": "unknown"},
    {"ssh_host_key": "SHA256:not-a-fingerprint"}, {"ssh_auth_method": "key", "ssh_private_key": ""},
    {"ssh_auth_method": "key", "ssh_private_key": "/missing/private/key"},
    {"ssh_known_hosts": "/missing/known_hosts"},
])
def test_invalid_tunnel_configuration_is_rejected(changes):
    valid = Connection(name="valid", kind="Redis", host="db.internal", ssh_enabled=True,
                       ssh_host="jump.internal", ssh_username="alice")
    with pytest.raises(ValueError):
        validate_ssh(replace(valid, **changes))


def test_fingerprint_is_openssh_sha256_format():
    key = paramiko.RSAKey.generate(2048)
    digest = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
    assert fingerprint(key) == "SHA256:" + digest
