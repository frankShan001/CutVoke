"""Lossless procedural source reuse must preserve editable authority and clocks."""
import copy
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import threading

import pytest

from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditService
from cutvoke.rendering.render import RenderCancelled, RenderError, RenderService


@pytest.fixture(params=["cutvoke.fx.lightWeave", "cutvoke.fx.opticalField"])
def workload(request, tmp_path, monkeypatch):
    project = EditService().create_project("procedural-cache", width=160, height=96, fps=R.of(24))
    clip = Clip("clip", AssetReference("source", str(tmp_path / "source.mp4")),
                R.of(0), R.of(4), R.of(0), effects=[
                    dict(effectId=request.param, params=dict(seed=17, intensity=.6))])
    project.sequence.tracks = [Track("video", "video", [clip])]
    calls = []

    def cached_source(params, width, height, fps, duration, ffmpeg, cancelled, **kwargs):
        if cancelled():
            raise RuntimeError("cancelled")
        # Each fresh producer yields distinct bytes. A hit must recover the
        # existing bytes rather than re-run this producer or keep a stale path.
        record = dict(params=copy.deepcopy(params), width=width, height=height,
                      fps=str(fps), duration=duration,
                      start=kwargs.get("start_time", 0), prefix=kwargs.get("prefix_duration"),
                      kind=kwargs.get("kind", "light"))
        calls.append(record)
        directory = Path(kwargs.get("transient_directory", str(tmp_path)))
        path = directory / f"source-{len(calls)}.mkv"
        path.write_bytes(f"lossless-movie-{len(calls)}".encode() * 40)
        return str(path)

    monkeypatch.setattr("cutvoke.rendering.vfx_light.cached_source", cached_source)
    return project, clip, RenderService(gpu_effects=False), calls


def prepared_bytes(renderer, project, window=(0, .5), cancel_event=None):
    original = copy.deepcopy(project.to_dict())
    with renderer._procedural_sources(project, render_window=window, cancel_event=cancel_event) as prepared:
        assert prepared is not project
        params = prepared.sequence.tracks[0].clips[0].effects[0]["params"]
        path = Path(params["_renderedLightPath"])
        result = path.read_bytes()
    assert project.to_dict() == original
    assert not path.exists(), "request-scoped intermediate leaked after render"
    return result


def test_reuses_lossless_bytes_after_temp_cleanup_and_composition_only_edits(workload):
    project, clip, renderer, calls = workload
    expected = prepared_bytes(renderer, project)
    assert prepared_bytes(renderer, project) == expected
    clip.effects.append(dict(effectId="cutvoke.transform", params=dict(position=dict(x=32, y=9))))
    assert prepared_bytes(renderer, project) == expected
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["params", "width", "height", "fps", "duration", "clock", "prefix"])
def test_render_identity_invalidates_for_changed_pixels_or_time(workload, change):
    project, clip, renderer, calls = workload
    original = prepared_bytes(renderer, project)
    window = (0, .5)
    if change == "params":
        clip.effects[0]["params"]["seed"] = 18
    elif change == "width":
        project.sequence.width = 192
    elif change == "height":
        project.sequence.height = 128
    elif change == "fps":
        project.sequence.fps = R.of(30)
    elif change == "duration":
        clip.timeline_end += R.of(1)
    elif change == "clock":
        renderer._render_ctx.clip_clocks = {clip.id: (.25, None)}
    elif change == "prefix":
        window = (0, .75)
    changed = prepared_bytes(renderer, project, window)
    assert changed != original
    assert len(calls) == 2
    assert prepared_bytes(renderer, project, window) == changed
    assert len(calls) == 2


def test_cancelled_warm_request_does_not_publish_cached_media(workload):
    project, _clip, renderer, calls = workload
    original = copy.deepcopy(project.to_dict())
    expected = prepared_bytes(renderer, project)
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(RenderCancelled):
        with renderer._procedural_sources(project, cancel_event=cancelled, render_window=(0, .5)):
            pytest.fail("cancelled request published prepared media")
    assert project.to_dict() == original
    assert prepared_bytes(renderer, project) == expected
    assert len(calls) == 1


def test_failed_producer_does_not_poison_retry_or_change_project(workload, monkeypatch):
    project, _clip, renderer, calls = workload
    from cutvoke.rendering import vfx_light
    original = copy.deepcopy(project.to_dict())
    produce = vfx_light.cached_source
    attempts = []

    def fail_once(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("injected producer failure")
        return produce(*args, **kwargs)

    monkeypatch.setattr(vfx_light, "cached_source", fail_once)
    with pytest.raises(RenderError, match="injected producer failure"):
        prepared_bytes(renderer, project)
    assert project.to_dict() == original
    expected = prepared_bytes(renderer, project)
    assert prepared_bytes(renderer, project) == expected
    assert len(calls) == 1
    assert len(attempts) == 2


def test_cancelled_request_does_not_wait_for_older_producer(workload, monkeypatch):
    project, _clip, renderer, _calls = workload
    from cutvoke.rendering import vfx_light
    produce = vfx_light.cached_source
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5), "producer was not released"
        return produce(*args, **kwargs)

    monkeypatch.setattr(vfx_light, "cached_source", slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(prepared_bytes, renderer, project)
        assert entered.wait(5)
        cancelled.set()
        waiter = pool.submit(prepared_bytes, renderer, project, cancel_event=cancelled)
        try:
            with pytest.raises(RenderCancelled):
                waiter.result(timeout=1)
        finally:
            release.set()
        assert owner.result(timeout=5)


def test_cancelled_producer_does_not_fail_uncancelled_waiter(workload, monkeypatch):
    project, _clip, renderer, calls = workload
    from cutvoke.rendering import render_memo, vfx_light
    produce = vfx_light.cached_source
    entered, waiting, release = threading.Event(), threading.Event(), threading.Event()
    owner_cancelled = threading.Event()
    attempts = []

    class ObservedFuture(Future):
        def result(self, *args, **kwargs):
            waiting.set()
            return super().result(*args, **kwargs)

    def cancel_first(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            entered.set()
            assert release.wait(5), "cancelled producer was not released"
        return produce(*args, **kwargs)

    monkeypatch.setattr(render_memo, "Future", ObservedFuture)
    monkeypatch.setattr(vfx_light, "cached_source", cancel_first)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(prepared_bytes, renderer, project, cancel_event=owner_cancelled)
        assert entered.wait(5)
        waiter = pool.submit(prepared_bytes, renderer, project)
        try:
            assert waiting.wait(5), "new request did not share the pending producer"
            owner_cancelled.set()
        finally:
            release.set()
        with pytest.raises(RenderCancelled):
            owner.result(timeout=5)
        expected = waiter.result(timeout=5)
    assert expected
    assert prepared_bytes(renderer, project) == expected
    assert len(attempts) == 2
    assert len(calls) == 1


def test_oversized_media_never_loads_file_bytes_or_enters_memory_cache(workload, monkeypatch):
    project, _clip, renderer, calls = workload
    renderer._procedural_item_bytes = 1
    read_bytes = Path.read_bytes

    def reject_source_read(path):
        if path.name.startswith("source-"):
            pytest.fail("oversized procedural movie was read into memory")
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_source_read)
    original = copy.deepcopy(project.to_dict())
    for _ in range(2):
        with renderer._procedural_sources(project, render_window=(0, .5)) as prepared:
            params = prepared.sequence.tracks[0].clips[0].effects[0]["params"]
            path = Path(params["_renderedLightPath"])
            # Review the mock media independently without invoking the protected
            # producer read_bytes path; production must retain its disk stream.
            with path.open("rb") as media:
                assert media.read().startswith(b"lossless-movie-")
        assert not path.exists()
    assert project.to_dict() == original
    assert len(calls) == 2
    assert renderer._procedural_cache.status()["entries"] == 0
    assert renderer._procedural_cache.status()["bytes"] == 0


def test_oversized_pending_waiter_owns_separate_file_and_cleanup(workload, monkeypatch):
    project, _clip, renderer, calls = workload
    from cutvoke.rendering import render_memo, vfx_light
    renderer._procedural_item_bytes = 1
    produce = vfx_light.cached_source
    entered, waiting, release = threading.Event(), threading.Event(), threading.Event()
    simultaneous = threading.Barrier(2)
    read_bytes = Path.read_bytes

    class ObservedFuture(Future):
        def result(self, *args, **kwargs):
            waiting.set()
            return super().result(*args, **kwargs)

    def slow_first(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(5), "oversized producer was not released"
        return produce(*args, **kwargs)

    def reject_source_read(path):
        if path.name.startswith("source-"):
            pytest.fail("oversized procedural movie was read into memory")
        return read_bytes(path)

    def request():
        with renderer._procedural_sources(project, render_window=(0, .5)) as prepared:
            params = prepared.sequence.tracks[0].clips[0].effects[0]["params"]
            path = Path(params["_renderedLightPath"])
            with path.open("rb") as media:
                content = media.read()
            simultaneous.wait(timeout=5)
        assert not path.exists()
        return path, content

    monkeypatch.setattr(render_memo, "Future", ObservedFuture)
    monkeypatch.setattr(vfx_light, "cached_source", slow_first)
    monkeypatch.setattr(Path, "read_bytes", reject_source_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(request)
        assert entered.wait(5)
        waiter = pool.submit(request)
        try:
            assert waiting.wait(5), "waiter did not observe the pending oversized source"
        finally:
            release.set()
        first, second = owner.result(timeout=5), waiter.result(timeout=5)
    assert first[0] != second[0], "request-scoped intermediate paths were shared"
    assert first[1] != second[1], "oversized waiter reused the producer's media"
    assert len(calls) == 2
    assert renderer._procedural_cache.status()["entries"] == 0
    assert renderer._procedural_cache.status()["bytes"] == 0
