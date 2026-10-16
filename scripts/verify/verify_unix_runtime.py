"""Audit every native image in the extracted download, never the build tree."""
from pathlib import Path
import json
import os
import platform
import re
import subprocess
import sys
from verify_unix_macho import MACH_MAGICS, read_macho

folder, output = map(Path, sys.argv[1:])
folder = folder.resolve()
mac = sys.platform == 'darwin'
arch = 'arm64' if platform.machine() in {'arm64', 'aarch64'} else 'x86_64'
report = {'passed': False, 'architecture': arch, 'os': platform.platform(), 'images': [], 'links': 0}

def run(*args):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'{args}:\n{result.stdout}')
    return result.stdout

try:
    for file in sorted(folder.rglob('*')):
        if file.is_symlink():
            assert file.resolve().is_relative_to(folder), f'Escaping symlink: {file}'
            assert file.exists(), f'Broken symlink: {file}'
            report['links'] += 1
        if not file.is_file() or file.is_symlink():
            continue
        with file.open('rb') as stream:
            magic = stream.read(4)
        relative = file.relative_to(folder).as_posix()
        if mac and magic in MACH_MAGICS:
            slices = read_macho(file.read_bytes())
            if not report['images']:
                sdk = subprocess.run(['/usr/bin/otool', '-L', str(file)], capture_output=True, text=True)
                report['hostSdkDiagnostic'] = {'exitCode': sdk.returncode, 'output': sdk.stdout + sdk.stderr}
            arches = [item['architecture'] for item in slices]
            assert arch in arches, (relative, arches)
            for item in slices:
                for dependency in item['dependencies'] + item['rpaths']:
                    if not dependency.startswith('/'):
                        assert dependency.startswith(('@rpath/', '@loader_path', '@executable_path')), (relative, dependency)
                        continue
                    assert dependency.startswith(('/System/Library/', '/usr/lib/')), (relative, dependency)
                assert all(version <= (13, 0, 0) for version in item['deployment']), (relative, item['deployment'])
            report['images'].append({'path': relative, 'architectures': arches, 'slices': slices})
        elif not mac and magic == b'\x7fELF':
            header = run('/usr/bin/readelf', '-h', str(file))
            assert 'Advanced Micro Devices X86-64' in header, relative
            dependencies = subprocess.run(['/usr/bin/ldd', str(file)], capture_output=True, text=True)
            dependency_text = dependencies.stdout + dependencies.stderr
            assert 'not found' not in dependency_text, (relative, dependency_text)
            assert dependencies.returncode == 0 or 'not a dynamic executable' in dependency_text or 'statically linked' in dependency_text, (relative, dependency_text)
            symbols = run('/usr/bin/readelf', '--version-info', str(file))
            versions = sorted({tuple(map(int, value.split('.'))) for value in re.findall(r'\bGLIBC_(\d+(?:\.\d+)+)', symbols)})
            assert all(value <= (2, 35) for value in versions), (relative, versions)
            report['images'].append({'path': relative, 'glibc': ['.'.join(map(str, v)) for v in versions], 'dependencies': dependency_text})
    assert len(report['images']) > 10, 'No complete native runtime found'
    if mac:
        app = folder / 'CutVoke.app'
        report['signatureVerification'] = run('/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2', str(app))
        signature = subprocess.run(['/usr/bin/codesign', '-dv', '--verbose=4', str(app)], capture_output=True, text=True)
        report['signature'] = signature.stdout + signature.stderr
        assert 'Signature=adhoc' in report['signature']
        assert 'TeamIdentifier=not set' in report['signature']
        gatekeeper = subprocess.run(['/usr/sbin/spctl', '--assess', '--type', 'execute', '--verbose=4', str(app)], capture_output=True, text=True)
        report['gatekeeperAssessment'] = {'exitCode': gatekeeper.returncode, 'output': gatekeeper.stdout + gatekeeper.stderr,
                                          'quarantinedFinderLaunchAccepted': False}
        executables = [app / 'Contents/MacOS/CutVoke', app / 'Contents/Resources/engine/cutvoke-engine']
    else:
        executables = [folder / 'cutvoke', folder / 'resources/engine/cutvoke-engine']
    bin_dir = executables[1].parent / '_internal/bin'
    executables += [bin_dir / 'ffmpeg', bin_dir / 'ffprobe']
    for executable in executables:
        assert os.access(executable, os.X_OK), executable
    report['passed'] = True
finally:
    output.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({'passed': True, 'images': len(report['images']), 'architecture': arch}))
