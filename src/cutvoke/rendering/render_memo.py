"""Bounded immutable render results, with one producer per concurrent key."""
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError
import os
import threading


def file_identity(path):
    """Invalidate replacement media as well as in-place edits of the same path."""
    stat = os.stat(path)
    return (os.path.normcase(os.path.abspath(path)), stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino)


class RenderMemo:
    UNCACHED = object()

    def __init__(self, max_entries=256, max_bytes=None):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._values = OrderedDict()
        self._pending = {}
        self._lock = threading.Lock()
        self._bytes = 0
        self._hits = self._misses = self._waits = 0

    def get_or_compute(self, key, compute, *, cancelled=None):
        with self._lock:
            if key in self._values:
                self._hits += 1
                value, _ = self._values[key]
                self._values.move_to_end(key)
                return value
            future = self._pending.get(key)
            owner = future is None
            if owner:
                self._misses += 1
                future = self._pending[key] = Future()
            else:
                self._waits += 1
        if not owner:
            if cancelled is not None:
                while True:
                    if cancelled():
                        raise InterruptedError('render cache wait cancelled')
                    try:
                        return future.result(timeout=.05)
                    except TimeoutError:
                        if future.done():
                            return future.result()
                        continue
            return future.result()
        try:
            value = compute()
            size = len(value) if self.max_bytes is not None and value is not self.UNCACHED else 0
            with self._lock:
                if value is not self.UNCACHED and self.max_entries > 0 and (self.max_bytes is None or size <= self.max_bytes):
                    while self._values and (len(self._values) >= self.max_entries or
                            (self.max_bytes is not None and self._bytes + size > self.max_bytes)):
                        _, (_, removed) = self._values.popitem(last=False)
                        self._bytes -= removed
                    self._values[key] = (value, size)
                    self._bytes += size
            future.set_result(value)
            return value
        except BaseException as exc:
            # Failures (including cancellation) are shared only with current
            # waiters. A later request must be able to retry.
            with self._lock:
                if self._pending.get(key) is future:
                    self._pending.pop(key)
            future.set_exception(exc)
            raise
        finally:
            with self._lock:
                if self._pending.get(key) is future:
                    self._pending.pop(key)

    def status(self):
        with self._lock:
            return dict(entries=len(self._values), bytes=self._bytes,
                        hits=self._hits, misses=self._misses, waits=self._waits)
