"""Run the delivered CMD entry on an isolated port/store; stop only our children."""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
import traceback

EVIDENCE_OUT = None
STAGE = 'arguments'


class ProcessEntry(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
               ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
               ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
               ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
               ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]


def parent_map():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    entry, mapping = ProcessEntry(), {}
    entry.dwSize = ctypes.sizeof(entry)
    try:
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            mapping[entry.th32ProcessID] = entry.th32ParentProcessID
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return mapping


def is_child(pid, root, parents):
    visited = set()
    while pid in parents and pid not in visited:
        visited.add(pid)
        pid = parents[pid]
        if pid == root:
            return True
    return False


def main():
    global EVIDENCE_OUT, STAGE
    parser = argparse.ArgumentParser()
    parser.add_argument('--launcher', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mutate-installed-runtime', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    EVIDENCE_OUT = output
    STAGE = 'launch-command'
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    data = output / 'data'
    environment = os.environ.copy()
    environment.update(CUTVOKE_PORT=str(port), CUTVOKE_DATA=str(data),
                       CUTVOKE_NO_BROWSER='1', CUTVOKE_PAUSE_ON_ERROR='0',
                       PYTHONIOENCODING='utf-8')
    environment.pop('PYTHONPATH', None)
    command = ['cmd.exe', '/d', '/c', str(args.launcher.resolve())]
    stdout = (output / 'startup.stdout.log').open('w', encoding='utf-8')
    stderr = (output / 'startup.stderr.log').open('w', encoding='utf-8')
    process = subprocess.Popen(command, env=environment, stdout=stdout, stderr=stderr,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    runtime = None
    altered_path, original_bytes = None, None
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(f'launcher exited early: {process.returncode}')
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/runtime', timeout=1) as response:
                    runtime = json.load(response)
                break
            except OSError:
                time.sleep(0.2)
        assert runtime and runtime['application'] == 'cutvoke'
        STAGE = 'validate-boot-identity'
        assert Path(runtime['dataPath']).resolve() == (data / 'projects.sqlite').resolve()
        assert is_child(runtime['processId'], process.pid, parent_map())
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=5) as response:
            html = response.read().decode('utf-8')
        assert 'id="root"' in html
        (output / 'index.html').write_text(html, encoding='utf-8')
        STAGE = 'reuse-same-build-and-data'
        reuse = subprocess.run(command, env=environment, capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=15)
        (output / 'reuse.stdout.log').write_text(reuse.stdout, encoding='utf-8')
        (output / 'reuse.stderr.log').write_text(reuse.stderr, encoding='utf-8')
        assert reuse.returncode == 0, reuse.stdout + reuse.stderr
        assert '"status": "current"' in reuse.stdout
        different = {**environment, 'CUTVOKE_DATA': str(output / 'different-data')}
        STAGE = 'refuse-different-data'
        refused = subprocess.run(command, env=different, capture_output=True, text=True,
                                 encoding='utf-8', errors='replace', timeout=15)
        (output / 'different-data.stdout.log').write_text(refused.stdout, encoding='utf-8')
        (output / 'different-data.stderr.log').write_text(refused.stderr, encoding='utf-8')
        assert refused.returncode != 0 and '"status": "different_data"' in refused.stdout
        stale_checks = {}
        if args.mutate_installed_runtime:
            STAGE = 'refuse-outdated-installed-build'
            altered_path = (Path(runtime['packageDirectory']) / 'runtime.py').resolve()
            assert altered_path.is_relative_to(args.launcher.resolve().parent / '.venv')
            original_bytes = altered_path.read_bytes()
            altered_path.write_bytes(original_bytes + b'\n# Isolated delivery validation of a disk-only source update.\n')
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/runtime', timeout=5) as response:
                identity_after_disk_change = json.load(response)
            assert identity_after_disk_change['sourceFingerprint'] == runtime['sourceFingerprint']
            outdated = subprocess.run(command, env=environment, capture_output=True, text=True,
                                      encoding='utf-8', errors='replace', timeout=15)
            (output / 'outdated.stdout.log').write_text(outdated.stdout, encoding='utf-8')
            (output / 'outdated.stderr.log').write_text(outdated.stderr, encoding='utf-8')
            assert outdated.returncode != 0 and '"status": "outdated_service"' in outdated.stdout
            altered_path.write_bytes(original_bytes)
            stale_checks = {'bootIdentityFixedAfterDiskChange': True,
                            'outdatedServiceRefused': True, 'installedSourceRestored': True}
        report = {'ok': True, 'launcher': str(args.launcher.resolve()), 'port': port,
                  'runtime': runtime, 'checks': {'actualCmdStartsWeb': True, 'htmlReactRoot': True,
                  'sameBuildAndDataReused': True, 'differentDataRefused': True,
                  'runtimePidConfirmedOwnDescendant': True, **stale_checks},
                  'reuseExitCode': reuse.returncode, 'differentDataExitCode': refused.returncode,
                  'original8787Touched': False}
        STAGE = 'complete'
        (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True))
    finally:
        if altered_path is not None and original_bytes is not None:
            altered_path.write_bytes(original_bytes)
        parents = parent_map()
        if runtime and is_child(runtime['processId'], process.pid, parents):
            os.kill(runtime['processId'], 15)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        stdout.close()
        stderr.close()


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
