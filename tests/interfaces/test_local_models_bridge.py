import copy
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from cutvoke.storage.agent_inbox import AgentInbox
from cutvoke.editing.service import EditError
from cutvoke.rendering.person_cutout import PersonCutoutJobManager,PersonCutoutCancelled
from cutvoke.interfaces.httpapi import HttpApi
from cutvoke.interfaces.mcp_server import MCPServer
from tests.support.composition_fixtures import setup

VISION={'selfAssessed':True,'imageInput':True,'toolImageInput':True}


def test_waiting_agent_receives_explicit_ui_request_claim_is_scoped_and_unknown_vision_cannot_claim(tmp_path):
    service,store,renderer,command=setup(tmp_path)
    one=AgentInbox(tmp_path/'inbox.sqlite');two=AgentInbox(tmp_path/'inbox.sqlite')
    try:
        project=service.get_project('composition');before=copy.deepcopy(project.to_dict())
        text_session=one.session('register',{'capabilities':{**VISION,'imageInput':False}})
        request=two.create(project,{'clipId':'base','expectedRevision':project.revision})
        with pytest.raises(EditError):one.act(project,{'operation':'claim','requestId':request['requestId'],**text_session})
        two.act(project,{'operation':'cancel','requestId':request['requestId']})
        session=one.session('register',{'capabilities':VISION,'name':'Test host'})
        output={}
        worker=threading.Thread(target=lambda:output.update(one.act(project,{'operation':'wait','waitSeconds':3,**session})))
        worker.start()
        for _ in range(30):
            if two.connections()['waitingVisionAgents']:break
            time.sleep(.02)
        assert two.connections()['waitingVisionAgents']==1
        assert two.connections('other-project')['waitingVisionAgents']==0
        request=two.create(project,{'clipId':'base','expectedRevision':project.revision,'instruction':'Check the selected subject'})
        worker.join(4);assert not worker.is_alive()
        assert output['requests'][0]['requestId']==request['requestId'] and not output['automaticWake']
        claim=one.act(project,{'operation':'claim','requestId':request['requestId'],**session})
        assert claim['request']['status']=='claimed'
        competitor=two.session('register',{'capabilities':VISION})
        with pytest.raises(ValueError):two.act(project,{'operation':'complete','requestId':request['requestId'],**competitor,'result':{'reviewedRevision':project.revision}})
        with pytest.raises(ValueError):one.act(project,{'operation':'complete','requestId':request['requestId'],**session,'result':{'reviewedRevision':project.revision,'summary':{}}})
        result=one.act(project,{'operation':'complete','requestId':request['requestId'],**session,'result':{'reviewedRevision':project.revision,'summary':'Manual selection reviewed'}})
        assert result['request']['status']=='completed'
        assert service.get_project('composition').to_dict()==before
        old=two.create(project,{'clipId':'base','expectedRevision':project.revision});command('effect.add',clipId='base',effectId='cutvoke.fx.blur',params={})
        stale=one.act(service.get_project('composition'),{'operation':'claim','requestId':old['requestId'],**session})
        assert stale['request']['status']=='stale'
    finally:one.close();two.close();service.close();store.close()


def test_mcp_discovers_self_contained_model_help_and_rejects_text_only_agent_before_job_creation(tmp_path):
    service,store,renderer,command=setup(tmp_path);server=MCPServer(service)
    try:
        tools={tool['name']:tool for tool in server.list_tools()}
        assert {'local_model_status','local_matting_start','local_matting_job','local_matting_apply','agent_session','agent_requests'}<=tools.keys()
        guide=server.call_tool('editor_help',{'topic':'local-models'})
        assert guide['ok'] and 'SELF-ASSESS' in __import__('cutvoke.interfaces.editor_guide',fromlist=['INSTRUCTIONS']).INSTRUCTIONS
        api=server._agent_tools().api
        result=server.call_tool('local_matting_start',{'projectId':'composition','expectedRevision':service.get_project('composition').revision,'clipId':'base','agentCapabilities':{**VISION,'toolImageInput':False}})
        assert result['error']['code']=='AGENT_VISION_REQUIRED'
        assert not api._person_cutout_jobs._jobs
        assert 'never guess' in tools['local_matting_start']['description'].lower()
    finally:server.close();store.close()


def test_cross_process_job_journal_status_and_cancellation(tmp_path):
    service,store,renderer,command=setup(tmp_path)
    a=PersonCutoutJobManager(tmp_path/'jobs.sqlite');b=PersonCutoutJobManager(tmp_path/'jobs.sqlite')
    entered=threading.Event()
    def generate(*args,cancelled,**kwargs):
        entered.set()
        for _ in range(200):
            if cancelled():raise PersonCutoutCancelled('cancelled from peer')
            time.sleep(.01)
        raise AssertionError('peer cancellation did not reach worker')
    try:
        with patch('cutvoke.rendering.person_cutout.availability',return_value={'ready':True,'mediapipeReady':True}),patch('cutvoke.rendering.person_cutout.generate_person_cutout',side_effect=generate):
            job=a.submit(service.get_project('composition'),'base',tmp_path/'media')
            assert entered.wait(2)
            assert b.get(job['jobId'])['projectId']=='composition'
            b.cancel(job['jobId']);a._threads[job['jobId']].join(3)
            assert a.get(job['jobId'])['status']=='cancelled'
    finally:a.close();b.close();service.close();store.close()


def test_agent_applies_ui_job_with_lease_and_lost_response_retry_is_idempotent(tmp_path):
    import subprocess
    from cutvoke.rendering.speech_recognition import source_signature
    service,store,renderer,command=setup(tmp_path)
    server=MCPServer(service);ui=HttpApi(service,media_dir=str(tmp_path/'media'))
    try:
        output=tmp_path/'media'/'.person-cutouts'/'result.mov'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=red:s=96x64:d=3:r=10','-c:v','qtrle','-pix_fmt','argb',str(output)],check=True)
        project=service.get_project('composition');clip=project.sequence.tracks[0].clips[0]
        ui._person_cutout_jobs._journal.put({'jobId':'shared','projectId':project.project_id,'clipId':clip.id,'sourceSignature':source_signature(clip),'status':'completed','phase':'complete','result':{'outputPath':str(output)}})
        lease=server.call_tool('edit_lock',{'projectId':'composition','action':'acquire','owner':'matting-test','ttlSeconds':120})
        assert lease['ok'],lease
        arguments={'projectId':'composition','expectedRevision':project.revision,'jobId':'shared','agentCapabilities':VISION,'editLeaseId':lease['lease']['leaseId'],'commandId':'matting-once'}
        first=server.call_tool('local_matting_apply',arguments);assert first['ok'],first
        second=server.call_tool('local_matting_apply',arguments);assert second['ok'] and second['idempotent'],second
        assert service.get_project('composition').revision==first['command']['revision']
        assert ui._person_cutout_jobs.get('shared')['status']=='applied'
        server.call_tool('edit_lock',{'projectId':'composition','action':'release','leaseId':lease['lease']['leaseId']})
        command('history.undo');assert service.get_project('composition').sequence.tracks[0].clips[0].asset_ref.source_path==str(tmp_path/'red.png')
    finally:ui.close();server.close();store.close()


def test_interrupted_worker_releases_persistent_job_limit(tmp_path):
    from cutvoke.storage.local_jobs import LocalJobs
    journal=LocalJobs(tmp_path/'jobs.sqlite')
    try:
        journal.put({'jobId':'interrupted','projectId':'one','status':'running','workerPid':99999999})
        journal.put({'jobId':'next','projectId':'one','status':'queued'},maximum_pending=1)
        assert journal.get('interrupted')['status']=='failed'
    finally:journal.close()


def test_directory_project_shares_agent_session_and_job_route(tmp_path):
    from cutvoke.storage.project_directory import ProjectLibraryStore
    from cutvoke.domain.protocol import Command
    from PIL import Image
    store=ProjectLibraryStore(str(tmp_path/'library.sqlite'));service=__import__('cutvoke.editing.service',fromlist=['EditService']).EditService(store)
    server=MCPServer(service)
    try:
        project=service.create_project('folder',width=96,height=64)
        source=tmp_path/'source.png';Image.new('RGB',(96,64),'blue').save(source)
        service.execute(Command('clip.insert',{'clipId':'clip','trackId':'v','createTrackKind':'video','sourcePath':str(source),'timelineStart':{'num':'0','den':'1'},'timelineEnd':{'num':'1','den':'1'}},project_id='folder',expected_revision=project.revision))
        project=service.get_project('folder');api=server._agent_tools().api
        session=server.call_tool('agent_session',{'operation':'register','capabilities':VISION})
        code,request=api.handle('POST','/api/v1/projects/folder/agent-requests',{'clipId':'clip','expectedRevision':project.revision})
        assert code==201,request
        claim=server.call_tool('agent_requests',{'projectId':'folder','operation':'claim','requestId':request['request']['requestId'],'agentId':session['agentId'],'sessionToken':session['sessionToken']})
        assert claim['ok'] and claim['request']['status']=='claimed',claim
        child=api._directory_apis['folder'];child._person_cutout_jobs._journal.put({'jobId':'folder-job','projectId':'folder','clipId':'clip','status':'completed'})
        code,job=api.handle('GET','/api/v1/person-cutout-jobs/folder-job',{})
        assert code==200 and job['projectId']=='folder',job
        assert server.call_tool('local_matting_job',{'projectId':'folder','jobId':'folder-job'})['ok']
    finally:server.close();store.close()
