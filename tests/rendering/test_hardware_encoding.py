"""Hardware encode detection and recovery must keep an export usable."""
import shutil
import subprocess
from unittest.mock import patch

import pytest
from PIL import Image

from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational as R
from cutvoke.rendering.render import RenderService, _nvenc_available
from cutvoke.editing.service import EditService


def test_driver_probe_is_real_and_bounded():
    _nvenc_available.cache_clear()
    try:
        with patch('cutvoke.rendering.render.subprocess.run',
                   side_effect=subprocess.TimeoutExpired('ffmpeg', 8)) as run:
            assert not _nvenc_available('unavailable-test-ffmpeg')
            assert not _nvenc_available('unavailable-test-ffmpeg')
            assert run.call_count == 1
            assert run.call_args.kwargs['timeout'] == 8
            assert 'h264_nvenc' in run.call_args.args[0]
    finally:
        _nvenc_available.cache_clear()


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
def test_lost_gpu_retries_same_color_managed_range_on_cpu(tmp_path):
    source = tmp_path/'source.png'
    Image.new('RGB', (320, 180), (190, 60, 30)).save(source)
    project = EditService().create_project('recovery', width=320, height=180, fps=R.of(24))
    project.sequence.tracks = [Track('v', 'video', [
        Clip('c', AssetReference('a', str(source)), R.of(0), R.of(2), R.of(0))])]
    output = tmp_path/'range.mp4'
    commands = []
    real_popen = subprocess.Popen
    def unavailable(command, *args, **kwargs):
        if 'h264_nvenc' in command:
            commands.append(list(command))
            # Exercise a real failed subprocess and the normal cleanup path.
            return real_popen(['ffmpeg', '-cutvoke-missing-gpu'], *args, **kwargs)
        if 'libx264' in command:
            commands.append(list(command))
        return real_popen(command, *args, **kwargs)
    renderer = RenderService()
    with patch('cutvoke.rendering.render._nvenc_available', return_value=True), \
         patch('cutvoke.rendering.render.subprocess.Popen', side_effect=unavailable):
        result = renderer.render(project, str(output), output_start=.5,
                                 output_duration=1, color_space='bt709')
    assert result['videoEncoder'] == 'libx264'
    assert result['encoderFallback'] is True
    assert result['duration'] == pytest.approx(1, abs=.05)
    assert result['color_primaries'] == 'bt709'
    assert len(commands) == 2
    graphs = [command[command.index('-filter_complex') + 1] for command in commands]
    assert graphs[0] == graphs[1]  # GPU failure must retain the same bounded graph.
    first = round((.5 - renderer._render_ctx.compile_origin) * 24)
    for command in commands:
        assert '-ss' not in command and '-t' not in command
        assert f'trim=start_frame={first}:end_frame={first+24}' in command[command.index('-filter_complex') + 1]
    assert not list(tmp_path.glob('*.tmp.mp4'))
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(output), '-f', 'null', '-'],
                   check=True, capture_output=True)
