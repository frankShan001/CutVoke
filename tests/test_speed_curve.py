"""A source-relative speed curve must survive editing and render in both paths."""

from __future__ import annotations

import copy
import shutil
import json
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from cutvoke.core.model import Clip
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.render import RenderError, RenderService, _has_minterpolate
from cutvoke.core.service import EditError, EditService


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class SpeedCurveTests(unittest.TestCase):
    def test_motion_interpolation_is_saved_and_shared_by_preview_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "moving-source.mp4"
            raw_source = root / "moving-source.rgb"
            with raw_source.open("wb") as stream:
                for index in range(12):
                    frame = bytearray(96 * 64 * 3)
                    x = index * 6
                    for y in range(8, 56):
                        start = (y * 96 + x) * 3
                        end = (y * 96 + x + 24) * 3
                        frame[start:end] = b"\xff" * (end - start)
                    stream.write(frame)
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", "96x64",
                "-framerate", "12", "-i", str(raw_source),
                "-c:v", "mpeg4", "-q:v", "2", str(source),
            ], check=True, capture_output=True)
            service = EditService()
            project = service.create_project("motion-interpolation", width=96, height=64)

            def apply(kind: str, payload: dict, index: int):
                current = service.get_project(project.project_id)
                return service.execute(Command(
                    type=kind, payload=payload, command_id=f"motion-{index}",
                    project_id=project.project_id, expected_revision=current.revision,
                    actor=Actor("human", "motion-test"),
                ))

            apply("track.add", {"trackId": "v1", "kind": "video"}, 1)
            apply("clip.insert", {"trackId": "v1", "clipId": "slow", "sourcePath": str(source),
                                  "timelineStart": {"num": 0, "den": 1},
                                  "timelineEnd": {"num": 1, "den": 1}}, 2)
            with self.assertRaisesRegex(EditError, "低于 1x"):
                apply("clip.speed", {"clipId": "slow", "frameInterpolation": "motion"},
                      "reject-normal-speed")
            apply("clip.speed", {"clipId": "slow", "curve": {"points": [
                {"at": 0, "speed": 0.5}, {"at": 1, "speed": 0.5},
            ]}}, 3)
            apply("effect.add", {"clipId": "slow", "effectId": "cutvoke.transform",
                                  "params": {"scale": 1.1}}, 4)
            apply("effect.setAnimation", {"clipId": "slow", "effectId": "cutvoke.anim.fadeIn",
                                           "params": {"duration": 0.5}}, 5)
            apply("clip.speed", {"clipId": "slow", "frameInterpolation": "motion"}, 6)
            edited = service.get_project(project.project_id)
            clip = edited.sequence.tracks[0].clips[0]
            self.assertEqual(clip.frame_interpolation, "motion")
            self.assertEqual(Clip.from_dict(clip.to_dict()).frame_interpolation, "motion")
            legacy_clip = clip.to_dict()
            legacy_clip.pop("frameInterpolation")
            self.assertEqual(Clip.from_dict(legacy_clip).frame_interpolation, "none")
            self.assertAlmostEqual(float(clip.duration.to_fraction()), 2.0)

            renderer = RenderService()
            with mock.patch("cutvoke.core.render._has_minterpolate", return_value=False):
                with self.assertRaisesRegex(RenderError, "does not include minterpolate"):
                    renderer.render(edited, str(root / "unsupported.mp4"))

            if not _has_minterpolate(renderer.ffmpeg):
                self.skipTest("installed FFmpeg has no minterpolate; missing-filter handling passed")

            baseline = copy.deepcopy(edited)
            baseline.sequence.tracks[0].clips[0].frame_interpolation = "none"
            baseline_output = root / "baseline.mp4"
            motion_output = root / "motion.mp4"
            baseline_preview = root / "baseline-preview.mp4"
            motion_preview = root / "motion-preview.mp4"
            renderer.render(baseline, str(baseline_output))
            renderer.render(edited, str(motion_output))
            renderer.render_preview_window(baseline, str(baseline_preview), 0, 2)
            renderer.render_preview_window(edited, str(motion_preview), 0, 2)

            def meaningful_frame_changes(path: Path) -> int:
                decoded = subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
                    "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
                ], check=True, capture_output=True).stdout
                frame_bytes = 96 * 64 * 3
                frames = [decoded[i:i + frame_bytes]
                          for i in range(0, len(decoded) - frame_bytes + 1, frame_bytes)]
                self.assertEqual(len(decoded) % frame_bytes, 0)
                changes = 0
                for previous, current in zip(frames, frames[1:]):
                    mean_difference = sum(abs(a - b) for a, b in zip(previous, current)) / frame_bytes
                    changes += mean_difference > 2.0
                return changes

            self.assertGreater(meaningful_frame_changes(motion_output),
                               meaningful_frame_changes(baseline_output) + 8)
            self.assertGreater(meaningful_frame_changes(motion_preview),
                               meaningful_frame_changes(baseline_preview) + 8)
            apply("history.undo", {}, 7)
            self.assertEqual(service.get_project(project.project_id).sequence.tracks[0]
                             .clips[0].frame_interpolation, "none")
            apply("history.redo", {}, 8)
            self.assertEqual(service.get_project(project.project_id).sequence.tracks[0]
                             .clips[0].frame_interpolation, "motion")
            apply("clip.speed", {"clipId": "slow", "speed": 1}, 9)
            self.assertEqual(service.get_project(project.project_id).sequence.tracks[0]
                             .clips[0].frame_interpolation, "none")

    def test_curve_audio_video_preview_save_and_undo(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=4",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
                "-map", "0:v", "-map", "1:a", "-c:v", "mpeg4", "-q:v", "3",
                "-c:a", "aac", str(source),
            ], check=True, capture_output=True)
            service = EditService()
            project = service.create_project("curve", width=160, height=90)

            def apply(kind: str, payload: dict, index: int):
                current = service.get_project(project.project_id)
                return service.execute(Command(
                    type=kind, payload=payload, command_id=f"curve-{index}",
                    project_id=project.project_id, expected_revision=current.revision,
                    actor=Actor("human", "curve-test"),
                ))

            apply("track.add", {"trackId": "v1", "kind": "video"}, 1)
            apply("clip.insert", {"trackId": "v1", "clipId": "c1",
                                  "sourcePath": str(source),
                                  "timelineStart": {"num": 0, "den": 1},
                                  "timelineEnd": {"num": 4, "den": 1}}, 2)
            apply("clip.speed", {"clipId": "c1", "curve": {"points": [
                {"at": 0, "speed": 1}, {"at": 0.5, "speed": 2},
                {"at": 1, "speed": 1},
            ]}}, 3)
            edited = service.get_project(project.project_id)
            clip = edited.sequence.tracks[0].clips[0]
            self.assertIsNotNone(clip.speed_curve)
            self.assertEqual(Clip.from_dict(clip.to_dict()).speed_curve, clip.speed_curve)
            self.assertAlmostEqual(float(clip.duration.to_fraction()), 2.772589, delta=0.001)
            renderer = RenderService()
            output = root / "export.mp4"
            preview = root / "preview.mp4"
            result = renderer.render(edited, str(output))
            renderer.render_preview_window(edited, str(preview), 0, 2.7)
            self.assertTrue(result["has_audio"])
            self.assertAlmostEqual(result["duration"], 2.77, delta=0.08)
            self.assertTrue(output.is_file() and preview.is_file())
            for media in (output, preview):
                probe = subprocess.run([
                    "ffprobe", "-v", "error", "-show_entries",
                    "stream=codec_type,duration", "-of", "json", str(media),
                ], check=True, capture_output=True, text=True)
                durations = {stream["codec_type"]: float(stream["duration"])
                             for stream in json.loads(probe.stdout)["streams"]}
                self.assertIn("audio", durations)
                self.assertAlmostEqual(durations["audio"], durations["video"], delta=0.07)
            apply("clip.split", {"clipId": "c1", "at": {"num": 1, "den": 1}}, 4)
            pieces = service.get_project(project.project_id).sequence.tracks[0].clips
            self.assertEqual(len(pieces), 2)
            self.assertTrue(all(piece.speed_curve is not None for piece in pieces))
            self.assertAlmostEqual(
                sum(float(piece.consumed_source_duration.to_fraction()) for piece in pieces),
                4.0, delta=0.002)
            split_render = renderer.render(service.get_project(project.project_id),
                                           str(root / "split.mp4"))
            self.assertAlmostEqual(split_render["duration"], 2.77, delta=0.08)
            apply("history.undo", {}, 5)
            self.assertEqual(len(service.get_project(project.project_id).sequence.tracks[0].clips), 1)
            apply("history.undo", {}, 6)
            restored = service.get_project(project.project_id).sequence.tracks[0].clips[0]
            self.assertIsNone(restored.speed_curve)
            self.assertEqual(restored.duration.num, 4)
            apply("history.redo", {}, 7)
            apply("clip.trim", {"clipId": "c1",
                                "timelineStart": {"num": 1, "den": 4},
                                "timelineEnd": {"num": 5, "den": 2}}, 8)
            trimmed = service.get_project(project.project_id).sequence.tracks[0].clips[0]
            self.assertIsNotNone(trimmed.speed_curve)
            self.assertGreater(float(trimmed.source_start.to_fraction()), 0)
            self.assertLess(float(trimmed.consumed_source_duration.to_fraction()), 4)
            trimmed_output = root / "trimmed.mp4"
            renderer.render(service.get_project(project.project_id), str(trimmed_output))
            self.assertTrue(trimmed_output.is_file())


if __name__ == "__main__":
    unittest.main()
