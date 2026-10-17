"""Dense editable graphic layers get native caching and bounded command lines."""

from pathlib import Path
import shutil

import numpy as np
from PIL import Image
import pytest

from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
import cutvoke.core.render as renderer_module


pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')


def test_static_stack_is_cached_without_baking_project_or_losing_transparency(tmp_path, monkeypatch):
    blank = tmp_path / 'blank.png'
    Image.new('RGBA', (128, 96)).save(blank)
    project = EditService().create_project('graphics', width=128, height=96)
    effects = [
        {'effectId': 'cutvoke.fx.shape', 'params': {'shape': 'ellipse', 'mode': 'outline',
         'x': .5, 'y': .5, 'w': .6, 'h': .6, 'color': '#0A84FF', 'strokeWidth': 4}},
        {'effectId': 'cutvoke.fx.glow', 'params': {'intensity': 1}},
        {'effectId': 'cutvoke.fx.chromatic', 'params': {'offset': 2}},
        {'effectId': 'cutvoke.fx.chromakey', 'params': {'color': '0x000000', 'similarity': .03, 'blend': .04}},
    ]
    clips = [Clip(f'c{i}', AssetReference(f'a{i}', str(blank)), Rational.of(i),
                  Rational.of(i + 1), Rational.of(0), effects=effects.copy()) for i in range(2)]
    project.sequence.tracks = [Track('v', 'video', clips)]
    renderer = RenderService()
    # Exercise the same file path used for a Windows command exceeding 32K.
    monkeypatch.setattr(renderer_module, '_FILTER_COMPLEX_INLINE_LIMIT', 1)
    with renderer._static_mask_sources(project) as prepared:
        first, second = prepared.sequence.tracks[0].clips
        assert first.asset_ref.source_path == second.asset_ref.source_path
        cache_path = Path(first.asset_ref.source_path)
        assert cache_path != blank and not first.effects
        rgba = np.asarray(Image.open(cache_path).convert('RGBA'))
        assert rgba[0, 0, 3] == 0 and np.count_nonzero(rgba[:, :, 3] > 100) > 300
        renderer.extract_frame(prepared, .2, str(tmp_path / 'preview.png'))
        renderer.extract_still(prepared, .2, str(tmp_path / 'still.png'))
        with Image.open(tmp_path / 'preview.png') as a, Image.open(tmp_path / 'still.png') as b:
            np.testing.assert_array_equal(np.asarray(a.convert('RGB')), np.asarray(b.convert('RGB')))
        assert not list(cache_path.parent.glob('*.ffgraph'))
    assert not cache_path.exists()
    assert clips[0].asset_ref.source_path == str(blank) and len(clips[0].effects) == 4


def test_time_ranged_or_temporal_effects_are_not_folded(tmp_path):
    source = tmp_path / 'blank.png'
    Image.new('RGBA', (32, 32)).save(source)
    project = EditService().create_project('dynamic', width=32, height=32)
    clip = Clip('c', AssetReference('a', str(source)), Rational.of(0), Rational.of(1), Rational.of(0),
                effects=[{'effectId': 'cutvoke.fx.shape', 'params': {'shape': 'rect'}},
                         {'effectId': 'cutvoke.fx.glitch', 'params': {'amount': .4}}])
    project.sequence.tracks = [Track('v', 'video', [clip])]
    with RenderService()._static_mask_sources(project) as prepared:
        assert prepared is project
