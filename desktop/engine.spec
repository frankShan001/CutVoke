from pathlib import Path
import shutil
import os
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
tools = Path(os.environ['CUTVOKE_FFMPEG_DIR']) if os.environ.get('CUTVOKE_FFMPEG_DIR') else None
ffmpeg = (tools / 'ffmpeg.exe' if tools else Path(shutil.which('ffmpeg'))).resolve()
ffprobe = (tools / 'ffprobe.exe' if tools else Path(shutil.which('ffprobe'))).resolve()
assets = root / 'src' / 'cutvoke' / 'assets'
# Ship product resources, excluding audit videos, review reports and retired
# title previews. The development tree can contain much larger evidence sets.
asset_data = [(str(p), str(Path('cutvoke/assets') / p.parent.relative_to(assets)))
              for p in assets.rglob('*') if p.is_file() and p.suffix.lower() in
              {'.wav', '.png', '.jpg', '.mp4', '.json', '.cube', '.ttf', '.txt', '.md'}
              and not any(part in p.name for part in ('.audit.', '.qa.', '.visual.', '__pycache__'))
              and not p.name.startswith(('text-flower_', 'text-label_', 'text-minimal_'))]
analysis = Analysis([str(root / 'desktop' / 'engine_entry.py')], pathex=[str(root / 'src')],
    binaries=[(str(ffmpeg), 'bin'), (str(ffprobe), 'bin')],
    datas=[*asset_data,
           (str(root / '.artifacts' / 'web'), 'cutvoke/web'),
           *[(str(p), 'cutvoke/core') for p in (root / 'src' / 'cutvoke' / 'core').glob('*.json')]],
    hiddenimports=collect_submodules('cutvoke'), excludes=['tkinter', 'pytest'],
    noarchive=False)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='cutvoke-engine',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
collect = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='cutvoke-engine')
