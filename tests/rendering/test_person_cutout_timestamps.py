"""Real timestamp resampling, audio alignment and cancellation regressions."""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from cutvoke.rendering import person_cutout as cutout


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class PersonCutoutTimestampTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / "variable-rate.mp4"
        # 25 fps in the first second, 5 fps afterwards, with the last timestamp
        # retained. The blue picture and the audio pulse both start at 1.2 s.
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=red:s=64x48:r=25:d=2",
            "-f", "lavfi", "-i", "aevalsrc=if(between(t\\,1.2\\,1.3)\\,0.4*sin(2*PI*440*t)\\,0):s=48000:d=2",
            "-vf", "drawbox=color=blue:t=fill:enable='gte(t,1.2)',select='lt(t,1)+not(mod(n,5))+eq(n,49)'",
            "-fps_mode", "vfr", "-c:v", "libx264", "-bf", "0", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(cls.source),
        ], check=True, capture_output=True)
        cls.source_hash = hashlib.sha256(cls.source.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def _read_frames(self, source, media=None):
        reader = cutout._CfrFrameReader(
            source, media or cutout._probe_video(source), shutil.which("ffmpeg"), cv2, np)
        frames = []
        try:
            while True:
                ok, frame = reader.read()
                if not ok:
                    break
                frames.append(frame)
        finally:
            reader.release()
        self.assertIsNotNone(reader._process.poll())
        self.assertFalse(reader._reader.is_alive())
        return frames

    def test_vfr_and_equal_rate_metadata_use_actual_packet_timestamps(self):
        media = cutout._probe_video(self.source)
        self.assertNotEqual(media["video"]["r_frame_rate"], media["video"]["avg_frame_rate"])
        frames = self._read_frames(self.source, media)
        self.assertEqual(len(frames), 50)
        self.assertGreater(float(frames[25][:, :, 2].mean()), 200)  # red at 1.0 s
        self.assertGreater(float(frames[31][:, :, 0].mean()), 200)  # blue at 1.24 s
        # Resampling is unconditional: even a demuxer which reports equal rates
        # cannot bypass timestamp handling and shorten the low-rate interval.
        media["video"]["avg_frame_rate"] = media["video"]["r_frame_rate"]
        self.assertEqual(len(self._read_frames(self.source, media)), 50)

    def test_vfr_reordered_packets_cannot_extend_past_the_container_duration(self):
        source = self.root / "reordered.mp4"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(self.source), "-fps_mode", "vfr", "-c:v", "libx264",
            "-bf", "2", "-c:a", "copy", str(source),
        ], check=True, capture_output=True)
        media = cutout._probe_video(source)
        frames = self._read_frames(source, media)
        self.assertLessEqual(len(frames) / 25, float(media["duration"]) + 0.04)
        self.assertEqual(len(frames), 50)

    def _fake_mediapipe(self, timestamps):
        def segment(_image, timestamp):
            timestamps.append(timestamp)
            return SimpleNamespace(confidence_masks=[SimpleNamespace(
                numpy_view=lambda: np.ones((48, 64), dtype=np.float32))])
        segmenter = SimpleNamespace(segment_for_video=segment)
        fake = SimpleNamespace(
            Image=lambda **_kwargs: None, ImageFormat=SimpleNamespace(SRGB=1),
            tasks=SimpleNamespace(
                BaseOptions=lambda **_kwargs: None,
                vision=SimpleNamespace(
                    ImageSegmenterOptions=lambda **_kwargs: None,
                    RunningMode=SimpleNamespace(VIDEO=1),
                    ImageSegmenter=SimpleNamespace(create_from_options=lambda _options:
                                                  contextlib.nullcontext(segmenter)),
                ),
            ),
        )
        vision = fake.tasks.vision
        vision.InteractiveSegmenterLegacyOptions = lambda **_kwargs: None
        vision.InteractiveSegmenterLegacy = SimpleNamespace(
            create_from_options=lambda _options: contextlib.nullcontext(SimpleNamespace(
                segment=lambda _image, _roi: segment(None, -1))))
        class Roi:
            Format = SimpleNamespace(KEYPOINT=1)

            def __init__(self, **_kwargs):
                pass
        vision.InteractiveSegmenterLegacyRegionOfInterest = Roi
        return fake

    def test_semantic_full_pipeline_keeps_duration_pixels_audio_and_source(self):
        timestamps = []
        output = self.root / "semantic.mov"
        model = self.root / "test-model"
        model.touch()
        with patch.dict("sys.modules", {"mediapipe": self._fake_mediapipe(timestamps)}):
            result = cutout.generate_person_cutout(self.source, output, model_file=model)
        self.assertEqual(timestamps, list(range(0, 2000, 40)))
        self._assert_result(output, result)

    def _assert_result(self, output, result):
        self.assertEqual(result["frameCount"], 50)
        self.assertEqual(result["durationSeconds"], 2)
        self.assertTrue(result["audioPreserved"])
        self.assertTrue(result["sourceTimeNormalized"])
        self.assertTrue(result["alpha"])
        frames = self._read_frames(output)
        self.assertGreater(float(frames[25][:, :, 2].mean()), 200)
        self.assertGreater(float(frames[31][:, :, 0].mean()), 200)
        audio = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(output),
            "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1",
        ], check=True, capture_output=True).stdout
        samples = np.frombuffer(audio, dtype=np.float32)
        self.assertGreaterEqual(len(samples), 96000)
        self.assertLess(float(np.sqrt(np.mean(samples[:48000] ** 2))), 0.001)
        self.assertGreater(float(np.sqrt(np.mean(samples[58000:62000] ** 2))), 0.1)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.source_hash)
        self.assertEqual(list(self.root.glob(".*.mov")), [])

    def test_semantic_and_magic_touch_selection_start_at_source_time(self):
        model = self.root / "test-model"
        model.touch()
        for mode in ("semantic", "magic_touch"):
            with self.subTest(mode=mode):
                modules = {
                    "mediapipe": self._fake_mediapipe([]),
                    "mediapipe.tasks.python.components.containers.keypoint": SimpleNamespace(
                        NormalizedKeypoint=lambda **_kwargs: None),
                }
                with patch.dict("sys.modules", modules), \
                     patch.object(cutout, "_interactive_model_is_valid", return_value=True), \
                     patch.object(cutout, "_track_prompt_point", return_value=(0.5, 0.5)):
                    output = self.root / f"selected-{mode}.mov"
                    result = cutout.generate_person_cutout(
                        self.source, output, model_file=model, tracking_mode=mode,
                        selection_point=(0.5, 0.5), selection_at_seconds=1.2)
                self.assertEqual(result["selectedFrames"], 20)
                self.assertEqual(result["selectionAtSeconds"], 1.2)
                self._assert_result(output, result)

    def test_sam2_extraction_and_encoding_share_source_time_and_prompt_frames(self):
        output = self.root / "sam2.mov"
        seen_prompts = []
        popen = subprocess.Popen

        def start_process(command, **kwargs):
            if any(Path(str(part)).name == "sam2_worker.py" for part in command):
                self.assertEqual(command[1], "-I")
                self.assertTrue(Path(command[2]).is_file())
                # Replace only inference. The production extraction, worker
                # supervision, mask reads and final encoder remain real.
                frames_dir = Path(command[command.index("--frames-dir") + 1])
                seen_prompts.extend(json.loads(command[command.index("--prompt-sets-json") + 1]))
                frames = sorted(frames_dir.glob("*.jpg"))
                self.assertEqual(len(frames), 50)
                self.assertGreater(float(cv2.imread(str(frames[31]))[:, :, 0].mean()), 200)
                masks_dir = Path(command[command.index("--masks-dir") + 1])
                for frame in frames:
                    cv2.imwrite(str(masks_dir / (frame.stem + ".png")), np.full((48, 64), 255, np.uint8))
                command = [sys.executable, "-c", "pass"]
            return popen(command, **kwargs)

        with patch.object(cutout, "availability", return_value={"sam2Ready": True}), \
             patch.object(cutout, "sam2_runtime_is_ready", return_value=True), \
             patch.object(cutout, "_file_has_sha256", return_value=True), \
             patch.object(cutout.subprocess, "Popen", side_effect=start_process):
            result = cutout.generate_person_cutout(
                self.source, output, tracking_mode="sam2",
                selection_prompts=[{"atSeconds": 1.2, "points": [(0.5, 0.5, 1)]}])
        self.assertEqual(seen_prompts[0]["frame"], 30)
        self._assert_result(output, result)

    def test_cancelled_reader_terminates_process_and_no_cfr_cache_is_created(self):
        cancelled = threading.Event()
        reader = cutout._CfrFrameReader(
            self.source, cutout._probe_video(self.source), shutil.which("ffmpeg"),
            cv2, np, cancelled.is_set)
        self.assertTrue(reader.read()[0])
        cancelled.set()
        started = time.monotonic()
        try:
            with self.assertRaises(cutout.PersonCutoutCancelled):
                reader.read()
        finally:
            reader.release()
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNotNone(reader._process.poll())
        self.assertFalse(reader._reader.is_alive())
        self.assertLessEqual(reader._frames.qsize(), 2)

    def test_cancel_while_decoder_stdout_is_blocked_terminates_child(self):
        cancelled = threading.Event()
        popen = subprocess.Popen
        media = cutout._probe_video(self.source)
        with patch.object(cutout.subprocess, "Popen", side_effect=lambda _command, **kwargs:
                          popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)):
            reader = cutout._CfrFrameReader(
                self.source, media, shutil.which("ffmpeg"),
                cv2, np, cancelled.is_set)
        timer = threading.Timer(0.1, cancelled.set)
        started = time.monotonic()
        timer.start()
        try:
            with self.assertRaises(cutout.PersonCutoutCancelled):
                reader.read()
        finally:
            reader.release()
            timer.cancel()
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNotNone(reader._process.poll())
        self.assertFalse(reader._reader.is_alive())

    def test_cancelled_generation_removes_partial_video_and_mux(self):
        model = self.root / "test-model"
        model.touch()
        for stage in ("segmentation", "mux"):
            with self.subTest(stage=stage):
                output = self.root / f"cancelled-{stage}.mov"
                cancelled = threading.Event()

                def progress(_phase, percent):
                    if (stage == "segmentation" and percent > 5) or percent == 94:
                        cancelled.set()

                with patch.dict("sys.modules", {"mediapipe": self._fake_mediapipe([])}):
                    with self.assertRaises(cutout.PersonCutoutCancelled):
                        cutout.generate_person_cutout(
                            self.source, output, model_file=model,
                            progress=progress, cancelled=cancelled.is_set)
                self.assertFalse(output.exists())
                self.assertEqual(list(self.root.glob(".*.mov")), [])
                self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.source_hash)


if __name__ == "__main__":
    unittest.main()
