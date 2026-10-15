"""Assemble the user download from electron-builder's real Windows output."""
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
import pefile
from cryptography.hazmat.backends.openssl.backend import backend as openssl_backend

root = Path(__file__).resolve().parents[2]
version = json.loads((root / 'desktop/package.json').read_text())['version']
name = f'CutVoke-{version}-windows-x64-portable'
staging = root / '.artifacts' / 'portable-staging' / name
if staging.exists():
    shutil.rmtree(staging)
shutil.copytree(root / '.artifacts/desktop/win-unpacked', staging)
for relative in ('CutVoke.exe', 'portable.json', 'resources/engine/cutvoke-engine.exe',
                 'LICENSE.electron.txt', 'LICENSES.chromium.html'):
    if not (staging / relative).is_file():
        raise RuntimeError(f'Missing portable runtime: {relative}')
shutil.copyfile(root / 'LICENSE', staging / 'LICENSE.txt')
shutil.copyfile(root / 'desktop/PORTABLE-README.txt', staging / 'START-HERE.txt')
licenses = staging / 'licenses'
licenses.mkdir()
notice_root = root / 'desktop/licenses'
for notice in json.loads((notice_root / 'provenance.json').read_text('utf-8')):
    assert hashlib.sha256((notice_root / notice['name']).read_bytes()).hexdigest() == notice['sha256'], notice['name']
shutil.copytree(notice_root, licenses / 'runtime-components')
shutil.copytree(root / '.artifacts/portable-ffmpeg/licenses', licenses / 'ffmpeg')
shutil.copyfile(root / '.artifacts/portable-ffmpeg/Corresponding-Source.zip', licenses / 'ffmpeg/Corresponding-Source.zip')
shutil.copyfile(root / 'desktop/ffmpeg-sources.json', licenses / 'ffmpeg-sources.json')
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
shutil.copyfile(python_license, licenses / 'Python-LICENSE.txt')
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
frozen_package = staging / 'resources/engine/_internal/cutvoke'
resource_manifest = json.loads((frozen_package / 'core/builtin_resource_pack.json').read_text('utf-8'))
for resource in resource_manifest['files']:
    path = frozen_package / resource['path']
    assert path.is_file(), f"Missing required resource: {resource['path']}"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == resource['sha256'], resource['path']
commit = os.environ.get('GITHUB_SHA') or subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
info = {'version': version, 'commit': commit, 'platform': 'windows-x64', 'signature': 'unsigned',
        'python': platform.python_version(), 'electron': json.loads((root / 'desktop/package-lock.json').read_text())['packages']['node_modules/electron']['version'],
        'ffmpeg': '7.1.5 (own reproducible GPLv3 build; corresponding source included)',
        'dependencies': dependencies,
        'openssl': {'python': ssl.OPENSSL_VERSION, 'cryptography': openssl_backend.openssl_version_text()},
        'runUrl': f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', 'frankShan001/CutVoke')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"}
(staging / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n', 'utf-8')
# Check that the packaged binaries are real Windows x64 PE executables.
for relative in ('CutVoke.exe', 'resources/engine/cutvoke-engine.exe', 'resources/engine/_internal/bin/ffmpeg.exe', 'resources/engine/_internal/bin/ffprobe.exe'):
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
with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as target:
    for file in sorted(staging.rglob('*')):
        if file.is_file():
            target.write(file, file.relative_to(staging.parent))
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(output / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n', 'ascii')
delivery = {**info, 'filename': archive.name, 'bytes': archive.stat().st_size, 'sha256': digest, 'files': len(files) + 1}
(output / 'download-info.json').write_text(json.dumps(delivery, indent=2) + '\n', 'utf-8')
print(json.dumps(delivery, indent=2))
