"""Verify the real stdio editing lifecycle without importing the tested package."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import traceback

EVIDENCE_OUT = None
STAGE = 'arguments'


class StdioClient:
    def __init__(self, executable: str, data: Path, output: Path, source: Path | None, entry: str = 'module'):
        environment = os.environ.copy()
        environment.pop('PYTHONPATH', None)
        environment['PYTHONIOENCODING'] = 'utf-8'
        if source:
            environment['PYTHONPATH'] = str(source / 'src')
        self.errors = (output / f'mcp-{time.time_ns()}.stderr.log').open('w', encoding='utf-8')
        command = ([str(Path(executable).parent / 'cutvoke-mcp.exe'), '--data', str(data)]
                   if entry == 'console' else [executable, '-m', 'cutvoke', 'mcp', '--data', str(data)])
        self.process = subprocess.Popen(command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors,
            text=True, encoding='utf-8', env=environment, cwd=str(output))
        self.messages = queue.Queue()
        self.transcript = []
        self.serial = 0
        def reader():
            for line in self.process.stdout:
                with (output / 'stdio-raw.stdout.log').open('a', encoding='utf-8') as evidence:
                    evidence.write(line)
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    self.messages.put({'unexpectedStdout': line})
            self.messages.put({'stdioClosed': True})
        threading.Thread(target=reader, daemon=True).start()

    def request(self, method: str, params: dict | None = None):
        global STAGE
        STAGE = method + (':' + params.get('name', '') if params and method == 'tools/call' else '')
        self.serial += 1
        request = {'jsonrpc': '2.0', 'id': self.serial, 'method': method}
        if params is not None:
            request['params'] = params
        with (EVIDENCE_OUT / 'stdio-requests.jsonl').open('a', encoding='utf-8') as evidence:
            evidence.write(json.dumps(request, ensure_ascii=False) + '\n')
        self.process.stdin.write(json.dumps(request, ensure_ascii=False) + '\n')
        self.process.stdin.flush()
        response = self.messages.get(timeout=120)
        self.transcript.append({'request': request, 'response': response})
        with (EVIDENCE_OUT / 'stdio-transcript.jsonl').open('a', encoding='utf-8') as evidence:
            evidence.write(json.dumps(self.transcript[-1], ensure_ascii=False) + '\n')
        assert response.get('id') == self.serial, response
        assert 'error' not in response, response
        return response['result']

    def call(self, name: str, arguments: dict, *, ok=True):
        raw = self.request('tools/call', {'name': name, 'arguments': arguments})
        result = json.loads(raw['content'][0]['text'])
        assert result['ok'] is ok, result
        assert raw['isError'] is (not ok), raw
        return result

    def close(self):
        self.process.stdin.close()
        try:
            code = self.process.wait(timeout=20)
            assert code == 0, code
        finally:
            if self.process.poll() is None:
                self.process.terminate()  # Only the child started by this verifier.
            self.errors.close()


def main():
    global EVIDENCE_OUT, STAGE
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--entry', choices=['module', 'console'], default='module')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    EVIDENCE_OUT = output
    STAGE = 'launch-stdio'
    data = output / 'data' / 'projects.sqlite'
    pid, clip_id = 'delivery-real-mcp', 'tide'
    records, checks = [], {}
    client = StdioClient(args.python, data, output, args.source_root, args.entry)
    try:
        info = client.request('initialize', {'protocolVersion': '2024-11-05',
                                          'capabilities': {}, 'clientInfo': {'name': 'delivery-qa', 'version': '1'}})
        tools = client.request('tools/list')['tools']
        assert {tool['name'] for tool in tools} >= {'runtime', 'command_preview', 'edit_lock', 'export'}
        identity = client.call('runtime', {})
        caps = client.call('capabilities', {})
        checks['initializeAndDiscovery'] = True
        invalid = client.call('project_query', {}, ok=False)
        assert invalid['error']['code'] == 'INVALID_ARGUMENT'
        client.request('ping')
        checks['invalidArgumentsRecoverable'] = True
        created = client.call('create_project', {'projectId': pid, 'name': '真实MCP交付验收',
                                                 'width': 320, 'height': 180, 'fps': 30})
        revision = created['revision']
        export_spec = next(item for item in caps['commands'] if item['type'] == 'caption.exportVtt')
        assert export_spec['previewSupported'] is False
        sentinel = output / 'preview-must-not-write.vtt'
        sentinel.write_bytes(b'Existing file must survive command_preview.\n')
        blocked = client.call('command_preview', {'projectId': pid, 'type': 'caption.exportVtt',
            'payload': {'outPath': str(sentinel)}, 'expectedRevision': revision}, ok=False)
        assert blocked['error']['code'] == 'INVALID_ARGUMENT'
        assert sentinel.read_bytes() == b'Existing file must survive command_preview.\n'
        assert client.call('project_summary', {'projectId': pid})['revision'] == revision
        checks['sideEffectPreviewRejectedAndFilePreserved'] = True
        lease = client.call('edit_lock', {'projectId': pid, 'action': 'acquire',
                                          'owner': 'delivery-qa', 'ttlSeconds': 120})['lease']
        def edit(kind, payload):
            nonlocal revision
            preview = client.call('command_preview', {'projectId': pid, 'type': kind,
                'payload': payload, 'expectedRevision': revision})
            assert client.call('project_summary', {'projectId': pid})['revision'] == revision
            result = client.call('command_apply', {'projectId': pid, 'type': kind,
                'payload': payload, 'expectedRevision': revision, 'editLeaseId': lease['leaseId'],
                'actorId': 'delivery-qa', 'commandId': f'qa-{kind}-{revision}'})
            revision = result['revision']
            return result
        edit('clip.insert', {'trackId': 'video', 'createTrackKind': 'video', 'clipId': clip_id,
            'sourcePath': str(args.image.resolve()), 'timelineStart': {'num': '0', 'den': '1'},
            'timelineEnd': {'num': '2', 'den': '1'}})
        edit('builtinPreset.apply', {'clipId': clip_id, 'presetId': 'cutvoke.preset.animation.gentleRock'})
        edit('caption.add', {'captionId': 'caption', 'text': 'MCP真实编辑验收',
            'start': {'num': '0', 'den': '1'}, 'end': {'num': '2', 'den': '1'},
            'style': {'fontSize': 64}})
        edit('caption.update', {'captionId': 'caption', 'fontFamily': 'Noto Serif SC', 'bold': True})
        project = client.call('project_query', {'projectId': pid})['project']
        assert len(project['sequence']['tracks'][0]['clips']) == 1
        assert project['sequence']['captions'][0]['fontFamily'] == 'Noto Serif SC'
        assert project['sequence']['captions'][0]['bold'] is True
        checks['fontFamilyAndWeightEditable'] = True
        client.call('edit_lock', {'projectId': pid, 'action': 'release', 'leaseId': lease['leaseId']})
        checks['previewLeaseEditQueryPersist'] = True
    finally:
        records.extend(client.transcript)
        client.close()
    client = StdioClient(args.python, data, output, args.source_root, args.entry)
    try:
        client.request('initialize', {'protocolVersion': '2024-11-05'})
        reopened = client.call('project_query', {'projectId': pid})['project']
        assert reopened == project
        checks['reopenExactProject'] = True
        undone = client.call('command_apply', {'projectId': pid, 'type': 'history.undo',
                        'payload': {}, 'expectedRevision': reopened['revision']})
        restored = client.call('project_query', {'projectId': pid})['project']
        assert restored['sequence']['captions'][0]['fontFamily'] == 'Noto Sans SC'
        assert restored['sequence']['captions'][0]['bold'] is False
        assert restored['sequence']['tracks'][0]['clips'][0]['effects']
        checks['persistedUndo'] = True
        preflight = client.call('command_apply', {'projectId': pid, 'type': 'project.preflight',
                        'payload': {}, 'expectedRevision': restored['revision']})
        assert preflight['changedEntities'][0]['report']['readyToRender'], preflight
        assert client.call('project_summary', {'projectId': pid})['revision'] == restored['revision']
        checks['readOnlyPreflightReady'] = True
        exported = client.call('export', {'projectId': pid, 'outPath': str(output / 'edited.mp4'), 'quality': 'medium'})
        checks['realExport'] = True
    finally:
        records.extend(client.transcript)
        client.close()
    probe = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
        '-of', 'json', str(output / 'edited.mp4')], capture_output=True, text=True, check=True).stdout)
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(output / 'edited.mp4'),
                    '-f', 'null', 'NUL'], capture_output=True, check=True, timeout=120)
    checks['fullDecode'] = True
    STAGE = 'complete'
    report = {'ok': all(checks.values()), 'checks': checks, 'runtime': identity, 'entry': args.entry,
              'serverInfo': info, 'toolCount': len(tools), 'commandCount': len(caps['commands']),
              'projectPath': str(output / 'project.json'), 'databasePath': str(data),
              'outputPath': str(output / 'edited.mp4'), 'export': exported, 'ffprobe': probe,
              'outputSha256': hashlib.sha256((output / 'edited.mp4').read_bytes()).hexdigest()}
    for name, value in (('project.json', restored), ('stdio-transcript.json', records), ('result.json', report)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'ok': report['ok'], 'checks': checks, 'toolCount': len(tools),
                      'commandCount': report['commandCount'], 'runtime': identity}, ensure_ascii=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        if EVIDENCE_OUT:
            report = {'ok': False, 'stage': STAGE, 'exceptionType': type(exc).__name__,
                      'message': str(exc), 'traceback': traceback.format_exc(),
                      'logsPreserved': True}
            for filename in ('failure.json', 'result.json'):
                (EVIDENCE_OUT / filename).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        raise
