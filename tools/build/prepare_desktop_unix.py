"""Download official Electron with its npm-pinned checksum, preserving modes/links."""
from pathlib import Path
import hashlib
import json
import platform
import subprocess
import sys
import urllib.request

root = Path(__file__).resolve().parents[2]
folder = root / 'apps/desktop/node_modules/electron'
version = json.loads((folder / 'package.json').read_text())['version']
system = {'darwin': 'darwin', 'linux': 'linux'}[sys.platform]
arch = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'x64', 'AMD64': 'x64'}[platform.machine()]
name = f'electron-v{version}-{system}-{arch}.zip'
expected = json.loads((folder / 'checksums.json').read_text())[name]
archive = root / '.artifacts/build/desktop-downloads' / name
archive.parent.mkdir(parents=True, exist_ok=True)
if not archive.exists():
    with urllib.request.urlopen(f'https://github.com/electron/electron/releases/download/v{version}/{name}', timeout=240) as source, archive.open('wb') as target:
        import shutil
        shutil.copyfileobj(source, target)
assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected, name
destination = folder / 'dist'
destination.mkdir(exist_ok=True)
if system == 'darwin':
    subprocess.run(['ditto', '-x', '-k', str(archive), str(destination)], check=True)
    executable = 'Electron.app/Contents/MacOS/Electron'
else:
    subprocess.run(['unzip', '-q', '-o', str(archive), '-d', str(destination)], check=True)
    executable = 'electron'
(folder / 'path.txt').write_text(executable)
assert (destination / executable).stat().st_mode & 0o111
print(f'Verified {name}: {expected}')
