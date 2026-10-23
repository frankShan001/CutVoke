"""Verify actual source pixels and amplitude, not just thumbnail HTTP responses."""
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image
import pytest

from cutvoke.domain.model import AssetReference, Clip
from cutvoke.domain.rational import Rational
from cutvoke.domain.speed_curve import SpeedCurve
from cutvoke.rendering.render import RenderService
from cutvoke.rendering.timeline_media import timeline_media


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    root = tmp_path_factory.mktemp("timeline-media")
    video = root / "colors.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:s=160x90:r=20:d=1",
                    "-f", "lavfi", "-i", "color=green:s=160x90:r=20:d=1", "-f", "lavfi", "-i", "color=blue:s=160x90:r=20:d=1",
                    "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-c:v", "libx264", str(video)],
                   check=True, capture_output=True)
    audio = root / "amplitude.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "aevalsrc=if(lt(t\\,1)\\,0.8*sin(2*PI*220*t)\\,if(lt(t\\,2)\\,0\\,0.3*sin(2*PI*220*t))):s=16000:d=3", str(audio)],
                   check=True, capture_output=True)
    return video, audio


def clip(path, *, start=0, duration=2, speed=1):
    return Clip("clip", AssetReference("asset", str(path)), Rational.of(0), Rational.from_float(duration),
                Rational.from_float(start), speed=Rational.from_float(speed))


def strip(tmp_path, item, kind="thumbnail", **kwargs):
    return timeline_media(render=RenderService(gpu_effects=False), clip=item, cache_dir=tmp_path,
                          kind=kind, width=192, **kwargs)


def colors(path):
    with Image.open(path) as image:
        return [image.convert("RGB").getpixel((x, 24)) for x in (48, 144)]


def test_thumbnail_follows_trim_reverse_speed_and_freeze(media, tmp_path):
    video, _ = media
    green, blue = colors(strip(tmp_path, clip(video, start=1)))
    assert green[1] > 100 and green[0] < 15 and blue[2] > 230
    blue, green = colors(strip(tmp_path, clip(video, start=1, speed=-1)))
    assert blue[2] > 230 and green[1] > 100
    item = clip(video, start=.5, duration=1, speed=2)
    green, blue = colors(strip(tmp_path, item))
    assert green[1] > 100 and blue[2] > 230
    item.freeze_at = Rational.of(1, 2)
    assert all(red[0] > 230 and red[1] < 15 for red in colors(strip(tmp_path, item)))


def test_ramp_and_visible_range_use_current_source_coordinates(media, tmp_path):
    video, _ = media
    item = clip(video)
    item.speed_curve = SpeedCurve.from_points(Rational.of(3), [{"at": 0, "speed": 1}, {"at": 1, "speed": 2}])
    item.timeline_end = item.speed_curve.timeline_duration
    # Only request the final blue source second; no entire-project render is involved.
    begin = item.speed_curve.timeline_at_source(2.1)
    assert all(value[2] > 230 for value in colors(strip(tmp_path, item, start=begin)))


def test_waveform_has_real_silence_and_reverse_mapping(media, tmp_path):
    _, audio = media
    forward = strip(tmp_path, clip(audio, duration=3), "waveform")
    with Image.open(forward) as image:
        alpha = np.asarray(image)[..., 3]
        heights = np.count_nonzero(alpha, axis=0)
        loud, silence, quiet = [float(np.median(part)) for part in np.array_split(heights, 3)]
        assert loud > quiet > silence and silence <= 2
    reverse = strip(tmp_path, clip(audio, start=0, duration=3, speed=-1), "waveform")
    with Image.open(reverse) as image:
        heights = np.count_nonzero(np.asarray(image)[..., 3], axis=0)
        quiet, silence, loud = [float(np.median(part)) for part in np.array_split(heights, 3)]
        assert loud > quiet > silence and silence <= 2


def test_reverse_lower_bound_and_trim_invalidate_visible_strip(media, tmp_path):
    video, _ = media
    item = clip(video, start=0, duration=3, speed=-1)
    original = strip(tmp_path, item, end=1)
    assert all(value[2] > 230 for value in colors(original))
    # Left trimming reverse removes the high source end, while its stored lower bound stays zero.
    item.timeline_start = Rational.of(1)
    trimmed = strip(tmp_path, item, end=1)
    assert trimmed != original
    assert all(value[1] > 100 and value[0] < 15 for value in colors(trimmed))


def test_cache_reuses_decode_and_source_change_invalidates(media, tmp_path, monkeypatch):
    video, _ = media
    item = clip(video)
    renderer = RenderService(gpu_effects=False)
    first = timeline_media(render=renderer, clip=item, cache_dir=tmp_path, kind="thumbnail", width=192)
    monkeypatch.setattr(renderer, "thumbnail_media", lambda *a, **kw: pytest.fail("cache hit decoded source again"))
    assert timeline_media(render=renderer, clip=item, cache_dir=tmp_path, kind="thumbnail", width=192) == first
    item.source_start = Rational.of(1)
    second = strip(tmp_path, item)
    assert second != first
    assert len(list(tmp_path.glob("*.png"))) == 2
    assert not list(tmp_path.glob("*.tmp*"))
    assert strip(tmp_path, clip(video), "waveform") is None


def test_invalid_ranges_fail_without_decoder(media, tmp_path):
    video, _ = media
    for start, end in ((-1, 1), (0, 0), (0, 3), (float("nan"), 1)):
        with pytest.raises(ValueError):
            strip(tmp_path, clip(video), start=start, end=end)


def test_same_source_strips_share_one_inflight_decode(media, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    video, _ = media
    renderer = RenderService(gpu_effects=False)
    original, lock, calls = renderer.thumbnail_media, threading.Lock(), []
    def counted(*args, **kwargs):
        with lock:
            calls.append(kwargs.get("time"))
        return original(*args, **kwargs)
    renderer.thumbnail_media = counted
    def request(index):
        item = clip(video)
        item.id = f"same-source-{index}"
        return timeline_media(render=renderer, clip=item, cache_dir=tmp_path, kind="thumbnail", width=192)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outputs = list(pool.map(request, range(8)))
    assert len(set(outputs)) == 1
    assert len(calls) == 2, "two tiles decoded once, regardless of the number of clip IDs"
    assert Image.open(outputs[0]).size == (192, 48)
    assert not list(tmp_path.glob("*.tmp*"))


def test_peer_process_publishes_same_cache_while_windows_target_is_open(media, tmp_path, monkeypatch):
    import cutvoke.rendering.timeline_media as module
    video, _ = media
    def peer_wins(source, destination):
        Path(destination).write_bytes(Path(source).read_bytes())
        raise PermissionError(13, "Windows reader currently holds target open")
    monkeypatch.setattr(module.os, "replace", peer_wins)
    output = strip(tmp_path, clip(video))
    assert Image.open(output).size == (192, 48)
    assert len(list(tmp_path.glob("*.png"))) == 1
