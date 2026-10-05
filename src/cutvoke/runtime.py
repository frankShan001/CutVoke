"""Read-only runtime identity and checks used by CLI, Web and MCP."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request


def web_directory() -> Path | None:
    package = Path(__file__).resolve().parent
    for candidate in (package / 'web', package.parent.parent / 'web' / 'dist'):
        if (candidate / 'index.html').is_file():
            return candidate
    return None


def source_fingerprint() -> str:
    package = Path(__file__).resolve().parent
    files = list(package.glob('*.py')) + list((package / 'core').glob('*.py'))
    files += list((package / 'core').glob('*.json'))
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.relative_to(package).as_posix().encode('utf-8'))
        digest.update(b'\0')
        digest.update(path.read_bytes())
    web = web_directory()
    if web:
        for path in sorted(p for p in web.rglob('*') if p.is_file()):
            digest.update(('web/' + path.relative_to(web).as_posix()).encode('utf-8'))
            digest.update(b'\0')
            digest.update(path.read_bytes())
    return digest.hexdigest()


def runtime_identity(data_path: str | None = None) -> dict:
    from . import __version__
    package = Path(__file__).resolve().parent
    web = web_directory()
    result = {
        'application': 'cutvoke', 'version': __version__,
        'sourceFingerprint': source_fingerprint(), 'processId': os.getpid(),
        'pythonExecutable': sys.executable, 'packageDirectory': str(package),
        'webDirectory': str(web) if web else None,
    }
    if data_path:
        resolved = str(Path(data_path).expanduser().resolve())
        result.update(dataPath=resolved, dataDirectory=str(Path(resolved).parent))
    return result


def diagnose_environment() -> dict:
    checks = []
    for name in ('ffmpeg', 'ffprobe'):
        executable = shutil.which(name)
        check = {'name': name, 'required': True, 'ok': False, 'path': executable}
        if executable:
            try:
                completed = subprocess.run([executable, '-version'], capture_output=True,
                                           text=True, errors='replace', timeout=10)
                check.update(ok=completed.returncode == 0,
                             version=(completed.stdout or completed.stderr).splitlines()[0])
            except (OSError, subprocess.TimeoutExpired, IndexError) as exc:
                check['message'] = str(exc)
        else:
            check['message'] = f'Install {name} and add its bin directory to PATH.'
        checks.append(check)
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg and checks[0]['ok']:
        for option, required_names in (('-encoders', ('libx264', 'aac')),
                                       ('-filters', ('overlay', 'drawtext', 'subtitles'))):
            try:
                completed = subprocess.run([ffmpeg, '-hide_banner', option], capture_output=True,
                                           text=True, errors='replace', timeout=10)
                output = completed.stdout + completed.stderr
                for name in required_names:
                    checks.append({'name': f'ffmpeg:{name}', 'required': True,
                                   'ok': completed.returncode == 0 and name in output})
            except (OSError, subprocess.TimeoutExpired) as exc:
                checks.append({'name': f'ffmpeg:{option}', 'required': True,
                               'ok': False, 'message': str(exc)})
    checks.append({'name': 'cryptography', 'required': True,
                   'ok': importlib.util.find_spec('cryptography') is not None,
                   'message': 'Install CutVoke using pip install -e . or its wheel.'})
    checks.append({'name': 'fontTools', 'required': True,
                   'ok': importlib.util.find_spec('fontTools') is not None,
                   'message': 'Required for matching caption font weights in preview and export.'})
    checks.append({'name': 'web-editor', 'required': True,
                   'ok': web_directory() is not None,
                   'message': 'For a source checkout, run npm ci and npm run build in web/app.'})
    package = Path(__file__).resolve().parent
    for relative in ('core/builtin_presets.json', 'core/builtin_stickers.json',
                     'core/builtin_resource_pack.json'):
        checks.append({'name': relative, 'required': True,
                       'ok': (package / relative).is_file()})
    return {
        'ok': all(check['ok'] for check in checks if check['required']),
        'platform': platform.platform(), 'python': platform.python_version(),
        'ffmpeg': ffmpeg, 'ffprobe': shutil.which('ffprobe'), 'checks': checks,
        'runtime': runtime_identity(),
        'optionalDependencies': {name: importlib.util.find_spec(module) is not None
                                 for name, module in (('asr', 'faster_whisper'),
                                                      ('person', 'mediapipe'))},
    }


def check_running_service(host: str, port: int, data_path: str) -> dict:
    expected = runtime_identity(data_path)
    base = f'http://{host}:{port}'
    headers = ({'X-CutVoke-Token': os.environ['CUTVOKE_TOKEN']}
               if os.environ.get('CUTVOKE_TOKEN') else {})
    try:
        with urllib.request.urlopen(urllib.request.Request(base + '/api/v1/runtime', headers=headers), timeout=2) as response:
            running = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            try:
                with urllib.request.urlopen(urllib.request.Request(base + '/api/v1/capabilities', headers=headers), timeout=2) as response:
                    caps = json.load(response)
                if isinstance(caps, dict) and 'commands' in caps:
                    return {'ok': False, 'status': 'legacy_service', 'url': base,
                            'message': 'A CutVoke service without runtime identity is already running. Stop its own launch window and start again, or select another CUTVOKE_PORT.'}
            except (OSError, ValueError):
                pass
        return {'ok': False, 'status': 'port_in_use', 'url': base,
                'message': f'Port {port} is occupied or requires authentication (HTTP {exc.code}); use its configured token or another CUTVOKE_PORT.'}
    except (OSError, ValueError) as exc:
        # Refused connections mean this launcher can start a service. A live
        # endpoint returning non-JSON is an occupied port, not a free port.
        if isinstance(exc, ValueError):
            return {'ok': False, 'status': 'port_in_use', 'url': base,
                    'message': 'The runtime endpoint did not return JSON.'}
        import socket
        with socket.socket() as connection:
            connection.settimeout(1)
            if connection.connect_ex((host, port)) == 0:
                return {'ok': False, 'status': 'port_in_use', 'url': base,
                        'message': 'The port is listening but its runtime identity could not be read.'}
        return {'ok': True, 'status': 'not_running', 'url': base}
    if not isinstance(running, dict) or running.get('application') != 'cutvoke':
        return {'ok': False, 'status': 'port_in_use', 'url': base,
                'message': 'The port is occupied by an unidentified application.'}
    if os.path.normcase(running.get('dataPath', '')) != os.path.normcase(expected['dataPath']):
        status = 'different_data'
    elif running.get('sourceFingerprint') != expected['sourceFingerprint']:
        status = 'outdated_service'
    else:
        status = 'current'
    return {'ok': status == 'current', 'status': status, 'url': base,
            'running': running, 'expected': expected,
            'message': ('Reuse the current CutVoke service.' if status == 'current' else
                        f'Running CutVoke PID {running.get("processId")} uses different data or an older build. Stop its own launch window and restart, or choose another CUTVOKE_PORT.')}
