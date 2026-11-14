"""Serve the real tutorial output for isolated browser acceptance."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('audio_tutorial', ROOT / 'examples/audio-first/demo.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory(prefix='cutvoke-audio-tutorial-') as temporary:
    output = Path(temporary) / 'tutorial'
    module.run(output)
    raise SystemExit(subprocess.call([sys.executable, '-m', 'cutvoke', 'serve', '--host', '127.0.0.1',
        '--port', os.environ.get('CUTVOKE_AUDIO_TUTORIAL_PORT', '8807'), '--data',
        str(output / 'roundtrip/projects.sqlite' if os.environ.get('CUTVOKE_AUDIO_TUTORIAL_RESTORED') == '1' else output / 'data')],
        env={**os.environ, 'CUTVOKE_EXTENSION_HOME': str(output / 'extensions'),
             'CUTVOKE_FONT_CACHE': str(output / 'font-cache'), 'PYTHONIOENCODING': 'utf-8'}))
