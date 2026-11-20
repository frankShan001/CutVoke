"""Alpha-marked VPx inputs retain real transparency, not just container tags."""
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditService
from cutvoke.rendering.render import RenderService, RenderError, ALPHA_CONTAINERS


@pytest.mark.parametrize('codec,alpha,expected', [
    ('vp8', True, ['-c:v', 'libvpx']), ('vp9', True, ['-c:v', 'libvpx-vp9']),
    ('vp8', False, []), ('vp9', False, []), ('h264', True, []), (None, False, []),
])
def test_decoder_override_is_narrow(codec, alpha, expected):
    render = RenderService(gpu_effects=False)
    with patch.object(render, 'probe_media', return_value={'video_codec': codec, 'webm_alpha': alpha}), \
         patch('cutvoke.rendering.render._alpha_decoder_available', return_value=True) as available:
        assert render._video_input_arguments('sample') == expected
        assert available.call_count == bool(expected)
    assert ALPHA_CONTAINERS == frozenset({'.mov'})


@pytest.mark.parametrize('codec,decoder', [('vp8', 'libvpx'), ('vp9', 'libvpx-vp9')])
def test_missing_alpha_decoder_fails_explicitly(codec, decoder):
    render = RenderService(gpu_effects=False)
    with patch.object(render, 'probe_media', return_value={'video_codec': codec, 'webm_alpha': True}), \
         patch('cutvoke.rendering.render._alpha_decoder_available', return_value=False):
        with pytest.raises(RenderError, match='WEBM_ALPHA_DECODER_UNAVAILABLE.*' + decoder):
            render._video_input_arguments('sample')


def _run(*args):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *map(str, args)], check=True, capture_output=True)


def _pixels(path):
    image = Image.open(path).convert('RGB')
    corner, center = image.getpixel((4, 4)), image.getpixel((32, 32))
    assert corner[2] > 230 and corner[0] < 20, corner
    assert center[0] > 230 and center[2] < 20, center


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg required')
@pytest.mark.parametrize('codec', ['libvpx', 'libvpx-vp9'])
def test_real_webm_alpha_composite_preview_still_and_export(tmp_path, codec):
    encoders = subprocess.check_output(['ffmpeg', '-hide_banner', '-encoders'], text=True)
    if not any(len(f := row.split()) > 1 and f[1] == codec for row in encoders.splitlines()):
        pytest.skip(f'fixture encoder {codec} unavailable')
    source = tmp_path / 'red.png'
    image = Image.new('RGBA', (64, 64), (255, 0, 0, 0))
    image.paste((255, 0, 0, 255), (16, 16, 48, 48)); image.save(source)
    webm = tmp_path / 'red.webm'
    _run('-loop', '1', '-i', source, '-t', '1', '-c:v', codec, '-pix_fmt', 'yuva420p', '-auto-alt-ref', '0', webm)
    background = tmp_path / 'blue.png'; Image.new('RGB', (64, 64), 'blue').save(background)
    service = EditService()
    try:
        project = service.create_project('alpha-vpx', width=64, height=64)
        def clip(name, path):
            return Clip(name, AssetReference(name, str(path)), R.of(0), R.of(1), R.of(0))
        project.sequence.tracks = [Track('bg', 'video', [clip('bg', background)], muted=True),
                                   Track('fg', 'video', [clip('fg', webm)], muted=True)]
        render = RenderService(gpu_effects=False); render.gpu_acceleration = False
        assert render.probe_media(str(webm))['webm_alpha'] is True
        opaque = tmp_path / 'opaque.webm'
        _run('-loop', '1', '-i', background, '-t', '0.1', '-c:v', codec, '-pix_fmt', 'yuv420p', opaque)
        assert render.probe_media(str(opaque))['webm_alpha'] is False
        assert render._video_input_arguments(str(opaque)) == []
        output = tmp_path / 'out.mp4'
        render.render(project, str(output), quality='high')
        frame = tmp_path / 'export.png'; _run('-i', output, '-frames:v', '1', frame); _pixels(frame)
        window = tmp_path / 'window.mp4'
        render.render(project, str(window), quality='high', output_start=.2, output_duration=.5)
        window_frame = tmp_path / 'window.png'; _run('-i', window, '-frames:v', '1', window_frame); _pixels(window_frame)
        still = tmp_path / 'still.png'; render.extract_still(project, .5, str(still)); _pixels(still)
        preview = tmp_path / 'preview.png'; render.extract_frame(project, .5, str(preview)); _pixels(preview)
        thumb = tmp_path / 'thumb.png'; render.thumbnail_media(str(webm), str(thumb), width=64)
        assert Image.open(thumb).convert('RGBA').getpixel((0, 0))[3] == 0
        # A decoder-less build must fail rather than write an opaque replacement.
        unavailable = tmp_path / 'unavailable.mp4'
        with patch('cutvoke.rendering.render._alpha_decoder_available', return_value=False):
            with pytest.raises(RenderError, match='WEBM_ALPHA_DECODER_UNAVAILABLE'):
                render.render(project, str(unavailable), quality='high')
        assert not unavailable.exists()
        unavailable.write_bytes(b'existing output')
        with patch('cutvoke.rendering.render._alpha_decoder_available', return_value=False):
            with pytest.raises(RenderError, match='WEBM_ALPHA_DECODER_UNAVAILABLE'):
                render.render(project, str(unavailable), quality='high', overwrite=True)
        assert unavailable.read_bytes() == b'existing output'
    finally:
        service.close()

@pytest.mark.parametrize('tags,expected', [(None, False), ([], False), ({'ALPHA_MODE': '1'}, True), ({'alpha_mode': '0'}, False)])
def test_probe_alpha_tags_are_typed_and_case_insensitive(tmp_path, tags, expected):
    source = tmp_path / 'fake.webm'; source.write_bytes(b'fixture')
    info = {'streams': [{'codec_type': 'video', 'codec_name': 'vp9', 'pix_fmt': 'yuv420p', 'tags': tags}], 'format': {}}
    result = subprocess.CompletedProcess([], 0, json.dumps(info), '')
    with patch('cutvoke.rendering.render.subprocess.run', return_value=result):
        metadata = RenderService()._probe_media_uncached(str(source))
    assert metadata['webm_alpha'] is expected
    assert metadata['pix_fmt'] == 'yuv420p'  # Container hint is not decoded pixels.
