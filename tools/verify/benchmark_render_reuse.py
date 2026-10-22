"""Repeat real static-layer edits and exports; record time and decoded-pixel hashes.

Run twice in fresh output directories. --renderer-source can load a saved pre-edit
render.py for an A/B comparison on exactly the same generated workload.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from PIL import Image, ImageDraw
from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditService
from cutvoke.rendering.render import RenderService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='Fresh benchmark output directory')
    parser.add_argument('--renderer-source', type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=False)
    renderer_class = RenderService
    if args.renderer_source:
        spec = importlib.util.spec_from_file_location('cutvoke_benchmark_baseline', args.renderer_source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        renderer_class = module.RenderService
    renderer = renderer_class(gpu_effects=False)
    # Keep the encoder identical between runs and portable to machines without NVENC.
    renderer._render_ctx.hardware_disabled = True
    source = args.directory.resolve() / 'source.png'
    image = Image.new('RGBA', (1280, 720), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((220, 100, 1060, 620), fill=(240, 90, 30, 210))
    image.save(source)
    project = EditService().create_project('render-reuse-benchmark', width=1280, height=720, fps=R.of(24))
    clip = Clip('graphic', AssetReference('source', str(source)), R.of(0), R.of(2), R.of(0), effects=[
        dict(effectId='cutvoke.fx.mask', params=dict(shape='circle', x=.5, y=.5, w=.7, h=.8, feather=.15)),
        dict(effectId='cutvoke.transform', params=dict(position=dict(x=0, y=0)))])
    project.sequence.tracks = [Track('graphics', 'video', [clip])]
    phases, counts = [], dict(staticProcesses=0, metadataProcesses=0)
    original_run = subprocess.run
    def counted(command, *a, **kw):
        if '[staticgraphic]' in command:
            counts['staticProcesses'] += 1
        if '-show_entries' in command and 'format=duration,format_name' in command:
            counts['metadataProcesses'] += 1
        return original_run(command, *a, **kw)
    started = time.perf_counter()
    with patch.object(subprocess, 'run', counted):
        for kind, repeats in [('poster', 4), ('preview', 2), ('export', 2)]:
            for index in range(repeats):
                # The source treatment stays unchanged while the edit moves it.
                clip.effects[-1]['params']['position']['x'] = index * 9
                target = args.directory.resolve() / f'{kind}-{index}.{"png" if kind == "poster" else "mp4"}'
                begin = time.perf_counter()
                if kind == 'poster':
                    renderer.extract_frame(project, .25, str(target), size=(640, 360))
                elif kind == 'preview':
                    renderer.render_preview_window(project, str(target), 0, .5)
                else:
                    renderer.render(project, str(target), quality='high', output_start=0, output_duration=.5)
                elapsed = time.perf_counter() - begin
                if kind == 'poster':
                    with Image.open(target) as frame:
                        raw = frame.convert('RGBA').tobytes()
                else:
                    raw = original_run([renderer.ffmpeg, '-v', 'error', '-i', str(target), '-an',
                        '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'], check=True, capture_output=True).stdout
                phases.append(dict(kind=kind, index=index, seconds=elapsed,
                                   pixelSha256=hashlib.sha256(raw).hexdigest()))
    report = dict(workload='1280x720 RGBA static feathered mask; transform edits; 24fps',
                  rendererSource=str(args.renderer_source or 'working tree'),
                  elapsedSeconds=time.perf_counter()-started, phases=phases, **counts)
    cache = getattr(renderer, '_graphic_cache', None)
    if cache is not None:
        report['graphicCache'] = cache.status()
    (args.directory / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
