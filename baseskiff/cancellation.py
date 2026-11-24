"""Small cross-thread cancellation primitives for blocking database work."""
from __future__ import annotations

from threading import Event, RLock


class QueryCancelled(RuntimeError):
    """Raised when a running database query was explicitly interrupted."""

    cancelled = True

    def __init__(self, message='Query cancelled.'):
        super().__init__(message)


class CancellationToken:
    """An event plus one optional driver-specific interrupt callback."""

    def __init__(self):
        self._event = Event()
        self._lock = RLock()
        self._callback = None
        self._committing = False

    @property
    def cancelled(self):
        return self._event.is_set()

    def raise_if_cancelled(self):
        if self.cancelled:
            raise QueryCancelled()

    def bind(self, callback):
        with self._lock:
            self._callback = callback
            cancelled = self.cancelled
        if cancelled:
            callback()

    def unbind(self, callback):
        with self._lock:
            if self._callback is callback:
                self._callback = None

    def cancel(self, dispatch=None):
        with self._lock:
            if self._committing:
                return False
            self._event.set()
            callback = self._callback
        if callback is not None:
            if dispatch is None:
                callback()
            else:
                dispatch(callback)
        return True

    def begin_commit(self):
        """Commit is a point of no return; never misreport committed writes as cancelled."""
        with self._lock:
            self.raise_if_cancelled()
            self._committing = True
