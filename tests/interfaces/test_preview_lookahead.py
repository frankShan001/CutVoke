"""Speculative playback must yield to seeks and share the exact cached pixels."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time

from PIL import Image

from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditService
from cutvoke.interfaces.httpapi import HttpApi
from cutvoke.rendering.render import RenderCancelled, RenderService


class CancellableRenderer(RenderService):
    def __init__(self):
        super().__init__(gpu_effects=False)
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancelled = threading.Event()
        self.calls = 0

    def probe_media(self, path):
        return {"has_video": True, "has_audio": False, "duration": 2,
                "width": 160, "height": 90}

    def render(self, project, path, cancel_event=None, **kwargs):
        self.calls += 1
        call = self.calls
        if call == 1:
            self.started.set()
            while not self.release.wait(.01):
                if cancel_event is not None and cancel_event.is_set():
                    self.cancelled.set()
                    raise RenderCancelled("speculative render yielded")
        Path(path).write_bytes(b"exact-window-" + str(project.sequence.tracks[0].clips[0].source_start).encode())
        return {"duration": 2}


def setup(tmp_path):
    service = EditService()
    project = service.create_project("lookahead", width=160, height=90)
    project.sequence.tracks = [Track("v", "video", [Clip(
        "c", AssetReference("a", str(tmp_path / "source.mp4")), R.of(0), R.of(12), R.of(0))])]
    renderer = CancellableRenderer()
    api = HttpApi(service, renderer, media_dir=str(tmp_path / "media"))
    return api, project, renderer


def test_foreground_seek_cancels_different_speculative_interval(tmp_path):
    api, project, renderer = setup(tmp_path)
    try:
        with ThreadPoolExecutor(2) as workers:
            parent = threading.Event()
            background = workers.submit(api._preview_window_file, project, 1, parent, priority=10)
            assert renderer.started.wait(2)
            foreground = workers.submit(api._preview_window_file, project, 3)
            path, digest, duration = foreground.result(timeout=2)
            assert renderer.cancelled.is_set()
            assert not parent.is_set(), "a seek must not cancel the full preparation"
            try:
                background.result(timeout=1)
                raise AssertionError("obsolete lookahead completed")
            except RenderCancelled:
                pass
            assert Path(path).is_file() and duration == 2
            assert not list(Path(api.preview_cache.directory).glob("*.building*"))
            # The interrupted interval remains retryable; no false cache hit.
            renderer.release.set()
            assert api._preview_window_file(project, 1)[1] != digest
    finally:
        renderer.release.set()
        api.close()


def test_demand_for_same_window_reuses_background_work(tmp_path):
    api, project, renderer = setup(tmp_path)
    try:
        with ThreadPoolExecutor(2) as workers:
            background = workers.submit(api._preview_window_file, project, 1, priority=10)
            assert renderer.started.wait(2)
            demand = workers.submit(api._preview_window_file, project, 1)
            renderer.release.set()
            assert background.result(timeout=2) == demand.result(timeout=2)
            assert renderer.calls == 1
            assert not renderer.cancelled.is_set()
    finally:
        renderer.release.set()
        api.close()


def test_external_visual_edit_terminates_old_preparation_without_blocking_next(tmp_path):
    service = EditService()
    project = service.create_project("external-edit", width=160, height=90)
    source = tmp_path / "source.png"
    Image.new("RGB", (160, 90), "red").save(source)
    project.sequence.tracks = [Track("v", "video", [Clip(
        "c", AssetReference("a", str(source)), R.of(0), R.of(2), R.of(0))])]
    api = HttpApi(service, RenderService(gpu_effects=False), media_dir=str(tmp_path / "media"))
    manager = api._preview_preparation
    original = manager.window
    calls = []

    def window(snapshot, index, event, **options):
        calls.append(snapshot.sequence.background_color)
        if len(calls) == 1:
            # An external Agent updated the live project, while this browser
            # never sent a cancel request for its previous immutable snapshot.
            project.sequence.background_color = "#123456"
        return original(snapshot, index, event, **options)

    manager.window = window

    def terminal(job):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = manager.get(job["jobId"])
            if result["state"] in {"completed", "failed", "cancelled"}:
                return result
            time.sleep(.01)
        raise AssertionError("preparation kept retrying an obsolete snapshot")

    try:
        assert terminal(manager.submit(project))["state"] == "cancelled"
        assert len(calls) == 1
        assert terminal(manager.submit(project))["state"] == "completed"
        assert len(calls) == 2
    finally:
        api.close()
