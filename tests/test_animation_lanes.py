"""Entrance, exit and loop animations must survive editing and render together."""

from __future__ import annotations

import copy
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.preset_catalog import PresetCatalog
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService


def _command(service: EditService, project_id: str, kind: str, payload: dict,
             command_id: str) -> None:
    project = service.get_project(project_id)
    service.execute(Command(type=kind, payload=payload, command_id=command_id,
                            project_id=project_id, expected_revision=project.revision,
                            actor=Actor("agent", "animation-lanes-test")))


class AnimationLanesTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_lower_frame_rate_video_fade_does_not_flash_black(self) -> None:
        """A 15 fps source on a 30 fps canvas must fill every animation segment."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=red:s=96x64:r=15:d=2",
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
            ], check=True, capture_output=True)
            service = EditService()
            project = service.create_project("lower-fps-fade", width=96, height=64,
                                             fps=Rational.of(30))
            clip = Clip("scene", AssetReference("scene", str(source)),
                        Rational.of(0), Rational.of(2), Rational.of(0), effects=[
                            {"effectId": "cutvoke.anim.fadeIn",
                             "params": {"duration": 1.0, "easing": "linear"}},
                        ])
            project.sequence.tracks = [Track("v1", "video", [clip])]
            rendered = root / "fade.mp4"
            RenderService().render_preview_window(project, str(rendered), 0, 2)
            raw = subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(rendered),
                "-an", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
            ], check=True, capture_output=True).stdout
            frame_size = 96 * 64
            frames = [raw[i:i + frame_size] for i in range(0, len(raw), frame_size)]
            self.assertGreaterEqual(len(frames), 58)
            # Ignore the intentionally almost-black first animation frames.
            for index in range(6, min(58, len(frames))):
                with self.subTest(frame=index):
                    self.assertGreater(sum(frames[index]) / frame_size, 5)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_overlay_entrance_reveals_lower_track_in_uncovered_area(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lower_path, upper_path = root / "red.png", root / "blue.png"
            Image.new("RGB", (96, 64), (220, 25, 25)).save(lower_path)
            Image.new("RGB", (96, 64), (25, 25, 220)).save(upper_path)
            service = EditService()
            project = service.create_project("overlay-entrance", width=96, height=64,
                                             fps=Rational.of(15))
            lower = Clip("lower", AssetReference("lower", str(lower_path)),
                         Rational.of(0), Rational.of(2), Rational.of(0))
            upper = Clip("upper", AssetReference("upper", str(upper_path)),
                         Rational.of(0), Rational.of(2), Rational.of(0), effects=[
                             {"effectId": "cutvoke.anim.slideRight",
                              "params": {"duration": 0.8, "distance": 1.0}},
                         ])
            project.sequence.tracks = [Track("v1", "video", [lower]),
                                       Track("v2", "video", [upper])]
            frame = root / "enter.png"
            RenderService().extract_frame(project, 0.13, str(frame))
            with Image.open(frame) as image:
                left = image.convert("RGB").getpixel((5, 32))
                right = image.convert("RGB").getpixel((90, 32))
            self.assertGreater(left[0], left[2] * 2)
            self.assertGreater(right[2], right[0] * 2)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_loop_candidates_do_not_reveal_black_canvas_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "solid-red.png"
            Image.new("RGB", (96, 64), (220, 30, 30)).save(source)
            service = EditService()
            project = service.create_project("loop-edges", width=96, height=64,
                                             fps=Rational.of(15))
            clip = Clip("image", AssetReference("image", str(source)),
                        Rational.of(0), Rational.of(2), Rational.of(0))
            project.sequence.tracks = [Track("v1", "video", [clip])]
            renderer = RenderService()
            loops = [item for item in PresetCatalog.builtin().all()
                     if item.family == "animation" and item.subcategory == "循环"]
            self.assertEqual(len(loops), 8)
            for preset in loops:
                clip.effects = [dict(effect) for effect in preset.effects]
                for at in (0.35, 0.95):
                    frame = root / f"{preset.id.rsplit('.', 1)[-1]}-{at}.png"
                    renderer.extract_frame(project, at, str(frame))
                    with Image.open(frame) as image:
                        rgb = image.convert("RGB")
                        for point in ((0, 0), (95, 0), (0, 63), (95, 63)):
                            with self.subTest(preset=preset.id, at=at, point=point):
                                self.assertGreater(rgb.getpixel(point)[0], 20)

    def test_setting_and_clearing_one_lane_preserves_other_lanes(self) -> None:
        service = EditService()
        project = service.create_project("animation-lanes")
        project.sequence.tracks = [Track("v1", "video", [Clip(
            "image", AssetReference("image", "image.png"),
            Rational.of(0), Rational.of(3), Rational.of(0),
        )])]
        for index, effect_id in enumerate(("cutvoke.anim.fadeIn", "cutvoke.anim.fadeOut",
                                           "cutvoke.anim.float")):
            _command(service, project.project_id, "effect.setAnimation",
                     {"clipId": "image", "effectId": effect_id}, f"set-{index}")
        clip = service.get_project(project.project_id).sequence.tracks[0].clips[0]
        self.assertEqual([item["effectId"] for item in clip.effects],
                         ["cutvoke.anim.fadeIn", "cutvoke.anim.fadeOut", "cutvoke.anim.float"])
        _command(service, project.project_id, "effect.setAnimation",
                 {"clipId": "image", "effectId": "cutvoke.anim.zoomIn"}, "replace-entrance")
        clip = service.get_project(project.project_id).sequence.tracks[0].clips[0]
        self.assertEqual([item["effectId"] for item in clip.effects],
                         ["cutvoke.anim.zoomIn", "cutvoke.anim.fadeOut", "cutvoke.anim.float"])
        _command(service, project.project_id, "effect.setAnimation",
                 {"clipId": "image", "effectId": "", "slot": "出场"}, "clear-exit")
        self.assertEqual([item["effectId"] for item in
                          service.get_project(project.project_id).sequence.tracks[0].clips[0].effects],
                         ["cutvoke.anim.zoomIn", "cutvoke.anim.float"])
        _command(service, project.project_id, "history.undo", {}, "undo-clear-exit")
        self.assertEqual(len(service.get_project(project.project_id)
                             .sequence.tracks[0].clips[0].effects), 3)
        _command(service, project.project_id, "builtinPreset.apply",
                 {"clipId": "image", "presetId": "cutvoke.preset.animation.slideOutLeft"},
                 "builtin-exit")
        clip = service.get_project(project.project_id).sequence.tracks[0].clips[0]
        self.assertEqual([item["effectId"] for item in clip.effects],
                         ["cutvoke.anim.zoomIn", "cutvoke.anim.slideOutLeft",
                          "cutvoke.anim.float"])
        self.assertEqual(clip.effects[1]["presetId"],
                         "cutvoke.preset.animation.slideOutLeft")
        _command(service, project.project_id, "history.undo", {}, "undo-builtin-exit")
        self.assertEqual([item["effectId"] for item in
                          service.get_project(project.project_id).sequence.tracks[0].clips[0].effects],
                         ["cutvoke.anim.zoomIn", "cutvoke.anim.fadeOut",
                          "cutvoke.anim.float"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_entrance_exit_and_loop_all_affect_real_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "shape.png"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                "-i", "color=c=black:s=96x64:r=15:d=1,drawbox=x=30:y=16:w=36:h=32:color=white:t=fill",
                "-frames:v", "1", str(source),
            ], check=True, capture_output=True)
            service = EditService()
            project = service.create_project("animation-render", width=96, height=64,
                                             fps=Rational.of(15))
            clip = Clip("shape", AssetReference("shape", str(source)),
                        Rational.of(0), Rational.of(3), Rational.of(0), effects=[
                            {"effectId": "cutvoke.anim.fadeIn", "params": {"duration": 0.8}},
                            {"effectId": "cutvoke.anim.fadeOut", "params": {"duration": 0.8}},
                            {"effectId": "cutvoke.anim.float", "params": {
                                "amplitude": 0.12, "period": 2.0}},
                        ])
            project.sequence.tracks = [Track("v1", "video", [clip])]
            renderer = RenderService()
            renderer.render_preview_window(project, str(root / "timeline.mp4"), 0, 3)

            def frame_mean(at: float, current_project, name: str) -> tuple[float, bytes]:
                target = root / f"{name}.png"
                renderer.extract_frame(current_project, at, str(target))
                raw = subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(target),
                    "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
                ], check=True, capture_output=True).stdout
                return sum(raw) / len(raw), raw

            entrance, _ = frame_mean(0.13, project, "entrance")
            middle, loop_frame = frame_mean(1.33, project, "middle")
            exit_mean, _ = frame_mean(2.87, project, "exit")
            self.assertGreater(middle, entrance * 1.5)
            self.assertGreater(middle, exit_mean * 1.5)
            without_loop = copy.deepcopy(project)
            without_loop.sequence.tracks[0].clips[0].effects.pop()
            _, still_frame = frame_mean(1.33, without_loop, "without-loop")
            self.assertGreater(sum(a != b for a, b in zip(loop_frame, still_frame)), 100)


if __name__ == "__main__":
    unittest.main()
