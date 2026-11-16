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


def test_one_dot_zero_copy_migrates_current_and_history_without_touching_source(tmp_path):
    source = tmp_path/'historical.sqlite'
    with ProjectStore(str(source)) as store:
        project = Project('1.0','p','0',Sequence('s',160,90,R.of(24)))
        store.save(project)
        store._conn.execute('INSERT INTO history(project_id,depth,snapshot_json,revision) VALUES(?,?,?,?)',
                            ('p',0,json.dumps(project.to_dict()),'0'))
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    probe = inspect_database(source,'p')
    assert probe['status'] == 'legacy'
    assert probe['sourceVersion'] == 1
    assert probe['sourceSchemaVersion'] == '1.0'
    result = convert_copy(source,'p',probe['token'],tmp_path/'converted')
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    with DirectoryProjectStore(result['directory']) as store:
        project = store.load(result['projectId'])
        assert project.schema_version == '2'
        history = json.loads(store.peek_history(result['projectId'],0))
        assert history['schemaVersion'] == '2'
        assert history['projectId'] == project.project_id


def test_inspect_and_convert_leave_closed_wal_source_file_set_unchanged(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    database = source / 'old.sqlite'
    make_legacy(database)
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    probe = inspect_database(database, 'p')
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    convert_copy(database, 'p', probe['token'], tmp_path / 'converted')
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before


def test_wal_only_commit_is_read_without_touching_source_sidecars(tmp_path):
    from cutvoke.storage.project_format_migration import readonly
    source = tmp_path / 'source'
    source.mkdir()
    database = source / 'old.sqlite'
    make_legacy(database)
    writer = sqlite3.connect(database)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute("UPDATE projects SET name='WAL-only name' WHERE project_id='p'")
        writer.commit()
        before = {p.name: p.read_bytes() for p in source.iterdir()}
        probe = inspect_database(database, 'p')
        assert probe['name'] == 'WAL-only name'
        assert {p.name: p.read_bytes() for p in source.iterdir()} == before
        converted = convert_copy(database, 'p', probe['token'], tmp_path / 'converted')
        assert {p.name: p.read_bytes() for p in source.iterdir()} == before
        assert inspect_directory(converted['directory'])['name'] == 'WAL-only name'
    finally:
        writer.close()


def test_snapshot_ignores_uncommitted_wal_frames(tmp_path):
    from cutvoke.storage.project_format_migration import readonly
    from contextlib import closing
    database = tmp_path / 'wal.sqlite'
    writer = sqlite3.connect(database)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('PRAGMA cache_size=2')
        writer.execute('CREATE TABLE values_test (value BLOB)')
        writer.execute("INSERT INTO values_test VALUES ('committed')")
        writer.commit()
        writer.executemany('INSERT INTO values_test VALUES (?)', [(b'x' * 8192,)] * 100)
        files = [database, Path(str(database) + '-wal'), Path(str(database) + '-shm')]
        before = {p: p.read_bytes() for p in files}
        with closing(readonly(database)) as reader:
            assert reader.execute('SELECT value FROM values_test').fetchall() == [('committed',)]
        assert {p: p.read_bytes() for p in files} == before
    finally:
        writer.rollback()
        writer.close()


def test_atomic_replacement_from_other_process_during_snapshot_is_rejected(tmp_path, monkeypatch):
    import subprocess
    import sys
    from cutvoke.storage.project_format_migration import readonly
    database = tmp_path / 'old.sqlite'
    make_legacy(database)
    original = database.read_bytes()
    read = Path.read_bytes
    calls = []
    def racing_read(path):
        data = read(path)
        if path == database:
            # A separate writer replaces an identical database: byte equality
            # alone must not mask the concurrent source identity change.
            subprocess.run([sys.executable, '-c',
                'from pathlib import Path; import os,sys; p=Path(sys.argv[1]); '
                'q=p.with_suffix(".replacement"); q.write_bytes(p.read_bytes()); os.replace(q,p)',
                str(database)], check=True)
            calls.append(1)
        return data
    monkeypatch.setattr(Path, 'read_bytes', racing_read)
    with pytest.raises(ProjectFormatError, match='changed while reading'):
        readonly(database)
    assert len(calls) == 3
    assert read(database) == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ['old.sqlite']
