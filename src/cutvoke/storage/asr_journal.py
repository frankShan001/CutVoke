"""Transactional ASR proposals in the owning project database, never project edits."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time

ACTIVE = ("queued", "running")
RETAIN_COMPLETED = 24
RETAIN_SECONDS = 7 * 24 * 60 * 60


class JobLock:
    """An OS-released lock distinguishes stopped work from another live server."""

    def __init__(self, path: Path | None):
        self.file = None
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        try:
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.file = handle
        except BaseException:
            handle.close()
            raise

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None


class AsrJournal:
    def __init__(self, database: str | None = None):
        self._lock = threading.RLock()
        self._lock_root = (Path(database).resolve().with_name(Path(database).name + ".asr-locks")
                           if database and database != ":memory:" else None)
        self._db = sqlite3.connect(database or ":memory:", isolation_level=None,
                                   check_same_thread=False, timeout=5)
        self._db.execute("PRAGMA busy_timeout=5000")
        with self.transaction():
            self._db.execute("CREATE TABLE IF NOT EXISTS asr_journal_meta ("
                             "key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            row = self._db.execute("SELECT value FROM asr_journal_meta WHERE key='schemaVersion'").fetchone()
            if row and row[0] != "1":
                raise ValueError("Unsupported ASR journal schema")
            self._db.execute("INSERT OR IGNORE INTO asr_journal_meta VALUES ('schemaVersion','1')")
            self._db.execute("CREATE TABLE IF NOT EXISTS asr_jobs ("
                             "job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
                             "status TEXT NOT NULL, updated_at REAL NOT NULL, "
                             "payload_json TEXT NOT NULL, snapshot_json TEXT NOT NULL)")
            self._db.execute("CREATE INDEX IF NOT EXISTS idx_asr_project ON asr_jobs(project_id,updated_at)")

    @contextmanager
    def transaction(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    def lock_job(self, job_id: str) -> JobLock | None:
        name = hashlib.sha256(job_id.encode()).hexdigest() + ".lock"
        try:
            return JobLock(self._lock_root / name if self._lock_root else None)
        except OSError:
            return None

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT payload_json FROM asr_jobs WHERE job_id=?", (job_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def snapshot(self, job_id: str) -> dict:
        with self._lock:
            row = self._db.execute("SELECT snapshot_json FROM asr_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("识别任务不存在")
            return json.loads(row[0])

    def jobs(self, project_id: str | None = None) -> list[dict]:
        with self._lock:
            query, args = "SELECT payload_json FROM asr_jobs", ()
            if project_id is not None:
                query, args = query + " WHERE project_id=?", (project_id,)
            rows = self._db.execute(query + " ORDER BY updated_at DESC,job_id", args).fetchall()
            return [json.loads(row[0]) for row in rows]

    def insert(self, job: dict, snapshot: dict):
        with self.transaction():
            self._check_capacity()
            self._db.execute("INSERT INTO asr_jobs VALUES (?,?,?,?,?,?)", (
                job["jobId"], job["projectId"], job["status"], job["updatedAt"],
                json.dumps(job, ensure_ascii=False), json.dumps(snapshot, ensure_ascii=False)))

    def _check_capacity(self):
        count = self._db.execute("SELECT count(*) FROM asr_jobs WHERE status IN ('queued','running')").fetchone()[0]
        if count >= 2:
            raise ValueError("已有两个识别任务在等待或运行，请稍后重试")

    def update(self, job_id: str, changes: dict, *, statuses=None, check_capacity=False,
               version_field: str | None = None) -> dict | None:
        with self.transaction():
            job = self.get(job_id)
            if job is None or (statuses is not None and job["status"] not in statuses):
                return job
            if version_field and job.get(version_field, 0) > changes[version_field]:
                return job  # A delayed request cannot overwrite a newer review.
            if check_capacity:
                self._check_capacity()
            job.update(changes, updatedAt=time.time())
            self._db.execute("UPDATE asr_jobs SET status=?,updated_at=?,payload_json=? WHERE job_id=?", (
                job["status"], job["updatedAt"], json.dumps(job, ensure_ascii=False), job_id))
            return job

    def prune(self):
        with self.transaction():
            rows = self._db.execute("SELECT job_id,updated_at FROM asr_jobs "
                                    "WHERE status NOT IN ('queued','running') ORDER BY updated_at DESC").fetchall()
            obsolete = [key for index, (key, updated) in enumerate(rows)
                        if index >= RETAIN_COMPLETED or updated < time.time() - RETAIN_SECONDS]
            self._db.executemany("DELETE FROM asr_jobs WHERE job_id=?", [(key,) for key in obsolete])
        if self._lock_root:
            for key in obsolete:
                lease = self.lock_job(key)
                if lease is not None:
                    lease.close()
                    try:
                        (self._lock_root / (hashlib.sha256(key.encode()).hexdigest() + ".lock")).unlink(missing_ok=True)
                    except OSError:
                        pass

    def close(self):
        with self._lock:
            self._db.close()


def relocate_asr_jobs(connection, source_root: Path, stage_root: Path, target_root: Path):
    """Rebind proposals only when a verified library copy moves their source."""
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='asr_jobs'").fetchone():
        return
    from cutvoke.domain.model import Project
    from cutvoke.rendering.speech_recognition import SpeechRecognitionError, find_source_clip, source_signature
    from cutvoke.storage.workspace_paths import path_key, path_values, replace_paths

    for job_id, payload, raw in connection.execute(
            "SELECT job_id,payload_json,snapshot_json FROM asr_jobs").fetchall():
        job, snapshot = json.loads(payload), json.loads(raw)
        try:
            clip = find_source_clip(Project.from_dict(snapshot), job["clipId"])
            original = Path(clip.asset_ref.source_path)
            if not original.is_absolute() or not original.is_relative_to(source_root):
                continue
            copied = stage_root / original.relative_to(source_root)
            if source_signature(clip) != job["sourceSignature"]:
                continue
            old_stat, copied_stat = original.stat(), copied.stat()
            if (old_stat.st_size, old_stat.st_mtime_ns) != (copied_stat.st_size, copied_stat.st_mtime_ns):
                continue
            mapping = {path_key(value): str(target_root / Path(value).relative_to(source_root))
                       for value in path_values(snapshot)
                       if Path(value).is_absolute() and Path(value).is_relative_to(source_root)}
            snapshot, _ = replace_paths(snapshot, mapping)
            moved_clip = next(c for t in Project.from_dict(snapshot).sequence.tracks
                              for c in t.clips if c.id == job["clipId"])
            signature = source_signature(moved_clip, stat_path=copied)
            if (job.get("result") or {}).get("sourceSignature") == job["sourceSignature"]:
                job["result"]["sourceSignature"] = signature
            job["sourceSignature"] = signature
            connection.execute("UPDATE asr_jobs SET payload_json=?,snapshot_json=? WHERE job_id=?", (
                json.dumps(job, ensure_ascii=False), json.dumps(snapshot, ensure_ascii=False), job_id))
        except (SpeechRecognitionError, OSError, ValueError, KeyError):
            # Keep invalid proposals invalid; resume/apply still rejects them.
            continue
