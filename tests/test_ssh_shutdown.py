"""Idle listeners must stop without waiting for their accept polling interval."""
import socket
import threading

import pytest

from baseskiff.models import Connection
from baseskiff.ssh import _Tunnel


@pytest.mark.parametrize('proxy', [False, True])
def test_close_wakes_idle_listener_and_does_not_forward_wakeup(proxy):
    tunnel = _Tunnel(Connection(name='shutdown', kind='Redis', host='localhost', port=6379))
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    # Deliberately longer than close's join budget: closing must cancel accept,
    # rather than merely waiting for the next idle poll to notice stopping.
    listener.settimeout(3)
    tunnel.listeners.append(listener)
    entered = threading.Event()
    served = []
    tunnel._serve = lambda *args: served.append(args)

    def accept():
        entered.set()
        tunnel._accept(listener, proxy)

    tunnel._spawn(accept)
    assert entered.wait(1)
    threads = list(tunnel.threads)
    try:
        tunnel.close()
        assert not any(thread.is_alive() for thread in threads)
        assert not tunnel.streams
        assert not served
        tunnel.close()  # Repeated shutdown must tolerate closed listeners.
    finally:
        listener.close()
        for thread in threads:
            thread.join(4)


@pytest.mark.parametrize('failing', ['client', 'stream'])
def test_close_releases_listeners_when_another_resource_raises(failing):
    tunnel = _Tunnel(Connection(name='Exceptional shutdown', kind='Redis'))
    tunnel._listen(False)
    original_client = tunnel.client

    class BrokenResource:
        def shutdown(self, _how):
            pass

        def close(self):
            raise RuntimeError('resource shutdown failed')

    resource = BrokenResource()
    if failing == 'client':
        tunnel.client = resource
    else:
        tunnel.streams.add(resource)
    try:
        with pytest.raises(RuntimeError, match='resource shutdown failed'):
            tunnel.close()
        assert all(listener.fileno() == -1 for listener in tunnel.listeners)
    finally:
        tunnel.client = original_client
        tunnel.streams.discard(resource)
        tunnel.close()
