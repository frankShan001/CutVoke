"""Bounded real-PTS regression for ordinary trimmed export and window preview."""
import hashlib
import json
import shutil
import subprocess

import numpy as np
import pytest

from cutvoke.domain.model import AssetReference, Clip, Project, Rational as R, Sequence, Track
from cutvoke.rendering.render import RenderService


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, timeout=60).stdout


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg required')
def test_vfr_trim_and_window_keep_visual_event_and_audio_pulse_aligned(tmp_path):
    # As in the cutout timestamp fixture: 25 fps then 5 fps, preserving the end.
    # This test exercises normal RenderService paths, not segmentation or ASR.
    source = tmp_path / 'vfr.mp4'
    run('ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'color=red:s=64x48:r=25:d=2',
        '-f', 'lavfi', '-i',
        r'aevalsrc=if(between(t\,1.2\,1.3)\,0.4*sin(2*PI*440*t)\,0):s=48000:d=2',
        '-vf', "drawbox=color=blue:t=fill:enable='gte(t,1.2)',select='lt(t,1)+not(mod(n,5))+eq(n,49)'",
        '-fps_mode', 'vfr', '-c:v', 'libx264', '-bf', '0', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', str(source))
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    packet_data = json.loads(run('ffprobe', '-v', 'error', '-select_streams', 'v',
        '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(source)))
    pts = [float(item['best_effort_timestamp_time']) for item in packet_data['frames']]
    deltas = np.diff(pts)
    assert np.all(deltas > 0)
    assert deltas.min() < .05 and deltas.max() > .15  # Actual timestamps, not nominal fps.

    project = Project(schema_version='1.0', project_id='vfr-probe', revision='1',
        sequence=Sequence(id='s', width=64, height=48, fps=R.of(25), tracks=[
            Track(id='v', kind='video', clips=[Clip(id='clip',
                asset_ref=AssetReference('v', str(source)), timeline_start=R.of(0),
                timeline_end=R.of(7, 5), source_start=R.of(2, 5))])]))
    before = project.to_dict()
    renderer = RenderService(gpu_effects=False)
    renderer.gpu_acceleration = False
    full, preview = tmp_path / 'full.mp4', tmp_path / 'preview.mp4'
    renderer.render(project, str(full), quality='high')
    renderer.render_preview_window(project, str(preview), .4, .8)

    for output, expected_event, expected_frames in ((full, .8, 35), (preview, .4, 20)):
        pixels = run('ffmpeg', '-v', 'error', '-i', str(output), '-an',
                     '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1')
        frames = np.frombuffer(pixels, np.uint8).reshape(-1, 48, 64, 3)
        assert len(frames) == expected_frames
        blue_frames = np.flatnonzero(frames[..., 2].mean(axis=(1, 2)) > 200)
        assert blue_frames.size, output.name
        visual_onset = blue_frames[0] / 25
        audio = np.frombuffer(run('ffmpeg', '-v', 'error', '-i', str(output), '-vn',
            '-ac', '1', '-ar', '48000', '-f', 'f32le', 'pipe:1'), np.float32)
        assert abs(len(audio) / 48000 - expected_frames / 25) <= .025
        pulse = np.flatnonzero(np.abs(audio) > .05)
        assert pulse.size, output.name
        audio_onset = pulse[0] / 48000
        assert abs(visual_onset - expected_event) <= 1 / 25 + 1e-9
        assert abs(audio_onset - expected_event) <= .025
        assert abs(visual_onset - audio_onset) <= 1 / 25 + 1e-9
        # Require both silent regions, not just a nonempty audio track.
        assert np.max(np.abs(audio[:int((expected_event - .05) * 48000)])) < .02
        assert np.max(np.abs(audio[int((expected_event + .15) * 48000):])) < .02
    assert project.to_dict() == before
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
