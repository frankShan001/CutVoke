"""Response-only polling hints agree across MCP/HTTP and both job backends."""
from unittest.mock import patch

import pytest

from cutvoke.editing.service import EditService
from cutvoke.interfaces.mcp_server import MCPServer
from cutvoke.storage.export_queue import ExportQueue
from cutvoke.storage.store import ProjectStore


@pytest.fixture(params=[False, True], ids=['memory', 'sqlite'])
def backend(request, tmp_path):
    store = ProjectStore(str(tmp_path / 'projects.sqlite')) if request.param else None
    service = EditService(store)
    project = service.create_project('p')
    server = MCPServer(service, media_dir=str(tmp_path / 'media'))
    queue = ExportQueue(store, service, autostart=False)
    service._export_queue = queue
    job = queue.submit(project, {'outPath': str(tmp_path / 'result.mp4')})
    try:
        yield service, server, queue, job
    finally:
        server.close()
        service.close()
        if store:
            store.close()


@pytest.mark.parametrize('status,delay', [
    ('queued', 1), ('running', 1), ('succeeded', None),
    ('failed', None), ('cancelled', None), ('interrupted', None),
])
def test_poll_hint_for_every_status_and_transport(backend, status, delay):
    service, server, queue, job = backend
    job_id = job['jobId']
    if service.store:
        service.store.finish_export_job(job_id, status=status)
        before = service.store.get_export_job(job_id)
    else:
        queue._jobs[job_id]['status'] = status
        before = queue.get(job_id)
    api = server._agent_tools().api
    # Status reads must never create a queue or trigger restart recovery.
    with patch.object(api, '_export_queue', side_effect=AssertionError('created queue')):
        mcp = server.call_tool('export_job', {'projectId': 'p', 'jobId': job_id})
        http_status, http = api.handle('POST', '/api/v1/agent/export_job',
                                      {'projectId': 'p', 'jobId': job_id})
    assert mcp['ok'], mcp
    assert http_status == 200, http
    for response in (mcp, http):
        assert response['status'] == status
        assert 'pollAfterSeconds' in response
        assert response['pollAfterSeconds'] == delay
        for key, value in before.items():
            assert response[key] == value
    after = service.store.get_export_job(job_id) if service.store else queue.get(job_id)
    assert after == before
    assert 'pollAfterSeconds' not in after


@pytest.mark.parametrize("transport", ["mcp", "http"])
def test_cancel_and_project_isolation(backend, transport):
    service, server, queue, job = backend
    service.create_project('other')
    job_id = job['jobId']
    def call(name, arguments):
        if transport == 'http':
            status, response = server._agent_tools().api.handle('POST', '/api/v1/agent/' + name, arguments)
            assert status == (409 if 'error' in response else 200), response
            return response
        return server.call_tool(name, arguments)
    wrong = call('export_job', {'projectId': 'other', 'jobId': job_id, 'action': 'cancel'})
    assert not wrong.get('ok', False)
    assert wrong['error']['code'] == 'NOT_FOUND'
    assert queue.get(job_id)['status'] == 'queued'
    cancelled = call('export_job', {'projectId': 'p', 'jobId': job_id, 'action': 'cancel'})
    assert cancelled.get('ok', True), cancelled
    assert cancelled['status'] == 'cancelled'
    assert cancelled['pollAfterSeconds'] is None
    missing = call('export_job', {'projectId': 'p', 'jobId': 'missing'})
    assert not missing.get('ok', False)
    assert missing['error']['code'] == 'NOT_FOUND'
    assert 'pollAfterSeconds' not in missing


def test_missing_memory_job_does_not_initialize_queue(tmp_path):
    service = EditService()
    service.create_project('p')
    server = MCPServer(service, media_dir=str(tmp_path / 'media'))
    try:
        api = server._agent_tools().api
        with patch.object(api, '_export_queue', side_effect=AssertionError('created queue')):
            result = server.call_tool('export_job', {'projectId': 'p', 'jobId': 'missing'})
        assert not result['ok']
        assert result['error']['code'] == 'NOT_FOUND'
        assert service._export_queue is None
    finally:
        server.close()


def test_ledger_only_historical_job_does_not_initialize_queue(tmp_path):
    store = ProjectStore(str(tmp_path / 'projects.sqlite'))
    service = EditService(store)
    service.create_project('p')
    store.create_export_job(job_id='historical', project_id='p', revision='0',
                            request_hash='old-job', out_path=str(tmp_path / 'old.mp4'),
                            quality='high', status='interrupted')
    server = MCPServer(service, media_dir=str(tmp_path / 'media'))
    try:
        api = server._agent_tools().api
        with patch.object(api, '_export_queue', side_effect=AssertionError('created queue')):
            result = server.call_tool('export_job', {'projectId': 'p', 'jobId': 'historical'})
        assert result['ok'], result
        assert result['status'] == 'interrupted'
        assert result['pollAfterSeconds'] is None
        assert service._export_queue is None
    finally:
        server.close()
        store.close()
