"""Measure a real immutable project render, with optional empty procedural cache."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from cutvoke.domain.model import Project
from cutvoke.rendering.render import RenderService


class MeasuredRenderer(RenderService):
    def __init__(self):
        super().__init__()
        self.phases = []

    def _run_ffmpeg(self, seq, graph, inputs, out_path, quality, *args, **kwargs):
        started = time.perf_counter()
        try:
            return super()._run_ffmpeg(seq, graph, inputs, out_path, quality, *args, **kwargs)
        finally:
            phase = dict(seconds=time.perf_counter()-started, inputCount=len(inputs),
                         graphBytes=len(graph.encode('utf-8')), quality=quality,
                         start=kwargs.get('output_start'), duration=kwargs.get('output_duration'),
                         lossless=kwargs.get('lossless', False),
                         encoder=getattr(self._render_ctx, 'video_encoder', None))
            self.phases.append(phase)
            print(json.dumps(phase), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path, help='UTF-8 project JSON snapshot; never modified')
    parser.add_argument('output', type=Path, help='Fresh output MP4')
    parser.add_argument('--cold', action='store_true', help='Use an empty, isolated disposable source cache')
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--start', type=float, default=0)
    parser.add_argument('--duration', type=float)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists; choose a fresh filename')
    if args.preview and (args.duration is None or not 0 < args.duration <= 8):
        parser.error('preview needs --duration in (0, 8]')
    project = Project.from_dict(json.loads(args.project.read_text(encoding='utf-8-sig')))
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    renderer = MeasuredRenderer()
    original_temp = tempfile.tempdir
    with tempfile.TemporaryDirectory(prefix='cutvoke-benchmark-') as directory:
        try:
            if args.cold:
                tempfile.tempdir = directory
            started = time.perf_counter()
            if args.preview:
                result = renderer.render_preview_window(project, str(output), args.start, args.duration)
            else:
                options = dict(output_start=args.start, output_duration=args.duration) if args.duration else {}
                result = renderer.render(project, str(output), quality='high', **options)
            report = dict(projectName=project.name, sourceCacheMode='empty' if args.cold else 'existing',
                          elapsedSeconds=time.perf_counter()-started, phases=renderer.phases, result=result)
        finally:
            tempfile.tempdir = original_temp
    output.with_suffix('.benchmark.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
