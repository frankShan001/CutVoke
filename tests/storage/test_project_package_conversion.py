from pathlib import Path
import json,hashlib,zipfile
import pytest
from cutvoke.domain.model import Project,Sequence,Track,Clip,AssetReference
from cutvoke.domain.rational import Rational as R
from cutvoke.storage.projectpack import pack_project,unpack_project,ProjectPackError
from cutvoke.storage.project_package_conversion import inspect_package_format,convert_package_copy


def old_package(tmp_path, *, media=False):
    project=Project('2','p','0',Sequence('s',160,90,R.of(24)))
    if media:
        from PIL import Image
        image=tmp_path/'image.png';Image.new('RGB',(160,90),'blue').save(image)
        project.sequence.tracks=[Track('t','video',clips=[Clip('c',AssetReference('a',str(image)),R.of(0),R.of(1),R.of(0))])]
    new=tmp_path/'new.zip'; pack_project(project,str(new))
    old=tmp_path/'old.zip'
    with zipfile.ZipFile(new) as src,zipfile.ZipFile(old,'w') as dst:
        for item in src.infolist():
            raw=src.read(item)
            if item.filename=='project.json':
                doc=json.loads(raw)['document'];doc['schemaVersion']='1'
                for t in doc.get('sequence',{}).get('tracks',[]):
                    for c in t.get('clips',[]):c.pop('keyframeTimeDomain',None)
                for seq in doc.get('sequences',[]):
                    for t in seq.get('tracks',[]):
                        for c in t.get('clips',[]):c.pop('keyframeTimeDomain',None)
                raw=json.dumps(doc).encode()
            if item.filename=='manifest.json':
                manifest=json.loads(raw);manifest['packageFormat']=1;manifest['schemaVersion']='1';raw=json.dumps(manifest).encode()
            dst.writestr(item,raw)
    return old,new


def test_legacy_choice_preserves_original_and_copies_media(tmp_path):
    old,new=old_package(tmp_path,media=True);original=old.read_bytes()
    plan=inspect_package_format(old);assert plan['status']=='legacy'
    assert inspect_package_format(new)['status']=='current'
    with pytest.raises(ProjectPackError):unpack_project(str(old),str(tmp_path/'unapproved'))
    assert not (tmp_path/'unapproved').exists()
    project,mapping,warnings=convert_package_copy(old,plan['token'],tmp_path/'converted')
    assert project.schema_version=='2' and not warnings
    assert project.sequence.tracks[0].clips[0].keyframe_time_domain=='signed-local'
    assert Path(project.sequence.tracks[0].clips[0].asset_ref.source_path).is_relative_to(tmp_path/'converted')
    assert old.read_bytes()==original


def test_stale_token_and_existing_destination_refused(tmp_path):
    old,_=old_package(tmp_path)
    plan=inspect_package_format(old)
    with pytest.raises(ProjectPackError):convert_package_copy(old,'bad',tmp_path/'bad')
    assert not (tmp_path/'bad').exists()
    target=tmp_path/'existing';target.mkdir();(target/'keep').write_text('keep')
    with pytest.raises(ProjectPackError):convert_package_copy(old,plan['token'],target)
    assert (target/'keep').read_text()=='keep'


def test_checksum_failure_never_returns_success(tmp_path):
    old,_=old_package(tmp_path,media=True)
    damaged=tmp_path/'damaged.zip'
    with zipfile.ZipFile(old) as src,zipfile.ZipFile(damaged,'w') as dst:
        for item in src.infolist():dst.writestr(item,b'bad' if item.filename.startswith('media/') else src.read(item))
    plan=inspect_package_format(damaged)
    with pytest.raises(ProjectPackError,match='verified copy'):
        convert_package_copy(damaged,plan['token'],tmp_path/'partial')


def test_http_package_conversion_is_explicit_and_registers_only_verified_copy(tmp_path):
    from cutvoke.editing.service import EditService
    from cutvoke.interfaces.httpapi import HttpApi
    from cutvoke.rendering.render import RenderService
    from cutvoke.storage.project_directory import ProjectLibraryStore
    old,_=old_package(tmp_path,media=True); original=old.read_bytes()
    with ProjectLibraryStore(str(tmp_path/'library.sqlite')) as store:
        api=HttpApi(EditService(store),RenderService(),media_dir=str(tmp_path/'media'))
        try:
            status,plan=api.handle('POST','/api/v1/packages/format/inspect',{'path':str(old)})
            assert status==200 and plan['status']=='legacy' and store.list_all()==[]
            status,result=api.handle('POST','/api/v1/packages/format/convert',{'path':str(old),'token':plan['token']})
            assert status==200
            assert store.load(result['projectId']).schema_version=='2'
        finally:api.close()
    assert old.read_bytes()==original
