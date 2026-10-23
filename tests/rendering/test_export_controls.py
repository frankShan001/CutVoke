"""Export controls exercise measured FFmpeg work and durable task outcomes."""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational
from cutvoke.editing.service import EditService
from cutvoke.rendering.render import RenderCancelled, RenderService
from cutvoke.storage.export_queue import ExportQueue
from cutvoke.storage.store import ProjectStore


def cpu_renderer():
    renderer = RenderService()
    renderer.gpu_acceleration = False
    return renderer


def project_with_image(tmp_path, service, duration=4, width=640, height=360):
    image = tmp_path / "source.png"
    Image.new("RGBA", (width, height), (20, 130, 230, 128)).save(image)
    project = service.create_project("export-controls", width=width, height=height,
                                     fps=Rational.of(30))
    project.sequence.tracks = [Track("v", "video", [Clip(
        "image", AssetReference("image", str(image)), Rational.of(0),
        Rational.of(duration), Rational.of(0))])]
    service.store.save(project)
    return project


def wait_terminal(queue, job_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = queue.get(job_id)
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(.02)
    pytest.fail(f"export did not finish: {queue.get(job_id)}")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_real_progress_and_export_parameters_survive_another_connection(tmp_path):
    class ObservedStore(ProjectStore):
        reports = None
        def update_export_job_progress(self, job_id, progress):
            result = super().update_export_job_progress(job_id, progress)
            if self.reports is not None:
                self.reports.append(progress)
            return result

    store = ObservedStore(str(tmp_path / "projects.sqlite"))
    store.reports = []
    service = EditService(store)
    renderer = cpu_renderer()
    queue = ExportQueue(store, service, render_factory=lambda: renderer)
    try:
        project = project_with_image(tmp_path, service, duration=8)
        payload = {"outPath": str(tmp_path / "result.mp4"), "quality": "medium",
                   "videoBitrateKbps": 1800, "audioBitrateKbps": 128,
                   "range": {"start": .5, "end": 7.5}}
        job = queue.submit(project, payload)
        result = wait_terminal(queue, job["jobId"])
        assert result["status"] == "succeeded", result
        assert abs(result["result"]["duration"] - 7) < .05
        encoded = [p for p in store.reports if p["phase"] == "encoding"]
        assert any(p.get("processedSeconds", 0) > 0 for p in encoded), encoded
        assert all(0 <= p["fraction"] <= 1 for p in encoded)
        assert all(p.get("etaSeconds") is None or p["etaSeconds"] >= 0 for p in encoded)
        assert store.reports[0]["phase"] == "preparing"
        assert store.reports[-1]["phase"] == "verifying"
        second = ProjectStore(str(tmp_path / "projects.sqlite"))
        try:
            persisted = second.get_export_job(job["jobId"])
            assert persisted["request"] == payload
            assert persisted["progress"]["phase"] == "verifying"
            assert persisted["status"] == "succeeded"
            assert persisted["result"]["videoEncoder"] == "libx264"
        finally:
            second.close()
    finally:
        queue.shutdown()
        store.close()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_cancellation_stops_real_ffmpeg_and_publishes_no_output(tmp_path):
    store = ProjectStore(str(tmp_path / "projects.sqlite"))
    service = EditService(store)
    event = threading.Event()
    measurements = []
    try:
        project = project_with_image(tmp_path, service, duration=120, width=1280, height=720)
        out = tmp_path / "cancelled.mp4"
        def measured(progress):
            measurements.append(progress)
            if progress.get("processedSeconds", 0) > 0:
                event.set()
        with pytest.raises(RenderCancelled):
            cpu_renderer().render(project, str(out),
                cancel_event=event, progress_callback=measured)
        assert any(p.get("processedSeconds", 0) > 0 for p in measurements)
        assert not out.exists()
        assert not Path(str(out) + ".tmp.mp4").exists()
    finally:
        store.close()


def test_queued_cancel_and_failed_job_keep_honest_durable_outcomes(tmp_path):
    store = ProjectStore(str(tmp_path / "projects.sqlite"))
    service = EditService(store)
    queue = ExportQueue(store, service, autostart=False)
    try:
        project = project_with_image(tmp_path, service)
        job = queue.submit(project, {"outPath": str(tmp_path / "cancel.mp4"), "quality": "low"})
        assert queue.cancel(job["jobId"])["cancelled"] is True
        assert store.get_export_job(job["jobId"])["status"] == "cancelled"
        assert queue.cancel(job["jobId"])["cancelled"] is False
        project.sequence.tracks[0].clips[0].asset_ref.source_path = str(tmp_path / "missing.mp4")
        failed = queue.submit(project, {"outPath": str(tmp_path / "failed.mp4")})
        queue.start()
        terminal = wait_terminal(queue, failed["jobId"])
        assert terminal["status"] == "failed"
        assert terminal["error"]["code"] == "RENDER_FAILED"
        assert store.get_export_job(failed["jobId"])["error"]["code"] == "RENDER_FAILED"
        assert not (tmp_path / "cancel.mp4").exists()
        assert not (tmp_path / "failed.mp4").exists()
    finally:
        queue.shutdown()
        store.close()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_running_queue_can_be_cancelled_from_another_editor_connection(tmp_path):
    store = ProjectStore(str(tmp_path / "projects.sqlite"))
    second_store = ProjectStore(str(tmp_path / "projects.sqlite"))
    service = EditService(store)
    queue = ExportQueue(store, service, render_factory=cpu_renderer)
    other_queue = ExportQueue(second_store, autostart=False)
    try:
        project = project_with_image(tmp_path, service, duration=120, width=1280, height=720)
        out = tmp_path / "running-cancel.mp4"
        job = queue.submit(project, {"outPath": str(out)})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            row = second_store.get_export_job(job["jobId"])
            if (row.get("progress") or {}).get("processedSeconds", 0) > 0:
                break
            time.sleep(.02)
        else:
            pytest.fail("No measured work from running queue")
        assert other_queue.cancel(job["jobId"])["cancelled"] is True
        assert second_store.get_export_job(job["jobId"])["status"] == "cancelled"
        queue.shutdown(timeout=10)
        assert not queue.reload_status()["busy"]
        assert queue.get(job["jobId"])["status"] == "cancelled"
        assert not out.exists()
        assert not Path(str(out) + ".tmp.mp4").exists()
    finally:
        for job in queue.list():
            if job["status"] in {"queued", "running"}:
                queue.cancel(job["jobId"])
        queue.shutdown()
        second_store.close()
        store.close()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_transparent_mov_is_real_prores_with_alpha(tmp_path):
    store = ProjectStore(str(tmp_path / "projects.sqlite"))
    service = EditService(store)
    queue = ExportQueue(store, service,
                        render_factory=cpu_renderer)
    try:
        project = project_with_image(tmp_path, service, duration=1, width=160, height=90)
        out = tmp_path / "transparent.mov"
        job = queue.submit(project, {"outPath": str(out), "transparent": True})
        completed = wait_terminal(queue, job["jobId"])
        assert completed["status"] == "succeeded", completed
        assert completed["result"]["has_alpha"] is True
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"],
            check=True, capture_output=True).stdout
        assert 100 < raw[3] < 160
    finally:
        queue.shutdown()
        store.close()
