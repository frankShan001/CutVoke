"""Keep current cryptography on Intel Macs by building a pinned static wheel.

Compilers are build-only. No older cryptography or Homebrew runtime is shipped.
"""
from pathlib import Path
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request

assert sys.platform == 'darwin' and platform.machine() == 'x86_64'
root = Path(__file__).resolve().parents[2]
output = root / '.artifacts/intel-crypto'
work = root / '.artifacts/intel-crypto-work'
output.mkdir(parents=True, exist_ok=True)
work.mkdir(parents=True, exist_ok=True)
lock = (root / 'desktop/requirements-build.lock').read_text()
without_crypto, replacements = re.subn(r'(?m)^cryptography==[^\n]+\n(?:[ \t]+--hash=[^\n]+\n)+', '', lock)
assert replacements == 1
build_lock = work / 'build-requirements.lock'
build_lock.write_text(without_crypto + (root / 'desktop/requirements-intel-crypto.lock').read_text())
subprocess.run([sys.executable, '-m', 'pip', 'install', '--require-hashes', '--only-binary=:all:', '-r', str(build_lock)], check=True)

if not (output / 'SHA256.json').exists():
    sources = work / 'sources'
    sources.mkdir(exist_ok=True)
    manifest = root / 'desktop/intel-crypto-sources.json'
    inputs = json.loads(manifest.read_text())
    for item in inputs:
        archive = sources / item['archive']
        if not archive.exists():
            with urllib.request.urlopen(item['url'], timeout=240) as source, archive.open('wb') as destination:
                shutil.copyfileobj(source, destination)
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == item['sha256'], item['name']
        with tarfile.open(archive) as source:
            source.extractall(work, filter='data')
    env = {**os.environ, 'MACOSX_DEPLOYMENT_TARGET': '13.0',
           'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')}
    openssl = work / 'openssl-4.0.3'
    prefix = work / 'openssl-static'
    subprocess.run(['./Configure', 'darwin64-x86_64-cc', 'no-shared', 'no-tests', '-fPIC',
                    '-mmacosx-version-min=13.0', f'--prefix={prefix}', '--libdir=lib'], cwd=openssl, env=env, check=True)
    subprocess.run(['make', f'-j{os.cpu_count() or 2}', 'build_libs'], cwd=openssl, env=env, check=True)
    subprocess.run(['make', 'install_dev'], cwd=openssl, env=env, check=True)
    crypto = work / 'cryptography-50.0.2'
    vendor = sources / 'rust-vendor'
    subprocess.run(['cargo', 'vendor', '--locked', '--versioned-dirs', str(vendor)], cwd=crypto, env=env, check=True)
    env.update(OPENSSL_DIR=str(prefix), OPENSSL_STATIC='1', CARGO_NET_OFFLINE='true')
    wheels = output / 'wheels'
    wheels.mkdir(exist_ok=True)
    subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation', '--no-cache-dir',
                    '--wheel-dir', str(wheels), str(crypto)], env=env, check=True)
    notices = output / 'licenses'
    notices.mkdir(exist_ok=True)
    shutil.copyfile(openssl / 'LICENSE.txt', notices / 'OpenSSL-4.0.3-LICENSE.txt')
    for crate in vendor.iterdir():
        if not crate.is_dir():
            continue
        for file in crate.rglob('*'):
            if file.is_file() and any(token in file.name.lower() for token in ('license', 'notice', 'copying')):
                destination = notices / 'rust' / crate.name / file.relative_to(crate)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, destination)
    for file in (manifest, Path(__file__), build_lock, root / 'desktop/requirements-intel-crypto.lock'):
        shutil.copyfile(file, sources / file.name)
    for name in ('Cargo.lock', 'Cargo.toml'):
        shutil.copyfile(crypto / name, sources / name)
    toolchain = '\n'.join(subprocess.check_output(command, text=True) for command in (['rustc', '--version'], ['cargo', '--version'], ['cc', '--version']))
    (sources / 'toolchain.txt').write_text(toolchain)
    shutil.make_archive(str(output / 'Corresponding-Source'), 'zip', sources)
    info = {'cryptography': '50.0.2', 'openssl': '4.0.3', 'architecture': 'x64',
            'deploymentTarget': '13.0', 'inputs': inputs, 'staticOpenSSL': True, 'toolchain': toolchain}
    (output / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n')
    hashes = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in output.rglob('*') if p.is_file()}
    (output / 'SHA256.json').write_text(json.dumps(hashes, indent=2) + '\n')

for name, expected in json.loads((output / 'SHA256.json').read_text()).items():
    assert hashlib.sha256((output / name).read_bytes()).hexdigest() == expected, name
wheel, = (output / 'wheels').glob('cryptography-50.0.2-*.whl')
digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
wheel_lock = work / 'runtime-wheel.lock'
wheel_lock.write_text(f'cryptography==50.0.2 --hash=sha256:{digest}\n')
subprocess.run([sys.executable, '-m', 'pip', 'install', '--require-hashes', '--no-deps', '--no-index',
                '--find-links', str(wheel.parent), '-r', str(wheel_lock)], check=True)
print(json.dumps({'wheel': wheel.name, 'sha256': digest, 'source': 'pinned native Intel build'}, indent=2))
subprocess.run([sys.executable, '-c',
    'from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; '
    'from cryptography.hazmat.backends.openssl.backend import backend; '
    'key=Ed25519PrivateKey.generate(); message=b"CutVoke Intel native package"; '
    'key.public_key().verify(key.sign(message),message); '
    'assert backend.openssl_version_text().startswith("OpenSSL 4.0.3"), backend.openssl_version_text(); '
    'print(backend.openssl_version_text())'], check=True)
