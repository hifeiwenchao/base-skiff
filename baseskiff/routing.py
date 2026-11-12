"""Per-client network routes which keep the database's TLS server identity."""
import socket
import ssl
from copy import copy


class _ServerIdentityContext:
    def __init__(self, context, hostname):
        if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
            raise RuntimeError("MongoDB SSH TLS routing requires certificate and hostname verification.")
        self._context = context
        self._hostname = hostname

    def wrap_socket(self, *args, **kwargs):
        kwargs["server_hostname"] = self._hostname
        return self._context.wrap_socket(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._context, name)


def mongo_tls_pool(hostname):
    """Override only the TLS peer name in PyMongo's per-client pool hook.

    PyMongo has no public socket/proxy option. Its pool-class hook avoids a
    process-wide socket patch, and the copied options keep other clients intact.
    The narrowly scoped private slot is checked before any connection is opened.
    """
    from pymongo.synchronous.pool import Pool

    class RoutedPool(Pool):
        def __del__(self):
            # Validation may reject an incompatible driver before Pool.__init__.
            if hasattr(self, "conns"):
                super().__del__()

        def __init__(self, address, options, *args, **kwargs):
            slot = "_PoolOptions__ssl_context"
            if not hasattr(options, slot):
                raise RuntimeError("This PyMongo version does not support SSH TLS routing.")
            context = options._ssl_context
            if context is None:
                raise RuntimeError("MongoDB SSH TLS routing requires an enabled TLS context.")
            options = copy(options)
            setattr(options, slot, _ServerIdentityContext(context, hostname))
            super().__init__(address, options, *args, **kwargs)

    return RoutedPool


def redis_connection_class(module, endpoint, tls):
    """Dial the tunnel while redis-py continues to verify its logical host."""
    base = module.SSLConnection if tls else module.Connection

    class RoutedConnection(base):
        def _connect(self):
            sock = socket.create_connection((endpoint.host, endpoint.port), self.socket_connect_timeout)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                if self.socket_keepalive:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                    for option, value in self.socket_keepalive_options.items():
                        sock.setsockopt(socket.IPPROTO_TCP, option, value)
                sock.settimeout(self.socket_timeout)
                # redis-py's SSL wrapper uses self.host for SNI and verification.
                return self._wrap_socket_with_ssl(sock) if tls else sock
            except BaseException:
                sock.close()
                raise

    return RoutedConnection
