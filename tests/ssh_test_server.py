"""A real loopback SSH server for transport and opt-in database tests."""
from __future__ import annotations

import base64
import hashlib
import select
import socket
import threading

import paramiko

from baseskiff.models import Connection


def _pump(left, right, stopping):
    try:
        while not stopping.is_set():
            ready, _, _ = select.select([left, right], [], [], 0.1)
            for source in ready:
                data = source.recv(65536)
                if not data:
                    return
                (right if source is left else left).sendall(data)
    except (OSError, EOFError):
        pass
    finally:
        for stream in (left, right):
            try:
                stream.close()
            except (OSError, EOFError, paramiko.SSHException):
                pass


class _Authentication(paramiko.ServerInterface):
    def __init__(self, owner):
        self.owner = owner
        self.destinations = {}

    def get_allowed_auths(self, username):
        return "password,publickey"

    def check_auth_password(self, username, password):
        self.owner.auth_attempts.append(("password", username))
        if username == self.owner.username and password == self.owner.password:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        self.owner.auth_attempts.append(("publickey", username))
        if username == self.owner.username and self.owner.allowed_key == key:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_direct_tcpip_request(self, chanid, origin, destination):
        self.destinations[chanid] = destination
        self.owner.destinations.append(destination)
        return paramiko.OPEN_SUCCEEDED


class TestSSHServer:
    """Context manager exposing a real SSH transport and TCP forwarding.

    ``connection(host, port, kind='Redis', **overrides)`` creates a pinned
    Connection using this server. No operating system SSH settings are read.
    """

    __test__ = False

    def __init__(self, password="ssh-secret", username="ssh-user", allowed_key=None, host_key=None):
        self.password = password
        self.username = username
        self.allowed_key = allowed_key
        self.host_key = host_key or paramiko.RSAKey.generate(2048)
        digest = base64.b64encode(hashlib.sha256(self.host_key.asbytes()).digest()).decode().rstrip("=")
        self.host_key_fingerprint = "SHA256:" + digest
        self.host = "127.0.0.1"
        self.auth_attempts = []
        self.destinations = []
        self.transports = []
        self.sockets = []
        self.threads = []
        self.stopping = threading.Event()

    def __enter__(self):
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((self.host, 0))
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self._thread(self._accept)
        return self

    def _thread(self, target, *args):
        thread = threading.Thread(target=target, args=args, daemon=True)
        self.threads.append(thread)
        thread.start()

    def _accept(self):
        while not self.stopping.is_set():
            try:
                incoming, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.sockets.append(incoming)
            self._thread(self._serve, incoming)

    def _serve(self, incoming):
        transport = paramiko.Transport(incoming)
        self.transports.append(transport)
        transport.add_server_key(self.host_key)
        authentication = _Authentication(self)
        try:
            transport.start_server(server=authentication)
            while not self.stopping.is_set() and transport.is_active():
                channel = transport.accept(0.1)
                if channel is None:
                    continue
                try:
                    destination = socket.create_connection(authentication.destinations[channel.chanid], timeout=3)
                except OSError:
                    channel.close()
                    continue
                self.sockets.append(destination)
                self._thread(_pump, channel, destination, self.stopping)
        except (paramiko.SSHException, EOFError, OSError):
            pass
        finally:
            transport.close()

    def connection(self, target_host, target_port, kind="Redis", **overrides):
        values = dict(name="SSH test", kind=kind, host=target_host, port=target_port,
                      ssh_enabled=True, ssh_host=self.host, ssh_port=self.port,
                      ssh_username=self.username, ssh_password=self.password,
                      ssh_host_key=self.host_key_fingerprint)
        values.update(overrides)
        return Connection(**values)

    def __exit__(self, *args):
        self.stopping.set()
        self.listener.close()
        for transport in self.transports:
            transport.close()
        for sock in self.sockets:
            sock.close()
        for thread in self.threads:
            thread.join(1)


class EchoServer:
    def __init__(self, greeting=b""):
        self.host = "127.0.0.1"
        self.greeting = greeting
        self.stopping = threading.Event()
        self.clients = []
        self.threads = []

    def __enter__(self):
        self.listener = socket.socket()
        self.listener.bind((self.host, 0))
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        thread = threading.Thread(target=self._accept, daemon=True)
        self.threads.append(thread)
        thread.start()
        return self

    def _accept(self):
        while not self.stopping.is_set():
            try:
                client, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.clients.append(client)
            thread = threading.Thread(target=self._serve, args=(client,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def _serve(self, client):
        client.settimeout(0.1)
        try:
            if self.greeting:
                client.sendall(self.greeting)
            while not self.stopping.is_set():
                try:
                    data = client.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    break
                client.sendall(data)
        except OSError:
            pass
        finally:
            client.close()

    def __exit__(self, *args):
        self.stopping.set()
        self.listener.close()
        for client in self.clients:
            client.close()
        for thread in self.threads:
            thread.join(1)
