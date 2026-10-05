from pathlib import Path
import shutil
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
ffmpeg = Path(shutil.which('ffmpeg')).resolve()
ffprobe = Path(shutil.which('ffprobe')).resolve()
analysis = Analysis([str(root / 'desktop' / 'engine_entry.py')], pathex=[str(root / 'src')],
    binaries=[(str(ffmpeg), 'bin'), (str(ffprobe), 'bin')],
    datas=[(str(root / 'src' / 'cutvoke' / 'assets'), 'cutvoke/assets'),
           (str(root / 'web' / 'dist'), 'cutvoke/web'),
           *[(str(p), 'cutvoke/core') for p in (root / 'src' / 'cutvoke' / 'core').glob('*.json')]],
    hiddenimports=collect_submodules('cutvoke'), excludes=['tkinter', 'pytest'],
    noarchive=False)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='cutvoke-engine',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
collect = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='cutvoke-engine')
