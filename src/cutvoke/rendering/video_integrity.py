"""Compare independently evaluated seam frames, not unrelated adjacent pictures."""
from __future__ import annotations

import math
import os
import subprocess

import numpy as np


class VideoIntegrityError(RuntimeError):
    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


def read_frames(path, width, height, *, ffmpeg, fps, tail=False, count=2, cancel_event=None):
    """Decode a bounded RGB sample; tail seeking avoids decoding whole windows."""
    command = [ffmpeg, '-v', 'error', '-nostdin', '-threads', '1']
    if tail:
        command += ['-sseof', '-0.25']
        count = max(2, math.ceil(float(fps) * .25) + 2)
    command += ['-i', str(path), '-an', '-vf',
                f'scale={width}:{height}:flags=area,format=rgba',
                '-frames:v', str(count), '-f', 'rawvideo', '-pix_fmt', 'rgba', 'pipe:1']
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, creationflags=0x08000000 if os.name == 'nt' else 0)
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError('video seam verification cancelled')
            try:
                pixels, error = process.communicate(timeout=.1)
                break
            except subprocess.TimeoutExpired:
                continue
        size = width * height * 4
        if process.returncode or not pixels or len(pixels) % size:
            raise VideoIntegrityError('video seam frame decode failed: ' + error.decode('utf8', errors='replace')[-800:],
                                      {'status': 'failed', 'reason': 'frame_decode'})
        return np.frombuffer(pixels, np.uint8).reshape(-1, height, width, 4)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def compare_seam(actual, reference, at_seconds, *, alpha=False):
    if actual.shape != reference.shape or len(actual) != 2:
        raise VideoIntegrityError('video seam verification requires two corresponding frames',
                                  {'status': 'failed', 'atSeconds': at_seconds, 'reason': 'frame_count'})
    channels = 4 if alpha else 3
    error = actual[..., :channels].astype(float) - reference[..., :channels].astype(float)
    means = error.mean(axis=(1, 2))
    rgb_jump = means[1, :3] - means[0, :3]
    frame_error = np.abs(error).mean(axis=(1, 2, 3))
    report = {'atSeconds': at_seconds, 'comparedFrames': 2,
              'maximumMeanChannelError': float(np.abs(means).max()),
              'maximumFrameMeanAbsoluteError': float(frame_error.max()),
              'meanRGBJumpError': rgb_jump.tolist(),
              'maximumRGBJumpError': float(np.abs(rgb_jump).max()),
              'limits': {'meanChannelError': 1., 'frameMeanAbsoluteError': 3., 'rgbJumpError': 1.}}
    report['status'] = 'passed' if (report['maximumMeanChannelError'] <= 1.
                                  and report['maximumFrameMeanAbsoluteError'] <= 3.
                                  and report['maximumRGBJumpError'] <= 1.) else 'failed'
    if report['status'] != 'passed':
        raise VideoIntegrityError(
            f'video seam verification failed at {at_seconds:.6f}s: '
            f'mean channel error {report["maximumMeanChannelError"]:.3f}, '
            f'RGB jump error {report["maximumRGBJumpError"]:.3f} (8-bit levels)', report)
    return report
