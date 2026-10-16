"""Assemble downloads from electron-builder output; retain executable modes/links."""
from pathlib import Path
import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import shutil
import struct
import ssl
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from cryptography.hazmat.backends.openssl.backend import backend as openssl_backend

root = Path(__file__).resolve().parents[2]
version = json.loads((root / 'desktop/package.json').read_text())['version']
system = {'win32': 'windows', 'darwin': 'macos', 'linux': 'linux'}[sys.platform]
arch = {'AMD64': 'x64', 'x86_64': 'x64', 'arm64': 'arm64', 'aarch64': 'arm64'}[platform.machine()]
name = f'CutVoke-{version}-{system}-{arch}' + ('-portable' if system == 'windows' else '')
staging = root / '.artifacts' / 'portable-staging' / name
if staging.exists():
    shutil.rmtree(staging)
if system == 'macos':
    staging.mkdir(parents=True)
    output_app = root / '.artifacts/desktop' / ('mac-arm64' if arch == 'arm64' else 'mac') / 'CutVoke.app'
    shutil.copytree(output_app, staging / 'CutVoke.app', symlinks=True)
    resources = Path('CutVoke.app/Contents/Resources')
    executable = Path('CutVoke.app/Contents/MacOS/CutVoke')
else:
    shutil.copytree(root / '.artifacts/desktop' / ('win-unpacked' if system == 'windows' else 'linux-unpacked'), staging, symlinks=True)
    resources = Path('resources')
    executable = Path('CutVoke.exe' if system == 'windows' else 'cutvoke')
engine = resources / 'engine' / ('cutvoke-engine.exe' if system == 'windows' else 'cutvoke-engine')
for relative in (executable, engine, *(['portable.json'] if system == 'windows' else [])):
    if not (staging / relative).is_file():
        raise RuntimeError(f'Missing portable runtime: {relative}')
for source, destination in [('LICENSE', 'LICENSE.electron.txt'), ('LICENSES.chromium.html', 'LICENSES.chromium.html')]:
    if not (staging / destination).exists():
        shutil.copyfile(root / 'desktop/node_modules/electron/dist' / source, staging / destination)
shutil.copyfile(root / 'LICENSE', staging / 'LICENSE.txt')
shutil.copyfile(root / 'desktop' / ('PORTABLE-README.txt' if system == 'windows' else 'UNIX-README.txt'), staging / 'START-HERE.txt')
licenses = staging / 'licenses'
licenses.mkdir()
notice_root = root / 'desktop/licenses'
for notice in json.loads((notice_root / 'provenance.json').read_text('utf-8')):
    assert hashlib.sha256((notice_root / notice['name']).read_bytes()).hexdigest() == notice['sha256'], notice['name']
shutil.copytree(notice_root, licenses / 'runtime-components')
if system == 'linux':
    native_notices = licenses / 'linux-runtime'
    native_notices.mkdir()
    packages = []
    for package in ('libssl3', 'libffi8', 'zlib1g', 'libbz2-1.0', 'liblzma5', 'libexpat1', 'libgcc-s1', 'libstdc++6', 'libuuid1'):
        notice = Path('/usr/share/doc') / package / 'copyright'
        if notice.is_file():
            shutil.copyfile(notice, native_notices / f'{package}-copyright.txt')
            packages.append(subprocess.check_output(['dpkg-query', '-W', '-f', '${Package}\t${Version}\n', package], text=True))
    (native_notices / 'distribution-packages.txt').write_text(''.join(packages))
shutil.copytree(root / '.artifacts/portable-ffmpeg/licenses', licenses / 'ffmpeg')
shutil.copyfile(root / '.artifacts/portable-ffmpeg/Corresponding-Source.zip', licenses / 'ffmpeg/Corresponding-Source.zip')
intel_crypto = root / '.artifacts/intel-crypto'
if system == 'macos' and arch == 'x64':
    shutil.copytree(intel_crypto / 'licenses', licenses / 'intel-cryptography')
    shutil.copyfile(intel_crypto / 'Corresponding-Source.zip', licenses / 'intel-cryptography/Corresponding-Source.zip')
    shutil.copyfile(intel_crypto / 'build-info.json', licenses / 'intel-cryptography/build-info.json')
shutil.copyfile(root / 'desktop/ffmpeg-sources.json', licenses / 'ffmpeg-sources.json')
python_licenses = [Path(sys.base_prefix) / 'LICENSE.txt',
                   Path(sys.base_prefix) / 'Resources/English.lproj/License.rtf',
                   Path(sys.base_prefix) / 'share/doc/python3.13/LICENSE.txt',
                   Path(sys.base_prefix) / 'lib/python3.13/LICENSE.txt']
python_license = next((p for p in python_licenses if p.is_file()), None)
if python_license is None:
    raise RuntimeError(f'Missing Python license in {sys.base_prefix}')
shutil.copyfile(python_license, licenses / ('Python-LICENSE.rtf' if python_license.suffix == '.rtf' else 'Python-LICENSE.txt'))
dependencies = []
for name_ in ('cryptography', 'fonttools', 'cffi', 'pycparser', 'pyinstaller'):
    dist = metadata.distribution(name_)
    destination = licenses / 'python' / name_
    destination.mkdir(parents=True)
    count = 0
    for file in dist.files or []:
        if '.dist-info/' in file.as_posix() and any(token in file.name.lower() for token in ('license', 'copying', 'notice')):
            shutil.copyfile(dist.locate_file(file), destination / file.name)
            count += 1
    if not count:
        raise RuntimeError(f'Missing runtime dependency license: {name_}')
    dependencies.append({'name': name_, 'version': dist.version,
                         'role': 'embedded bootloader' if name_ == 'pyinstaller' else 'Python runtime dependency'})
lock = json.loads((root / 'web/app/package-lock.json').read_text())
for package, item in lock['packages'].items():
    if not package or item.get('dev'):
        continue
    folder = root / 'web/app' / package
    destination = licenses / 'web' / package.removeprefix('node_modules/').replace('/', '_')
    destination.mkdir(parents=True, exist_ok=True)
    files = [p for p in folder.iterdir() if p.is_file() and p.name.lower().startswith(('license', 'copying', 'notice'))]
    if not files:
        raise RuntimeError(f'Missing frontend license: {package}')
    for file in files:
        shutil.copyfile(file, destination / file.name)
    dependencies.append({'name': package.removeprefix('node_modules/'), 'version': item['version'], 'license': item.get('license')})
shutil.copytree(root / 'src/cutvoke/assets/fonts', licenses / 'fonts', ignore=shutil.ignore_patterns('*.ttf'))
for font, expected in [('NotoSansSC-VF.ttf', 'd68bafcb48a2707749396aa12bbbd833cb70401f3a9a689fd2902c7e0d295964'),
                       ('NotoSerifSC-VF.ttf', '5326cfb097e3ab26fcb39329752b5c0a439bf8d5c4649520e4b492939c352a09')]:
    assert hashlib.sha256((root / 'src/cutvoke/assets/fonts' / font).read_bytes()).hexdigest() == expected, font
frozen_package = staging / resources / 'engine/_internal/cutvoke'
resource_manifest = json.loads((frozen_package / 'core/builtin_resource_pack.json').read_text('utf-8'))
for resource in resource_manifest['files']:
    path = frozen_package / resource['path']
    assert path.is_file(), f"Missing required resource: {resource['path']}"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == resource['sha256'], resource['path']
commit = os.environ.get('GITHUB_SHA') or subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
info = {'version': version, 'commit': commit, 'platform': f'{system}-{arch}',
        'signature': 'ad-hoc; no Developer ID; not notarized' if system == 'macos' else 'unsigned',
        'python': platform.python_version(), 'electron': json.loads((root / 'desktop/package-lock.json').read_text())['packages']['node_modules/electron']['version'],
        'ffmpeg': '7.1.5 (own reproducible GPLv3 build; corresponding source included)',
        'dependencies': dependencies,
        'openssl': {'python': ssl.OPENSSL_VERSION, 'cryptography': openssl_backend.openssl_version_text()},
        'runUrl': f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', 'frankShan001/CutVoke')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"}
if system == 'macos' and arch == 'x64':
    info['nativeCryptography'] = json.loads((intel_crypto / 'build-info.json').read_text())
(staging / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n', 'utf-8')
# Check that the packaged binaries are real Windows x64 PE executables.
for relative in (('CutVoke.exe', 'resources/engine/cutvoke-engine.exe', 'resources/engine/_internal/bin/ffmpeg.exe', 'resources/engine/_internal/bin/ffprobe.exe') if system == 'windows' else []):
    import pefile
    with (staging / relative).open('rb') as binary:
        assert binary.read(2) == b'MZ', relative
        binary.seek(0x3c)
        offset = struct.unpack('<I', binary.read(4))[0]
        binary.seek(offset)
        assert binary.read(6) == b'PE\0\0\x64\x86', relative
    if relative in ('CutVoke.exe', 'resources/engine/cutvoke-engine.exe'):
        levels = []
        with pefile.PE(staging / relative) as pe:
            for resource in pe.DIRECTORY_ENTRY_RESOURCE.entries:
                if resource.id == 24:  # RT_MANIFEST
                    for entry in resource.directory.entries:
                        for language in entry.directory.entries:
                            data = language.data.struct
                            manifest = ET.fromstring(pe.get_data(data.OffsetToData, data.Size))
                            levels += [element.attrib.get('level') for element in manifest.iter()
                                       if element.tag.endswith('requestedExecutionLevel')]
        assert levels and all(level == 'asInvoker' for level in levels), (relative, levels)
files = {}
for file in sorted(staging.rglob('*')):
    if file.is_file():
        assert not any(part in {'.git', 'node_modules', '__pycache__', 'CutVoke Data', '.venv'} for part in file.relative_to(staging).parts), file
        files[file.relative_to(staging).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
(staging / 'file-manifest.json').write_text(json.dumps(files, indent=2) + '\n', 'utf-8')
output = root / 'dist/portable'
output.mkdir(parents=True, exist_ok=True)
archive = output / f'{name}.zip'
if system == 'windows':
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as target:
        for file in sorted(staging.rglob('*')):
            if file.is_file():
                target.write(file, file.relative_to(staging.parent))
elif system == 'macos':
    subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(staging), str(archive)], check=True)
else:
    archive = output / f'{name}.tar.gz'
    import tarfile
    with tarfile.open(archive, 'w:gz') as target:
        target.add(staging, arcname=name)
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(output / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n', 'ascii')
delivery = {**info, 'filename': archive.name, 'bytes': archive.stat().st_size, 'sha256': digest, 'files': len(files) + 1}
(output / 'download-info.json').write_text(json.dumps(delivery, indent=2) + '\n', 'utf-8')
print(json.dumps(delivery, indent=2))
