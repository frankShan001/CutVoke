"""Disposable preview media, retained across sessions with a disk byte budget.

Only known artifact names in this directory are ever removed. Project/source
files are not cache entries. SQLite records validation and recency separately
from the media, so an in-memory index eviction never deletes useful disk data.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
import uuid

from .render import RenderCancelled

ARTIFACT = re.compile(r"(?:window|preview|prepared|frame|proxy)_[a-f0-9]{32,64}\.(?:mp4|png|webp)$")
DEFAULT_BYTES = 10 * 1024**3


class _Rows:
    def __init__(self, rows): self.rows = rows
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows


class _Index:
    """Short connections avoid retaining Windows file handles in idle clients."""
    def __init__(self, path): self.path = path
    def execute(self, sql, values=()):
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            cursor = connection.execute(sql, values)
            rows = cursor.fetchall() if cursor.description else []
            connection.commit()
            return _Rows(rows)
        finally:
            connection.close()
    def commit(self): pass
    def close(self): pass


class PreviewDiskCache:
    def __init__(self, media_dir: str, *, grace_seconds: float = 30):
        self.default_dir = Path(media_dir).resolve() / ".preview-cache-v2"
        self.legacy_dir = Path(media_dir).resolve() / ".preview-cache"
        self.config_file = Path(media_dir).resolve().parent / "preview-cache.json"
        self.lock = threading.RLock()
        self.pins: dict[str, int] = {}
        self.grace_seconds = grace_seconds
        self.hits = self.misses = 0
        self.db = None
        self.directory = self.default_dir
        self.max_bytes = DEFAULT_BYTES
        self._config_stamp = None
        self._reload()

    def _reload(self):
        stamp = self.config_file.stat().st_mtime_ns if self.config_file.is_file() else None
        if self.db is not None and stamp == self._config_stamp:
            return
        config = json.loads(self.config_file.read_text("utf-8")) if stamp else {}
        directory = Path(config.get("directory", self.default_dir)).expanduser().resolve()
        budget = config.get("maxBytes", DEFAULT_BYTES)
        if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
            raise ValueError("cache maxBytes must be a positive integer")
        if self.db is not None and directory == self.directory:
            self.max_bytes, self._config_stamp = budget, stamp
            return
        new_directory = not directory.exists()
        directory.mkdir(parents=True, exist_ok=True)
        if new_directory and directory == self.default_dir and self.legacy_dir.is_dir():
            # Old Web processes use an eight-entry destructive cache. Keep the
            # persistent cache separate while adopting their reusable outputs.
            for original in self.legacy_dir.iterdir():
                if ARTIFACT.fullmatch(original.name) and original.is_file() and not original.is_symlink():
                    try:
                        try: os.link(original, directory / original.name)
                        except OSError: shutil.copyfile(original, directory / original.name)
                    except OSError:
                        pass  # A legacy process may already be evicting it.
        if self.db is not None:
            self.db.close()
        self.directory, self.max_bytes = directory, budget
        self.db = _Index(directory / "index.sqlite")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS artifacts "
                        "(name TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, accessed REAL, metadata TEXT)")
        self.db.commit()
        self._config_stamp = stamp
        # Adopt the inventory without trusting media validation. Old cache files
        # are validated once on access; interrupted builds are never published.
        for path in directory.iterdir():
            if ARTIFACT.fullmatch(path.name) and path.is_file() and not path.is_symlink():
                stat = path.stat()
                self.db.execute("INSERT OR IGNORE INTO artifacts VALUES (?, ?, ?, ?, NULL)",
                                (path.name, stat.st_size, stat.st_mtime_ns, stat.st_mtime))
        self.db.commit()

    def path(self, name: str) -> Path:
        if not ARTIFACT.fullmatch(name):
            raise ValueError("invalid preview artifact name")
        with self.lock:
            self._reload()
            return self.directory / name

    def get(self, name: str, metadata: dict | None = None, validate=None) -> str | None:
        with self.lock:
            path = self.path(name)
            row = self.db.execute("SELECT size, mtime, metadata FROM artifacts WHERE name=?", (name,)).fetchone()
            if not path.is_file() or path.is_symlink():
                self.db.execute("DELETE FROM artifacts WHERE name=?", (name,))
                self.db.commit()
                self.misses += 1
                return None
            stat = path.stat()
            expected = json.dumps(metadata or {}, sort_keys=True)
            trusted = row and row == (stat.st_size, stat.st_mtime_ns, expected)
            if not trusted and validate is not None and not validate(str(path)):
                self._remove(name)
                self.db.commit()
                self.misses += 1
                return None
            self._record(path, expected)
            self.hits += 1
            return str(path)

    def _record(self, path: Path, metadata: str):
        stat = path.stat()
        self.db.execute("INSERT OR REPLACE INTO artifacts VALUES (?, ?, ?, ?, ?)",
                        (path.name, stat.st_size, stat.st_mtime_ns, time.time(), metadata))
        self.db.commit()

    def metadata(self, name: str) -> dict:
        with self.lock:
            self.path(name)
            row = self.db.execute("SELECT metadata FROM artifacts WHERE name=?", (name,)).fetchone()
            return json.loads(row[0]) if row and row[0] else {}

    def publish(self, name: str, temporary: str | Path, metadata: dict | None = None) -> str:
        with self.lock:
            path = self.path(name)
            os.replace(temporary, path)
            self._record(path, json.dumps(metadata or {}, sort_keys=True))
            self.trim()
            return str(path)

    @contextmanager
    def pin(self, path: str | Path):
        key = str(Path(path).resolve())
        with self.lock:
            self.pins[key] = self.pins.get(key, 0) + 1
        try:
            yield
        finally:
            with self.lock:
                self.pins[key] -= 1
                if not self.pins[key]:
                    self.pins.pop(key)

    def _remove(self, name):
        path = self.directory / name
        if (not ARTIFACT.fullmatch(name) or path.is_symlink() or
                self.pins.get(str(path.resolve()))):
            return False
        try:
            path.unlink(missing_ok=True)
        except OSError:  # Windows protects an open media handle in another process.
            return False
        self.db.execute("DELETE FROM artifacts WHERE name=?", (name,))
        return True

    def trim(self, *, clear=False) -> dict:
        with self.lock:
            self._reload()
            rows = self.db.execute("SELECT name, size, accessed FROM artifacts ORDER BY accessed").fetchall()
            total = sum(row[1] for row in rows)
            freed = 0
            # Leave room for original media and exports. The currently used
            # artifacts may temporarily exceed the budget; they are protected.
            pressure = max(0, 1024**3 - shutil.disk_usage(self.directory).free)
            target = 0 if clear else max(0, self.max_bytes - pressure)
            for name, size, accessed in rows:
                if total <= target:
                    break
                if time.time() - accessed < self.grace_seconds:
                    continue
                if self._remove(name):
                    total -= size
                    freed += size
            self.db.commit()
            return {"freedBytes": freed, "remainingBytes": total}

    def configure(self, *, directory=None, max_bytes=None) -> dict:
        with self.lock:
            self._reload()
            destination = Path(directory).expanduser().resolve() if directory else self.directory
            if directory and destination != self.directory and destination.name not in (".preview-cache-v2", ".cutvoke-preview-cache"):
                destination = destination / ".cutvoke-preview-cache"
            budget = self.max_bytes if max_bytes is None else max_bytes
            if not isinstance(budget, int) or isinstance(budget, bool) or not 256 * 1024**2 <= budget <= 1024**4:
                raise ValueError("缓存容量需要在 256 MiB 到 1 TiB 之间")
            if directory is not None and (not isinstance(directory, str) or not directory.strip()):
                raise ValueError("缓存目录不能为空")
            destination.mkdir(parents=True, exist_ok=True)
            probe = destination / (".write-check-" + uuid.uuid4().hex)
            probe.write_bytes(b"")
            probe.unlink()
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.config_file.with_name(self.config_file.name + "." + uuid.uuid4().hex)
            temporary.write_text(json.dumps({"directory": str(destination), "maxBytes": budget}), "utf-8")
            os.replace(temporary, self.config_file)
            self._reload()
            self.trim()
            return self.status()

    def status(self):
        with self.lock:
            self._reload()
            groups = self.db.execute("SELECT substr(name,1,instr(name,'_')-1),count(*),sum(size) "
                                     "FROM artifacts GROUP BY 1").fetchall()
            used = sum(row[2] for row in groups)
            return {"directory": str(self.directory), "maxBytes": self.max_bytes,
                    "usedBytes": used, "freeBytes": shutil.disk_usage(self.directory).free,
                    "entries": sum(row[1] for row in groups), "hits": self.hits, "misses": self.misses,
                    "overBudget": used > self.max_bytes, "protectedEntries": len(self.pins),
                    "categories": [{"kind": kind, "entries": count, "bytes": size}
                                   for kind, count, size in groups]}

    def close(self):
        with self.lock:
            if self.db is not None:
                self.db.close()
                self.db = None


class PreviewRenderScheduler:
    """Bound FFmpeg memory; foreground seeks precede background preparation.

    Cache hits never enter this queue. The caller rechecks the cache after
    admission, which coalesces concurrent requests for the same artifact.
    """
    def __init__(self):
        self.condition = threading.Condition()
        self.queue = []
        self.active = False
        self.serial = 0

    @contextmanager
    def slot(self, cancel: threading.Event, priority=0):
        with self.condition:
            self.serial += 1
            ticket = (priority, self.serial)
            self.queue.append(ticket)
            try:
                while self.active or ticket != min(self.queue):
                    if cancel.is_set():
                        raise RenderCancelled("preview request superseded")
                    self.condition.wait(.05)
                if cancel.is_set():
                    raise RenderCancelled("preview request superseded")
                self.active = True
            finally:
                self.queue.remove(ticket)
                self.condition.notify_all()
        try:
            yield
        finally:
            with self.condition:
                self.active = False
                self.condition.notify_all()
