"""Real streamed upload boundaries, interruption cleanup and identity-safe folder recovery."""
import hashlib
import http.client
import io
import json
from pathlib import Path
import socket
import subprocess
import threading
import time
import sys
import os
import signal
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest
from PIL import Image

from cutvoke.domain.protocol import Actor, Command
from cutvoke.domain.rational import Rational
from cutvoke.editing.service import EditService
from cutvoke.interfaces.httpapi import HttpApi, build_handler
from cutvoke.interfaces.security import Limits
from cutvoke.rendering.render import RenderService
from cutvoke.storage.media_uploads import receive_upload, UploadError
from cutvoke.storage.store import ProjectStore


@pytest.fixture(scope="module")
def api_server(tmp_path_factory):
    root=tmp_path_factory.mktemp("streamed-import")
    store=ProjectStore(str(root/'projects.sqlite'))
    service=EditService(store)
    api=HttpApi(service,RenderService(),media_dir=str(root/'media'),
        limits=Limits(max_body_bytes=1024,max_media_bytes=64*1024*1024))
    server=ThreadingHTTPServer(('127.0.0.1',0),build_handler(api))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield api,server.server_address[1],root
    server.shutdown();server.server_close();thread.join();api.close();store.close()


def request(port,method,path,data=None,headers=None):
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=30)
    conn.request(method,path,body=data,headers=headers or {})
    response=conn.getresponse();raw=response.read();conn.close()
    return response.status,json.loads(raw) if raw else {}


def png(color,size=(32,32)):
    output=io.BytesIO();Image.new('RGB',size,color).save(output,format='PNG');return output.getvalue()


def test_staging_reads_bounded_blocks_and_cleans_every_incomplete_upload(tmp_path):
    class Source(io.BytesIO):
        def read(self,length=-1):
            assert 0<length<=1024*1024
            return super().read(length)
    data=b'x'*(3*1024*1024+19)
    with receive_upload(Source(data),len(data),str(tmp_path),'large.mp4') as staged:
        assert staged.path.stat().st_size==len(data)
        assert staged.sha256==hashlib.sha256(data).hexdigest()
    assert not list((tmp_path/'.uploads').iterdir())
    with pytest.raises(UploadError,match='中断'):
        with receive_upload(Source(b'x'*42),100,str(tmp_path),'partial.mp4'): pass
    assert not list((tmp_path/'.uploads').iterdir())


def test_real_mp4_over_16mib_streams_fully_without_relaxing_json_limit(api_server):
    api,port,root=api_server
    source=root/'large.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=640x360:rate=30:duration=2.5',
        '-vf','noise=alls=100:allf=t+u','-c:v','libx264','-preset','ultrafast','-crf','0','-pix_fmt','yuv420p',str(source)],check=True,capture_output=True)
    assert source.stat().st_size>16*1024*1024
    with source.open('rb') as body:
        status,asset=request(port,'POST','/api/v1/assets?name=large.mp4',body,
            {'Content-Length':str(source.stat().st_size),'Content-Type':'application/octet-stream'})
    assert status==201,asset
    assert Path(asset['path']).stat().st_size==source.stat().st_size
    assert asset['sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
    assert asset['hasVideo'] and asset['width']==640
    Path(asset['path']).unlink()
    with source.open('rb') as body:
        status,restored=request(port,'POST',f"/api/v1/assets/{asset['assetId']}/relink?name=large.mp4&verifyIdentity=1",body,
            {'Content-Length':str(source.stat().st_size),'Content-Type':'application/octet-stream'})
    assert status==200 and restored['path']==asset['path']
    assert Path(restored['path']).stat().st_size==source.stat().st_size
    status,error=request(port,'POST','/api/v1/projects',b' '*1025,{'Content-Type':'application/json'})
    assert status==413 and error['error']['code']=='BODY_TOO_LARGE'
    assert not list((Path(api.media_dir)/'.uploads').iterdir())


def test_disconnect_and_oversize_media_leave_no_partial_asset(api_server):
    api,port,_root=api_server
    sock=socket.create_connection(('127.0.0.1',port))
    sock.sendall(f'POST /api/v1/assets?name=cancelled.mp4 HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: {10*1024*1024}\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()+b'x'*(1024*1024))
    deadline=time.monotonic()+3
    while not list((Path(api.media_dir)/'.uploads').glob('*')) and time.monotonic()<deadline:time.sleep(.01)
    sock.close()
    while list((Path(api.media_dir)/'.uploads').glob('*')) and time.monotonic()<deadline:time.sleep(.01)
    assert not list((Path(api.media_dir)/'.uploads').glob('*'))
    assert not any(asset['name']=='cancelled.mp4' for asset in api.service.store.list_assets())
    conn=http.client.HTTPConnection('127.0.0.1',port)
    conn.putrequest('POST','/api/v1/assets?name=oversize.mp4');conn.putheader('Content-Length',str(65*1024*1024));conn.endheaders()
    response=conn.getresponse();assert response.status==413;response.read();conn.close()
    assert not list((Path(api.media_dir)/'.uploads').glob('*'))
    status,_=request(port,'POST','/api/v1/assets?name=forbidden.png',png('red'),{'Origin':'https://untrusted.example'})
    assert status==403


def test_folder_preview_detects_ambiguity_and_verified_apply_rejects_wrong_content(api_server):
    api,port,_root=api_server
    red,blue=png('red'),png('blue')
    length=max(len(red),len(blue));red=red.ljust(length,b' ');blue=blue.ljust(length,b' ')
    def upload(name,data):
        status,asset=request(port,'POST','/api/v1/assets?'+urlencode({'name':Path(name).name,'relativePath':name}),data)
        assert status==201,asset
        Path(asset['path']).unlink();return asset
    first=upload('day1/a.png',red);second=upload('b.png',blue)
    manifest={'files':[{'fileId':'one','relativePath':'folder/day1/a.png','size':length},
        {'fileId':'two','relativePath':'folder1/b.png','size':length},{'fileId':'three','relativePath':'folder2/b.png','size':length}]}
    status,result=request(port,'POST','/api/v1/assets/relink/scan',json.dumps(manifest).encode(),{'Content-Type':'application/json'})
    assert status==200,result
    rows={row['assetId']:row for row in result['items']}
    assert rows[first['assetId']]['status']=='matched'
    assert rows[second['assetId']]['status']=='ambiguous'
    path=f"/api/v1/assets/{first['assetId']}/relink?name=a.png&verifyIdentity=1"
    status,error=request(port,'POST',path,blue)
    assert status==409 and error['error']['code']=='RELINK_IDENTITY_MISMATCH'
    assert not Path(first['path']).exists()
    status,restored=request(port,'POST',path,red)
    assert status==200 and restored['path']==first['path']
    assert Path(first['path']).read_bytes()==red
    assert not Path(second['path']).exists()
    manifest['files'][0]['relativePath']='../escape/a.png'
    status,error=request(port,'POST','/api/v1/assets/relink/scan',json.dumps(manifest).encode(),{'Content-Type':'application/json'})
    assert status==400 and error['error']['code']=='INVALID_RELATIVE_PATH'


def test_legacy_directory_recovery_requires_confirmation_and_compatible_specs(api_server):
    api,port,_root=api_server
    data=png('red')
    path=Path(api.media_dir)/'asset_legacy.png'
    api.service.store.add_asset(asset_id='asset_legacy',name='legacy.png',path=str(path),size=len(data),kind='image',
        has_video=True,width=32,height=32)
    manifest={'files':[{'fileId':'old','relativePath':'restored/legacy.png','size':len(data)}]}
    status,result=request(port,'POST','/api/v1/assets/relink/scan',json.dumps(manifest).encode(),{'Content-Type':'application/json'})
    row=next(item for item in result['items'] if item['assetId']=='asset_legacy')
    assert row['status']=='unverified' and row['candidates']
    url='/api/v1/assets/asset_legacy/relink?name=legacy.png&verifyIdentity=1'
    status,error=request(port,'POST',url,data)
    assert status==409 and not path.exists()
    api.service.store.add_asset(asset_id='asset_legacy',name='legacy.png',path=str(path),size=len(data),kind='image',
        has_video=True,width=64,height=64)
    status,error=request(port,'POST',url+'&confirmLegacy=1',data)
    assert status==409 and error['error']['code']=='RELINK_MEDIA_MISMATCH'
    assert not path.exists()
    api.service.store.add_asset(asset_id='asset_legacy',name='legacy.png',path=str(path),size=len(data),kind='image',
        has_video=True,width=32,height=32)
    status,restored=request(port,'POST',url+'&confirmLegacy=1',data)
    assert status==200 and restored['sha256']==hashlib.sha256(data).hexdigest()


def test_timeline_media_only_resolves_referenced_active_clip_and_valid_ranges(api_server,monkeypatch):
    import cutvoke.rendering.timeline_media as module
    api,_port,root=api_server
    project=api.service.create_project('timeline-api',width=160,height=90,fps=Rational.of(24))
    source=root/'referenced.png';source.write_bytes(png('red'))
    result=root/'timeline.png';result.write_bytes(png('blue'))
    for kind,payload in [('track.add',{'trackId':'v1','kind':'video'}),('clip.insert',{
        'trackId':'v1','clipId':'allowed','sourcePath':str(source),'timelineStart':{'num':'0','den':'1'},'timelineEnd':{'num':'2','den':'1'}})]:
        project=api.service.get_project('timeline-api')
        api.service.execute(Command(type=kind,command_id=kind,project_id='timeline-api',expected_revision=project.revision,
            actor=Actor('human','test'),payload=payload))
    observed=[]
    def render(**kwargs):observed.append(kwargs);return result
    monkeypatch.setattr(module,'timeline_media',render)
    path='/api/v1/projects/timeline-api/clips/allowed/timeline-media'
    status,data=api.handle('GET',path,{'kind':'thumbnail','w':'320','from':'.5','to':'1.5','path':'C:/private.txt'})
    assert status==200 and data['__png__']==result.read_bytes()
    assert observed[0]['clip'].asset_ref.source_path==str(source)
    status,_=api.handle('GET',path.replace('allowed','not-referenced'),{})
    assert status==404
    for query in ({'from':'nan'},{'from':'-1'},{'to':'3'},{'w':'10'},{'kind':'file'}):
        assert api.handle('GET',path,query)[0]==400
    project=api.service.get_project('timeline-api')
    api.service.execute(Command(type='clip.insert',command_id='fractional-clip',project_id='timeline-api',expected_revision=project.revision,
        actor=Actor('human','test'),payload={'trackId':'v1','clipId':'fractional','sourcePath':str(source),
            'timelineStart':{'num':'2','den':'1'},'timelineEnd':{'num':'8','den':'3'}}))
    conn=http.client.HTTPConnection('127.0.0.1',_port)
    conn.request('GET',path.replace('allowed','fractional')+'?kind=waveform&w=128&from=0.1&to=0.666667')
    response=conn.getresponse();assert response.status==200;assert response.read()==result.read_bytes();conn.close()
    assert observed[-1]['kind']=='waveform' and observed[-1]['width']==128
    assert observed[-1]['start']==.1 and observed[-1]['end']==pytest.approx(2/3)


def test_sync_route_passes_actual_http_query_token(api_server):
    api,port,_root=api_server
    api.service.create_project('sync-query')
    status,first=request(port,'GET','/api/v1/projects/sync-query/sync')
    assert status==200 and not first['unchanged'] and first['project']
    status,second=request(port,'GET','/api/v1/projects/sync-query/sync?'+urlencode({'token':first['token']}))
    assert status==200 and second['unchanged'] and 'project' not in second


def test_killed_upload_is_reaped_on_startup_without_touching_live_or_unrelated_files(tmp_path):
    media=tmp_path/'media';media.mkdir()
    child=subprocess.Popen([sys.executable,'-u','-c',
        "import sys,time,os; from cutvoke.storage.media_uploads import receive_upload\n"
        "class Source:\n"
        " def __init__(self): self.first=True\n"
        " def read(self,size):\n"
        "  if self.first: self.first=False; return b'x'*size\n"
        "  print('receiving:'+str(os.getpid()),flush=True); time.sleep(60); return b''\n"
        "with receive_upload(Source(),32*1024*1024,sys.argv[1],'killed.mp4'): pass\n",str(media)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    owner = None
    try:
        ready=child.stdout.readline().strip()
        assert ready.startswith('receiving:')
        owner=int(ready.split(':')[1])
        dead=list((media/'.uploads').glob(f'{owner}-*.part.mp4'))
        assert len(dead)==1 and dead[0].stat().st_size==1024*1024
        os.kill(owner,signal.SIGTERM);child.wait(timeout=5)
        other=media/'.uploads'/'keep.txt';other.write_text('unrelated')
        with receive_upload(io.BytesIO(b'live'),4,str(media),'live.mp4') as active:
            store=ProjectStore(str(tmp_path/'projects.sqlite'))
            api=HttpApi(EditService(store),RenderService(),media_dir=str(media))
            try:
                assert not dead[0].exists()
                assert active.path.exists() and other.read_text()=='unrelated'
            finally:api.close();store.close()
        assert not list((media/'.uploads').glob('*.part*'))
    finally:
        if owner and child.poll() is None:
            try:os.kill(owner,signal.SIGTERM)
            except ProcessLookupError:pass
        if child.poll() is None:child.kill();child.wait(timeout=5)
        child.stdout.close();child.stderr.close()
