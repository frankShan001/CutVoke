"""Thumbnail timestamps select decoded pictures, including reordered HEVC."""
import shutil
import subprocess
from unittest.mock import patch

import pytest
from PIL import Image

from cutvoke.rendering.render import RenderService, RenderError


def run(*args):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *map(str, args)], capture_output=True, check=True)


@pytest.fixture(scope='module')
def hevc(tmp_path_factory):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg required')
    encoders = subprocess.check_output(['ffmpeg', '-hide_banner', '-encoders'], text=True)
    if 'libx265' not in encoders:
        pytest.skip('HEVC fixture encoder unavailable')
    path = tmp_path_factory.mktemp('thumbnail') / 'deep.mp4'
    run('-f', 'lavfi', '-i', 'testsrc2=s=160x96:r=60:d=12', '-c:v', 'libx265',
        '-x265-params', 'pools=1:frame-threads=1:keyint=420:min-keyint=420:scenecut=0:bframes=16:b-adapt=0:b-pyramid=1:log-level=error',
        '-pix_fmt', 'yuv420p', path)
    return path


def assert_reference(render, source, target, at, reference):
    render.thumbnail_media(str(source), str(target), time=at, width=160)
    # Independent output-side timestamp selection, not the production trim filter.
    run('-i', source, '-ss', str(at), '-frames:v', '1', '-vf', 'scale=160:-2', reference)
    assert Image.open(target).tobytes() == Image.open(reference).tobytes()


def test_deep_reordered_gop_and_repeated_backward_requests(hevc, tmp_path):
    render = RenderService(gpu_effects=False)
    for at in [0, .5, 6.9, 7.1, 11.5, 3.2, .5, 9, 1, 11.983333333333]:
        assert_reference(render, hevc, tmp_path/'out.png', at, tmp_path/'ref.png')
    old = (tmp_path/'out.png').read_bytes()
    for at in (12, 99):
        with pytest.raises(RenderError, match='no frame'):
            render.thumbnail_media(str(hevc), str(tmp_path/'out.png'), time=at)
        assert (tmp_path/'out.png').read_bytes() == old
    assert not list(tmp_path.glob('.thumbnail-*'))


def test_vfr_and_nonzero_origin(hevc, tmp_path):
    for name, args in [('vfr', ['-vf', "select='not(mod(n,3))+not(mod(n,7))'", '-fps_mode', 'vfr']),
                       ('offset', ['-vf', 'setpts=PTS+5/TB', '-copyts']),
                       ('vfr_offset', ['-vf', "select='not(mod(n,3))+not(mod(n,7))',setpts=PTS+5/TB", '-fps_mode', 'vfr', '-copyts'])]:
        source = tmp_path / (name+'.mkv')
        run('-i', hevc, *args, '-c:v', 'ffv1', source)
        render = RenderService(gpu_effects=False)
        for at in [0, .23, 6.9, 11.5]:
            assert_reference(render, source, tmp_path/'out.png', at, tmp_path/'ref.png')


def test_timeout_and_empty_success_preserve_existing_output(hevc, tmp_path):
    render = RenderService(gpu_effects=False)
    target = tmp_path/'existing.png'; target.write_bytes(b'keep')
    with patch.object(render, '_video_input_arguments', return_value=[]):
        with patch('cutvoke.rendering.render.subprocess.run', side_effect=subprocess.TimeoutExpired('ffmpeg', .01)) as call:
            with pytest.raises(RenderError, match='timed out'):
                render.thumbnail_media(str(hevc), str(target), time=6.9, timeout=.01)
            assert 0 < call.call_args.kwargs['timeout'] <= .01
        with patch('cutvoke.rendering.render.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            with pytest.raises(RenderError, match='no frame'):
                render.thumbnail_media(str(hevc), str(target), time=6.9)
    assert target.read_bytes() == b'keep'
    assert not list(tmp_path.glob('.thumbnail-*'))


@pytest.mark.parametrize('at', [-1, float('inf'), float('nan'), True])
def test_invalid_times_rejected(hevc, tmp_path, at):
    with pytest.raises(RenderError, match='finite and nonnegative'):
        RenderService().thumbnail_media(str(hevc), str(tmp_path/'out.png'), time=at)


@pytest.mark.parametrize("first_pts,first_key", [(6900001, 1), (6800000, 0)])
def test_coverage_backoff_and_atomic_publication(hevc, tmp_path, first_pts, first_key):
    render = RenderService(gpu_effects=False)
    target = tmp_path/'out.png'
    calls = []
    def fake(cmd, **kwargs):
        calls.append((cmd, kwargs['timeout']))
        from pathlib import Path
        Path(cmd[-1]).write_bytes(b'wrong' if len(calls) == 1 else b'correct')
        # Rounded pts_time would equal the target. Integer PTS proves it is later.
        pts = first_pts if len(calls) == 1 else 5900000
        key = first_key if len(calls) == 1 else 1
        return subprocess.CompletedProcess(cmd, 0, '',
            '[showinfo@coverage @ x] config in time_base: 1/1000000, frame_rate: 60/1\n'
            f'[showinfo@coverage @ x] n: 0 pts: {pts} pts_time:6.9 iskey:{key}')
    with patch.object(render, '_video_input_arguments', return_value=[]), patch(
            'cutvoke.rendering.render.subprocess.run', side_effect=fake):
        render.thumbnail_media(str(hevc), str(target), time=6.9)
    assert [float(c[c.index('-ss')+1]) for c, _ in calls] == [6.9, 5.9]
    assert calls[1][1] <= calls[0][1] <= 15
    assert target.read_bytes() == b'correct'


def test_missing_coverage_falls_back_to_start(hevc, tmp_path):
    render = RenderService(gpu_effects=False)
    calls = []
    def fake(cmd, **kwargs):
        from pathlib import Path
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b'start' if '-ss' not in cmd else b'unverified')
        return subprocess.CompletedProcess(cmd, 0, '', '')
    with patch.object(render, '_video_input_arguments', return_value=[]), patch(
            'cutvoke.rendering.render.subprocess.run', side_effect=fake):
        render.thumbnail_media(str(hevc), str(tmp_path/'out.png'), time=.5)
    assert len(calls) == 2 and '-ss' not in calls[-1]
    assert (tmp_path/'out.png').read_bytes() == b'start'


def test_multiple_video_streams_keep_auto_selection(hevc, tmp_path):
    source = tmp_path/'multi.mkv'
    run('-f', 'lavfi', '-i', 'color=red:s=32x32:r=10:d=2',
        '-f', 'lavfi', '-i', 'color=blue:s=160x96:r=10:d=2',
        '-map', '0:v', '-map', '1:v', '-c:v', 'ffv1', source)
    render = RenderService(gpu_effects=False)
    for at in [0, .5, 1.5]:
        assert_reference(render, source, tmp_path/'out.png', at, tmp_path/'ref.png')


def test_retry_deadline_preserves_existing_output(hevc, tmp_path):
    render = RenderService(gpu_effects=False)
    target = tmp_path/'out.png'; target.write_bytes(b'keep')
    def fake(cmd, **kwargs):
        from pathlib import Path
        Path(cmd[-1]).write_bytes(b'unverified')
        return subprocess.CompletedProcess(cmd, 0, '', '')
    with patch.object(render, '_video_input_arguments', return_value=[]), patch(
            'cutvoke.rendering.render.subprocess.run', side_effect=fake) as call, patch(
            'time.monotonic', side_effect=[100, 100.1, 102]):
        with pytest.raises(RenderError, match='timed out'):
            render.thumbnail_media(str(hevc), str(target), time=6.9, timeout=1)
    assert call.call_count == 1
    assert target.read_bytes() == b'keep'
    assert not list(tmp_path.glob('.thumbnail-*'))


def test_transport_stream_random_access_requires_reference_frames(hevc, tmp_path):
    source = tmp_path/'negative.ts'
    run('-i', hevc, '-c', 'copy', '-output_ts_offset', '-2',
        '-mpegts_copyts', '1', '-muxdelay', '0', '-avoid_negative_ts', 'disabled', source)
    render = RenderService(gpu_effects=False)
    for at in [0, .1, 1.9, 2.1, 6.9, 7.1, 11.5]:
        assert_reference(render, source, tmp_path/'out.png', at, tmp_path/'ref.png')
