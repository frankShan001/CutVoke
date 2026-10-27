"""Small transactional job journal shared by desktop and MCP processes."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import threading
import time


class LocalJobs:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection=sqlite3.connect(str(path),isolation_level=None,check_same_thread=False,timeout=10)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('CREATE TABLE IF NOT EXISTS local_jobs (id TEXT PRIMARY KEY, project_id TEXT, status TEXT, data TEXT NOT NULL)')
        self.lock=threading.RLock()
        with self.transaction():self._recover_interrupted()

    @staticmethod
    def _alive(pid):
        if pid==os.getpid():return True
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=kernel.OpenProcess(0x1000,False,pid)
            if not handle:return ctypes.get_last_error()==5
            try:
                code=wintypes.DWORD();return not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value==259
            finally:kernel.CloseHandle(handle)
        try:os.kill(pid,0);return True
        except ProcessLookupError:return False
        except PermissionError:return True

    def _recover_interrupted(self):
        rows=self.connection.execute("SELECT data FROM local_jobs WHERE status IN ('queued','running')").fetchall()
        for row in rows:
            job=json.loads(row[0]);pid=job.get('workerPid')
            if isinstance(pid,int) and not self._alive(pid):
                failed={**job,'status':'failed','phase':'本地处理进程已退出','error':'The local worker exited before completion. Start a new job; the original media is unchanged.','updatedAt':time.time()}
                self.connection.execute("UPDATE local_jobs SET status='failed',data=? WHERE id=? AND status IN ('queued','running')",(json.dumps(failed,ensure_ascii=False),job['jobId']))

    @contextmanager
    def transaction(self):
        with self.lock:
            self.connection.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.connection.execute('COMMIT')
            except BaseException:
                self.connection.execute('ROLLBACK');raise

    def get(self, job_id):
        with self.lock:
            self._recover_interrupted()
            row=self.connection.execute('SELECT data FROM local_jobs WHERE id=?',(job_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def put(self, job, *, maximum_pending=None):
        with self.transaction():
            self._recover_interrupted()
            if maximum_pending is not None:
                count=self.connection.execute("SELECT COUNT(*) FROM local_jobs WHERE status IN ('queued','running','applying')").fetchone()[0]
                if count>=maximum_pending:raise ValueError(f'local matting accepts at most {maximum_pending} pending jobs')
            self._write(job)

    def _write(self, job):
        job={**job,'updatedAt':time.time()}
        self.connection.execute('INSERT OR REPLACE INTO local_jobs VALUES (?,?,?,?)',
            (job['jobId'],job['projectId'],job['status'],json.dumps(job,ensure_ascii=False)))
        return job

    def update(self, job_id, changes, *, expected=None, project_id=None):
        with self.transaction():
            job=self.get(job_id)
            if job is None or expected is not None and job['status'] not in expected or project_id is not None and job['projectId']!=project_id:return None
            job.update(changes);return self._write(job)

    def list(self, project_id=None):
        with self.lock:
            query='SELECT data FROM local_jobs';args=()
            if project_id is not None:query+=' WHERE project_id=?';args=(project_id,)
            return [json.loads(row[0]) for row in self.connection.execute(query,args)]

    def close(self):
        with self.lock:self.connection.close()
