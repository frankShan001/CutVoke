"""Official-CLI extraction and native stdio smoke test, NOT desktop-host acceptance.

Run only after native portable acceptance, against the same private staging tree.
The harness uses Python/Node; the tested manifest process has an empty PATH.
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import platform
from pathlib import Path
import queue
import shutil
import signal
import uuid
import subprocess
import threading

ROOT = Path(__file__).resolve().parents[2]


def run(command, **kwargs):
    try:
        return subprocess.run(command, check=True, timeout=180, capture_output=True, **kwargs)
    except subprocess.CalledProcessError as error:
        diagnostic = error.stderr
        print(diagnostic.decode('utf-8', errors='replace') if isinstance(diagnostic, bytes) else diagnostic)
        raise


class Client:
    def __init__(self, install, data, evidence):
        manifest = json.loads((install / 'manifest.json').read_text('utf-8'))
        config = manifest['server']['mcp_config']
        def expand(value):
            return value.replace('${__dirname}', str(install)).replace('${user_config.data_directory}', str(data))
        command = [expand(config['command']), *map(expand, config['args'])]
        assert all('${' not in value for value in command), command
        self.env = os.environ.copy()
        for name in ('PYTHONPATH', 'PYTHONHOME', 'CUTVOKE_FFMPEG_DIR', 'CUTVOKE_FFMPEG', 'CUTVOKE_FFPROBE', 'FFMPEG_BINARY', 'FFPROBE_BINARY'):
            self.env.pop(name, None)
        self.env['PATH'] = ''
        self.stderr = (evidence / (install.name + '.stderr.log')).open('w', encoding='utf-8')
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.stderr, text=True, encoding='utf-8', cwd=evidence, env=self.env,
            **({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}))
        self.messages = queue.Queue()
        self.serial = 0
        self.log = evidence / 'transcript.jsonl'
        def reader():
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    self.messages.put({'unexpectedStdout': line})
            self.messages.put({'stdioClosed': True})
        threading.Thread(target=reader, daemon=True).start()

    def request(self, method, params=None):
        self.serial += 1
        request = {'jsonrpc': '2.0', 'id': self.serial, 'method': method}
        if params is not None:
            request['params'] = params
        self.process.stdin.write(json.dumps(request, ensure_ascii=False) + '\n')
        self.process.stdin.flush()
        response = self.messages.get(timeout=180)
        with self.log.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'request': request, 'response': response}, ensure_ascii=False) + '\n')
        assert response.get('id') == self.serial and 'error' not in response, response
        return response['result']

    def initialized(self):
        self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + '\n')
        self.process.stdin.flush()

    def call(self, tool_name, /, **arguments):
        raw = self.request('tools/call', {'name': tool_name, 'arguments': arguments})
        result = json.loads(next(c['text'] for c in raw['content'] if c['type'] == 'text'))
        assert result['ok'] and not raw.get('isError'), result
        return result, raw

    def close(self):
        try:
            self.process.stdin.close()
            assert self.process.wait(timeout=20) == 0
        finally:
            if self.process.poll() is None:
                if os.name == 'nt':
                    taskkill = Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'
                    subprocess.run([str(taskkill), '/PID', str(self.process.pid), '/T', '/F'], timeout=20, check=False, capture_output=True)
                else:
                    os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=20)
            self.stderr.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staging-parent', type=Path, default=ROOT / '.artifacts/portable-staging')
    parser.add_argument('--output', type=Path, default=ROOT / '.artifacts/acceptance/mcpb')
    args = parser.parse_args()
    staging, = [p for p in args.staging_parent.iterdir() if (p / 'build-info.json').is_file()]
    info = json.loads((staging / 'build-info.json').read_text('utf-8'))
    native_os = {'Windows': 'windows', 'Darwin': 'macos', 'Linux': 'linux'}[platform.system()]
    native_arch = {'AMD64': 'x64', 'x86_64': 'x64', 'arm64': 'arm64', 'aarch64': 'arm64'}[platform.machine()]
    assert info['platform'] == f'{native_os}-{native_arch}', info
    head = run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).stdout.strip()
    assert info['commit'] == head, 'Staging does not match the checked-out verifier commit'
    evidence = args.output.resolve()
    evidence.mkdir(parents=True, exist_ok=False)  # Never reuse stale success evidence.
    spec = importlib.util.spec_from_file_location('build_mcpb', ROOT / 'tools/build/build_mcpb.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    bundle = evidence / 'candidate.mcpb'
    record = builder.build(staging, bundle)
    (evidence / 'build.json').write_text(json.dumps(record, indent=2), 'utf-8')
    npx = shutil.which('npx.cmd' if os.name == 'nt' else 'npx')
    assert npx, 'Node/npm are required for official extraction, not the native server'
    cli = [npx, '--yes', '@anthropic-ai/mcpb@2.1.2']
    data = evidence / '外部 用户数据'
    expected = None
    for iteration in (1, 2):
        install = evidence / f'install-{iteration}'
        result = run([*cli, 'unpack', str(bundle), str(install)])
        (evidence / f'unpack-{iteration}.log').write_bytes(result.stdout + result.stderr)
        run([*cli, 'validate', str(install / 'manifest.json')])
        suffix = '.exe' if os.name == 'nt' else ''
        ffmpeg = install / 'engine/_internal/bin' / ('ffmpeg' + suffix)
        ffprobe = install / 'engine/_internal/bin' / ('ffprobe' + suffix)
        client = Client(install, data, evidence)
        try:
            client.request('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                'clientInfo': {'name': 'native-mcpb-ci', 'version': '1'}})
            client.initialized()
            names = {t['name'] for t in client.request('tools/list')['tools']}
            assert {'create_project', 'media_import', 'preview_frames', 'export'} <= names
            if iteration == 1:
                image = evidence / 'source.png'
                run([str(ffmpeg), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=320x180',
                     '-frames:v', '1', '-threads', '1', str(image)], env=client.env)
                created, _ = client.call('create_project', projectId='mcpb-smoke', name='MCPB 验收', width=320, height=180, fps=30)
                imported, _ = client.call('media_import', projectId='mcpb-smoke', paths=[str(image)])
                lease, _ = client.call('edit_lock', projectId='mcpb-smoke', action='acquire', owner='native-mcpb-ci')
                client.call('command_apply', projectId='mcpb-smoke', type='clip.insert',
                    expectedRevision=created['revision'], editLeaseId=lease['lease']['leaseId'],
                    commandId='insert', actorId='native-mcpb-ci', payload={'trackId': 'video',
                    'createTrackKind': 'video', 'clipId': 'red', 'assetId': imported['assets'][0]['assetId'],
                    'timelineStart': {'num': '0', 'den': '1'}, 'timelineEnd': {'num': '1', 'den': '1'}})
                client.call('edit_lock', projectId='mcpb-smoke', action='release', leaseId=lease['lease']['leaseId'])
            project, _ = client.call('project_query', projectId='mcpb-smoke')
            assert project['revision'] == '1', project
            if expected is not None:
                assert project == expected, 'Same-version reinstallation changed the project'
            expected = project
            frames, wire = client.call('preview_frames', projectId='mcpb-smoke', expectedRevision='1', times=[0.5], width=320)
            png = base64.b64decode(next(c['data'] for c in wire['content'] if c['type'] == 'image'))
            assert png.startswith(b'\x89PNG\r\n\x1a\n')
            preview = evidence / f'preview-{iteration}.png'
            preview.write_bytes(png)
            directory, _ = client.call('project_directory', projectId='mcpb-smoke')
            assert Path(directory['directory']).resolve().is_relative_to(data.resolve()), directory
            video = Path(directory['exportDirectory']) / f'acceptance-{iteration}.mp4'
            client.call('export', projectId='mcpb-smoke', expectedRevision='1', outPath=str(video), quality='low')
            probe = json.loads(run([str(ffprobe), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(video)], env=client.env).stdout)
            stream = next(s for s in probe['streams'] if s['codec_type'] == 'video')
            assert (stream['codec_name'], stream['width'], stream['height']) == ('h264', 320, 180), probe
            assert abs(float(probe['format']['duration']) - 1) < 0.1, probe
            # Decode BOTH preview and exported first frame; a successful export alone is not a picture test.
            for source in (preview, video):
                rgb = run([str(ffmpeg), '-v', 'error', '-i', str(source), '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'], env=client.env).stdout
                assert len(rgb) == 320 * 180 * 3
                pixel = rgb[(90 * 320 + 160) * 3:][:3]
                assert pixel[0] > 200 and pixel[1] < 40 and pixel[2] < 40, list(pixel)
            (evidence / f'probe-{iteration}.json').write_text(json.dumps(probe, indent=2), 'utf-8')
        finally:
            client.close()
        if iteration == 1:
            install.rename(evidence / ('retired-' + uuid.uuid4().hex))
            assert not install.exists()
    (evidence / 'result.json').write_text(json.dumps({'ok': True, 'platform': info['platform'],
        'commit': info['commit'], 'bundleSha256': record['sha256'], 'officialCli': '2.1.2',
        'nativeStdio': True, 'emptyPath': True, 'sameVersionReinstall': True,
        'hostInstallationVerified': False, 'crossVersionMigrationVerified': False}, indent=2), 'utf-8')


if __name__ == '__main__':
    main()
