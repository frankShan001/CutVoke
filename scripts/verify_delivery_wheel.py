"""Install a wheel in a clean environment and verify bundled byte identities."""
from __future__ import annotations
import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
import traceback

EVIDENCE_OUT = None
STAGE = 'arguments'


def main():
    global EVIDENCE_OUT, STAGE
    parser = argparse.ArgumentParser()
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    args = parser.parse_args()
    output, root = args.output.resolve(), args.source_root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    EVIDENCE_OUT = output
    STAGE = 'prepare-release'
    release = output / 'release'
    release.mkdir(exist_ok=True)
    wheel = release / args.wheel.name
    shutil.copy2(args.wheel.resolve(), wheel)
    shutil.copy2(root / 'start-cutvoke-wheel.cmd', release / 'start-cutvoke-wheel.cmd')
    shutil.copy2(root / 'docs/LOCAL_DELIVERY_MCP.md', release / 'LOCAL_DELIVERY_MCP.md')
    environment = os.environ.copy()
    environment.pop('PYTHONPATH', None)
    environment['PYTHONIOENCODING'] = 'utf-8'
    clean_env = release / '.venv'
    STAGE = 'create-clean-venv'
    assert not clean_env.exists(), f'Use a fresh evidence output directory; existing environment: {clean_env}'
    created = subprocess.run([sys.executable, '-m', 'venv', str(clean_env)], capture_output=True,
                             text=True, encoding='utf-8', errors='replace')
    (output / 'venv.stdout.log').write_text(created.stdout, encoding='utf-8')
    (output / 'venv.stderr.log').write_text(created.stderr, encoding='utf-8')
    assert created.returncode == 0, created.stderr
    executable = clean_env / 'Scripts/python.exe'
    STAGE = 'install-wheel'
    installed = subprocess.run([str(executable), '-m', 'pip', 'install', '--disable-pip-version-check',
        str(wheel)], env=environment, cwd=release, capture_output=True, text=True, encoding='utf-8', errors='replace')
    (output / 'install.stdout.log').write_text(installed.stdout, encoding='utf-8')
    (output / 'install.stderr.log').write_text(installed.stderr, encoding='utf-8')
    assert installed.returncode == 0, installed.stderr
    (clean_env / 'cutvoke-wheel.sha256').write_text(hashlib.sha256(wheel.read_bytes()).hexdigest())
    def query(arguments):
        global STAGE
        STAGE = 'installed-cli-' + arguments[0]
        result = subprocess.run([str(executable), '-m', 'cutvoke', *arguments], env=environment,
            cwd=release, capture_output=True, text=True, encoding='utf-8', errors='replace')
        (output / (arguments[0] + '.stdout.log')).write_text(result.stdout, encoding='utf-8')
        (output / (arguments[0] + '.stderr.log')).write_text(result.stderr, encoding='utf-8')
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)
    doctor = query(['doctor', '--json'])
    assert doctor['ok']
    runtime = doctor['runtime']
    installed_package = Path(runtime['packageDirectory']).resolve()
    assert installed_package.is_relative_to(clean_env)
    assert Path(runtime['webDirectory']).resolve().is_relative_to(installed_package)
    caps = query(['capabilities', '--json'])
    STAGE = 'installed-cold-caption-font'
    font_cache = output / 'cold-font-cache'
    assert not font_cache.exists(), 'Cold font verification must use a new isolated cache.'
    font_environment = {**environment, 'CUTVOKE_FONT_CACHE': str(font_cache)}
    font_code = '''from cutvoke.core.caption_render import _caption_font_instance, resolve_title_font
from fontTools.ttLib import TTFont
from pathlib import Path
import time,json,hashlib
t=time.perf_counter(); alias,path=_caption_font_instance("Noto Sans SC",False); cold=time.perf_counter()-t
t=time.perf_counter(); _caption_font_instance("Noto Sans SC",False); warm=time.perf_counter()-t
with TTFont(path) as f:
 assert "fvar" not in f
 assert f["OS/2"].usWeightClass==400
 assert f["name"].getDebugName(1)==alias
print(json.dumps({"sourceFont":resolve_title_font("Noto Sans SC"),"cachePath":path,"staticFont":True,"weight":400,"coldSeconds":cold,"warmSeconds":warm,"sha256":hashlib.sha256(Path(path).read_bytes()).hexdigest()}))
'''
    font_run = subprocess.run([str(executable), '-c', font_code], env=font_environment,
        cwd=release, capture_output=True, text=True, encoding='utf-8', errors='replace')
    (output / 'cold-font.stdout.log').write_text(font_run.stdout, encoding='utf-8')
    (output / 'cold-font.stderr.log').write_text(font_run.stderr, encoding='utf-8')
    assert font_run.returncode == 0, font_run.stdout + font_run.stderr
    cold_font = json.loads(font_run.stdout)
    assert Path(cold_font['sourceFont']).resolve().is_relative_to(installed_package)
    assert Path(cold_font['cachePath']).resolve().is_relative_to(font_cache)
    source_package = root / 'src/cutvoke'
    allowed = {'.wav', '.png', '.jpg', '.mp4', '.json', '.cube', '.ttf', '.txt', '.md'}
    excluded = ('assets/preset_previews/text-flower_*-v1-0-0.*',
                'assets/preset_previews/text-label_*-v1-0-0.*',
                'assets/preset_previews/text-minimal_*-v1-0-0.*')
    expected = list(source_package.glob('*.py')) + list((source_package / 'core').glob('*.py'))
    expected += list((source_package / 'core').glob('*.json'))
    expected += [p for p in (source_package / 'assets').rglob('*') if p.is_file() and p.suffix in allowed
                 and not any(fnmatch.fnmatchcase(p.relative_to(source_package).as_posix(), pattern)
                             for pattern in excluded)]
    compared = []
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert not any('__pycache__' in name or name.endswith(('.pyc', '.pyo')) for name in names)
        for path in expected:
            relative = path.relative_to(source_package).as_posix()
            STAGE = 'compare-package-file:' + relative
            packed = 'cutvoke/' + relative
            assert packed in names, f'missing: {packed}'
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert hashlib.sha256(archive.read(packed)).hexdigest() == digest, f'wheel mismatch: {packed}'
            assert hashlib.sha256((installed_package / relative).read_bytes()).hexdigest() == digest
            compared.append({'path': relative, 'sha256': digest})
        web = [p for p in (root / 'web/dist').rglob('*') if p.is_file()]
        for path in web:
            relative = 'web/' + path.relative_to(root / 'web/dist').as_posix()
            STAGE = 'compare-web-file:' + relative
            packed = 'cutvoke/' + relative
            assert packed in names, f'missing: {packed}'
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert hashlib.sha256(archive.read(packed)).hexdigest() == digest
            assert hashlib.sha256((installed_package / relative).read_bytes()).hexdigest() == digest
            compared.append({'path': relative, 'sha256': digest})
    report = {'ok': True, 'wheelPath': str(wheel), 'wheelSha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
        'wheelBytes': wheel.stat().st_size, 'cleanEnvironment': str(clean_env), 'pythonExecutable': str(executable),
        'noSourcePythonPath': True, 'packageImportedFromCleanEnvironment': True,
        'doctor': doctor, 'coldCaptionFont': cold_font, 'commandCount': len(caps['commands']), 'comparedPackageFileCount': len(compared),
        'comparedAssetFileCount': sum(item['path'].startswith('assets/') for item in compared),
        'comparedWebFileCount': len(web), 'bytecodeLeakCount': 0, 'files': compared}
    manifest = json.loads((installed_package / 'core/builtin_resource_pack.json').read_text(encoding='utf-8'))
    report['builtinResourcePack'] = {'packId': manifest['packId'], 'version': manifest['version'],
                                   'resourceCount': len(manifest['resources'])}
    assert manifest['version'] == '1.38.1'
    STAGE = 'complete'
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key not in ('files', 'doctor')}, ensure_ascii=True))


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
