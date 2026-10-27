import copy
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from cutvoke.interfaces.extension_installation import ExtensionInstaller
from cutvoke.interfaces.mcp_server import MCPServer
from cutvoke.rendering.extension_device import inspect_device
from cutvoke.rendering.extension_downloads import Downloader, DownloadUnavailable, InstallCancelled
from cutvoke.rendering.extension_runtime import import_offline, safe_extract
from cutvoke.storage.extension_jobs import ExtensionJobs
from tests.support.composition_fixtures import setup


def artifact(data=b'pinned release bytes', name='model.bin'):
    sha=hashlib.sha256(data).hexdigest()
    return {'id':sha[:16],'filename':name,'sizeBytes':len(data),'sha256':sha,'installedBytes':len(data),'sources':[]}


@pytest.fixture
def origins():
    content=b'fixed immutable model\x00'*60000
    events=[];release=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            events.append((self.path,self.headers.get('Range')))
            if self.path=='/html':
                self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(b'<html>sign in</html>');return
            data=b'x'*len(content) if self.path=='/corrupt' else content
            start=0;end=len(data)-1
            if value:=self.headers.get('Range'):
                a,b=value.removeprefix('bytes=').split('-');start=int(a);end=int(b) if b else end
                self.send_response(206);self.send_header('Content-Range',f'bytes {start}-{end}/{len(data)}')
            else:self.send_response(200)
            if self.path=='/ignore' and start:
                # Range is ignored on the resumed request, forcing a safe restart.
                self.send_header('Content-Range',f'bytes 0-{len(data)-1}/{len(data)}');start=0;end=len(data)-1
            self.send_header('Content-Length',str(end-start+1));self.end_headers()
            try:
                body=data[start:end+1]
                if self.path=='/slow' and len(body)>1024:
                    self.wfile.write(body[:1024]);self.wfile.flush();release.wait(3);body=body[1024:]
                self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:yield content,events,f'http://127.0.0.1:{server.server_port}',release
    finally:release.set();server.shutdown();server.server_close();worker.join(2)


def test_real_download_hash_failure_fallback_resume_and_cache(tmp_path,origins):
    data,events,url,_=origins;a=artifact(data)
    a['sources']=[{'label':'bad','region':'cn','url':url+'/corrupt'},{'label':'good','region':'global','url':url+'/good'}]
    downloader=Downloader(tmp_path,mode='china',allow_http=True)
    path=downloader.fetch(a);assert path.read_bytes()==data
    assert any(p=='/corrupt' and r is None for p,r in events)
    count=len(events);assert downloader.fetch(a)==path and len(events)==count
    path.unlink();partial=path.with_suffix(path.suffix+'.part');partial.write_bytes(data[:333333]);a['sources']=a['sources'][1:]
    assert downloader.fetch(a).read_bytes()==data
    assert ('/good','bytes=333333-') in events


def test_cancel_partial_retention_offline_no_network_and_invalid_sources(tmp_path,origins):
    data,events,url,_=origins;a=artifact(data);a['sources']=[{'url':url+'/good','region':'cn'}]
    flag=threading.Event()
    downloader=Downloader(tmp_path,allow_http=True,event=lambda **value:flag.set() if value.get('fileBytes') else None)
    with pytest.raises(InstallCancelled):downloader.fetch(a,cancelled=flag.is_set)
    assert downloader.path(a).with_suffix('.bin.part').exists()
    count=len(events)
    with pytest.raises(DownloadUnavailable):Downloader(tmp_path,mode='offline',allow_http=True).fetch(a)
    assert len(events)==count
    flag.clear();assert downloader.fetch(a,cancelled=lambda:False).read_bytes()==data
    bad=artifact(b'other');bad['sources']=[{'url':url+'/html','region':'cn'}]
    with pytest.raises(DownloadUnavailable,match='web page'):downloader.fetch(bad)


def test_slow_origin_reports_available_bytes_and_can_cancel_without_filling_a_large_buffer(tmp_path,origins):
    data,events,url,release=origins;a=artifact(data);a['sources']=[{'url':url+'/slow','region':'cn'}]
    cancel=threading.Event();done=threading.Event();errors=[]
    downloader=Downloader(tmp_path,allow_http=True,event=lambda **value:cancel.set() if value.get('fileBytes') else None)
    def transfer():
        try:downloader.fetch(a,cancelled=cancel.is_set)
        except Exception as error:errors.append(error)
        finally:done.set()
    worker=threading.Thread(target=transfer,daemon=True);worker.start()
    try:
        assert done.wait(1.5),'progress/cancellation waited for a full read buffer'
        assert len(errors)==1 and isinstance(errors[0],InstallCancelled)
    finally:release.set();worker.join(3)


def test_offline_bundle_hash_completeness_duplicate_and_zip_escape(tmp_path):
    data=b'real file';a=artifact(data);downloader=Downloader(tmp_path/'cache',mode='offline');archive=tmp_path/'offline.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr(a['filename'],data)
    import_offline(archive,[a],downloader,lambda:False);assert downloader.fetch(a).read_bytes()==data
    archive2=tmp_path/'unsafe.zip'
    with zipfile.ZipFile(archive2,'w') as z:z.writestr('../escape','no')
    with pytest.raises(ValueError):safe_extract(archive2,tmp_path/'extract',100)
    assert not (tmp_path/'escape').exists()
    with zipfile.ZipFile(archive2,'w') as z:z.writestr(a['filename'],b'bad bytes')
    with pytest.raises(ValueError):import_offline(archive2,[artifact(b'new data')],downloader,lambda:False)
    with zipfile.ZipFile(archive2,'w') as z:
        z.writestr(a['filename'],data)
        with pytest.warns(UserWarning):z.writestr(a['filename'],data)
    with pytest.raises(ValueError,match='duplicate'):import_offline(archive2,[a],downloader,lambda:False)


def catalog():
    a=artifact();profiles=[{'id':'person-win','extensionId':'person','system':'windows','architecture':'x64','backend':'cpu','artifacts':[a['id']],'models':{'semantic':a['id']}},
        {'id':'sam2-win-cpu','extensionId':'object','system':'windows','architecture':'x64','backend':'cpu','artifacts':[a['id']],'models':{'sam2':a['id']}},
        {'id':'sam2-win-cuda','extensionId':'object','system':'windows','architecture':'x64','backend':'cuda128','artifacts':[a['id']],'models':{'sam2':a['id']}}]
    return {'version':'test','extensions':[{'id':'person'},{'id':'object'}],'profiles':profiles,'artifacts':{a['id']:a}}


def device(identity,profiles):return {'profileId':'person-win' if identity=='person' else 'sam2-win-cpu','suitability':'recommended'}


def test_device_selects_own_extension_and_memory_gpu_platform_constraints():
    inventory={'system':'windows','architecture':'x64','ramBytes':16*1024**3,'gpus':[]}
    with patch('cutvoke.rendering.extension_device._inventory',return_value=inventory):
        assert inspect_device('object',catalog()['profiles'])['profileId']=='sam2-win-cpu'
        assert inspect_device('person',catalog()['profiles'])['profileId']=='person-win'
        inventory['gpus']=[{'memoryMiB':16000,'driver':'580.1','name':'test'}]
        assert inspect_device('object',catalog()['profiles'])['profileId']=='sam2-win-cuda'
        assert inspect_device('person',catalog()['profiles'])['profileId']=='person-win'
        inventory['ramBytes']=4*1024**3
        assert inspect_device('object',catalog()['profiles'])['suitability']=='not_recommended'
        inventory['system']='macos'
        assert inspect_device('person',catalog()['profiles'])['suitability']=='unsupported'


def test_first_download_progress_uses_the_whole_installation_total(tmp_path):
    captured=[];data=catalog();small=artifact(b'a'*10,'first.bin');large=artifact(b'b'*90,'second.bin')
    data['artifacts']={a['id']:a for a in (small,large)}
    data['profiles'][0]['artifacts']=[small['id'],large['id']]
    class PartialDownload:
        def __init__(self,cache,*,event,**kwargs):self.event=event
        def fetch(self,a,**kwargs):
            self.event(phase='downloading',fileBytes=5,fileTotal=10)
            captured.append(manager.journal.get(job['jobId'])['progress'])
            raise InstallCancelled('cancel during first file')
    manager=ExtensionInstaller(tmp_path/'home',catalog=data,device_provider=device,allow_legacy=False,downloader_factory=PartialDownload)
    try:
        plan=manager.plan('person',install_directory=tmp_path/'runtimes');receipt=manager.confirm(plan['planId'],'accept','Install','ui')
        # Run synchronously so the observed first progress value is deterministic.
        job,_=manager.journal.create_job(plan['planId'],receipt['confirmationId'],os.getpid())
        manager._run(job,plan)
        assert captured==[3],captured
        assert manager.job(job['jobId'])['status']=='cancelled'
    finally:manager.close()


def test_confirmation_required_decline_plan_change_and_expiry(tmp_path):
    manager=ExtensionInstaller(tmp_path/'home',catalog=catalog(),device_provider=device,allow_legacy=False)
    try:
        plan=manager.plan('person',install_directory=tmp_path/'one')
        with pytest.raises(ValueError,match='USER_CONFIRMATION_REQUIRED'):manager.install(plan['planId'],'invented')
        declined=manager.confirm(plan['planId'],'decline','不安装','ui')
        assert manager.plan('person',install_directory=tmp_path/'one')['state']=='declined'
        with pytest.raises(ValueError,match='USER_CONFIRMATION_REQUIRED'):manager.install(plan['planId'],declined['confirmationId'])
        receipt=manager.confirm(plan['planId'],'accept','现在安装人物抠像','agent')
        switched=manager.plan('person',install_directory=tmp_path/'one',source_mode='global')
        assert switched['planId']==plan['planId'] and switched['confirmation']['confirmationId']==receipt['confirmationId']
        changed=manager.plan('person',install_directory=tmp_path/'two')
        with pytest.raises(ValueError,match='USER_CONFIRMATION_REQUIRED'):manager.install(changed['planId'],receipt['confirmationId'])
        manager.journal.db.execute('UPDATE plans SET expires=0 WHERE id=?',(plan['planId'],))
        with pytest.raises(ValueError,match='expired'):manager.install(plan['planId'],receipt['confirmationId'])
        assert not manager.journal.list()
    finally:manager.close()


def test_ui_mcp_shared_job_coalesces_peer_cancel_retries_and_preserves_old_install(tmp_path):
    home=tmp_path/'home';one=ExtensionInstaller(home,catalog=catalog(),device_provider=device,allow_legacy=False)
    two=ExtensionInstaller(home,catalog=catalog(),device_provider=device,allow_legacy=False);entered=threading.Event()
    def build(*args,cancelled,**kwargs):
        entered.set()
        while not cancelled():time.sleep(.01)
        raise InstallCancelled('cancelled')
    try:
        plan=one.plan('person',install_directory=tmp_path/'runtimes');receipt=one.confirm(plan['planId'],'accept','安装','ui')
        with patch('cutvoke.interfaces.extension_installation.install_runtime',side_effect=build):
            job=one.install(plan['planId'],receipt['confirmationId']);assert entered.wait(2)
            assert two.install(plan['planId'],receipt['confirmationId'])['jobId']==job['jobId']
            two.job(job['jobId'],'cancel')
            for _ in range(100):
                if two.job(job['jobId'])['status']=='cancelled':break
                time.sleep(.02)
            assert two.job(job['jobId'])['status']=='cancelled'
        old={'person':{'verified':True,'root':'original-install'}}
        (home/'active.json').write_text(json.dumps(old))
        with patch('cutvoke.interfaces.extension_installation.install_runtime',side_effect=RuntimeError('smoke failed')):
            retry=two.install(plan['planId'],receipt['confirmationId'])
            for _ in range(100):
                if two.job(retry['jobId'])['status']=='failed':break
                time.sleep(.02)
            assert two.job(retry['jobId'])['status']=='failed'
        assert json.loads((home/'active.json').read_text())==old
    finally:one.close();two.close()


def test_upgrade_cannot_install_new_bytes_under_old_consent_but_same_hash_mirror_changes_are_allowed(tmp_path):
    data=catalog();manager=ExtensionInstaller(tmp_path/'home',catalog=data,device_provider=device,allow_legacy=False)
    try:
        plan=manager.plan('person',install_directory=tmp_path/'runtime');receipt=manager.confirm(plan['planId'],'accept','Install this plan','agent')
        a=next(iter(data['artifacts'].values()));a['sources'].append({'url':'https://mirror.example/model.bin','region':'cn'})
        same=manager.plan('person',install_directory=tmp_path/'runtime')
        assert same['planId']==plan['planId'] and same['confirmation']['confirmationId']==receipt['confirmationId']
        a['sha256']='f'*64
        with pytest.raises(ValueError,match='INSTALLATION_PLAN_CHANGED'):manager.install(plan['planId'],receipt['confirmationId'])
        with pytest.raises(ValueError,match='INSTALLATION_PLAN_CHANGED'):manager.confirm(plan['planId'],'accept','Old plan','agent')
        new=manager.plan('person',install_directory=tmp_path/'runtime')
        assert new['planId']!=plan['planId'] and new['confirmation'] is None
        assert not manager.journal.list()
    finally:manager.close()


def test_dead_worker_is_recoverable_and_installation_is_single_global_job(tmp_path):
    journal=ExtensionJobs(tmp_path/'jobs.sqlite')
    try:
        plan=journal.plan({'installable':True,'extensionId':'person','profileId':'person-win'},'same')
        consent=journal.confirm(plan['planId'],'accept','Install','agent')
        dead,_=journal.create_job(plan['planId'],consent['confirmationId'],2147483647)
        assert journal.get(dead['jobId'])['status']=='interrupted'
        live,_=journal.create_job(plan['planId'],consent['confirmationId'],os.getpid())
        other=journal.plan({'installable':True,'extensionId':'object','profileId':'sam2-win-cpu'},'other');receipt=journal.confirm(other['planId'],'accept','Install object','ui')
        with pytest.raises(ValueError,match='INSTALLATION_BUSY'):journal.create_job(other['planId'],receipt['confirmationId'],os.getpid())
        assert journal.get(live['jobId'])['status']=='queued'
    finally:journal.close()


def test_mcp_installer_discoverable_without_project_and_vision_not_required_for_status(tmp_path,monkeypatch):
    monkeypatch.setenv('CUTVOKE_EXTENSION_HOME',str(tmp_path/'extensions'))
    service,store,renderer,command=setup(tmp_path);server=MCPServer(service)
    try:
        tools={tool['name']:tool for tool in server.list_tools()}
        assert {'extension_status','extension_plan','extension_confirm','extension_install','extension_job','extension_export_offline'}<=tools.keys()
        status=server.call_tool('extension_status',{});assert status['ok'] and status['automaticModelDownload'] is False
        api=server._agent_tools().api;api.extension_installer.allow_legacy=False
        plan=server.call_tool('extension_plan',{'extensionId':'person','installDirectory':str(tmp_path/'runtime')});assert plan['ok'],plan
        denied=server.call_tool('extension_install',{'planId':plan['plan']['planId'],'confirmationId':'fabricated'})
        assert denied['error']['code']=='USER_CONFIRMATION_REQUIRED',denied
        assert not api.extension_installer.journal.list()
        guide=server.call_tool('editor_help',{'topic':'local-models'});assert 'extension_confirm' in json.dumps(guide)
    finally:server.close();store.close()
