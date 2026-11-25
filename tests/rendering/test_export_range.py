"""Timeline range exports retain full-project timing and flow through HTTP jobs."""

from __future__ import annotations

import contextlib
from fractions import Fraction
import http.client
from http.server import ThreadingHTTPServer
import json
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

from cutvoke.interfaces.httpapi import HttpApi, build_handler
from cutvoke.domain.model import (CURRENT_PROJECT_SCHEMA, AssetReference, Caption, Clip,
                                  Project, Sequence, Track)
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


    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg required")
    def test_off_grid_http_exports_keep_every_real_picture_and_exact_audio_clock(self) -> None:
        fps = Fraction(30000, 1001)
        width, height = 160, 96

        def ffmpeg(*args, stdin=None):
            return subprocess.run(["ffmpeg", "-nostdin", "-v", "error", *map(str, args)],
                                  input=stdin, capture_output=True, timeout=30, check=True).stdout

        def encoded_id(index):
            row = b"".join(bytes([224 if index & (1 << bit) else 32]) * 16
                           for bit in range(9)) + bytes([128]) * 16
            return row * height

        def pictures(path, fmt="gray"):
            bytes_per_frame = width * height * (3 if fmt == "rgb24" else 1)
            raw = ffmpeg("-i", path, "-map", "0:v:0", "-fps_mode", "passthrough",
                         "-threads", "1", "-f", "rawvideo", "-pix_fmt", fmt, "pipe:1")
            self.assertTrue(raw and len(raw) % bytes_per_frame == 0)
            return [raw[i:i + bytes_per_frame] for i in range(0, len(raw), bytes_per_frame)]

        def picture_id(raw):
            levels = [raw[14 * width + bit * 16 + 8] for bit in range(9)]
            self.assertTrue(all(level < 64 or level > 192 for level in levels), levels)
            return sum((level > 128) << bit for bit, level in enumerate(levels))

        def frame_time(index):
            seconds = Fraction(index) / fps
            return Rational.of(seconds.numerator, seconds.denominator)

        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "numbered.mp4")
            ffmpeg("-f", "rawvideo", "-pixel_format", "gray", "-video_size", f"{width}x{height}",
                   "-framerate", "30000/1001", "-i", "pipe:0", "-an", "-c:v", "libx264",
                   "-pix_fmt", "yuv420p", "-threads", "1", "-g", "30", "-bf", "2",
                   "-sc_threshold", "0", source, stdin=b"".join(map(encoded_id, range(374))))
            self.assertEqual(list(map(picture_id, pictures(source))), list(range(374)))
            audio = os.path.join(directory, "chirp.wav")
            ffmpeg("-f", "lavfi", "-i",
                   "aevalsrc=0.25*sin(2*PI*(211*t+18*t*t)):s=48000:d=12.012",
                   "-c:a", "pcm_s16le", audio)

            service = EditService()
            renderer = RenderService(gpu_effects=False)
            renderer.gpu_acceleration = False
            api = HttpApi(service, render=renderer, media_dir=os.path.join(directory, "media"))
            server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(api))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()

            def request(method, route, body=None):
                with contextlib.closing(http.client.HTTPConnection(
                        "127.0.0.1", server.server_port, timeout=10)) as conn:
                    conn.request(method, route, json.dumps(body) if body is not None else None,
                                 headers={"Content-Type": "application/json"} if body is not None else {})
                    response = conn.getresponse()
                    return response.status, json.loads(response.read())

            def export(pid, tag, start_tick, end_tick, strategy=None):
                start, end = (float(Fraction(tick) / fps) for tick in (start_tick, end_tick))
                route = f"/api/v1/projects/{pid}/exports"
                path = os.path.join(directory, tag + ".mp4")
                status, reply = request("POST", route, {"outPath": path, "quality": "low",
                                                       "range": {"start": start, "end": end}})
                self.assertEqual(status, 202, reply)
                deadline = time.monotonic() + 40
                job = {}
                while time.monotonic() < deadline:
                    _, listing = request("GET", route)
                    job = next((item for item in listing["jobs"] if item["jobId"] == reply["jobId"]), {})
                    if job.get("status") in {"succeeded", "failed", "cancelled", "interrupted"}:
                        break
                    time.sleep(.05)
                self.assertEqual(job.get("status"), "succeeded", job.get("error"))
                self.assertEqual(job["request"]["range"], {"start": start, "end": end})
                self.assertEqual(job["result"].get("renderStrategy"), strategy)
                return path, job["result"], start, end

            try:
                for kind, cuts in (("direct", 1), ("source-eof", 1), ("dense", 30),
                                   ("dense-audio", 30), ("decorated", 1)):
                    project = Project(schema_version=CURRENT_PROJECT_SCHEMA, name=kind,
                        project_id=kind, revision="0",
                        sequence=Sequence(kind + "-seq", width, height,
                                          Rational.of(fps.numerator, fps.denominator)))
                    step = 360 // cuts
                    clips = [
                        Clip(f"shot-{i}", AssetReference("numbered", source),
                             frame_time(i), frame_time(i + step),
                             frame_time(i + (14 if kind == "source-eof" else 0)))
                        for i in range(0, 360, step)
                    ]
                    project.sequence.tracks = [Track("picture", "video", clips)]
                    if kind in {"dense-audio", "decorated"}:
                        project.sequence.tracks.append(Track("audio", "audio", [
                            Clip("chirp", AssetReference("chirp", audio),
                                 r(0), frame_time(360), r(0))]))
                    if kind == "decorated":
                        clips[0].effects = [{"effectId": "cutvoke.fx.invert", "params": {},
                                             "range": {"start": frame_time(86).to_json(),
                                                       "end": frame_time(89).to_json()}}]
                        project.sequence.captions = [
                            Caption("caption", "END", frame_time("89.5"), frame_time(95),
                                    color="#ffff00", fontSize=21, y=.75)]
                    service.import_project(project, project_id=kind)

                for pid, first, last in (("direct", "3.75", "90.25"),
                                         ("dense", "3.25", "274.75"),
                                         ("dense", "3.75", "274.25"),
                                         ("source-eof", "358.85", "359.25"),
                                         ("dense-audio", "3.65", "243.75"),
                                         ("dense", "3.65", "244.1")):
                    with self.subTest(pid=pid, first=first, last=last):
                        dense = pid.startswith("dense")
                        source_offset = 14 if pid == "source-eof" else 0
                        path, result, start, end = export(pid, f"{pid}-{first}-{last}", first, last,
                                                         "lossless-windows" if dense else None)
                        self.assertEqual(list(map(picture_id, pictures(path))),
                                         list(range(math.ceil(Fraction(first)) + source_offset,
                                                    math.ceil(Fraction(last)) + source_offset)))
                        if pid == "dense-audio":
                            audit = result["audioVerification"]
                            self.assertEqual(audit["status"], "passed")
                            self.assertTrue(audit["windows"])
                            self.assertAlmostEqual(audit["checkedSeconds"],
                                (round(end * 48000) - round(start * 48000)) / 48000, delta=2 / 8000)
                            self.assertTrue(all(window["estimatedOffsetSeconds"] == 0 and
                                                window["zeroOffsetCorrelation"] > .99
                                                for window in audit["windows"]))
                        if dense:
                            if last == "243.75":
                                self.assertEqual(result["windowCount"], 1)
                            else:
                                self.assertEqual(result["windowCount"], 2)
                                self.assertEqual(result["videoVerification"]["status"], "passed")

                decorated, result, start, end = export("decorated", "decorated-trim", "3.75", "90.25")
                ids = list(map(picture_id, pictures(decorated)))
                self.assertEqual(len(ids), 87)
                self.assertEqual((ids[0], ids[87 - 4], ids[-1]), (4, 511 ^ 87, 90))
                rgb = pictures(decorated, "rgb24")
                def yellow(frame):
                    pixels = rgb[frame - 4]
                    return sum(red > blue + 35 and green > blue + 35
                               for red, green, blue in zip(pixels[::3], pixels[1::3], pixels[2::3]))
                self.assertLess(yellow(88), 4)
                self.assertGreater(yellow(90), 15)
                self.assertAlmostEqual(result["audioVerification"]["checkedSeconds"],
                    (round(end * 48000) - round(start * 48000)) / 48000, delta=2 / 8000)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)
                api.close()
                service.close()

if __name__ == "__main__":
    unittest.main()
