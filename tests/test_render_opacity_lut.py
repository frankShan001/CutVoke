"""Frame-accurate alpha LUTs must preserve colors and existing transparency."""
from fractions import Fraction
import shutil
import subprocess

import numpy as np
import pytest

from cutvoke.core.keyframes import Keyframe, expression
from cutvoke.core.model import AssetReference, Clip
from cutvoke.core.rational import Rational as R
from cutvoke.core.render import RenderService


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30000, 1001)])
@pytest.mark.parametrize('easing', ['linear', 'ease-in', 'ease-out'])
def test_lut_matches_per_pixel_opacity_and_keeps_chroma(tmp_path, fps, easing):
    lane = [Keyframe('a', R.of(1, 5), .1, easing),
            Keyframe('b', R.of(7, 10), .9, easing),
            Keyframe('c', R.of(1), .4)]
    clip = Clip('c', AssetReference('a', ''), R.of(0), R.of(6, 5), R.of(0),
                keyframes={'opacity': lane})
    opacity = expression(lane, f'(N*{fps.denominator}/{fps.numerator})', 1)
    reference = ("geq=lum='p(X,Y)':cb='p(X,Y)':cr='p(X,Y)':"
                 f"a='alpha(X,Y)*clip({opacity},0,1)'")
    accelerated = RenderService._opacity_filter(clip, fps, 'test')
    # Full-range chroma catches the LUT default's hidden 16..240 clipping;
    # variable source alpha catches implementations that replace transparency.
    rng = np.random.default_rng(42)
    source = rng.integers(0, 256, (4, 16, 24), dtype=np.uint8).tobytes()
    outputs = []
    for index, chain in enumerate([reference, accelerated]):
        graph = tmp_path / f'{index}.ffgraph'
        graph.write_text(f'[0:v]{chain}[v]', encoding='utf-8')
        result = subprocess.run([
            'ffmpeg', '-v', 'error', '-filter_complex_threads', '2',
            '-f', 'rawvideo', '-pixel_format', 'yuva444p', '-video_size', '24x16',
            '-framerate', str(fps), '-i', 'pipe:0', '-/filter_complex', str(graph),
            '-map', '[v]', '-frames:v', '36', '-f', 'rawvideo', 'pipe:1'],
            input=source * 36, capture_output=True, check=True)
        outputs.append(np.frombuffer(result.stdout, dtype=np.uint8).reshape(36, 4, 16, 24))
    np.testing.assert_array_equal(outputs[0][:, :3], outputs[1][:, :3])
    # The Python and libav expression evaluators can straddle an integer floor
    # by one alpha unit; there must be no frame-delay or larger alpha error.
    assert np.max(np.abs(outputs[0][:, 3].astype(int) - outputs[1][:, 3])) <= 1


def test_constant_opacity_does_not_emit_commands():
    clip = Clip('c', AssetReference('a', ''), R.of(0), R.of(3600), R.of(0),
                effects=[{'effectId': 'cutvoke.transform', 'params': {'opacity': .5}}])
    chain = RenderService._opacity_filter(clip, Fraction(24), 'constant')
    assert 'sendcmd' not in chain
    assert 'geq' not in chain
    assert len(chain) < 100
