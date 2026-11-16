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


def test_failed_checksum_leaves_no_destination_or_parent(tmp_path):
    old,_=old_package(tmp_path,media=True)
    damaged=tmp_path/'damaged.zip'
    with zipfile.ZipFile(old) as src,zipfile.ZipFile(damaged,'w') as dst:
        for item in src.infolist():dst.writestr(item,b'bad' if item.filename.startswith('media/') else src.read(item))
    before=damaged.read_bytes();plan=inspect_package_format(damaged)
    with pytest.raises(ProjectPackError):convert_package_copy(damaged,plan['token'],tmp_path/'absent'/'copy')
    assert not (tmp_path/'absent').exists()
    assert damaged.read_bytes()==before


@pytest.mark.parametrize('name',['media/../escape.png','media/a\\b.png','media/NUL.png','media/a./b.png'])
def test_unsafe_media_members_never_touch_destination(tmp_path,name):
    old,_=old_package(tmp_path,media=True);bad=tmp_path/'bad.zip'
    with zipfile.ZipFile(old) as src,zipfile.ZipFile(bad,'w') as dst:
        for item in src.infolist():
            raw=src.read(item)
            if item.filename=='manifest.json':
                data=json.loads(raw);data['assets'][0]['packagePath']=name;raw=json.dumps(data).encode()
            dst.writestr(name if item.filename.startswith('media/') else item.filename,raw)
    plan=inspect_package_format(bad)
    with pytest.raises(ProjectPackError):convert_package_copy(bad,plan['token'],tmp_path/'copy')
    assert not (tmp_path/'copy').exists()
    assert not (tmp_path/'escape.png').exists()


def test_symlink_destination_cannot_write_outside(tmp_path):
    old,_=old_package(tmp_path,media=True);outside=tmp_path/'outside';outside.mkdir()
    link=tmp_path/'link'
    try:link.symlink_to(outside,target_is_directory=True)
    except OSError:pytest.skip('symlink creation unavailable on this platform')
    plan=inspect_package_format(old)
    with pytest.raises(ProjectPackError,match='symlink'):
        convert_package_copy(old,plan['token'],link/'copy')
    assert not list(outside.iterdir())


def test_source_changed_during_unpack_never_publishes(tmp_path,monkeypatch):
    from cutvoke.storage import project_package_conversion as conversion
    old,_=old_package(tmp_path,media=True);plan=inspect_package_format(old)
    unpack=conversion.unpack_project
    def changing(*args,**kwargs):
        result=unpack(*args,**kwargs)
        old.write_bytes(old.read_bytes()+b'concurrent change')
        return result
    monkeypatch.setattr(conversion,'unpack_project',changing)
    with pytest.raises(ProjectPackError,match='changed during conversion'):
        convert_package_copy(old,plan['token'],tmp_path/'copy')
    assert old.read_bytes().endswith(b'concurrent change')
    assert not (tmp_path/'copy').exists()


def test_publication_io_failure_removes_only_created_output(tmp_path,monkeypatch):
    from cutvoke.storage import project_package_conversion as conversion
    old,_=old_package(tmp_path,media=True);plan=inspect_package_format(old);original=old.read_bytes()
    def fail(source,target,*args,**kwargs):
        target.write(b'partial')
        raise OSError('injected disk full')
    monkeypatch.setattr(conversion.shutil,'copyfileobj',fail)
    with pytest.raises(OSError,match='disk full'):
        convert_package_copy(old,plan['token'],tmp_path/'new-parent'/'copy')
    assert not (tmp_path/'new-parent').exists() and old.read_bytes()==original


def test_concurrent_destination_creation_is_never_overwritten(tmp_path,monkeypatch):
    from cutvoke.storage import project_package_conversion as conversion
    old,_=old_package(tmp_path,media=True);plan=inspect_package_format(old)
    destination=tmp_path/'copy';publish=conversion._publish_new_copy
    def race(stage,target):
        destination.mkdir();(destination/'other-writer').write_text('keep')
        return publish(stage,target)
    monkeypatch.setattr(conversion,'_publish_new_copy',race)
    with pytest.raises(ProjectPackError,match='new destination'):
        convert_package_copy(old,plan['token'],destination)
    assert list(destination.iterdir())==[destination/'other-writer']
    assert (destination/'other-writer').read_text()=='keep'


def test_http_explicit_conversion_destination_and_symlink_refusal(tmp_path):
    from cutvoke.editing.service import EditService
    from cutvoke.interfaces.httpapi import HttpApi
    from cutvoke.rendering.render import RenderService
    from cutvoke.storage.project_directory import ProjectLibraryStore
    old,_=old_package(tmp_path,media=True);original=old.read_bytes()
    with ProjectLibraryStore(str(tmp_path/'library.sqlite')) as store:
        api=HttpApi(EditService(store),RenderService(),media_dir=str(tmp_path/'media'))
        try:
            status,plan=api.handle('POST','/api/v1/packages/format/inspect',{'path':str(old)})
            assert status==200
            status,result=api.handle('POST','/api/v1/packages/format/convert',
                {'path':str(old),'token':plan['token'],'targetDir':'manual/converted'})
            assert status==200,result
            assert store.load(result['projectId']).schema_version=='2'
            assert list((tmp_path/'manual'/'converted'/'media').iterdir())
            before=store.list_all()
            real=tmp_path/'real';real.mkdir();link=tmp_path/'link'
            try:link.symlink_to(real,target_is_directory=True)
            except OSError:pytest.skip('symlink creation unavailable on this platform')
            status,error=api.handle('POST','/api/v1/packages/format/convert',
                {'path':str(old),'token':plan['token'],'targetDir':'link/converted'})
            assert status==400,error
            assert store.list_all()==before and not list(real.iterdir())
            assert old.read_bytes()==original
        finally:api.close()


@pytest.mark.parametrize('failure_stage', ['catalogue_registration', 'after_commit'])
def test_http_import_failure_preserves_verified_copy_without_claiming_success(tmp_path, monkeypatch, failure_stage):
    from cutvoke.editing.service import EditService
    from cutvoke.interfaces.httpapi import HttpApi
    from cutvoke.rendering.render import RenderService
    from cutvoke.storage.project_directory import ProjectLibraryStore

    old, _ = old_package(tmp_path, media=True)
    original = old.read_bytes()
    target = tmp_path / 'verified-copy'
    with ProjectLibraryStore(str(tmp_path / 'library.sqlite')) as store:
        service = EditService(store)
        calls = []
        real_import = service.import_project
        if failure_stage == 'catalogue_registration':
            # Child snapshot commits first; fail only catalogue registration.
            store._conn.execute("""CREATE TRIGGER fail_registration
                BEFORE INSERT ON project_directories BEGIN
                SELECT RAISE(ABORT, 'injected catalogue failure'); END""")
            store._conn.commit()
        def failing_import(project):
            calls.append(project)
            result = real_import(project)
            if failure_stage == 'after_commit':
                raise RuntimeError('injected error after durable import')
            return result
        monkeypatch.setattr(service, 'import_project', failing_import)
        api = HttpApi(service, RenderService(), media_dir=str(tmp_path / 'media'))
        try:
            plan = inspect_package_format(old)
            status, result = api.handle('POST', '/api/v1/packages/format/convert', {
                'path': str(old), 'token': plan['token'], 'targetDir': str(target)})
            assert status == 500
            assert 'projectId' not in result
            error = result['error']
            assert error['code'] == 'PACKAGE_CONVERSION_IMPORT_UNCONFIRMED'
            assert error['retryable'] is False
            assert error['details'] == {'verifiedCopyPath': str(target), 'importStatus': 'unconfirmed'}
            assert '副本已验证，但导入未确认' in error['message']
            assert len(calls) == 1  # no hidden retry
            assert target.is_dir() and any(target.rglob('*.png'))
            assert old.read_bytes() == original
            if failure_stage == 'catalogue_registration':
                assert store.list_all() == []
                assert service._projects == {}
            else:
                assert store.list_all() == [calls[0].project_id]
                loaded = store.load(calls[0].project_id)
                assert loaded.schema_version == '2'
                for track in loaded.sequence.tracks:
                    for clip in track.clips:
                        assert Path(clip.asset_ref.source_path).is_file()
        finally:
            api.close()
