"""Opt-in, test-server-only preview timing; never records arguments or errors."""
from contextlib import contextmanager
from functools import wraps
import itertools
import json
import threading
import time


@contextmanager
def install_preview_diagnostics(api, output, *, enabled=False, max_records=10000):
    """Temporarily instrument one isolated API instance; disabled by default.

    Enable only in an explicit e2e server opt-in. Exit after request threads have
    stopped. Output failures are ignored so instrumentation cannot break previews.
    """
    if not enabled:
        yield
        return
    if max_records < 1:
        raise ValueError('max_records must be positive')
    lock = threading.Lock()
    local = threading.local()
    serial = itertools.count(1)
    count = 0
    saved = []

    def emit(stage, event, ident, parent, started):
        nonlocal count
        with lock:
            if count > max_records:
                return
            if count == max_records:
                stage, event = 'diagnostics', 'truncated'
            count += 1
            record = dict(stage=stage, event=event, elapsed_s=time.monotonic()-started,
                          thread_id=threading.get_ident(), id=ident, parent_id=parent)
            try:
                output.write(json.dumps(record) + '\n')
                output.flush()
            except Exception:
                pass

    @contextmanager
    def span(stage):
        stack = getattr(local, 'stack', None)
        if stack is None:
            stack = local.stack = []
        with lock:
            ident = next(serial)
        parent = stack[-1] if stack else None
        started = time.monotonic()
        stack.append(ident)
        emit(stage, 'start', ident, parent, started)
        try:
            yield lambda event: emit(stage, event, ident, parent, started)
        except BaseException:
            emit(stage, 'error', ident, parent, started)
            raise
        else:
            emit(stage, 'end', ident, parent, started)
        finally:
            stack.pop()

    def install(obj, name, stage, slot=False):
        original = getattr(obj, name)
        had = name in vars(obj)
        previous = vars(obj).get(name)
        if slot:
            @wraps(original)
            @contextmanager
            def wrapper(*args, **kwargs):
                with span(stage) as event:
                    event('waiting')
                    acquired = False
                    try:
                        with original(*args, **kwargs) as value:
                            acquired = True
                            event('acquired')
                            yield value
                    finally:
                        if acquired:
                            event('released')
        else:
            @wraps(original)
            def wrapper(*args, **kwargs):
                with span(stage):
                    return original(*args, **kwargs)
        setattr(obj, name, wrapper)
        saved.append((obj, name, had, previous))

    try:
        install(api, '_preview_frame_file', 'preview')
        install(api._frame_scheduler, 'slot', 'slot', slot=True)
        install(api, '_require_current_preview', 'current')
        install(api.render, 'extract_frame', 'extract')
        install(api.render, '_compile', 'compile')
        yield
    finally:
        for obj, name, had, previous in reversed(saved):
            if had:
                setattr(obj, name, previous)
            else:
                delattr(obj, name)
