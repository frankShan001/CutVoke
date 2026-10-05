"""Clip-local visual effect intervals stay editable and render consistently."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image

from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.keyframes import Keyframe, evaluate as evaluate_keyframes
from cutvoke.core.protocol import Actor, Command, ErrorCode
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditError, EditService


def r(value: int, denominator: int = 1) -> Rational:
    return Rational.of(value, denominator)


def json_time(value: int, denominator: int = 1) -> dict[str, str]:
    return r(value, denominator).to_json()


class EffectTemporalRangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = EditService()
        self.project = self.service.create_project("effect-range", width=160, height=90)
        self.revision = self.project.revision
        self._apply("track.add", {"trackId": "v1", "kind": "video"})
        self._apply("clip.insert", {
            "clipId": "clip", "trackId": "v1", "sourcePath": "poster.png",
            "timelineStart": json_time(0), "timelineEnd": json_time(3),
        })

    def _apply(self, command_type: str, payload: dict):
        result = self.service.execute(Command(
            type=command_type,
            payload=payload,
            command_id=f"effect-range-{self.revision}-{command_type}",
            project_id=self.project.project_id,
            expected_revision=self.revision,
            actor=Actor("human", "effect-range-test"),
        ))
        self.revision = result.revision
        self.project = self.service.get_project(self.project.project_id)
        return result

    def _add_ranged_grayscale(self) -> None:
        self._apply("effect.add", {"clipId": "clip", "effectId": "cutvoke.fx.grayscale"})
        self._apply("effect.update", {
            "clipId": "clip", "effectId": "cutvoke.fx.grayscale", "effectIndex": 0,
            "range": {"start": json_time(1, 2), "end": json_time(5, 2)},
        })

    def test_effect_range_is_validated_and_rebased_on_trim_and_split(self) -> None:
        self._add_ranged_grayscale()
        original = self.project.sequence.tracks[0].clips[0]
        self.assertEqual(original.effects[0]["range"], {
            "start": json_time(1, 2), "end": json_time(5, 2),
        })

        with self.assertRaises(EditError) as raised:
            self._apply("effect.update", {
                "clipId": "clip", "effectId": "cutvoke.fx.grayscale",
                "range": {"start": json_time(2), "end": json_time(4)},
            })
        self.assertEqual(raised.exception.code, ErrorCode.INVALID_ARGUMENT)

        self._apply("clip.split", {"clipId": "clip", "at": json_time(3, 2)})
        clips = {clip.id: clip for clip in self.project.sequence.tracks[0].clips}
        self.assertEqual(clips["clip"].effects[0]["range"], {
            "start": json_time(1, 2), "end": json_time(3, 2),
        })
        self.assertEqual(clips["clip_r"].effects[0]["range"], {
            "start": json_time(0), "end": json_time(1),
        })

    def test_left_trim_rebases_effect_interval(self) -> None:
        self._add_ranged_grayscale()
        self._apply("clip.trim", {
            "clipId": "clip", "timelineStart": json_time(1),
            "timelineEnd": json_time(3), "sourceStart": json_time(1),
        })
        clip = self.project.sequence.tracks[0].clips[0]
        self.assertEqual(clip.effects[0]["range"], {
            "start": json_time(0), "end": json_time(3, 2),
        })

    def test_animation_policy_combines_or_replaces_manual_opacity_keyframes(self) -> None:
        for time, value in ((0, 0.2), (3, 1.0)):
            self._apply("clip.keyframe", {
                "clipId": "clip", "param": "opacity", "time": json_time(time), "value": value,
            })
        self._apply("effect.setAnimation", {
            "clipId": "clip", "effectId": "cutvoke.anim.fadeIn", "slot": "入场",
            "params": {"duration": 1, "easing": "linear"},
            "keyframePolicy": "combine",
        })
        clip = self.project.sequence.tracks[0].clips[0]
        self.assertEqual(len(clip.keyframes["opacity"]), 2)
        self.assertIn("cutvoke.anim.fadeIn", [effect["effectId"] for effect in clip.effects])

        self._apply("effect.setAnimation", {
            "clipId": "clip", "effectId": "cutvoke.anim.fadeOut", "slot": "出场",
            "params": {"duration": 1, "easing": "linear"},
            "keyframePolicy": "replace",
        })
        clip = self.project.sequence.tracks[0].clips[0]
        self.assertNotIn("opacity", clip.keyframes)
        self.assertEqual(
            {effect["effectId"] for effect in clip.effects},
            {"cutvoke.anim.fadeIn", "cutvoke.anim.fadeOut"},
        )

    def test_keyframe_parameters_and_easing_are_applied(self) -> None:
        for param, value in (("x", 24), ("y", -12), ("scale", 1.5), ("rotation", 45)):
            self._apply("clip.keyframe", {
                "clipId": "clip", "param": param, "time": json_time(1), "value": value,
            })
        clip = self.project.sequence.tracks[0].clips[0]
        self.assertEqual(
            {param: frames[0].value for param, frames in clip.keyframes.items()},
            {"x": 24.0, "y": -12.0, "scale": 1.5, "rotation": 45.0},
        )
        with self.assertRaises(EditError):
            self._apply("clip.keyframe", {
                "clipId": "clip", "param": "scale", "time": json_time(2), "value": 0,
            })
        with self.assertRaises(EditError):
            self._apply("clip.keyframe", {
                "clipId": "clip", "param": "opacity", "time": json_time(2), "value": 2,
            })
        with self.assertRaises(EditError):
            self._apply("clip.keyframe", {
                "clipId": "clip", "param": "colorTemperature", "time": json_time(2), "value": 2,
            })

        ease_in = [Keyframe("a", r(0), 0, "ease-in"), Keyframe("b", r(2), 100)]
        ease_out = [Keyframe("a", r(0), 0, "ease-out"), Keyframe("b", r(2), 100)]
        self.assertAlmostEqual(evaluate_keyframes(ease_in, r(1)), 25)
        self.assertAlmostEqual(evaluate_keyframes(ease_out, r(1)), 75)

    def test_keyframe_batch_is_atomic_and_same_time_add_updates_existing_key(self) -> None:
        self._apply("clip.keyframe", {
            "clipId": "clip", "action": "batch", "keyframes": [
                {"param": "x", "time": json_time(1), "value": 24},
                {"param": "y", "time": json_time(1), "value": -12},
            ],
        })
        original = self.project.sequence.tracks[0].clips[0]
        original_x_id = original.keyframes["x"][0].id
        self.assertEqual(len(original.keyframes["x"]), 1)
        self.assertEqual(len(original.keyframes["y"]), 1)

        self._apply("clip.keyframe", {
            "clipId": "clip", "param": "x", "time": json_time(2, 2),
            "value": 36, "interpolation": "ease-out",
        })
        updated = self.project.sequence.tracks[0].clips[0]
        self.assertEqual(len(updated.keyframes["x"]), 1)
        self.assertEqual(updated.keyframes["x"][0].id, original_x_id)
        self.assertEqual(updated.keyframes["x"][0].value, 36)
        self.assertEqual(updated.keyframes["x"][0].interpolation, "ease-out")

        with self.assertRaises(EditError):
            self._apply("clip.keyframe", {
                "clipId": "clip", "action": "batch", "keyframes": [
                    {"param": "x", "time": json_time(2), "value": 48},
                    {"param": "scale", "time": json_time(2), "value": 0},
                ],
            })
        self.project = self.service.get_project(self.project.project_id)
        after_rejected_batch = self.project.sequence.tracks[0].clips[0]
        self.assertEqual(len(after_rejected_batch.keyframes["x"]), 1)
        self.assertEqual(after_rejected_batch.keyframes["x"][0].value, 36)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_manual_opacity_keyframes_render_alongside_animation(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            image = os.path.join(media_dir, "pattern.png")
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=15",
                "-frames:v", "1", image,
            ], check=True, capture_output=True)

            def make_project(with_keyframes: bool):
                project = EditService().create_project(
                    "animated-opacity", width=160, height=90, fps=Rational.of(15))
                clip = Clip("clip", AssetReference("pattern", image), r(0), r(3), r(0))
                clip.effects = [{
                    "effectId": "cutvoke.anim.fadeIn",
                    "params": {"duration": 1.0, "easing": "linear"},
                }]
                if with_keyframes:
                    clip.keyframes["opacity"] = [
                        Keyframe("low", r(0), 0.2, "linear"),
                        Keyframe("high", r(3), 0.8, "linear"),
                    ]
                project.sequence.tracks = [Track("v1", "video", [clip])]
                return project

            def frame(path: str, at: float) -> bytes:
                return subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-ss", str(at), "-i", path, "-frames:v", "1",
                    "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                ], check=True, capture_output=True).stdout

            renderer = RenderService()
            plain = os.path.join(media_dir, "animation-only.mp4")
            combined = os.path.join(media_dir, "animation-with-keyframes.mp4")
            renderer.render(make_project(False), plain, quality="low", overwrite=True)
            renderer.render(make_project(True), combined, quality="low", overwrite=True)
            a, b = frame(plain, 2.0), frame(combined, 2.0)
            difference = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
            self.assertGreater(difference, 8.0)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_transform_keyframes_change_position_scale_and_rotation_in_render(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            image = os.path.join(media_dir, "pattern.png")
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=15",
                "-frames:v", "1", image,
            ], check=True, capture_output=True)

            def make_project(param: str, start: float, end: float):
                project = EditService().create_project(
                    f"transform-{param}", width=160, height=90, fps=Rational.of(15))
                clip = Clip("clip", AssetReference("pattern", image), r(0), r(3), r(0))
                clip.keyframes[param] = [
                    Keyframe("start", r(0), start), Keyframe("end", r(3), end),
                ]
                project.sequence.tracks = [Track("v1", "video", [clip])]
                return project

            def frame(path: str, at: float) -> bytes:
                return subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-ss", str(at), "-i", path, "-frames:v", "1",
                    "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                ], check=True, capture_output=True).stdout

            renderer = RenderService()
            cases = {
                "x": (0, 48),
                "y": (0, 24),
                "scale": (0.5, 1.5),
                "rotation": (-30, 30),
            }
            for param, (start, end) in cases.items():
                output = os.path.join(media_dir, f"{param}.mp4")
                renderer.render(make_project(param, start, end), output,
                                quality="low", overwrite=True)
                difference = sum(abs(a - b) for a, b in zip(
                    frame(output, 0.1), frame(output, 2.9))) / (160 * 90 * 3)
                self.assertGreater(difference, 2.0, f"{param} keyframes must affect rendered frames")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_range_render_matches_preview_and_leaves_outside_frames_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            image = os.path.join(media_dir, "pattern.png")
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=15",
                "-frames:v", "1", image,
            ], check=True, capture_output=True)

            def make_project(with_range: bool):
                service = EditService()
                project = service.create_project(
                    "ranged-render", width=160, height=90, fps=Rational.of(15))
                clip = Clip("clip", AssetReference("pattern", image), r(0), r(3), r(0))
                clip.effects = [{
                    "effectId": "cutvoke.anim.fadeIn",
                    "params": {"duration": 1.0, "easing": "linear"},
                }]
                if with_range:
                    clip.effects.append({
                        "effectId": "cutvoke.fx.grayscale",
                        "version": "1.0.0",
                        "params": {},
                        "range": {"start": json_time(1), "end": json_time(2)},
                    })
                project.sequence.tracks = [Track("v1", "video", [clip])]
                return project

            def frame(path: str, at: float) -> bytes:
                return subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-ss", str(at), "-i", path, "-frames:v", "1",
                    "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                ], check=True, capture_output=True).stdout

            renderer = RenderService()
            baseline = os.path.join(media_dir, "baseline.mp4")
            rendered = os.path.join(media_dir, "ranged.mp4")
            preview = os.path.join(media_dir, "preview.mp4")
            renderer.render(make_project(False), baseline, quality="low", overwrite=True)
            ranged_project = make_project(True)
            renderer.render(ranged_project, rendered, quality="low", overwrite=True)
            renderer.render_preview_window(ranged_project, preview, 0.5, 2.0)

            before, active, after = (frame(rendered, t) for t in (0.5, 1.5, 2.5))
            reference_before, reference_active, reference_after = (
                frame(baseline, t) for t in (0.5, 1.5, 2.5))
            preview_active = frame(preview, 1.0)

            def mean_difference(left: bytes, right: bytes) -> float:
                return sum(abs(a - b) for a, b in zip(left, right)) / len(left)

            self.assertGreater(len(active), 160 * 90 * 3 - 1)
            self.assertLess(mean_difference(before, reference_before), 5.0)
            self.assertGreater(mean_difference(active, reference_active), 8.0)
            self.assertLess(mean_difference(after, reference_after), 5.0)
            self.assertLess(mean_difference(active, preview_active), 5.0)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_crop_range_changes_only_its_interval_and_matches_preview_export(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            image = os.path.join(media_dir, "quadrants.png")
            artwork = Image.new("RGB", (160, 90))
            for y in range(90):
                for x in range(160):
                    if y < 45:
                        color = (232, 24, 24) if x < 80 else (20, 220, 28)
                    else:
                        color = (24, 32, 232) if x < 80 else (232, 220, 20)
                    artwork.putpixel((x, y), color)
            artwork.save(image)

            def make_project(crop_mode: str):
                project = EditService().create_project(
                    "crop-range", width=160, height=90, fps=Rational.of(15))
                clip = Clip("clip", AssetReference("quadrants", image), r(0), r(3), r(0))
                if crop_mode != "none":
                    effect = {
                        "effectId": "cutvoke.fx.crop",
                        "version": "1.0.0",
                        "params": {"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0},
                    }
                    if crop_mode == "range":
                        effect["range"] = {"start": json_time(1), "end": json_time(2)}
                    clip.effects = [effect]
                project.sequence.tracks = [Track("v1", "video", [clip])]
                return project

            def frame(path: str, at: float) -> bytes:
                return subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-ss", str(at), "-i", path, "-frames:v", "1",
                    "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                ], check=True, capture_output=True).stdout

            renderer = RenderService()
            baseline = os.path.join(media_dir, "baseline.mp4")
            full_crop = os.path.join(media_dir, "full-crop.mp4")
            ranged = os.path.join(media_dir, "crop-range.mp4")
            preview = os.path.join(media_dir, "crop-preview.mp4")
            renderer.render(make_project("none"), baseline, quality="low", overwrite=True)
            renderer.render(make_project("full"), full_crop, quality="low", overwrite=True)
            ranged_project = make_project("range")
            renderer.render(ranged_project, ranged, quality="low", overwrite=True)
            renderer.render_preview_window(ranged_project, preview, 0.5, 2.0)

            def mean_difference(left: bytes, right: bytes) -> float:
                return sum(abs(a - b) for a, b in zip(left, right)) / len(left)

            baseline_before = frame(baseline, 0.5)
            crop_at_start = frame(ranged, 1.0)
            crop_midpoint = frame(ranged, 1.5)
            crop_last_frame = frame(ranged, 29 / 15)
            baseline_at_end = frame(baseline, 2.0)
            ranged_at_end = frame(ranged, 2.0)
            baseline_after = frame(baseline, 2.5)
            ranged_after = frame(ranged, 2.5)
            full_at_start = frame(full_crop, 1.0)
            full_midpoint = frame(full_crop, 1.5)
            preview_midpoint = frame(preview, 1.0)

            self.assertLess(mean_difference(frame(ranged, 0.5), baseline_before), 5.0)
            self.assertLess(mean_difference(crop_at_start, full_at_start), 5.0)
            self.assertLess(mean_difference(crop_midpoint, full_midpoint), 5.0)
            self.assertLess(mean_difference(crop_last_frame, frame(full_crop, 29 / 15)), 5.0)
            self.assertLess(mean_difference(ranged_at_end, baseline_at_end), 5.0)
            self.assertLess(mean_difference(ranged_after, baseline_after), 5.0)
            self.assertLess(mean_difference(crop_midpoint, preview_midpoint), 5.0)


if __name__ == "__main__":
    unittest.main()
