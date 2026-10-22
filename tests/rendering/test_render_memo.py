"""Reuse must stay bounded, concurrent, revision-aware and pixel-exact."""
import copy
from concurrent.futures import Future, ThreadPoolExecutor
from fractions import Fraction
import os
from pathlib import Path
import shutil
import threading
from unittest.mock import patch

import numpy as np
from PIL import Image
import pytest

from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditService
from cutvoke.rendering.render import RenderService
from cutvoke.rendering.render_memo import RenderMemo


def test_lru_byte_budget_and_oversized_results():
    memo = RenderMemo(max_entries=3, max_bytes=6)
    memo.get_or_compute('a', lambda: b'aaa')
    memo.get_or_compute('b', lambda: b'bb')
    memo.get_or_compute('a', lambda: pytest.fail('lost hot entry'))
    memo.get_or_compute('c', lambda: b'ccc')
    assert memo.status()['bytes'] == 6
    assert memo.get_or_compute('b', lambda: b'B') == b'B'  # b was least recent
    memo.get_or_compute('large', lambda: b'1234567')
    assert memo.status()['bytes'] <= 6
    assert memo.get_or_compute('large', lambda: b'retry') == b'retry'


def test_producer_timeout_is_not_confused_with_wait_timeout():
    memo = RenderMemo()
    future = Future()
    future.set_exception(TimeoutError('producer timed out'))
    memo._pending['timeout'] = future
    with pytest.raises(TimeoutError, match='producer timed out'):
        memo.get_or_compute('timeout', lambda: None, cancelled=lambda: False)


def test_concurrent_producers_share_work_and_failed_work_can_retry():
    memo = RenderMemo()
    barrier = threading.Barrier(8)
    calls = []
    def request(_):
        barrier.wait(timeout=5)
        return memo.get_or_compute('same', lambda: calls.append(1) or b'pixels')
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(request, range(8))) == [b'pixels'] * 8
    assert calls == [1]
    def fail():
        raise ValueError('failed render')
    with pytest.raises(ValueError, match='failed render'):
        memo.get_or_compute('failed', fail)
    assert memo.get_or_compute('failed', lambda: False) is False
    assert memo.get_or_compute('failed', lambda: True) is False


@pytest.mark.parametrize('fail', [False, True])
def test_waiting_request_shares_result_or_failure_without_holding_cache_lock(fail):
    entered, waiting, release = threading.Event(), threading.Event(), threading.Event()
    class ObservedFuture(Future):
        def result(self, *args, **kwargs):
            waiting.set()
            return super().result(*args, **kwargs)
    memo = RenderMemo()
    def compute():
        entered.set()
        assert release.wait(5)
        if fail:
            raise ValueError('producer failed')
        return b'pixels'
    with patch('cutvoke.rendering.render_memo.Future', ObservedFuture):
        with ThreadPoolExecutor(max_workers=2) as pool:
            owner = pool.submit(memo.get_or_compute, 'k', compute)
            assert entered.wait(5)
            waiter = pool.submit(memo.get_or_compute, 'k', lambda: pytest.fail('duplicate producer'))
            try:
                assert waiting.wait(5)
                assert memo.get_or_compute('unrelated', lambda: b'other') == b'other'
            finally:
                release.set()
            for future in (owner, waiter):
                if fail:
                    with pytest.raises(ValueError, match='producer failed'):
                        future.result(timeout=5)
                else:
                    assert future.result(timeout=5) == b'pixels'
    assert memo.status()['waits'] == 1
    if fail:
        assert memo.get_or_compute('k', lambda: b'retry') == b'retry'


def test_probe_and_frame_clock_share_revision_aware_results(tmp_path):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'first')
    renderer = RenderService()
    with patch.object(renderer, '_probe_media_uncached', return_value={'width': 10}) as probe:
        assert renderer.probe_media(str(source)) == {'width': 10}
        renderer.probe_media(str(source))['width'] = 99
        assert renderer.probe_media(str(source))['width'] == 10
        assert probe.call_count == 1
        source.write_bytes(b'replaced file')
        renderer.probe_media(str(source))
        assert probe.call_count == 2
    with patch('cutvoke.rendering.vfx_matte.regular_timestamps', return_value=True) as clock:
        for _ in range(3):
            assert renderer._regular_timestamps(str(source), 0, 1, Fraction(24))
        assert clock.call_count == 1
        renderer._regular_timestamps(str(source), 1, 1, Fraction(24))
        renderer._regular_timestamps(str(source), 0, 1, Fraction(30))
        assert clock.call_count == 3
        source.write_bytes(b'new source clock')
        renderer._regular_timestamps(str(source), 0, 1, Fraction(24))
        assert clock.call_count == 4
        assert clock.call_args.kwargs == {'exact_interval': True}


def test_ordinary_timeline_does_not_copy_for_raster_mattes():
    project = EditService().create_project('no masks')
    project.sequence.tracks = [Track('v', 'video', [
        Clip(str(i), AssetReference('a', 'fake.mp4'), R.of(i), R.of(i+1), R.of(0))
        for i in range(1000)])]
    renderer = RenderService()
    with patch('cutvoke.rendering.render.copy.deepcopy', side_effect=AssertionError('copied')):
        with renderer._raster_mask_sources(project, window=(500, 501)) as prepared:
            assert prepared is project


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
def test_static_pixels_reuse_across_requests_but_invalidate_source_effect_and_scale(tmp_path):
    source = tmp_path / 'source.png'
    Image.new('RGBA', (128, 96), (240, 60, 20, 200)).save(source)
    project = EditService().create_project('cache', width=128, height=96)
    clip = Clip('c', AssetReference('a', str(source)), R.of(0), R.of(2), R.of(0), effects=[
        dict(effectId='cutvoke.fx.mask', params=dict(shape='circle', x=.5, y=.5, w=.6, h=.8, feather=.1))])
    project.sequence.tracks = [Track('v', 'video', [clip])]
    original = copy.deepcopy(project.to_dict())
    renderer = RenderService(gpu_effects=False)
    def pixels():
        with renderer._static_mask_sources(project) as prepared:
            path = Path(prepared.sequence.tracks[0].clips[0].asset_ref.source_path)
            assert path != source
            with Image.open(path) as image:
                result = np.array(image.convert('RGBA'))
        assert not path.exists()
        return result
    cold = pixels()
    np.testing.assert_array_equal(pixels(), cold)
    assert renderer._graphic_cache.status()['misses'] == 1
    # Composition-only edits reuse the source treatment.
    clip.effects.append(dict(effectId='cutvoke.transform', params=dict(position=dict(x=12, y=3))))
    np.testing.assert_array_equal(pixels(), cold)
    assert renderer._graphic_cache.status()['hits'] == 2
    clip.effects.pop()
    assert project.to_dict() == original
    clip.effects[0]['params']['w'] = .3
    changed = pixels()
    assert not np.array_equal(changed, cold)
    renderer._render_ctx.preview_scale = .5
    assert pixels().shape == (48, 64, 4)
    renderer._render_ctx.preview_scale = 1
    Image.new('RGBA', (128, 96), (20, 80, 230, 200)).save(source)
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    changed_source = pixels()
    assert not np.array_equal(changed_source, changed)
    assert renderer._graphic_cache.status()['misses'] == 4
    fresh = RenderService(gpu_effects=False)
    with fresh._static_mask_sources(project) as prepared:
        with Image.open(prepared.sequence.tracks[0].clips[0].asset_ref.source_path) as image:
            np.testing.assert_array_equal(changed_source, np.array(image.convert('RGBA')))
