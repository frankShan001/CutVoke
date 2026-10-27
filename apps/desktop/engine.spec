from pathlib import Path
import shutil
import os
import json
import importlib
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent.parent
import runpy
runpy.run_path(str(root / 'tools/build/prepare_extension_payloads.py'))['prepare'](root)
# PyInstaller treats unavailable optional imports as warnings. These modules are
# required for real projects (titles, masks, optical effects and image assets),
# so reject a minimal build environment instead of shipping a broken renderer.
runtime_modules = ['PIL.Image', 'PIL.ImageDraw', 'PIL.ImageFont', 'numpy', 'cv2', 'fontTools.ttLib', 'cryptography', 'moderngl', 'glcontext']
for module in runtime_modules:
    try:
        importlib.import_module(module)
    except ImportError as error:
        raise SystemExit(f'Desktop renderer dependency unavailable: {module}. '
                         'Install apps/desktop/requirements-build.lock before freezing.') from error
tools = Path(os.environ['CUTVOKE_FFMPEG_DIR']) if os.environ.get('CUTVOKE_FFMPEG_DIR') else None
suffix = '.exe' if os.name == 'nt' else ''
ffmpeg = (tools / f'ffmpeg{suffix}' if tools else Path(shutil.which('ffmpeg'))).resolve()
ffprobe = (tools / f'ffprobe{suffix}' if tools else Path(shutil.which('ffprobe'))).resolve()
assets = root / 'src' / 'cutvoke' / 'assets'
# Current runtime qualification verifies its review sidecars and audited exports.
# Keep every file pinned by that manifest; only unrelated development evidence
# and retired title previews can be omitted without weakening the quality gate.
resource_manifest = json.loads((root / 'src/cutvoke/catalogs/builtin_resource_pack.json').read_text('utf-8'))
required_assets = {item['path'] for item in resource_manifest['files'] if item['path'].startswith('assets/')}
asset_data = [(str(p), str(Path('cutvoke/assets') / p.parent.relative_to(assets)))
              for p in assets.rglob('*') if p.is_file() and p.suffix.lower() in
              {'.wav', '.png', '.jpg', '.mp4', '.json', '.cube', '.ttf', '.txt', '.md'}
              and ((Path('assets') / p.relative_to(assets)).as_posix() in required_assets or
                   (not any(part in p.name for part in ('.audit.', '.qa.', '.visual.', '__pycache__'))
                    and not p.name.startswith(('text-flower_', 'text-label_', 'text-minimal_'))))]
analysis = Analysis([str(root / 'apps' / 'desktop' / 'engine_entry.py')], pathex=[str(root / 'src')],
    binaries=[(str(ffmpeg), 'bin'), (str(ffprobe), 'bin')],
    datas=[*asset_data,
           (str(root / '.artifacts' / 'web'), 'cutvoke/web'),
           *[(str(p), 'cutvoke/assets/extension_packages') for p in (assets / 'extension_packages').glob('*') if p.suffix in {'.whl','.zip','.json','.md'}],
           *[(str(p), 'cutvoke/catalogs') for p in (root / 'src' / 'cutvoke' / 'catalogs').glob('*.json')]],
    hiddenimports=collect_submodules('cutvoke') + collect_submodules('glcontext') + runtime_modules, excludes=['tkinter', 'pytest'],
    noarchive=False)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='cutvoke-engine',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
collect = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='cutvoke-engine')
