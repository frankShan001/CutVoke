"""Failure recovery and boot identity contracts for local delivery."""
import io
import json
from unittest.mock import patch

from cutvoke.core.mcp_server import MCPServer
from cutvoke.core.service import EditService
from cutvoke import runtime


def test_bad_stdio_request_does_not_break_the_next_request():
    incoming = io.StringIO('\n'.join([
        '{broken', '[]',
        json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}),
        json.dumps({'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
                    'params': {'name': 'project_query', 'arguments': {}}}),
        json.dumps({'jsonrpc': '2.0', 'id': 8, 'method': 'ping'}),
    ]) + '\n')
    outgoing = io.StringIO()
    with patch('sys.stdin', incoming), patch('sys.stdout', outgoing):
        MCPServer().run_stdio()
    messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert len(messages) == 4  # initialized notification has no response
    assert messages[0]['error']['code'] == -32700
    assert messages[1]['error']['code'] == -32600
    assert messages[2]['result']['isError'] is True
    assert json.loads(messages[2]['result']['content'][0]['text'])['error']['code'] == 'INVALID_ARGUMENT'
    assert messages[3] == {'jsonrpc': '2.0', 'id': 8, 'result': {}}


def test_schema_rejects_typo_and_invalid_geometry_before_creating_project():
    service = EditService()
    server = MCPServer(service)
    for arguments in ({'width': True}, {'height': 0}, {'fps': float('nan')},
                      {'fps': 0}, {'projectID': 'typo'}, []):
        result = server.call_tool('create_project', arguments)
        assert result['error']['code'] == 'INVALID_ARGUMENT'
    assert service._projects == {}
    created = server.call_tool('create_project', {'projectId': 'fractional', 'fps': 23.976})
    assert created['ok']
    assert created['fps'] == {'num': '2997', 'den': '125'}


def test_edit_error_retains_revision_recovery_flags_and_command_contract():
    service = EditService()
    server = MCPServer(service)
    server.call_tool('create_project', {'projectId': 'contract'})
    response = server.call_tool('command_apply', {
        'projectId': 'contract', 'expectedRevision': '0', 'type': 'track.add',
        'payload': {'trackId': 'video'}, 'commandId': 'once',
    })
    assert response['ok'] and response['commandId'] == 'once'
    assert 'warnings' in response
    response = server.call_tool('command_apply', {
        'projectId': 'contract', 'expectedRevision': '0', 'type': 'track.add', 'payload': {},
    })
    assert not response['ok']
    assert 'retryable' in response['error'] and 'committed' in response['error']


def test_doctor_fails_when_required_executables_are_missing():
    with patch('cutvoke.runtime.shutil.which', return_value=None):
        report = runtime.diagnose_environment()
    assert report['ok'] is False
    missing = {check['name'] for check in report['checks'] if not check['ok']}
    assert {'ffmpeg', 'ffprobe'} <= missing


def test_doctor_requires_caption_font_dependency():
    original = runtime.importlib.util.find_spec
    with patch('cutvoke.runtime.importlib.util.find_spec',
               side_effect=lambda name: None if name == 'fontTools' else original(name)):
        report = runtime.diagnose_environment()
    assert report['ok'] is False
    assert any(check['name'] == 'fontTools' and check['required'] and not check['ok']
               for check in report['checks'])


def test_running_service_comparison_requires_source_and_same_store(tmp_path):
    store = str(tmp_path / 'projects.sqlite')
    expected = runtime.runtime_identity(store)
    running = {**expected, 'sourceFingerprint': 'old-build'}
    def response(_request, **_kwargs):
        return io.BytesIO(json.dumps(running).encode())
    with patch('cutvoke.runtime.urllib.request.urlopen', side_effect=response):
        assert runtime.check_running_service('127.0.0.1', 9999, store)['status'] == 'outdated_service'
        running['sourceFingerprint'] = expected['sourceFingerprint']
        assert runtime.check_running_service('127.0.0.1', 9999, store)['status'] == 'current'
        running['dataPath'] = str(tmp_path / 'other.sqlite')
        assert runtime.check_running_service('127.0.0.1', 9999, store)['status'] == 'different_data'
