"""Real copy migration regressions: dependencies, moved roots and ASR namespace."""
import json
from pathlib import Path
import sqlite3
import pytest
from cutvoke.domain.model import Project, Sequence, Track, Clip, AssetReference
from cutvoke.domain.rational import Rational as R
from cutvoke.storage.store import ProjectStore
from cutvoke.storage.project_directory import DirectoryProjectStore
from cutvoke.storage.project_format_migration import inspect_database, convert_copy
from cutvoke.storage.project_format import ProjectFormatError
from cutvoke.storage.asr_journal import AsrJournal


def project(asset, path=''):
    clip = Clip('c', AssetReference(asset, path), R.of(0), R.of(2), R.of(0))
    return Project('1', 'p', '0', Sequence('s',160,90,R.of(24),tracks=[Track('v','video',clips=[clip])]))


def test_id_only_current_and_history_dependencies_are_owned(tmp_path):
    db=tmp_path/'old.sqlite'
    files={key:tmp_path/(key+'.png') for key in ('current','history','other')}
    for key,path in files.items(): path.write_bytes(key.encode())
    with ProjectStore(str(db)) as store:
        for key,path in files.items():
            store.add_asset(asset_id=key,name=key,path=str(path),size=path.stat().st_size,kind='image')
        store.save(project('current'))
        store._conn.execute('INSERT INTO history VALUES (?,?,?,?)',('p',1,json.dumps(project('history').to_dict()),'0'))
    before=db.read_bytes()
    result=convert_copy(db,'p',inspect_database(db,'p')['token'],tmp_path/'copy')
    assert db.read_bytes()==before
    with DirectoryProjectStore(result['directory']) as store:
        assert store.get_asset('other') is None
        for key in ('current','history'):
            copied=Path(store.get_asset(key)['path'])
            assert copied.is_relative_to(tmp_path/'copy'/'media')
            assert copied.read_bytes()==key.encode()
            files[key].unlink()
            assert copied.read_bytes()==key.encode()
        snapshot=json.loads(store.peek_history(result['projectId'],1))
        assert snapshot['projectId']==result['projectId']
        assert snapshot['schemaVersion']=='2'
        assert 'history' in json.dumps(snapshot)


@pytest.mark.parametrize('old_exists',[False,True])
def test_moved_root_uses_selected_directory_not_obsolete_copy(tmp_path,old_exists):
    old=tmp_path/'old'; selected=tmp_path/'selected'; selected.mkdir()
    if old_exists:
        (old/'media').mkdir(parents=True);(old/'media'/'a.png').write_bytes(b'WRONG')
    (selected/'media').mkdir();(selected/'media'/'a.png').write_bytes(b'RIGHT')
    db=selected/'project.sqlite'; oldpath=str(old/'media'/'a.png')
    with ProjectStore(str(db)) as store:
        store.save(project('a',oldpath))
        store.add_asset(asset_id='a',name='a',path=oldpath,size=5,kind='image')
    manifest={'format':'cutvoke.project-directory','version':1,'database':'project.sqlite','projectId':'p','rootPath':str(old)}
    entry=selected/'project.cutvoke';entry.write_text(json.dumps(manifest)); before=(db.read_bytes(),entry.read_bytes())
    result=convert_copy(db,'p',inspect_database(db,'p')['token'],tmp_path/'copy')
    assert (db.read_bytes(),entry.read_bytes())==before
    with DirectoryProjectStore(result['directory']) as store:
        assert Path(store.get_asset('a')['path']).read_bytes()==b'RIGHT'
        clip=store.load(result['projectId']).sequence.tracks[0].clips[0]
        assert Path(clip.asset_ref.source_path).read_bytes()==b'RIGHT'


def test_manifest_change_invalidates_confirmation_token(tmp_path):
    db=tmp_path/'project.sqlite'
    with ProjectStore(str(db)) as store:store.save(Project('1','p','0',Sequence('s',160,90,R.of(24))))
    entry=tmp_path/'project.cutvoke'
    manifest={'format':'cutvoke.project-directory','version':1,'database':'project.sqlite','projectId':'p','rootPath':'/old'}
    entry.write_text(json.dumps(manifest));token=inspect_database(db,'p')['token']
    manifest['rootPath']='/different';entry.write_text(json.dumps(manifest))
    with pytest.raises(ProjectFormatError,match='changed'):
        convert_copy(db,'p',token,tmp_path/'copy')
    assert not (tmp_path/'copy').exists()


def test_missing_id_only_dependency_never_marks_ready(tmp_path):
    db=tmp_path/'old.sqlite'
    with ProjectStore(str(db)) as store:store.save(project('missing'))
    with pytest.raises(ProjectFormatError,match='Missing asset dependency'):
        convert_copy(db,'p',inspect_database(db,'p')['token'],tmp_path/'copy')
    assert json.loads((tmp_path/'copy'/'project.cutvoke').read_text())['state']=='preparing'


def test_asr_uses_real_v2_namespace_even_with_legacy_table_present(tmp_path):
    root=tmp_path/'new'
    with DirectoryProjectStore(root,create_id='p') as store:
        store.save(Project('2','p','0',Sequence('s',160,90,R.of(24))))
    db=root/'project-v2.sqlite'
    # Simulate an old client having created a separate legacy ASR table.
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE asr_jobs (job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,status TEXT NOT NULL,updated_at REAL NOT NULL,payload_json TEXT NOT NULL,snapshot_json TEXT NOT NULL)')
        conn.execute('INSERT INTO asr_jobs VALUES (?,?,?,?,?,?)',('legacy','old','completed',0,'{"jobId":"legacy"}','{}'))
    job={'jobId':'new','projectId':'p','status':'completed','updatedAt':1}
    journal=AsrJournal(str(db))
    try:
        assert journal.get('legacy') is None
        journal.insert(job,{'schemaVersion':'2'})
    finally:journal.close()
    reopened=AsrJournal(str(db))
    try:
        assert reopened.get('new')==job
        assert reopened.snapshot('new')=={'schemaVersion':'2'}
    finally:reopened.close()
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT job_id FROM asr_jobs').fetchall()==[('legacy',)]
        assert conn.execute('SELECT job_id FROM cv2_asr_jobs').fetchall()==[('new',)]
