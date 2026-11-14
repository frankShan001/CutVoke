"""Current-lease review boundaries and privacy, across memory/SQLite peers."""
import json
from unittest.mock import patch
import pytest
from cutvoke.domain.protocol import Actor, Command
from cutvoke.editing.service import EditError, EditService
from cutvoke.storage.store import ProjectStore


def write(service, token='', cid='change'):
    p = service.get_project('p')
    return service.execute(Command(type='track.add', payload={'trackId': cid, 'kind': 'video'},
        command_id=cid, project_id='p', expected_revision=p.revision,
        edit_lease_id=token, actor=Actor('agent' if token else 'human', 'test')))


@pytest.fixture(params=[False, True])
def pair(request, tmp_path):
    if request.param:
        stores = [ProjectStore(str(tmp_path / 'p.db')) for _ in range(2)]
        a, b = [EditService(store=s) for s in stores]
    else:
        stores = []
        a = b = EditService()
    a.create_project('p')
    yield a, b
    for s in stores: s.close()


def test_boundary_excludes_prior_edits_renew_keeps_boundary_and_read_is_nonmutating(pair):
    a, b = pair
    write(a, cid='before')
    lease = a.acquire_edit_lease('p')
    token = lease['leaseId']
    write(a, token, 'first')
    a.renew_edit_lease('p', token)
    a.acquire_edit_lease('p', lease_id=token)
    write(a, token, 'second')
    before = b.get_project('p').to_dict()
    review = b.review_edit_lease('p', token)
    assert review['available'] and review['acquiredRevision'] == '1' and review['revision'] == '3'
    assert [c['commandId'] for c in review['changes']] == ['first', 'second']
    assert b.get_project('p').to_dict() == before
    assert review['changes'][0]['entityKinds'] == [{'type':'track','change':'created','count':1}]
    assert not review['hasMore']
    assert b.review_edit_lease('p', token, 1)['hasMore']
    assert b.review_edit_lease('p', token, 1)['changes'][0]['commandId'] == 'second'


def test_replacement_release_expiry_reject_stale_review(pair):
    a, b = pair
    old = a.acquire_edit_lease('p')['leaseId']
    write(a, old)
    a.release_edit_lease('p', old)
    with pytest.raises(EditError): b.review_edit_lease('p', old)
    new = a.acquire_edit_lease('p')['leaseId']
    with pytest.raises(EditError): b.review_edit_lease('p', old)
    assert b.review_edit_lease('p', new)['changes'] == []
    with patch('time.time', return_value=10**12):
        with pytest.raises(EditError): b.review_edit_lease('p', new)


def test_legacy_boundary_unavailable_not_guessed(tmp_path):
    import sqlite3
    path = str(tmp_path / 'legacy.db')
    initial = ProjectStore(path)
    EditService(store=initial).create_project('p')
    initial.close()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE edit_leases')
        db.execute('CREATE TABLE edit_leases(project_id TEXT PRIMARY KEY, lease_id TEXT NOT NULL, owner TEXT NOT NULL, expires_at REAL NOT NULL, acquired_at REAL NOT NULL)')
        db.execute("INSERT INTO edit_leases VALUES('p','old','agent',999999999999,0)")
    store = ProjectStore(path)
    a = EditService(store=store)
    a.acquire_edit_lease('p', lease_id='old')
    assert not a.review_edit_lease('p', 'old')['available']
    store.close()


def test_summary_omits_changed_entity_payload(pair):
    a, b = pair
    token = a.acquire_edit_lease('p')['leaseId']
    a.import_srt('p', '1\n00:00:00,000 --> 00:00:01,000\nPRIVATE CAPTION\n', edit_lease_id=token)
    review = b.review_edit_lease('p', token)
    assert review['changes']
    assert 'PRIVATE CAPTION' not in json.dumps(review)
    assert set(review['changes'][0]) == {'previousRevision', 'revision', 'commandId', 'entityCount', 'entityKinds'}


@pytest.mark.parametrize('limit', [0, 51, True, 1.5, '20', None])
def test_invalid_limits_and_http(pair, tmp_path, limit):
    from cutvoke.interfaces.httpapi import HttpApi
    a, b = pair
    token = a.acquire_edit_lease('p')['leaseId']
    with pytest.raises(EditError): b.review_edit_lease('p', token, limit)
    api = HttpApi(service=b, media_dir=str(tmp_path / 'media'))
    status, result = api.handle('POST', '/api/v1/projects/p/edit-lock/review', {'leaseId':token,'limit':limit})
    assert status == 400
    status, result = api.handle('POST', '/api/v1/projects/p/edit-lock/review', {'leaseId':token})
    assert status == 200 and result['changes'] == []


def test_store_review_snapshot_does_not_mix_concurrent_commit(tmp_path):
    stores = [ProjectStore(str(tmp_path / 'snapshot.db')) for _ in range(2)]
    a, b = [EditService(store=s) for s in stores]
    a.create_project('p')
    token = a.acquire_edit_lease('p')['leaseId']
    write(a, token, 'first')
    original = stores[1].db_revision
    def write_between_lease_and_revision(project_id):
        write(a, token, 'second')
        return original(project_id)
    with patch.object(stores[1], 'db_revision', side_effect=write_between_lease_and_revision):
        review = b.review_edit_lease('p', token)
    assert review['revision'] == '1'
    assert [c['commandId'] for c in review['changes']] == ['first']
    assert b.review_edit_lease('p', token)['revision'] == '2'
    for s in stores: s.close()


def test_simultaneous_old_schema_migration(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3
    import threading
    path = str(tmp_path / 'old.db')
    initial = ProjectStore(path)
    initial.close()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE edit_leases')
        db.execute('CREATE TABLE edit_leases(project_id TEXT PRIMARY KEY, lease_id TEXT NOT NULL, owner TEXT NOT NULL, expires_at REAL NOT NULL, acquired_at REAL NOT NULL)')
    barrier = threading.Barrier(2)
    def initialize(_):
        barrier.wait()
        s = ProjectStore(path)
        columns = [r[1] for r in s._conn.execute('PRAGMA table_info(edit_leases)')]
        s.close()
        return columns
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(initialize, range(2)))
    assert all('start_seq' in columns and 'start_revision' in columns for columns in results)
