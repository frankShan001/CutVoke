"""Vectorized ink coverage; transient lossless media, never project content."""
from __future__ import annotations

import math
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .mask_motion import progress


def regular_timestamps(ffprobe: str, source: str, start: float, duration: float, fps, *, exact_interval=False) -> bool:
    """Phone/VFR footage must retain its actual effect clock, even at equal AVG/R rates."""
    try:
        result = subprocess.run([
            ffprobe, '-v', 'error', '-select_streams', 'v:0', '-read_intervals',
            # A demuxer can seek to a much earlier keyframe. A relative end
            # counts from that keyframe and can stop before the requested start.
            (f'{start:.9f}%{start + duration + 1:.9f}' if exact_interval
             else f'{start:.9f}%+{duration + 1:.9f}'), '-show_frames',
            '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', source],
            check=True, capture_output=True, timeout=10)
        times = [float(frame['best_effort_timestamp_time'])
                 for frame in json.loads(result.stdout)['frames']
                 if 'best_effort_timestamp_time' in frame]
        selected = [t for t in times if start-1e-6 <= t <= start+duration+1/float(fps)]
        return len(selected) >= 2 and all(
            abs(b-a-1/float(fps)) < 2e-6 for a,b in zip(selected, selected[1:]))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return False


def supports(params: dict) -> bool:
    # Moving/rotating geometry keeps the existing exact expression path. This
    # fast path covers ink growth with a fixed coordinate system only.
    return params.get('shape') == 'ink' and all(
        float(params.get('motion' + end + name, value)) == value
        for end in ('From', 'To')
        for name, value in [('X', 0), ('Y', 0), ('Rotation', 0), ('Scale', 1)])


def coverage_frames(params: dict, width: int, height: int, fps, duration: float, start_time=0.):
    """Same ink field/easing as geq, with trigonometry computed once per matte."""
    x = float(f"{max(0., min(1., float(params.get('x', .15)))):.6f}")
    y = float(f"{max(0., min(1., float(params.get('y', .15)))):.6f}")
    detail, seed = float(params.get('inkDetail', 1)), float(params.get('inkSeed', 17))
    rounded = lambda value: float(f'{value:.6f}')
    xx, yy = np.meshgrid(np.arange(width, dtype=float), np.arange(height, dtype=float))
    noise = (.50*np.sin(xx/width*rounded(23*detail)+rounded(seed))*
             np.sin(yy/height*rounded(19*detail)-rounded(seed)) +
             .25*np.sin((xx+yy)/width*rounded(57*detail))*
             np.cos((xx-yy)/height*rounded(41*detail)) +
             .125*np.sin(xx/width*rounded(113*detail)+yy/height*rounded(89*detail)+rounded(seed*2)))
    field = -np.sqrt(((xx/width-x)/.85)**2 + ((yy/height-y)/.85)**2) + noise*.25
    feather = max(rounded(max(0., min(.5, float(params.get('feather', .05))))), .015)
    start = float(f"{float(params.get('motionStart', 0)):.9f}")
    length = float(f"{max(.001, float(params.get('motionDuration', 1))):.9f}")
    curve = params.get('motion', 'none')
    for index in range(max(1, math.ceil(duration*float(fps)-1e-7))):
        q = 1. if curve == 'none' else progress(start_time+index/float(fps), start, length, curve,
                                               float(params.get('motionRamp', .25)))
        mask = np.zeros_like(field) if q <= 0 else np.ones_like(field) if q >= 1 else (
            np.clip((2*q+field)/feather, 0, 1))
        if params.get('invert', False):
            mask = 1-mask
        yield np.floor(mask*65535).astype('<u2')


def write_source(path: Path, params: dict, width: int, height: int, fps,
                 duration: float, ffmpeg: str, cancelled=None, *, start_time=0.):
    command = [ffmpeg, '-v', 'error', '-y', '-nostdin', '-f', 'rawvideo',
               '-pixel_format', 'gray16le', '-video_size', f'{width}x{height}',
               '-framerate', str(fps), '-i', 'pipe:0', '-an', '-c:v', 'ffv1',
               '-level', '3', '-pix_fmt', 'gray16le', '-threads', '2', str(path)]
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)
        try:
            for frame in coverage_frames(params, width, height, fps, duration, start_time):
                if cancelled and cancelled():
                    raise InterruptedError('matte preparation cancelled')
                process.stdin.write(frame.tobytes())
            process.stdin.close()
            if process.wait(timeout=30):
                errors.seek(0)
                raise RuntimeError(errors.read().decode('utf-8', 'replace')[-1000:])
        except BaseException:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            path.unlink(missing_ok=True)
            raise
        finally:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
