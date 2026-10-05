"""One isolated concurrent cold-font build verifies atomic first publication."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import time

from fontTools.ttLib import TTFont
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/acceptance/product-completion-20261003/delivery/caption-consistency/font-race'
OUT.mkdir(parents=True, exist_ok=False)
environment = os.environ.copy()
environment.update({'TEMP': str(OUT), 'TMP': str(OUT), 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONIOENCODING': 'utf-8'})
code = '''from cutvoke.core.caption_render import _caption_font_instance
from fontTools.ttLib import TTFont
from pathlib import Path
import json,hashlib,time
t=time.perf_counter(); alias,path=_caption_font_instance("Noto Sans SC",False)
with TTFont(path) as f:
 assert "fvar" not in f
 assert f["OS/2"].usWeightClass==400
 assert f["name"].getDebugName(1)==alias
print(json.dumps({"path":path,"sha256":hashlib.sha256(Path(path).read_bytes()).hexdigest(),"elapsedSeconds":time.perf_counter()-t,"weight":400}),flush=True)
'''
import sys
children = []
for i in range(2):
    stdout = (OUT / f'worker-{i}.stdout.log').open('w', encoding='utf-8')
    stderr = (OUT / f'worker-{i}.stderr.log').open('w', encoding='utf-8')
    children.append((subprocess.Popen([sys.executable, '-c', code], stdout=stdout, stderr=stderr,
                                      env=environment, cwd=OUT), stdout, stderr))
published_checks = 0
while any(child.poll() is None for child, _, _ in children):
    for path in (OUT / 'cutvoke-caption-fonts').glob('*-400-v1.ttf'):
        with TTFont(path) as font:
            assert 'fvar' not in font and font['OS/2'].usWeightClass == 400
            published_checks += 1
    time.sleep(.05)
records = []
for i, (child, stdout, stderr) in enumerate(children):
    stdout.close(); stderr.close()
    assert child.returncode == 0, (OUT / f'worker-{i}.stderr.log').read_text(encoding='utf-8')
    records.append(json.loads((OUT / f'worker-{i}.stdout.log').read_text(encoding='utf-8')))
target = Path(records[0]['path'])
with TTFont(target) as final:
    assert 'fvar' not in final and final['OS/2'].usWeightClass == 400
assert records[0]['path'] == records[1]['path']
assert not list(target.parent.glob('.caption-*'))
report = {'ok': True, 'concurrentColdWorkers': 2, 'workers': records,
          'validPublishedSamples': published_checks, 'noPartialPublishedFont': True,
          'remainingTemporaryFonts': 0, 'finalSha256': hashlib.sha256(target.read_bytes()).hexdigest()}
(OUT / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
