"""Short-lived, verified SSH forwards used by database operations.

The direct listener supports server-first database protocols. A separate
loopback listener accepts SOCKS5 and HTTP CONNECT, retaining the original
database hostname for drivers that perform TLS hostname verification.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import select
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import paramiko

from .models import DEFAULT_PORTS, Connection


@dataclass(frozen=True)
class TunnelEndpoint:
    host: str
    port: int
    proxy_port: int


def fingerprint(key: paramiko.PKey) -> str:
    """Return the OpenSSH SHA256 fingerprint, without base64 padding."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def validate_ssh(connection: Connection, require_credentials: bool = False) -> None:
    """Validate enabled tunnel settings without opening a network connection.

    Passwords may be empty: saved secrets are resolved by the service, and
    some SSH servers deliberately accept an empty password.
    """
    if not connection.ssh_enabled:
        return
    if connection.kind == "SQLite":
        raise ValueError("SQLite files do not support SSH tunnels.")
    if not connection.ssh_host.strip():
        raise ValueError("SSH host is required.")
    if not connection.ssh_username.strip():
        raise ValueError("SSH username is required.")
    if not connection.host.strip():
        raise ValueError("Database host is required for an SSH tunnel.")
    for label, port in (("SSH", connection.ssh_port),
                        ("Database", connection.port or DEFAULT_PORTS.get(connection.kind, 0))):
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError(f"{label} port must be between 1 and 65535.")
    if connection.ssh_auth_method not in {"password", "key", "agent"}:
        raise ValueError("SSH authentication must be password, key, or agent.")
    pin = connection.ssh_host_key.strip()
    if pin:
        try:
            if not pin.startswith("SHA256:"):
                raise ValueError
            encoded = pin.removeprefix("SHA256:").rstrip("=")
            decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True)
            if len(decoded) != 32 or len(encoded) != 43:
                raise ValueError
        except (ValueError, binascii.Error):
            raise ValueError("SSH host key fingerprint must use OpenSSH SHA256: format.") from None
    if connection.ssh_auth_method == "key":
        if not connection.ssh_private_key.strip():
            raise ValueError("SSH private key file is required.")
        if not Path(connection.ssh_private_key).expanduser().is_file():
            raise ValueError("SSH private key file does not exist.")
    if connection.ssh_known_hosts and not Path(connection.ssh_known_hosts).expanduser().is_file():
        raise ValueError("SSH known_hosts file does not exist.")


class _VerifyHostKey(paramiko.MissingHostKeyPolicy):
    def __init__(self, pin: str):
        self.pin = pin.strip().rstrip("=")

    def missing_host_key(self, client, hostname, key):
        observed = fingerprint(key)
        if self.pin and hmac.compare_digest(self.pin, observed):
            return
        if self.pin:
            raise paramiko.SSHException(
                f"SSH host key fingerprint mismatch for {hostname}: observed {observed}. "
                "Verify the server host key through a trusted channel before changing the saved fingerprint.")
        raise paramiko.SSHException(
            f"Unknown SSH host key for {hostname}: {observed}. "
            "Verify this fingerprint through a trusted channel, then save it or add the key to known_hosts.")


def _normal_host(host: str) -> str:
    host = host.strip().removeprefix("[").removesuffix("]")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host.lower()


class _ProxyReader:
    def __init__(self, sock: socket.socket, stopping: threading.Event):
        self.sock = sock
        self.stopping = stopping
        self.deadline = time.monotonic() + 10
        # A short poll also cancels readers accepted while close() takes its snapshot.
        sock.settimeout(0.2)

    def read(self, length: int) -> bytes:
        data = bytearray()
        while len(data) < length:
            if self.stopping.is_set():
                raise EOFError("SSH tunnel has closed.")
            if time.monotonic() >= self.deadline:
                raise TimeoutError("SSH proxy handshake timed out.")
            try:
                chunk = self.sock.recv(length - len(data))
            except socket.timeout:
                continue
            if not chunk:
                raise EOFError("Proxy client closed the connection.")
            data.extend(chunk)
        return bytes(data)


def _close_stream(stream) -> None:
    try:
        stream.close()
    except (OSError, EOFError, paramiko.SSHException):
        pass


class _Tunnel:
    def __init__(self, connection: Connection):
        self.connection = connection
        self.target = (_normal_host(connection.host), connection.port or DEFAULT_PORTS[connection.kind])
        self.client = paramiko.SSHClient()
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.listeners: list[socket.socket] = []
        self.streams: set = set()
        self.threads: set[threading.Thread] = set()

    def start(self) -> TunnelEndpoint:
        c = self.connection
        self.client.set_missing_host_key_policy(_VerifyHostKey(c.ssh_host_key))
        if not c.ssh_host_key.strip():
            self.client.load_system_host_keys()
            path = Path(c.ssh_known_hosts).expanduser() if c.ssh_known_hosts else Path.home() / ".ssh/known_hosts"
            if path.is_file():
                self.client.load_host_keys(str(path))
        options = dict(hostname=_normal_host(c.ssh_host), port=c.ssh_port,
                       username=c.ssh_username, timeout=8, banner_timeout=8,
                       auth_timeout=10, channel_timeout=10,
                       allow_agent=False, look_for_keys=False)
        if c.ssh_auth_method == "password":
            options["password"] = c.ssh_password
        elif c.ssh_auth_method == "key":
            try:
                options["pkey"] = paramiko.PKey.from_path(
                    Path(c.ssh_private_key).expanduser(),
                    passphrase=c.ssh_passphrase.encode("utf-8") if c.ssh_passphrase else None)
            except (OSError, ValueError, TypeError, paramiko.SSHException) as error:
                raise ValueError(f"Cannot read SSH private key: {error}") from error
        else:
            options.update(allow_agent=True, look_for_keys=False)
        try:
            self.client.connect(**options)
        except paramiko.BadHostKeyException as error:
            raise paramiko.SSHException(
                f"SSH host key changed for {c.ssh_host}: observed {fingerprint(error.key)}. "
                "Verify the server host key before updating known_hosts.") from error
        self.transport = self.client.get_transport()
        self.transport.set_keepalive(30)
        direct = self._listen(False)
        proxy = self._listen(True)
        return TunnelEndpoint("127.0.0.1", direct, proxy)

    def _spawn(self, target, *args):
        def run():
            try:
                target(*args)
            finally:
                with self.lock:
                    self.threads.discard(threading.current_thread())
        thread = threading.Thread(target=run, name="BaseSkiff SSH", daemon=True)
        with self.lock:
            if self.stopping.is_set():
                return
            self.threads.add(thread)
            thread.start()

    def _track(self, stream) -> bool:
        with self.lock:
            if self.stopping.is_set():
                _close_stream(stream)
                return False
            self.streams.add(stream)
            return True

    def _listen(self, proxy: bool) -> int:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listeners.append(listener)
        listener.bind(("127.0.0.1", 0))
        listener.listen(32)
        listener.settimeout(0.2)
        self._spawn(self._accept, listener, proxy)
        return listener.getsockname()[1]

    def _accept(self, listener, proxy):
        while not self.stopping.is_set():
            try:
                incoming, address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if self._track(incoming):
                self._spawn(self._serve, incoming, address, proxy)

    def _open(self, address):
        channel = self.transport.open_channel("direct-tcpip", self.target, address, timeout=8)
        if not self._track(channel):
            raise EOFError("SSH tunnel has closed.")
        channel.settimeout(10)
        return channel

    def _allowed(self, host: str, port: int) -> bool:
        return (_normal_host(host), port) == self.target

    def _socks(self, incoming, address, read, initial):
        version, count = initial[0], read(1)[0]
        methods = read(count)
        if version != 5 or 0 not in methods:
            incoming.sendall(b"\x05\xff")
            return None, b""
        incoming.sendall(b"\x05\x00")
        version, command, reserved, address_type = read(4)
        if address_type == 1:
            host = socket.inet_ntop(socket.AF_INET, read(4))
        elif address_type == 4:
            host = socket.inet_ntop(socket.AF_INET6, read(16))
        elif address_type == 3:
            host = read(read(1)[0]).decode("ascii")
        else:
            incoming.sendall(b"\x05\x08\x00\x01" + b"\x00" * 6)
            return None, b""
        port = int.from_bytes(read(2), "big")
        if version != 5 or command != 1 or reserved != 0 or not self._allowed(host, port):
            incoming.sendall(b"\x05\x02\x00\x01" + b"\x00" * 6)
            return None, b""
        try:
            channel = self._open(address)
        except (OSError, EOFError, paramiko.SSHException):
            incoming.sendall(b"\x05\x05\x00\x01" + b"\x00" * 6)
            return None, b""
        incoming.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
        return channel, b""

    def _http(self, incoming, address, read, initial):
        header = bytearray(initial)
        while b"\r\n\r\n" not in header:
            header.extend(read(1))
            if len(header) > 16384:
                incoming.sendall(b"HTTP/1.1 431 Request Header Fields Too Large\r\n\r\n")
                return None, b""
        try:
            method, authority, version = bytes(header).split(b"\r\n", 1)[0].decode("ascii").split()
            host, port = authority.rsplit(":", 1)
            port = int(port)
            allowed = method == "CONNECT" and version in {"HTTP/1.0", "HTTP/1.1"} and self._allowed(host, port)
        except (ValueError, UnicodeError):
            allowed = False
        if not allowed:
            incoming.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            return None, b""
        try:
            channel = self._open(address)
        except (OSError, EOFError, paramiko.SSHException):
            incoming.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
            return None, b""
        incoming.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        return channel, b""

    def _serve(self, incoming, address, proxy):
        channel = None
        try:
            incoming.settimeout(10)
            if proxy:
                read = _ProxyReader(incoming, self.stopping).read
                initial = read(1)
                channel, pending = (self._socks if initial == b"\x05" else self._http)(incoming, address, read, initial)
                if channel is None:
                    return
                if pending:
                    channel.sendall(pending)
                incoming.settimeout(10)
            else:
                channel = self._open(address)
            while not self.stopping.is_set():
                ready, _, _ = select.select([incoming, channel], [], [], 0.2)
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    (channel if source is incoming else incoming).sendall(data)
        except (OSError, EOFError, UnicodeError, ValueError, paramiko.SSHException):
            pass
        finally:
            for stream in (incoming, channel):
                if stream is not None:
                    _close_stream(stream)
                    with self.lock:
                        self.streams.discard(stream)

    def close(self):
        self.stopping.set()
        for listener in self.listeners:
            _close_stream(listener)
        with self.lock:
            streams = list(self.streams)
            threads = list(self.threads)
        for stream in streams:
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except (OSError, EOFError, paramiko.SSHException):
                pass
            _close_stream(stream)
        _close_stream(self.client)
        deadline = time.monotonic() + 2
        for thread in threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))


@contextmanager
def ssh_endpoint(connection: Connection):
    """Yield a loopback endpoint, closing SSH and forwarded sockets on exit."""
    if not connection.ssh_enabled:
        yield None
        return
    validate_ssh(connection, require_credentials=True)
    tunnel = _Tunnel(connection)
    try:
        yield tunnel.start()
    finally:
        tunnel.close()
