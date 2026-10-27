"""Persistent human-to-agent handoff; never starts or impersonates an AI host."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import uuid


def require_vision(capabilities):
    from cutvoke.editing.service import EditError
    if not isinstance(capabilities,dict) or any(capabilities.get(key) is not True for key in ('selfAssessed','imageInput','toolImageInput')):
        raise EditError('AGENT_VISION_REQUIRED','This workflow requires inspecting actual image tool results. Assess your current model AND host tool-image support. If unsupported or unknown, do not use AI matting/visual review; ask the user to use the manual UI. Never infer vision support from a model name or tool availability.')


class AgentInbox:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(path),isolation_level=None,check_same_thread=False,timeout=10)
        self.db.row_factory=sqlite3.Row;self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY, token TEXT, profile TEXT, expires REAL, watching REAL DEFAULT 0)')
        if 'watching_project' not in {row['name'] for row in self.db.execute('PRAGMA table_info(agents)')}:
            self.db.execute("ALTER TABLE agents ADD COLUMN watching_project TEXT DEFAULT ''")
        self.db.execute('CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,project_id TEXT,revision TEXT,kind TEXT,clip_id TEXT,job_id TEXT,instruction TEXT,status TEXT,owner TEXT,claim_until REAL,created REAL,updated REAL,result TEXT)')
        self.lock=threading.RLock()

    def session(self,operation,arguments):
        now=time.time()
        with self.lock:
            if operation=='register':
                capabilities=arguments.get('capabilities')
                if not isinstance(capabilities,dict) or capabilities.get('selfAssessed') is not True:raise ValueError('selfAssessed capabilities are required; unknown vision support must be declared false')
                identity='agent_'+uuid.uuid4().hex;token=secrets.token_hex(32)
                profile={'name':str(arguments.get('name','External agent'))[:128],'capabilities':capabilities,'delivery':'explicit-tool-poll'}
                self.db.execute('INSERT INTO agents(id,token,profile,expires,watching) VALUES (?,?,?,?,0)',(identity,hashlib.sha256(token.encode()).hexdigest(),json.dumps(profile),now+120))
                return {'agentId':identity,'sessionToken':token,'expiresAt':now+120,'automaticWake':False,
                        'next':'Call agent_requests(operation=wait, agentId, sessionToken, waitSeconds<=25) while actively handling user-authorized requests. A waiting tool call returns when the UI creates a matching request. Idle hosts are not automatically launched.'}
            agent=self._agent(arguments)
            if operation=='close':self.db.execute('DELETE FROM agents WHERE id=?',(agent['id'],));return {'closed':True}
            if operation!='heartbeat':raise ValueError('operation must be register/heartbeat/close')
            self.db.execute('UPDATE agents SET expires=? WHERE id=?',(now+120,agent['id']))
            self.db.execute("UPDATE requests SET claim_until=? WHERE owner=? AND status='claimed' AND claim_until>?",(now+120,agent['id'],now))
            return {'agentId':agent['id'],'expiresAt':now+120,'automaticWake':False}

    def _agent(self,arguments):
        row=self.db.execute('SELECT * FROM agents WHERE id=?',(arguments.get('agentId'),)).fetchone()
        digest=hashlib.sha256(str(arguments.get('sessionToken','')).encode()).hexdigest()
        if not row or row['expires']<=time.time() or not secrets.compare_digest(row['token'],digest):raise ValueError('agent session is missing, expired or token does not match')
        return row

    def connections(self,project_id=None):
        now=time.time()
        with self.lock:
            rows=self.db.execute('SELECT profile,watching,watching_project FROM agents WHERE expires>?',(now,)).fetchall()
        profiles=[json.loads(row['profile']) for row in rows]
        eligible=[row for row,profile in zip(rows,profiles) if all(profile['capabilities'].get(key) is True for key in ('selfAssessed','imageInput','toolImageInput'))]
        return {'connectedVisionAgents':len(eligible),'waitingVisionAgents':sum(row['watching']>now and (project_id is None or row['watching_project']==project_id) for row in eligible),
                'capabilityVerification':'self-declared',
                'automaticWake':False,'delivery':'A currently waiting agent tool call can receive a request; merely being connected does not wake an idle model.'}

    def create(self,project,body):
        from cutvoke.rendering.person_cutout import find_video_clip
        clip=find_video_clip(project,body.get('clipId'))
        if body.get('expectedRevision')!=project.revision:raise ValueError('request requires the current expectedRevision')
        kind=body.get('kind','matting.select')
        if kind not in {'matting.select','matting.review'}:raise ValueError('kind must be matting.select or matting.review')
        instruction=body.get('instruction','')
        if not isinstance(instruction,str) or len(instruction)>1200:raise ValueError('instruction must be at most 1200 characters')
        now=time.time();identity='request_'+uuid.uuid4().hex
        with self.lock:
            count=self.db.execute("SELECT COUNT(*) FROM requests WHERE project_id=? AND status IN ('pending','claimed')",(project.project_id,)).fetchone()[0]
            if count>=32:raise ValueError('at most 32 active AI requests per project; complete or cancel earlier requests')
            self.db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (identity,project.project_id,project.revision,kind,clip.id,body.get('jobId'),instruction,'pending','',0,now,now,'{}'))
        return self.get(identity,project.project_id)

    def _serialize(self,row):
        return {'requestId':row['id'],'projectId':row['project_id'],'revision':row['revision'],'kind':row['kind'],'clipId':row['clip_id'],
            'jobId':row['job_id'],'instruction':row['instruction'],'status':row['status'],'owner':row['owner'],
            'claimUntil':row['claim_until'],'createdAt':row['created'],'updatedAt':row['updated'],'result':json.loads(row['result']),
            'requires':{'imageInput':True,'toolImageInput':True},'guide':{'tool':'editor_help','arguments':{'topic':'local-models'}},
            'scope':'Only the explicitly selected clip and requested matting selection/review. Claiming/finishing does not edit the timeline. Use normal revision/lease-controlled tools for authorized edits.'}

    def get(self,identity,project_id):
        with self.lock:
            row=self.db.execute('SELECT * FROM requests WHERE id=? AND project_id=?',(identity,project_id)).fetchone()
            if not row:raise ValueError('AI request not found in this project')
            return self._serialize(row)

    def list(self,project_id,status=None):
        now=time.time()
        with self.lock:
            self.db.execute("UPDATE requests SET status='pending',owner='',claim_until=0 WHERE status='claimed' AND claim_until<?",(now,))
            query='SELECT * FROM requests WHERE project_id=?';args=[project_id]
            if status:query+=' AND status=?';args.append(status)
            query+=' ORDER BY created DESC LIMIT 50'
            return [self._serialize(row) for row in self.db.execute(query,args)]

    def act(self,project,arguments):
        operation=arguments.get('operation','list');pid=project.project_id
        if operation=='list':return {'requests':self.list(pid,arguments.get('status')),'connection':self.connections(pid)}
        if operation=='get':return {'request':self.get(arguments.get('requestId'),pid)}
        if operation=='wait':
            seconds=arguments.get('waitSeconds',20)
            if type(seconds) not in (int,float) or not 0<=seconds<=25:raise ValueError('waitSeconds must be 0..25')
            with self.lock:
                agent=self._agent(arguments);require_vision(json.loads(agent['profile'])['capabilities'])
                self.db.execute('UPDATE agents SET watching=?,watching_project=?,expires=? WHERE id=?',(time.time()+seconds+1,pid,time.time()+120,agent['id']))
            try:
                end=time.monotonic()+seconds
                while True:
                    pending=self.list(pid,'pending')
                    if pending or time.monotonic()>=end:return {'requests':pending,'timedOut':not pending,'automaticWake':False}
                    time.sleep(.2)
            finally:
                with self.lock:self.db.execute('UPDATE agents SET watching=0 WHERE id=?',(agent['id'],))
        identity=arguments.get('requestId')
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                request=self.get(identity,pid)
                if operation=='cancel':
                    if request['status'] not in {'pending','claimed'}:raise ValueError('only active requests can be cancelled')
                    self.db.execute("UPDATE requests SET status='cancelled',updated=? WHERE id=?",(time.time(),identity))
                else:
                    agent=self._agent(arguments);require_vision(json.loads(agent['profile'])['capabilities'])
                    if operation=='claim':
                        if request['revision']!=project.revision:
                            self.db.execute("UPDATE requests SET status='stale',updated=? WHERE id=?",(time.time(),identity))
                        elif request['status']=='pending' or request['status']=='claimed' and request['claimUntil']<time.time():
                            self.db.execute("UPDATE requests SET status='claimed',owner=?,claim_until=?,updated=? WHERE id=?",(agent['id'],time.time()+120,time.time(),identity))
                        else:raise ValueError('request is already owned or no longer pending')
                    elif operation in {'complete','decline'}:
                        if request['status']!='claimed' or request['owner']!=agent['id'] or request['claimUntil']<time.time():raise ValueError('claim is missing, expired or belongs to another agent')
                        result=arguments.get('result',{})
                        if not isinstance(result,dict) or len(json.dumps(result))>16000:raise ValueError('result must be a small structured review object, at most 16 KB')
                        if 'summary' in result and (not isinstance(result['summary'],str) or len(result['summary'])>1200):raise ValueError('result.summary must be text, at most 1200 characters')
                        if operation=='complete' and result.get('reviewedRevision')!=project.revision:raise ValueError('completion requires result.reviewedRevision matching the current project revision; re-inspect after any edit')
                        self.db.execute('UPDATE requests SET status=?,result=?,updated=? WHERE id=?',('completed' if operation=='complete' else 'declined',json.dumps(result,ensure_ascii=False),time.time(),identity))
                    else:raise ValueError('unknown AI request operation')
                self.db.execute('COMMIT')
            except BaseException:self.db.execute('ROLLBACK');raise
            return {'request':self.get(identity,pid)}

    def close(self):
        with self.lock:self.db.close()
