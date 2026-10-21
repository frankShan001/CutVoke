"""Cold preview decoding keeps source frames, alpha and audio clocks intact."""
import copy
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from cutvoke.core import gpu_effects
from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.rational import Rational as R
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.vfx_matte import regular_timestamps
from cutvoke.core.preview_window import bounded


def test_long_gop_seek_keeps_exact_frames_and_shared_audio(tmp_path):
    r = RenderService(gpu_effects=False)
    source = str(tmp_path/'long-gop.mp4')
    subprocess.run([r.ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'testsrc2=s=160x96:r=24:d=10', '-f', 'lavfi', '-i',
        'sine=frequency=437:sample_rate=48000:duration=10', '-c:v', 'libx264',
        '-g', '240', '-keyint_min', '240', '-sc_threshold', '0', '-c:a', 'aac', source], check=True)
    # Seeking lands at keyframe zero. A relative read interval used to inspect
    # only 0..2s and wrongly reject this genuinely regular late section.
    assert regular_timestamps(r.ffprobe, source, 6.25, 1, Fraction(24), exact_interval=True)
    p = EditService().create_project('seek', width=160, height=96, fps=R.of(24))
    clip = Clip('v', AssetReference('a', source), R.of(0), R.of(3), R.of(25,4))
    audio = Clip('a', AssetReference('a', source), R.of(0), R.of(3), R.of(17,4))
    p.sequence.tracks = [Track('v', 'video', [clip], muted=True), Track('a', 'audio', [audio])]
    authority = copy.deepcopy(p.to_dict())
    frames, samples = [], []
    for optimized in (False, True):
        out = tmp_path/f'{optimized}.mp4'
        with patch.object(r, '_preview_seek_inputs', wraps=r._preview_seek_inputs) as plan:
            if not optimized:
                plan.side_effect = lambda graph, paths, fps: graph
            r.render_preview_window(p, str(out), 1.25, .5)
        if optimized:
            assert r._render_ctx.input_seeks[str(Path(source).resolve())] == 3
        png = tmp_path/f'{optimized}.png'
        subprocess.run([r.ffmpeg, '-v', 'error', '-y', '-i', str(out), '-frames:v', '1', str(png)], check=True)
        frames.append(np.asarray(Image.open(png)).astype(float))
        result = subprocess.run([r.ffmpeg, '-v', 'error', '-i', str(out), '-vn',
            '-f', 's16le', '-acodec', 'pcm_s16le', 'pipe:1'], capture_output=True, check=True)
        samples.append(result.stdout)
    assert np.array_equal(*frames)
    assert samples[0] == samples[1]
    assert p.to_dict() == authority


def test_static_picture_has_no_decode_history_and_animated_png_is_retained(tmp_path):
    path = tmp_path/'picture.png'
    Image.new('RGBA', (32,32), (80,170,230,128)).save(path)
    p = EditService().create_project('still', fps=R.of(24))
    p.sequence.tracks = [Track('v','video',[Clip('c',AssetReference('a',str(path)),
        R.of(0), R.of(30), R.of(4))])]
    snapshot, clocks, origin = bounded(p, (20,21), RenderService())
    assert snapshot.sequence.tracks[0].clips[0].source_start == R.of(0)
    assert clocks['c'][0] == 20 and origin == 20
    assert p.sequence.tracks[0].clips[0].source_start == R.of(4)
    Image.new('RGBA',(32,32),'red').save(path, save_all=True,
        append_images=[Image.new('RGBA',(32,32),'blue')], duration=100, loop=0)
    assert bounded(p,(20,21),RenderService()) == (p,{},None)


def test_concurrent_gpu_requests_share_one_driver_probe():
    started, release = threading.Event(), threading.Event()
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        started.set()
        assert release.wait(2)
        return subprocess.CompletedProcess(args, 0, b'\0'*(32*32*4), b'')
    with patch.object(gpu_effects.subprocess, 'run', run):
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(gpu_effects.device, 'single-flight-test')
            assert started.wait(1)
            second = pool.submit(gpu_effects.device, 'single-flight-test')
            release.set()
            assert first.result() == second.result()
    assert len(calls) == 1


def test_speed_clip_does_not_force_independent_effect_lanes_to_replay_history(tmp_path):
    path = tmp_path/'layer.png'
    Image.new('RGBA',(160,96),(70,140,220,160)).save(path)
    p = EditService().create_project('independent',width=160,height=96,fps=R.of(24))
    base = Clip('speed',AssetReference('a',str(path)),R.of(0),R.of(8),R.of(0),speed=R.of(2))
    layer = Clip('light',AssetReference('a',str(path)),R.of(0),R.of(8),R.of(0),effects=[
        dict(effectId='cutvoke.fx.mask',params=dict(shape='rect',x=.5,y=.5,w=.6,h=.7,
            motion='linear',motionDuration=8,motionFromX=-.4,motionToX=.4)),
        dict(effectId='cutvoke.transform',params=dict(opacity=.35))])
    p.sequence.tracks=[Track('v','video',[base]),Track('top','video',[layer])]
    r=RenderService(gpu_effects=False)
    snapshot,clocks,origin=bounded(p,(6.25,6.5),r)
    assert origin == 0 and 'speed' not in clocks and clocks['light'][0] == 6.25
    assert snapshot.sequence.tracks[0].clips[0].to_dict() == base.to_dict()
    images=[]
    for reference in (False,True):
        out=str(tmp_path/f'partial-{reference}.png')
        with patch('cutvoke.core.preview_window.bounded',wraps=bounded) as crop:
            if reference:
                crop.side_effect=lambda p,w,r:(p,{},None)
            r.extract_frame(p,6.25,out)
        images.append(np.asarray(Image.open(out)).astype(float))
    assert np.abs(images[0]-images[1]).mean() < 1.5
