from pathlib import Path
import hashlib
import json
import sqlite3
import pytest
from cutvoke.domain.model import Project,Sequence
from cutvoke.domain.rational import Rational as R
from cutvoke.storage.store import ProjectStore
from cutvoke.storage.project_directory import DirectoryProjectStore,ProjectLibraryStore
from cutvoke.storage.project_format import ProjectFormatError
from cutvoke.storage.project_format_migration import inspect_database,inspect_directory,convert_copy


def make_legacy(path):
    with ProjectStore(str(path)) as store:
        store.save(Project('1','p','0',Sequence('s',160,90,R.of(24))))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_copy_conversion_keeps_source_and_isolated_tables(tmp_path):
    source=tmp_path/'old.sqlite'; original=make_legacy(source)
    probe=inspect_database(source,'p')
    assert probe['status']=='legacy'
    result=convert_copy(source,'p',probe['token'],tmp_path/'copy')
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original
    assert inspect_directory(result['directory'])['status']=='current'
    with DirectoryProjectStore(result['directory']) as store:
        assert store.load(result['projectId']).schema_version=='2'
        assert store.load('p') is None
    # A shipped generic store can create empty legacy tables, but cannot access
    # or overwrite authoritative namespaced projects via its normal operations.
    database=Path(result['directory'])/'project-v2.sqlite'
    with ProjectStore(str(database)) as legacy:
        assert legacy.load(result['projectId']) is None
        assert legacy.list_all()==[]
    with DirectoryProjectStore(result['directory']) as current:
        assert current.load(result['projectId']).schema_version=='2'


def test_stale_source_no_destination_created(tmp_path):
    source=tmp_path/'old.sqlite';make_legacy(source)
    token=inspect_database(source,'p')['token']
    with ProjectStore(str(source)) as s:s.rename_project('p','changed')
    with pytest.raises(ProjectFormatError,match='changed'):
        convert_copy(source,'p',token,tmp_path/'copy')
    assert not (tmp_path/'copy').exists()


def test_detect_legacy_directory_without_modifying_manifest_or_database(tmp_path):
    root=tmp_path/'old';root.mkdir();source=root/'project.sqlite'
    original=make_legacy(source)
    manifest={'format':'cutvoke.project-directory','version':1,'database':'project.sqlite','projectId':'p','rootPath':'/old/location'}
    entry=root/'project.cutvoke';entry.write_text(json.dumps(manifest));before=entry.read_bytes()
    assert inspect_directory(root)['status']=='legacy'
    with ProjectLibraryStore(str(tmp_path/'library.sqlite')) as library:
        with pytest.raises(ProjectFormatError):library.open_directory(root)
        assert library.directory('p') is None
    assert entry.read_bytes()==before
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original


def test_existing_destination_and_unknown_schema_refused(tmp_path):
    source=tmp_path/'old.sqlite';make_legacy(source)
    token=inspect_database(source,'p')['token'];dest=tmp_path/'copy';dest.mkdir()
    with pytest.raises(FileExistsError):convert_copy(source,'p',token,dest)
    with sqlite3.connect(source) as c:c.execute("UPDATE projects SET schema_version='999'")
    with pytest.raises(ProjectFormatError):inspect_database(source,'p')


def test_new_format_directory_registers_only_after_conversion(tmp_path):
    source=tmp_path/'old.sqlite';make_legacy(source)
    with ProjectLibraryStore(str(tmp_path/'library.sqlite')) as library:
        result=convert_copy(source,'p',inspect_database(source,'p')['token'],tmp_path/'copy',shared=library)
        assert library.directory(result['projectId']) is None
        opened=library.open_directory(result['directory'])
        assert opened.project_id==result['projectId']
        assert library.load(opened.project_id).schema_version=='2'


def test_http_choice_boundary_no_implicit_conversion(tmp_path):
    from cutvoke.editing.service import EditService
    from cutvoke.interfaces.httpapi import HttpApi
    from cutvoke.rendering.render import RenderService
    source=tmp_path/'library.sqlite';original=make_legacy(source)
    with ProjectLibraryStore(str(source)) as library:
        api=HttpApi(EditService(library),RenderService(),media_dir=str(tmp_path/'media'))
        try:
            status,plan=api.handle('POST','/api/v1/projects/p/format/inspect',{})
            assert status==200 and plan['status']=='legacy'
            assert library.directory('p') is None
            status,blocked=api.handle('GET','/api/v1/projects/p',{})
            assert status==409 and blocked['error']['code']=='PROJECT_CONVERSION_REQUIRED'
            status,result=api.handle('POST','/api/v1/projects/p/format/convert',{'token':plan['token']})
            assert status==200
            assert result['projectId']!='p'
            assert library.load(result['projectId']).schema_version=='2'
        finally:
            api.close()
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original
