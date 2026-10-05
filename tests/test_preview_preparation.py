"""Playback preparation retains evicted windows and publishes only complete media."""
import os
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from cutvoke.core.model import AssetReference, Caption, Clip, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderCancelled, RenderService
from cutvoke.core.service import EditService
from cutvoke.core.preview_prepare import PreviewPreparation, preview_identity
from cutvoke.core.httpapi import HttpApi


class PreviewPreparationTests(unittest.TestCase):
    def project(self, source, duration=96):
        project = EditService().create_project("prepare", width=128, height=72)
        project.sequence.tracks = [Track("video", "video", [Clip(
            "clip", AssetReference("asset", str(source)), Rational.of(0),
            Rational.of(duration), Rational.of(0))])]
        return project

    def wait(self, manager, job_id):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = manager.get(job_id)
            if result["state"] in ("completed", "failed", "cancelled"):
                return result
            time.sleep(0.025)
        self.fail("preparation did not finish")

    def test_identity_tracks_nested_content_and_source_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            source.write_bytes(b"old")
            project = self.project(source)
            initial = preview_identity(project, 15)
            project.name = "renamed"
            project.revision = "99"
            project.sequence.captions = [Caption(id="caption", text="text", start=Rational.of(0), end=Rational.of(1))]
            self.assertEqual(initial, preview_identity(project, 15))
            source.write_bytes(b"replacement")
            self.assertNotEqual(initial, preview_identity(project, 15))

    def test_cancel_interrupts_worker_and_never_publishes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            started = threading.Event()
            def window(project, index, event):
                started.set()
                event.wait(10)
                raise RenderCancelled("cancelled")
            manager = PreviewPreparation(directory, RenderService(), window, threading.RLock(), renderer_version=15)
            try:
                job = manager.submit(self.project(Path(directory) / "source"))
                self.assertTrue(started.wait(2))
                self.assertEqual(manager.cancel(job["jobId"])["state"], "cancelled")
            finally:
                manager.close()
            self.assertFalse(list(Path(directory).glob("prepared_*.mp4")))
            self.assertFalse(list(Path(directory).glob("prepare-*")))

    def test_http_refuses_stale_revision_and_unfinished_media(self):
        with tempfile.TemporaryDirectory() as directory:
            service = EditService()
            project = service.create_project("missing", width=128, height=72)
            project.sequence.tracks = self.project(Path(directory) / "missing.png", 2).sequence.tracks
            api = HttpApi(service, RenderService(), media_dir=directory)
            try:
                status, result = api.handle("POST", "/api/v1/projects/missing/preview-jobs", {"revision": "stale"})
                self.assertEqual(status, 409)
                self.assertEqual(result["error"]["code"], "REVISION_CONFLICT")
                status, result = api.handle("POST", "/api/v1/projects/missing/preview-jobs", {"revision": project.revision})
                self.assertEqual(status, 202)
                terminal = self.wait(api._preview_preparation, result["jobId"])
                self.assertEqual(terminal["state"], "failed")
                status, _ = api.handle("GET", f"/api/v1/preview-jobs/{result['jobId']}/media", {})
                self.assertEqual(status, 422)
                self.assertEqual(api.handle("GET", "/api/v1/preview-jobs/unknown", {})[0], 404)
            finally:
                api.close()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_twelve_windows_survive_eviction_join_and_reopen_from_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "eight.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "testsrc2=s=128x72:r=30:d=8", "-c:v", "libx264", "-preset", "ultrafast",
                            "-pix_fmt", "yuv420p", str(source)], check=True, capture_output=True)
            calls, cache = [], []
            def window(project, index, event):
                calls.append(index)
                path = root / f"window_{index}.mp4"
                shutil.copyfile(source, path)
                cache.append(path)
                if len(cache) > 2:
                    cache.pop(0).unlink()
                return str(path), str(index), 8.0
            renderer = RenderService()
            manager = PreviewPreparation(str(root / "prepared"), renderer, window,
                                         threading.RLock(), renderer_version=15)
            try:
                project = self.project(source)
                result = self.wait(manager, manager.submit(project)["jobId"])
                self.assertEqual(result["state"], "completed", result.get("error"))
                self.assertEqual(result["completedWindows"], 12)
                media, _, duration = manager.media(result["jobId"])
                renderer._verify(media, 96, 128, 72)
                timing = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                         "-show_entries", "stream=start_time,duration", "-of", "json", media],
                                        check=True, capture_output=True, text=True)
                self.assertAlmostEqual(float(json.loads(timing.stdout)["streams"][0]["start_time"]), 0, places=3)
                count = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                                        "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", media],
                                       check=True, capture_output=True, text=True)
                self.assertEqual(int(count.stdout.strip()), 2880)
                project.revision = "2"
                warm = self.wait(manager, manager.submit(project)["jobId"])
                self.assertTrue(warm["cached"])
                self.assertEqual(len(calls), 12)
            finally:
                manager.close()
