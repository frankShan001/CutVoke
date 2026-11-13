"""Opt-in, test-server-only preview timing; never records arguments or errors."""
from contextlib import contextmanager
from functools import wraps
import itertools
import json
import threading
import time

# Shared process-local monotonic origin across diagnostic contexts.
_PROCESS_ORIGIN = time.monotonic()


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
    install_lock = threading.RLock()
    installed = set()
    active = True

    def emit(stage, event, ident, parent, started):
        nonlocal count
        with lock:
            if count > max_records:
                return
            if count == max_records:
                stage, event = 'diagnostics', 'truncated'
            count += 1
            now = time.monotonic()
            record = dict(stage=stage, event=event, elapsed_s=now-started,
                          t_s=now-_PROCESS_ORIGIN,
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

    def install(obj, name, stage=None, slot=False, factory=False):
        # Called only while holding install_lock; saved references prevent id reuse.
        key = (id(obj), name)
        if key in installed:
            return
        original = getattr(obj, name)
        had = name in vars(obj)
        previous = vars(obj).get(name)
        if factory:
            @wraps(original)
            def wrapper(*args, **kwargs):
                child = original(*args, **kwargs)
                instrument(child)
                return child
        elif slot:
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
        installed.add(key)

    def instrument(instance):
        with install_lock:
            if not active:
                return
            install(instance, '_preview_frame_file', 'preview')
            install(instance._frame_scheduler, 'slot', 'slot', slot=True)
            install(instance, '_require_current_preview', 'current')
            install(instance.render, 'extract_frame', 'extract')
            install(instance.render, '_compile', 'compile')
            if hasattr(instance, 'project_api'):
                install(instance, 'project_api', factory=True)

    try:
        instrument(api)
        yield
    finally:
        with install_lock:
            active = False
            for obj, name, had, previous in reversed(saved):
                if had:
                    setattr(obj, name, previous)
                else:
                    delattr(obj, name)
