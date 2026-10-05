"""A real four-clip grade copy must survive undo, reopening and rendering."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat

from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditError, EditService
from cutvoke.core.store import ProjectStore


def _rat(seconds: int) -> dict[str, str]:
    return {"num": str(seconds), "den": "1"}


def _apply(service: EditService, kind: str, payload: dict, serial: int) -> None:
    project = service.get_project("copy-visual")
    service.execute(Command(
        type=kind, payload=payload, command_id=f"copy-visual-{serial}",
        project_id=project.project_id, expected_revision=project.revision,
        actor=Actor("agent", "copy-visual-test"),
    ))


def _effects(service: EditService, clip_id: str) -> list[dict]:
    return next(clip.effects for track in service.get_project("copy-visual").sequence.tracks
                for clip in track.clips if clip.id == clip_id)


def _frame(video: Path, at: float, path: Path) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", str(at), "-i", str(video), "-frames:v", "1", str(path)],
                   check=True, capture_output=True, timeout=30)


def _mean_rgb_difference(left: Path, right: Path) -> float:
    with Image.open(left) as a, Image.open(right) as b:
        means = ImageStat.Stat(ImageChops.difference(a.convert("RGB"), b.convert("RGB"))).mean
        return sum(means) / len(means)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class EffectCopyVisualTests(unittest.TestCase):
    def test_copy_second_clip_grade_to_first_and_fourth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "test-pattern.mp4"
            art = root / "test-pattern.png"
            canvas = Image.new("RGB", (160, 90), (24, 45, 72))
            draw = ImageDraw.Draw(canvas)
            draw.rectangle((0, 0, 79, 44), fill=(225, 74, 66))
            draw.rectangle((80, 0, 159, 44), fill=(42, 181, 153))
            draw.rectangle((0, 45, 79, 89), fill=(222, 181, 51))
            draw.rectangle((80, 45, 159, 89), fill=(68, 77, 191))
            canvas.save(art)
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-loop", "1", "-framerate", "15", "-i", str(art), "-t", "1",
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
            ], check=True, capture_output=True, timeout=30)
            database = root / "project.sqlite"
            with ProjectStore(str(database)) as store:
                service = EditService(store)
                service.create_project("copy-visual", width=160, height=90,
                                       fps=Rational.of(15))
                for index in range(4):
                    _apply(service, "clip.insert", {
                        "trackId": "video", "clipId": f"scene-{index + 1}",
                        **({"createTrackKind": "video"} if index == 0 else {}),
                        "sourcePath": str(source), "timelineStart": _rat(index),
                        "timelineEnd": _rat(index + 1),
                    }, index + 1)
                _apply(service, "builtinPreset.apply", {
                    "clipId": "scene-2", "presetId": "cutvoke.preset.filter.sepiaTone",
                }, 5)
                _apply(service, "effect.update", {
                    "clipId": "scene-2", "effectId": "cutvoke.fx.sepia",
                    "params": {"strength": 0.55},
                }, 6)
                _apply(service, "effect.add", {
                    "clipId": "scene-2", "effectId": "cutvoke.color",
                    "params": {"brightness": 0.08, "saturation": 1.1},
                }, 7)
                _apply(service, "effect.add", {
                    "clipId": "scene-4", "effectId": "cutvoke.fx.grayscale",
                    "params": {"strength": 0.4},
                }, 8)
                _apply(service, "effect.setAnimation", {
                    "clipId": "scene-4", "effectId": "cutvoke.anim.fadeOut",
                    "params": {"duration": 0.1},
                }, 9)
                _apply(service, "effect.add", {
                    "clipId": "scene-2", "effectId": "cutvoke.fx.pan",
                    "params": {"balance": 0.3},
                }, 10)
                before_revision = service.get_project("copy-visual").revision
                original_fourth = _effects(service, "scene-4")
                _apply(service, "effect.copyVisual", {
                    "sourceClipId": "scene-2", "targetClipIds": ["scene-1", "scene-4"],
                    "mode": "replace",
                }, 11)
                self.assertEqual(int(service.get_project("copy-visual").revision),
                                 int(before_revision) + 1)
                source_grade = [effect for effect in _effects(service, "scene-2")
                                if effect["effectId"] in ("cutvoke.fx.sepia", "cutvoke.color")]
                self.assertEqual(_effects(service, "scene-1"), source_grade)
                fourth_grade = [effect for effect in _effects(service, "scene-4")
                                if effect["effectId"] in ("cutvoke.fx.sepia", "cutvoke.color")]
                self.assertEqual(fourth_grade, source_grade)
                self.assertEqual(_effects(service, "scene-4")[0]["effectId"],
                                 "cutvoke.anim.fadeOut")
                self.assertFalse(any(effect["effectId"] == "cutvoke.fx.grayscale"
                                     for effect in _effects(service, "scene-4")))
                self.assertFalse(any(effect["effectId"] == "cutvoke.fx.pan"
                                     for effect in _effects(service, "scene-4")))
                self.assertEqual(source_grade[0]["presetId"], "cutvoke.preset.filter.sepiaTone")
                self.assertEqual(source_grade[0]["params"]["strength"], 0.55)

                _apply(service, "history.undo", {}, 12)
                self.assertEqual(_effects(service, "scene-1"), [])
                self.assertEqual(_effects(service, "scene-4"), original_fourth)
                _apply(service, "history.redo", {}, 13)
                self.assertEqual([effect for effect in _effects(service, "scene-4")
                                  if effect["effectId"] in ("cutvoke.fx.sepia", "cutvoke.color")],
                                 source_grade)
                saved_revision = service.get_project("copy-visual").revision

            with ProjectStore(str(database)) as reopened:
                service = EditService(reopened)
                project = service.get_project("copy-visual")
                self.assertEqual(project.revision, saved_revision)
                self.assertEqual([effect for effect in _effects(service, "scene-4")
                                  if effect["effectId"] in ("cutvoke.fx.sepia", "cutvoke.color")],
                                 source_grade)
                renderer = RenderService()
                second_preview = root / "second-preview.mp4"
                fourth_preview = root / "fourth-preview.mp4"
                export = root / "export.mp4"
                renderer.render_preview_window(project, str(second_preview), 1, 1)
                renderer.render_preview_window(project, str(fourth_preview), 3, 1)
                renderer.render(project, str(export), quality="low")
                second_frame = root / "second.png"
                fourth_frame = root / "fourth.png"
                export_frame = root / "export-fourth.png"
                _frame(second_preview, 0.5, second_frame)
                _frame(fourth_preview, 0.5, fourth_frame)
                _frame(export, 3.5, export_frame)
                self.assertLess(_mean_rgb_difference(second_frame, fourth_frame), 4)
                self.assertLess(_mean_rgb_difference(fourth_frame, export_frame), 4)

    def test_bad_target_list_does_not_modify_any_clip(self) -> None:
        with ProjectStore(":memory:") as store:
            service = EditService(store)
            service.create_project("copy-visual")
            for index in range(3):
                _apply(service, "clip.insert", {
                    "trackId": "video", "clipId": f"scene-{index + 1}",
                    **({"createTrackKind": "video"} if index == 0 else {}),
                    "sourcePath": f"scene-{index + 1}.png",
                    "timelineStart": _rat(index), "timelineEnd": _rat(index + 1),
                }, index + 1)
            _apply(service, "effect.add", {
                "clipId": "scene-2", "effectId": "cutvoke.fx.sepia",
                "params": {"strength": 0.7},
            }, 4)
            before = service.get_project("copy-visual").to_dict()
            with self.assertRaises(EditError):
                _apply(service, "effect.copyVisual", {
                    "sourceClipId": "scene-2", "targetClipIds": ["scene-1", "missing"],
                }, 5)
            self.assertEqual(service.get_project("copy-visual").to_dict(), before)

            _apply(service, "effect.add", {
                "clipId": "scene-3", "effectId": "cutvoke.fx.blur",
                "params": {"sigma": 5},
            }, 6)
            _apply(service, "effect.add", {
                "clipId": "scene-3", "effectId": "cutvoke.fx.sepia",
                "params": {"strength": 0.2},
            }, 7)
            _apply(service, "effect.copyVisual", {
                "sourceClipId": "scene-2", "targetClipIds": ["scene-3"],
                "mode": "merge",
            }, 8)
            self.assertEqual([effect["effectId"] for effect in _effects(service, "scene-3")],
                             ["cutvoke.fx.blur", "cutvoke.fx.sepia"])
            self.assertEqual(_effects(service, "scene-3")[1]["params"]["strength"], 0.7)


if __name__ == "__main__":
    unittest.main()
