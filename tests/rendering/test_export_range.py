"""Timeline range exports retain full-project timing and flow through HTTP jobs."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import struct
import tempfile
import threading
import time
import unittest
import wave

from PIL import Image

from cutvoke.interfaces.httpapi import HttpApi
from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational
from cutvoke.rendering.render import RenderService
from cutvoke.editing.service import EditError, EditService


def r(value: int) -> Rational:
    return Rational.of(value)


class CaptureRenderer:
    def __init__(self) -> None:
        self.options: dict | None = None
        self.called = threading.Event()

    def render(self, project, out_path: str, **options) -> dict:
        self.options = options
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "wb") as output:
            output.write(b"rendered")
        self.called.set()
        return {
            "output_path": out_path,
            "duration": options.get("output_duration", 4.0),
            "width": project.sequence.width,
            "height": project.sequence.height,
            "has_audio": False,
            "warnings": [],
        }


class ExportRangeTests(unittest.TestCase):
    def make_project(self, project_id: str = "range-test"):
        service = EditService()
        project = service.create_project(project_id, width=160, height=90)
        clip = Clip("clip", AssetReference("video", "unused.mp4"),
                    r(0), r(4), r(0))
        project.sequence.tracks = [Track("v1", "video", [clip])]
        return service, project

    def test_range_validation_rejects_invalid_and_out_of_bounds_values(self) -> None:
        service, project = self.make_project()
        self.addCleanup(service.close)
        self.assertEqual(service._validated_export_range(
            project, {"start": "0.5", "end": "2.25"}), (0.5, 2.25))
        for value in (
            {"start": -0.1, "end": 1},
            {"start": 1, "end": 1},
            {"start": 3, "end": 4.1},
            {"start": float("nan"), "end": 2},
        ):
            with self.subTest(value=value), self.assertRaises(EditError):
                service._validated_export_range(project, value)

    def test_http_sync_and_queue_exports_forward_range(self) -> None:
        service, project = self.make_project("range-http")
        self.addCleanup(service.close)
        renderer = CaptureRenderer()
        media = tempfile.TemporaryDirectory(prefix="cutvoke-export-api-")
        self.addCleanup(media.cleanup)
        api = HttpApi(service, render=renderer, media_dir=media.name)
        self.addCleanup(api.close)
        with tempfile.TemporaryDirectory() as output_dir:
            sync_status, _ = api._dispatch(
                "POST", f"/api/v1/projects/{project.project_id}/export",
                {"outPath": os.path.join(output_dir, "sync.mp4"),
                 "range": {"start": 1, "end": 2.5}})
            self.assertEqual(sync_status, 200)
            self.assertEqual(renderer.options["output_start"], 1.0)
            self.assertEqual(renderer.options["output_duration"], 1.5)

            queue_status, queued = api._dispatch(
                "POST", f"/api/v1/projects/{project.project_id}/exports",
                {"outPath": os.path.join(output_dir, "queued.mp4"),
                 "range": {"start": 0.75, "end": 2.25}, "overwrite": True})
            self.assertEqual(queue_status, 202)
            self.assertTrue(renderer.called.wait(3))
            deadline = time.monotonic() + 3
            jobs = []
            while time.monotonic() < deadline:
                _, response = api._dispatch(
                    "GET", f"/api/v1/projects/{project.project_id}/exports", {})
                jobs = response["jobs"]
                matching = next((job for job in jobs
                                 if job.get("jobId") == queued["jobId"]), None)
                if matching and matching.get("status") == "succeeded":
                    break
                time.sleep(0.02)
            self.assertEqual(renderer.options["output_start"], 0.75)
            self.assertEqual(renderer.options["output_duration"], 1.5)
            self.assertTrue(renderer.options["overwrite"])
            self.assertEqual(matching["status"], "succeeded")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg required")
    def test_bounded_audio_checks_alignment_on_the_continuous_sample_clock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rate = 48000
            audio = os.path.join(directory, "chirp.wav")
            # Distinct sweeps make correlation discriminate small real shifts.
            samples = []
            for i in range(rate * 4):
                t = i / rate
                sample = 0.0 if 2.0 <= t < 2.1 else (
                    0.2 * math.sin(2 * math.pi * (137 * t + 18 * t * t))
                    + 0.12 * math.sin(2 * math.pi * (941 * t + 31 * t * t)))
                samples.append(round(sample * 32767))
            with wave.open(audio, "wb") as output:
                output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                output.writeframes(struct.pack(f"<{len(samples)}h", *samples))

            image = os.path.join(directory, "picture.png")
            Image.new("RGB", (160, 90), (55, 105, 192)).save(image)
            service = EditService()
            try:
                for label, fps, start, source, overlap in (
                    ("leading-silence", r(30), 0.5, r(2), False),
                    ("overlap", r(30), 0.75, r(2), True),
                    ("fractional-rate", Rational.of(30000, 1001), 0.517,
                     Rational.of(1001, 500), False),
                ):
                    with self.subTest(label=label):
                        project = service.create_project(label, width=160, height=90, fps=fps)
                        project.sequence.tracks = [
                            Track("v", "video", [Clip("pic", AssetReference("pic", image), r(0), r(3), r(0))]),
                            Track("a", "audio", [Clip("sound", AssetReference("sound", audio),
                                Rational.of(1, 2), Rational.of(3, 2), source)]),
                        ]
                        if overlap:
                            project.sequence.tracks.append(Track("b", "audio", [
                                Clip("overlap", AssetReference("sound", audio),
                                     Rational.of(7, 10), Rational.of(9, 5), Rational.of(2, 5))
                            ]))
                        renderer = RenderService()
                        renderer.gpu_acceleration = False
                        path = os.path.join(directory, label + ".mp4")
                        result = renderer.render(project, path, quality="low",
                                                 output_start=start, output_duration=1.0)
                        self.assertTrue(os.path.isfile(path))
                        verification = result["audioVerification"]
                        self.assertEqual(verification["status"], "passed")
                        self.assertTrue(verification["windows"])
                        self.assertLess(abs(verification["lengthDifferenceSeconds"]), 0.03)
                        for window in verification["windows"]:
                            self.assertEqual(window["estimatedOffsetSeconds"], 0)
                            self.assertGreater(window["zeroOffsetCorrelation"], 0.99)
            finally:
                service.close()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg required")
    def test_main_export_range_matches_same_window_from_full_render(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            image = os.path.join(media_dir, "pattern.png")
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30",
                "-frames:v", "1", image,
            ], check=True, capture_output=True)
            service = EditService()
            self.addCleanup(service.close)
            project = service.create_project("range-render", width=160, height=90)
            clip = Clip("effect", AssetReference("pattern", image),
                        r(0), r(2), r(0))
            clip.effects = [{"effectId": "cutvoke.fx.glitch",
                             "params": {"amount": 0.65}}]
            project.sequence.tracks = [Track("v1", "video", [clip])]
            renderer = RenderService()
            ranged = os.path.join(media_dir, "ranged.mp4")
            preview = os.path.join(media_dir, "preview.mp4")
            result = renderer.render(
                # Compare timing with the same encoder quality as playback.
                # Different CRFs can exceed the pixel tolerance on glitch noise.
                project, ranged, quality="preview", overwrite=True,
                output_start=0.5, output_duration=1.0)
            renderer.render_preview_window(project, preview, 0.5, 1.0)

            def frame(path: str, at: float) -> bytes:
                return subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-ss", str(at), "-i", path, "-frames:v", "1",
                    "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                ], check=True, capture_output=True).stdout

            self.assertAlmostEqual(result["duration"], 1.0, delta=0.08)
            ranged_frame = frame(ranged, 0.4)
            preview_frame = frame(preview, 0.4)
            self.assertEqual(len(ranged_frame), 160 * 90 * 3)
            difference = sum(abs(a - b) for a, b in zip(
                ranged_frame, preview_frame)) / len(ranged_frame)
            self.assertLess(difference, 5.0)


if __name__ == "__main__":
    unittest.main()
