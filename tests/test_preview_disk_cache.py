"""Regression gates for long projects, restart reuse and render queue fairness."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import time

import pytest

from cutvoke.core.preview_cache import PreviewDiskCache, PreviewRenderScheduler
from cutvoke.core.render import RenderCancelled
from cutvoke.core.httpapi import HttpApi
from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.rational import Rational as R
from cutvoke.core.service import EditService


class Renderer:
    def __init__(self):
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.block = False

    def render(self, project, path, **kwargs):
        self.calls += 1
        self.started.set()
        if self.block:
            assert self.release.wait(5)
        Path(path).write_bytes(b"encoded-preview")
        return {"duration": 8}


def project():
    service = EditService()
    p = service.create_project("cache-long")
    p.sequence.tracks = [Track("video", "video", clips=[Clip(
        "shot", AssetReference("source", "missing-for-fake.mp4"), R.of(0), R.of(160), R.of(0))])]
    return service, p


def test_more_than_eight_windows_and_restart_never_reencode(tmp_path):
    service, p = project()
    first = Renderer()
    api = HttpApi(service, first, media_dir=str(tmp_path / "media"))
    try:
        paths = [api._preview_window_file(p, i)[0] for i in range(20)]
        assert first.calls == 20
        assert all(Path(path).exists() for path in paths)
        for index in (0, 19, 1, 15, 4, 0):
            assert api._preview_window_file(p, index)[0] == paths[index]
        assert first.calls == 20
    finally:
        api.close()
    second = Renderer()
    api = HttpApi(service, second, media_dir=str(tmp_path / "media"))
    try:
        for index in range(20):
            assert api._preview_window_file(p, index)[0] == paths[index]
        assert second.calls == 0
    finally:
        api.close()


def test_cached_seek_bypasses_blocked_render_and_duplicates_coalesce(tmp_path):
    service, p = project()
    renderer = Renderer()
    api = HttpApi(service, renderer, media_dir=str(tmp_path / "media"))
    warm = api._preview_window_file(p, 0)
    renderer.started.clear(); renderer.block = True
    try:
        with ThreadPoolExecutor(3) as workers:
            first = workers.submit(api._preview_window_file, p, 1)
            assert renderer.started.wait(1)
            duplicate = workers.submit(api._preview_window_file, p, 1)
            cached = workers.submit(api._preview_window_file, p, 0)
            assert cached.result(timeout=.5) == warm
            renderer.release.set()
            assert first.result() == duplicate.result()
        assert renderer.calls == 2
    finally:
        renderer.release.set(); api.close()


def test_priority_and_cancelled_obsolete_work():
    scheduler = PreviewRenderScheduler()
    order = []
    hold = threading.Event(); admitted = threading.Event()
    cancel = threading.Event()
    def run(name, priority, event=None):
        with scheduler.slot(event or threading.Event(), priority):
            order.append(name)
            if name == "active":
                admitted.set(); assert hold.wait(2)
    with ThreadPoolExecutor(4) as workers:
        active = workers.submit(run, "active", 0)
        assert admitted.wait(1)
        background = workers.submit(run, "background", 10)
        stale = workers.submit(run, "stale", 0, cancel)
        foreground = workers.submit(run, "foreground", 0)
        deadline = time.monotonic() + 1
        while len(scheduler.queue) < 3 and time.monotonic() < deadline:
            time.sleep(.001)
        cancel.set()
        with pytest.raises(RenderCancelled): stale.result(timeout=1)
        hold.set()
        active.result(); foreground.result(); background.result()
    assert order == ["active", "foreground", "background"]


def test_byte_lru_pins_and_project_files_are_preserved(tmp_path):
    cache = PreviewDiskCache(str(tmp_path / "media"), grace_seconds=0)
    cache.max_bytes = 15
    def put(index):
        temporary = tmp_path / "temporary"
        temporary.write_bytes(b"0123456789")
        return cache.publish(f"window_{index:032x}.mp4", temporary)
    try:
        old = put(1)
        with cache.pin(old):
            new = put(2)
            assert Path(old).exists()
            assert not Path(new).exists() # Only unpinned LRU candidates may go.
        unmanaged = cache.directory / "my-project.mp4"
        unmanaged.write_bytes(b"original")
        cache.trim(clear=True)
        assert unmanaged.read_bytes() == b"original"
        assert not Path(old).exists()
    finally: cache.close()


def test_persisted_settings_and_replaced_file_validation(tmp_path):
    cache = PreviewDiskCache(str(tmp_path / "media"))
    result = cache.configure(directory=str(tmp_path / "other"), max_bytes=512 * 1024**2)
    assert result["directory"].endswith(".cutvoke-preview-cache")
    name = "frame_" + "a" * 32 + ".png"
    temporary = tmp_path / "tmp"; temporary.write_bytes(b"frame")
    cache.publish(name, temporary, {"valid": True}); cache.close()
    cache = PreviewDiskCache(str(tmp_path / "media"))
    try:
        assert cache.status()["maxBytes"] == 512 * 1024**2
        assert cache.get(name, {"valid": True}, lambda _: pytest.fail("Already verified media was probed again"))
        cache.path(name).write_bytes(b"corrupt replacement")
        assert cache.get(name, {"valid": True}, lambda _: False) is None
        with pytest.raises(ValueError): cache.path("../source.mp4")
    finally: cache.close()
