"""Disposable, bounded same-snapshot video-window recovery (never audio).

SQLite transactions publish complete blobs atomically and serialize eviction with
readers across processes. A hit is copied into a request-owned directory, so later
eviction/cancellation cannot remove another export's input. No user-supplied path
or manifest is interpreted. Cache errors are misses, never export failures.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

ABI = 'dense-ffv1-v1'


def digest_file(path, cancel=None):
    before = os.stat(path)
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        while block := source.read(1024 * 1024):
            if cancel is not None and cancel.is_set():
                return None
            digest.update(block)
    after = os.stat(path)
    if stamp(before) != stamp(after):
        return None
    return digest.hexdigest(), stamp(after)


def stamp(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def simple_snapshot(project):
    """Fail closed before preprocessing can replace dependencies by temp files."""
    seq = project.sequence
    if seq.captions or seq.compound_layout or seq.exposed_properties or seq.multicam:
        return None
    paths = set()
    for track in seq.tracks:
        for clip in track.clips:
            if (clip.effects or clip.nested or clip.sequence_ref or clip.role
                    or clip.compositing or clip.property_links or clip.property_overrides
                    or clip.motion_path or clip.asset_ref.resource_ref):
                return None
            path = clip.asset_ref.source_path
            if not path or not os.path.isfile(path):
                return None
            paths.add(os.path.abspath(path))
    return project.to_dict(), sorted(paths)


class WindowCache:
    MAX_ITEM = 64 * 1024**2
    MAX_BYTES = 256 * 1024**2
    MAX_ENTRIES = 64

    def __init__(self, directory):
        self.database = Path(directory) / 'windows.sqlite3'

    def _connect(self):
        if self.database.parent.is_symlink() or any(
                Path(str(self.database) + suffix).is_symlink()
                for suffix in ('', '-journal', '-wal', '-shm')):
            raise OSError('cache symlinks are not supported')
        self.database.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.database, timeout=0.25)
        try:
            db.execute('PRAGMA auto_vacuum=FULL')
            # Bounds allocated database pages too; rollback journal may temporarily
            # require another database-sized allocation. No WAL grows without bound.
            page_size = db.execute('PRAGMA page_size').fetchone()[0]
            db.execute(f'PRAGMA max_page_count={320 * 1024**2 // page_size}')
            db.execute('CREATE TABLE IF NOT EXISTS windows '
                       '(key TEXT PRIMARY KEY, data BLOB NOT NULL, digest TEXT NOT NULL, used REAL NOT NULL)')
        except sqlite3.Error:
            db.close()
            raise
        return db

    def restore(self, key, target):
        try:
            with closing(self._connect()) as db, db:
                row = db.execute('SELECT data,digest FROM windows WHERE key=? AND length(data)<=?',
                                 (key, self.MAX_ITEM)).fetchone()
                if (row is None or not isinstance(row[0], bytes)
                        or not isinstance(row[1], str)
                        or hashlib.sha256(row[0]).hexdigest() != row[1]):
                    return False
                # Holding the bytes, not a shared cache path, makes eviction safe.
                Path(target).write_bytes(row[0])
                db.execute('UPDATE windows SET used=? WHERE key=?', (time.time(), key))
                return True
        except (OSError, sqlite3.Error):
            return False

    def publish(self, key, source):
        try:
            size = Path(source).stat().st_size
            if not 0 < size <= min(self.MAX_ITEM, self.MAX_BYTES):
                return False
            with open(source, 'rb') as stream:
                data = stream.read(self.MAX_ITEM + 1)
            if len(data) != size:
                return False
            with closing(self._connect()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM windows WHERE key=?', (key,))
                while True:
                    count, total = db.execute('SELECT count(*),coalesce(sum(length(data)),0) FROM windows').fetchone()
                    if count < self.MAX_ENTRIES and total + size <= self.MAX_BYTES:
                        break
                    db.execute('DELETE FROM windows WHERE key=(SELECT key FROM windows ORDER BY used LIMIT 1)')
                db.execute('INSERT INTO windows VALUES (?,?,?,?)',
                           (key, data, hashlib.sha256(data).hexdigest(), time.time()))
            return True
        except (OSError, sqlite3.Error):
            return False


class RecoverySession:
    def __init__(self, cache, identity, sources):
        self.cache, self.sources = cache, sources
        self.identity = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def unchanged(self):
        try:
            return all(stamp(os.stat(path)) == identity for path, identity in self.sources)
        except OSError:
            return False

    def key(self, cursor, duration):
        return hashlib.sha256(json.dumps([self.identity, cursor, duration],
                                        sort_keys=True).encode()).hexdigest()


def recovery_session(directory, snapshot, ffmpeg, options, cancel=None):
    if snapshot is None:
        return None
    try:
        document, paths = snapshot
        hashes, sources = [], []
        for path in paths:
            result = digest_file(path, cancel)
            if result is None:
                return None
            digest, identity = result
            hashes.append((path, digest))
            sources.append((path, identity))
        version = subprocess.run([ffmpeg, '-version'], capture_output=True, check=True,
                                 timeout=10).stdout.decode('utf-8', errors='replace')
        # Include domain timing/keyframes and every preprocessing dependency,
        # not just the renderer entry point. Missing frozen sources fail closed.
        package = Path(__file__).resolve().parents[1]
        if not (package / 'rendering' / 'render.py').is_file():
            return None
        files = sorted(package.rglob('*.py'))
        implementation = []
        for path in files:
            result = digest_file(path, cancel)
            if result is None:
                return None
            implementation.append((path.relative_to(package).as_posix(), result[0]))
        if files != sorted(package.rglob('*.py')):
            return None
        identity = [ABI, implementation, document, hashes, version, options]
        return RecoverySession(WindowCache(directory), identity, sources)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return None
