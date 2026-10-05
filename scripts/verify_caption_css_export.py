"""Re-export the same real Web project without writing its database."""
from pathlib import Path
import copy
import hashlib
import json
import subprocess
import time

from PIL import Image, ImageDraw
from cutvoke.core.model import Project
from cutvoke.core.render import RenderService
from cutvoke.core.caption_render import _css_font_size_to_ass

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'output/acceptance/product-completion-20261003/web/manual-project-final.json'
OUT = ROOT / 'output/acceptance/product-completion-20261003/delivery/caption-consistency'
OUT.mkdir(parents=True, exist_ok=True)
original = json.loads(SOURCE.read_text(encoding='utf-8'))
report = {'sourceProject': str(SOURCE), 'sourceProjectSha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
          'captionRendererSha256': hashlib.sha256((ROOT / 'src/cutvoke/core/caption_render.py').read_bytes()).hexdigest(),
          'rendererSha256': hashlib.sha256((ROOT / 'src/cutvoke/core/render.py').read_bytes()).hexdigest(),
          'variants': []}
for slug, family, bold in [('sans', 'Noto Sans SC', False), ('serif', 'Noto Serif SC', False),
                          ('sans-bold', 'Noto Sans SC', True), ('serif-bold', 'Noto Serif SC', True)]:
    data = copy.deepcopy(original)
    project = Project.from_dict(data)
    project.sequence.captions[0].fontFamily = family
    project.sequence.captions[0].bold = bold
    (OUT / f'{slug}-project.json').write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=2), encoding='utf-8')
    target = OUT / f'{slug}-export.mp4'
    start = time.perf_counter()
    result = RenderService().render(project, str(target), quality='high', overwrite=True)
    elapsed = time.perf_counter() - start
    probe = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-show_streams', '-show_format',
                            '-of', 'json', str(target)], capture_output=True, text=True, encoding='utf-8')
    decode = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(target), '-f', 'null', 'NUL'],
                            capture_output=True, text=True, encoding='utf-8')
    (OUT / f'{slug}-ffprobe.json').write_text(probe.stdout, encoding='utf-8')
    (OUT / f'{slug}-decode.stderr.log').write_text(decode.stderr, encoding='utf-8')
    assert probe.returncode == decode.returncode == 0
    images = []
    for instant in [1, 2.5, 3.5]:
        tag = str(instant).replace('.', '-')
        path = OUT / f'{slug}-frame-{tag}.png'
        frame = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(instant), '-i', str(target),
                                '-frames:v', '1', '-vf', 'scale=860:-1', '-y', str(path)], capture_output=True)
        assert frame.returncode == 0 and path.exists()
        images.append(path)
    report['variants'].append({'family': family, 'bold': bold, 'domainFontSize': 32,
                               'assFontSize': _css_font_size_to_ass(32, family),
                               'assOutlineRadius': 1, 'elapsedSeconds': elapsed, 'render': result,
                               'exportSha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                               'decodeExitCode': decode.returncode, 'frames': [str(p) for p in images]})
rows = []
for label, path in [('Web current font binding', ROOT / 'output/acceptance/product-completion-20261003/web/playback-fixed-frame-1.png'),
                    ('Export before fix', ROOT / 'output/acceptance/product-completion-20261003/web/exports/export-frame-1.png'),
                    ('Export Sans corrected', OUT / 'sans-frame-1.png'),
                    ('Export Serif corrected', OUT / 'serif-frame-1.png'),
                    ('Export Sans bold', OUT / 'sans-bold-frame-1.png'),
                    ('Export Serif bold', OUT / 'serif-bold-frame-1.png')]:
    pic = Image.open(path).convert('RGB')
    cropped = pic.crop((250, 190, 610, 275)).resize((1080, 255))
    row = Image.new('RGB', (1080, 285), '#15171a')
    row.paste(cropped, (0, 30))
    ImageDraw.Draw(row).text((10, 8), label, fill='white')
    rows.append(row)
sheet = Image.new('RGB', (1080, 285 * len(rows)))
for i, row in enumerate(rows):
    sheet.paste(row, (0, i * 285))
sheet.save(OUT / 'before-after-caption-crops.png')
assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == report['sourceProjectSha256']
(OUT / 'render-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'ok': True, 'variants': [(v['family'], v['elapsedSeconds']) for v in report['variants']], 'output': str(OUT)}))
