"""Cross-process plans, explicit confirmation receipts and installation jobs."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid


class ExtensionJobs:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(path),isolation_level=None,check_same_thread=False,timeout=15)
        self.db.row_factory=sqlite3.Row;self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,identity TEXT UNIQUE,expires REAL,data TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS confirmations(id TEXT PRIMARY KEY,plan_id TEXT UNIQUE,decision TEXT,data TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS installs(id TEXT PRIMARY KEY,plan_id TEXT,status TEXT,data TEXT)')
        self.lock=threading.RLock()

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:yield;self.db.execute('COMMIT')
            except BaseException:self.db.execute('ROLLBACK');raise

    def plan(self,value,identity):
        now=time.time()
        with self.transaction():
            row=self.db.execute('SELECT * FROM plans WHERE identity=?',(identity,)).fetchone()
            existing=json.loads(row['data']) if row else None
            value={**value,'planId':existing['planId'] if existing else 'plan_'+uuid.uuid4().hex,'planHash':identity,'expiresAt':now+86400}
            self.db.execute('INSERT OR REPLACE INTO plans VALUES(?,?,?,?)',(value['planId'],identity,value['expiresAt'],json.dumps(value)))
            return value

    def get_plan(self,identity):
        with self.lock:
            row=self.db.execute('SELECT data,expires FROM plans WHERE id=?',(identity,)).fetchone()
            if not row:raise ValueError('installation plan not found')
            if row['expires']<time.time():raise ValueError('installation plan expired; request a new plan')
            return json.loads(row['data'])

    def confirm(self,plan_id,decision,user_response,channel):
        if decision not in {'accept','decline'}:raise ValueError('decision must be accept/decline')
        if not isinstance(user_response,str) or not user_response.strip() or len(user_response)>1200:raise ValueError('record the actual user response, at most 1200 characters')
        if channel not in {'ui','agent'}:raise ValueError('confirmation channel must be ui/agent')
        with self.transaction():
            plan=self.get_plan(plan_id)
            if not plan.get('installable'):raise ValueError('this installation plan is not currently installable')
            row=self.db.execute('SELECT data FROM confirmations WHERE plan_id=?',(plan_id,)).fetchone()
            value=json.loads(row['data']) if row else None
            if value and value['decision']==decision:return value
            value={'confirmationId':'confirmation_'+uuid.uuid4().hex,'planId':plan_id,'planHash':plan['planHash'],'decision':decision,
                   'userResponse':user_response,'channel':channel,'createdAt':time.time()}
            self.db.execute('INSERT OR REPLACE INTO confirmations VALUES(?,?,?,?)',(value['confirmationId'],plan_id,decision,json.dumps(value)))
            return value

    def confirmation(self,plan_id):
        with self.lock:
            row=self.db.execute('SELECT data FROM confirmations WHERE plan_id=?',(plan_id,)).fetchone()
            return json.loads(row['data']) if row else None

    def create_job(self,plan_id,confirmation_id,worker_pid,*,already_installed=False):
        from cutvoke.storage.local_jobs import LocalJobs
        with self.transaction():
            plan=self.get_plan(plan_id);consent=self.confirmation(plan_id)
            if not consent or consent['confirmationId']!=confirmation_id or consent['decision']!='accept' or consent['planHash']!=plan['planHash']:
                raise ValueError('USER_CONFIRMATION_REQUIRED: install only after the user explicitly accepted this plan; pass its confirmation receipt')
            if not plan.get('installable'):raise ValueError('installation plan is not installable')
            if already_installed:
                row=self.db.execute("SELECT data FROM installs WHERE plan_id=? AND status='completed' ORDER BY rowid DESC LIMIT 1",(plan_id,)).fetchone()
                if row:return json.loads(row['data']),False
            rows=self.db.execute("SELECT data FROM installs WHERE status IN ('queued','running','cancelling') ORDER BY rowid DESC").fetchall()
            for row in rows:
                job=json.loads(row['data'])
                if not LocalJobs._alive(job['workerPid']):
                    self._write({**job,'status':'interrupted','phase':'interrupted','retryable':True,'error':'Installation worker stopped; retry reuses downloaded files.'});continue
                if job['planId']==plan_id:return job,False
                raise ValueError('INSTALLATION_BUSY: another optional extension is being installed; wait for that job or cancel it')
            job={'jobId':'install_'+uuid.uuid4().hex,'extensionId':plan['extensionId'],'planId':plan_id,'profileId':plan['profileId'],
                 'status':'queued','phase':'queued','progress':0,'workerPid':worker_pid,'createdAt':time.time(),'error':'','retryable':True}
            self._write(job);return job,True

    def _write(self,job):
        job={**job,'updatedAt':time.time()}
        self.db.execute('INSERT OR REPLACE INTO installs VALUES(?,?,?,?)',(job['jobId'],job['planId'],job['status'],json.dumps(job)))
        return job

    def get(self,job_id):
        with self.lock:
            row=self.db.execute('SELECT data FROM installs WHERE id=?',(job_id,)).fetchone()
            if not row:raise ValueError('installation job not found')
            job=json.loads(row['data'])
            if job['status'] in {'queued','running','cancelling'}:
                from cutvoke.storage.local_jobs import LocalJobs
                if not LocalJobs._alive(job['workerPid']):
                    job=self._write({**job,'status':'interrupted','phase':'interrupted','retryable':True,'error':'Installation worker stopped; retry reuses downloaded files.'})
            return job

    def update(self,job_id,**changes):
        with self.transaction():
            job=self.get(job_id)
            if job['status'] in {'completed','cancelled','failed','interrupted'}:return job
            if job['status']=='cancelling' and changes.get('status') not in {'cancelled','failed'}:return job
            return self._write({**job,**changes})

    def list(self,extension_id=None):
        with self.lock:
            rows=self.db.execute('SELECT id FROM installs ORDER BY rowid DESC LIMIT 20').fetchall()
            jobs=[self.get(row['id']) for row in rows]
            return [job for job in jobs if extension_id is None or job['extensionId']==extension_id]

    def close(self):
        with self.lock:self.db.close()
