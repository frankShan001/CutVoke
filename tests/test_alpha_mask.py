"""A geometry mask must reveal lower tracks through real pixel alpha."""

from __future__ import annotations

import copy
import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from cutvoke.core.protocol import Actor, Command
from cutvoke.core.render import RenderService, _bezier_closed_path, _smooth_closed_path
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore


def _rat(value: int) -> dict[str, str]:
    return {"num": str(value), "den": "1"}


def _edit(service: EditService, name: str, payload: dict, number: int) -> None:
    project = service.get_project("alpha-mask")
    service.execute(Command(type=name, payload=payload,
                            command_id=f"alpha-mask-{number}",
                            project_id=project.project_id,
                            expected_revision=project.revision,
                            actor=Actor("agent", "alpha-mask-test")))


def _frame(video: Path, at: float, output: Path) -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(at), "-i", str(video), "-frames:v", "1", str(output),
    ], check=True, capture_output=True)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class AlphaMaskTests(unittest.TestCase):
    def test_builtin_background_shows_through_mask_in_preview_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            background = (Path(__file__).resolve().parents[1] /
                          "src/cutvoke/assets/backgrounds/bg_solid_white.png")
            overlay = root / "blue-overlay.png"
            Image.new("RGB", (160, 90), (30, 70, 230)).save(overlay)
            database = root / "project.sqlite"
            params = {"shape": "circle", "x": 0.5, "y": 0.5,
                      "w": 0.58, "h": 0.72, "feather": 0, "invert": False}
            with ProjectStore(str(database)) as store:
                service = EditService(store)
                service.create_project("alpha-mask", width=160, height=90)
                _edit(service, "clip.insert", {
                    "trackId": "background-track", "createTrackKind": "video",
                    "clipId": "background", "sourcePath": str(background),
                    "timelineStart": _rat(0), "timelineEnd": _rat(2),
                }, 1)
                _edit(service, "clip.insert", {
                    "trackId": "overlay-track", "createTrackKind": "video",
                    "clipId": "overlay", "sourcePath": str(overlay),
                    "timelineStart": _rat(0), "timelineEnd": _rat(2),
                }, 2)
                _edit(service, "effect.add", {
                    "clipId": "overlay", "effectId": "cutvoke.fx.mask",
                    "params": params,
                }, 3)

            with ProjectStore(str(database)) as reopened:
                project = EditService(reopened).get_project("alpha-mask")
                renderer = RenderService()
                exported, preview = root / "background-mask.mp4", root / "background-preview.mp4"
                renderer.render(project, str(exported), quality="low")
                renderer.render_preview_window(project, str(preview), 0.25, 1.0)
                export_frame, preview_frame = root / "background-mask.png", root / "background-preview.png"
                _frame(exported, 0.75, export_frame)
                _frame(preview, 0.5, preview_frame)
                with Image.open(export_frame) as image, Image.open(preview_frame) as other:
                    image, other = image.convert("RGB"), other.convert("RGB")
                    center = image.getpixel((80, 45))
                    corner = image.getpixel((4, 4))
                    self.assertGreater(center[2], 180)
                    self.assertLess(center[0], 90)
                    self.assertGreater(min(corner), 200)
                    diff = ImageStat.Stat(ImageChops.difference(image, other)).mean
                    self.assertLess(sum(diff) / 3, 5)

    def test_feathered_circle_reveals_lower_track_after_reload_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lower, upper = root / "lower.mp4", root / "upper.png"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=red:s=160x90:r=30:d=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(lower),
            ], check=True, capture_output=True)
            Image.new("RGB", (160, 90), (30, 70, 230)).save(upper)
            database = root / "project.sqlite"
            params = {"shape": "circle", "x": 0.5, "y": 0.5,
                      "w": 0.6, "h": 0.7, "feather": 0.3, "invert": False}
            with ProjectStore(str(database)) as store:
                service = EditService(store)
                service.create_project("alpha-mask", width=160, height=90)
                _edit(service, "clip.insert", {
                    "trackId": "base-track", "createTrackKind": "video", "clipId": "base",
                    "sourcePath": str(lower), "timelineStart": _rat(0),
                    "timelineEnd": _rat(2),
                }, 1)
                _edit(service, "clip.insert", {
                    "trackId": "overlay-track", "createTrackKind": "video", "clipId": "overlay",
                    "sourcePath": str(upper), "timelineStart": _rat(0),
                    "timelineEnd": _rat(2),
                }, 2)
                _edit(service, "effect.add", {
                    "clipId": "overlay", "effectId": "cutvoke.fx.mask", "params": params,
                }, 3)
                _edit(service, "history.undo", {}, 4)
                self.assertFalse(service.get_project("alpha-mask")
                                 .sequence.tracks[1].clips[0].effects)
                _edit(service, "history.redo", {}, 5)

            with ProjectStore(str(database)) as reopened:
                service = EditService(reopened)
                project = service.get_project("alpha-mask")
                self.assertEqual(project.sequence.tracks[1].clips[0]
                                 .effects[0]["params"], params)
                renderer = RenderService()
                exported, preview = root / "export.mp4", root / "preview.mp4"
                renderer.render(project, str(exported), quality="low")
                renderer.render_preview_window(project, str(preview), 0.25, 1.0)
                export_frame, preview_frame = root / "export.png", root / "preview.png"
                _frame(exported, 0.75, export_frame)
                _frame(preview, 0.5, preview_frame)
                with Image.open(export_frame) as image, Image.open(preview_frame) as other:
                    image, other = image.convert("RGB"), other.convert("RGB")
                    center = image.getpixel((80, 45))
                    corner = image.getpixel((4, 4))
                    feather = image.getpixel((125, 45))
                    self.assertGreater(center[2], 180)  # blue upper image
                    self.assertGreater(corner[0], 170)  # red lower image
                    self.assertLess(corner[2], 90)
                    self.assertGreater(feather[0], center[0] + 35)
                    self.assertGreater(feather[2], corner[2] + 35)
                    diff = ImageStat.Stat(ImageChops.difference(image, other)).mean
                    self.assertLess(sum(diff) / 3, 5)

                alpha_project = copy.deepcopy(project)
                alpha_project.sequence.tracks = alpha_project.sequence.tracks[1:]
                still = root / "masked.png"
                renderer.extract_still(alpha_project, 0.75, str(still), alpha=True)
                with Image.open(still) as image:
                    self.assertLess(image.convert("RGBA").getpixel((4, 4))[3], 20)
                    self.assertGreater(image.convert("RGBA").getpixel((80, 45))[3], 230)

                _edit(service, "effect.update", {
                    "clipId": "overlay", "effectId": "cutvoke.fx.mask",
                    "params": {"invert": True},
                }, 6)
                inverted = root / "inverted.png"
                renderer.extract_frame(service.get_project("alpha-mask"), 0.75,
                                       str(inverted))
                with Image.open(inverted) as image:
                    image = image.convert("RGB")
                    self.assertGreater(image.getpixel((80, 45))[0], 170)
                    self.assertGreater(image.getpixel((4, 4))[2], 180)
                _edit(service, "history.undo", {}, 7)
                self.assertFalse(service.get_project("alpha-mask")
                                 .sequence.tracks[1].clips[0].effects[0]["params"]["invert"])
                _edit(service, "history.redo", {}, 8)
                self.assertTrue(service.get_project("alpha-mask")
                                .sequence.tracks[1].clips[0].effects[0]["params"]["invert"])

    def test_text_and_freehand_masks_render_real_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lower, upper = root / "lower.mp4", root / "upper.png"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=red:s=160x90:r=30:d=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(lower),
            ], check=True, capture_output=True)
            Image.new("RGB", (160, 90), (30, 70, 230)).save(upper)
            database = root / "project.sqlite"
            text_params = {"shape": "text", "x": 0.08, "y": 0.1,
                           "content": "蒙版", "fontSize": 0.5,
                           "feather": 0.02, "invert": False}
            with ProjectStore(str(database)) as store:
                service = EditService(store)
                service.create_project("alpha-mask", width=160, height=90)
                _edit(service, "clip.insert", {
                    "trackId": "base-track", "createTrackKind": "video", "clipId": "base",
                    "sourcePath": str(lower), "timelineStart": _rat(0), "timelineEnd": _rat(2),
                }, 1)
                _edit(service, "clip.insert", {
                    "trackId": "overlay-track", "createTrackKind": "video", "clipId": "overlay",
                    "sourcePath": str(upper), "timelineStart": _rat(0), "timelineEnd": _rat(2),
                }, 2)
                _edit(service, "effect.add", {
                    "clipId": "overlay", "effectId": "cutvoke.fx.mask", "params": text_params,
                }, 3)
                renderer = RenderService()
                project = service.get_project("alpha-mask")
                text_export, text_preview = root / "text-export.mp4", root / "text-preview.mp4"
                renderer.render(project, str(text_export), quality="low")
                renderer.render_preview_window(project, str(text_preview), 0.25, 1.0)
                text_frame, text_preview_frame = root / "text.png", root / "text-preview.png"
                _frame(text_export, 0.75, text_frame)
                _frame(text_preview, 0.5, text_preview_frame)
                with Image.open(text_frame) as frame, Image.open(text_preview_frame) as preview:
                    frame = frame.convert("RGB")
                    preview = preview.convert("RGB")
                    blue_pixels = sum(
                        1 for y in range(0, 75) for x in range(0, 120)
                        if (pixel := frame.getpixel((x, y)))[2] > pixel[0] + 30
                    )
                    self.assertGreater(blue_pixels, 30, "text glyphs should reveal the upper image")
                    outside = frame.getpixel((145, 70))
                    self.assertGreater(outside[0], 170, "outside text should reveal the lower image")
                    diff = ImageStat.Stat(ImageChops.difference(frame, preview)).mean
                    self.assertLess(sum(diff) / 3, 5)

                curve_points = [
                    {"x": round(0.3 + 0.25 * math.cos(index * math.tau / 12), 6),
                     "y": round(0.5 + 0.4 * math.sin(index * math.tau / 12), 6)}
                    for index in range(12)
                ]
                freehand_params = {
                    "shape": "freehand", "feather": 0.02, "invert": False,
                    "pathMode": "smooth", "points": curve_points,
                }
                smooth_path = _smooth_closed_path(
                    [(point["x"], point["y"]) for point in curve_points])
                self.assertGreater(len(smooth_path), len(curve_points))
                self.assertLessEqual(len(smooth_path), 256)
                _edit(service, "effect.update", {
                    "clipId": "overlay", "effectId": "cutvoke.fx.mask",
                    "params": freehand_params,
                }, 4)
                _edit(service, "history.undo", {}, 5)
                self.assertEqual(service.get_project("alpha-mask").sequence.tracks[1]
                                 .clips[0].effects[0]["params"]["shape"], "text")
                _edit(service, "history.redo", {}, 6)

            with ProjectStore(str(database)) as reopened:
                project = EditService(reopened).get_project("alpha-mask")
                persisted = project.sequence.tracks[1].clips[0].effects[0]["params"]
                self.assertEqual(persisted["points"], freehand_params["points"])
                self.assertEqual(persisted["pathMode"], "smooth")
                renderer = RenderService()
                path_export, path_preview = root / "path-export.mp4", root / "path-preview.mp4"
                renderer.render(project, str(path_export), quality="low")
                renderer.render_preview_window(project, str(path_preview), 0.25, 1.0)
                path_frame, path_preview_frame = root / "path.png", root / "path-preview.png"
                _frame(path_export, 0.75, path_frame)
                _frame(path_preview, 0.5, path_preview_frame)
                with Image.open(path_frame) as frame, Image.open(path_preview_frame) as preview:
                    frame = frame.convert("RGB")
                    preview = preview.convert("RGB")
                    inside, outside = frame.getpixel((40, 45)), frame.getpixel((130, 45))
                    self.assertGreater(inside[2], inside[0] + 100)
                    self.assertGreater(outside[0], outside[2] + 100)
                    diff = ImageStat.Stat(ImageChops.difference(frame, preview)).mean
                    self.assertLess(sum(diff) / 3, 5)

    def test_bezier_pen_handles_render_real_alpha_preview_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lower, upper = root / "lower.mp4", root / "upper.png"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=red:s=160x90:r=30:d=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(lower),
            ], check=True, capture_output=True)
            Image.new("RGB", (160, 90), (30, 70, 230)).save(upper)
            points = [
                {"x": 0.5, "y": 0.15, "inHandle": {"x": 0.335, "y": 0.15},
                 "outHandle": {"x": 0.665, "y": 0.15}, "handlesLinked": False},
                {"x": 0.8, "y": 0.5, "inHandle": {"x": 0.8, "y": 0.335},
                 "outHandle": {"x": 0.8, "y": 0.665}},
                {"x": 0.5, "y": 0.85, "inHandle": {"x": 0.665, "y": 0.85},
                 "outHandle": {"x": 0.335, "y": 0.85}},
                {"x": 0.2, "y": 0.5, "inHandle": {"x": 0.2, "y": 0.665},
                 "outHandle": {"x": 0.2, "y": 0.335}},
            ]
            sampled = _bezier_closed_path([(point["x"], point["y"]) for point in points], points)
            self.assertEqual(len(sampled), 32)
            self.assertTrue(all(0 <= x <= 1 and 0 <= y <= 1 for x, y in sampled))
            mask_params = {"shape": "freehand", "feather": 0, "invert": False,
                           "pathMode": "bezier", "points": points}
            database = root / "project.sqlite"
            with ProjectStore(str(database)) as store:
                service = EditService(store)
                service.create_project("alpha-mask", width=160, height=90)
                _edit(service, "clip.insert", {
                    "trackId": "base-track", "createTrackKind": "video", "clipId": "base",
                    "sourcePath": str(lower), "timelineStart": _rat(0), "timelineEnd": _rat(2),
                }, 1)
                _edit(service, "clip.insert", {
                    "trackId": "overlay-track", "createTrackKind": "video", "clipId": "overlay",
                    "sourcePath": str(upper), "timelineStart": _rat(0), "timelineEnd": _rat(2),
                }, 2)
                _edit(service, "effect.add", {
                    "clipId": "overlay", "effectId": "cutvoke.fx.mask", "params": mask_params,
                }, 3)

            with ProjectStore(str(database)) as reopened:
                service = EditService(reopened)
                project = service.get_project("alpha-mask")
                persisted = project.sequence.tracks[1].clips[0].effects[0]["params"]
                self.assertEqual(persisted["pathMode"], "bezier")
                self.assertEqual(persisted["points"], points)
                self.assertIs(persisted["points"][0]["handlesLinked"], False)
                renderer = RenderService()
                output, preview = root / "bezier-export.mp4", root / "bezier-preview.mp4"
                renderer.render(project, str(output), quality="low")
                renderer.render_preview_window(project, str(preview), 0.25, 1.0)
                output_frame, preview_frame = root / "bezier.png", root / "bezier-preview.png"
                _frame(output, 0.75, output_frame)
                _frame(preview, 0.5, preview_frame)
                with Image.open(output_frame) as frame, Image.open(preview_frame) as preview_image:
                    frame = frame.convert("RGB")
                    preview_image = preview_image.convert("RGB")
                    inside, outside = frame.getpixel((80, 45)), frame.getpixel((10, 45))
                    self.assertGreater(inside[2], inside[0] + 100)
                    self.assertGreater(outside[0], outside[2] + 100)
                    diff = ImageStat.Stat(ImageChops.difference(frame, preview_image)).mean
                    self.assertLess(sum(diff) / 3, 5)

                changed = copy.deepcopy(mask_params)
                changed["points"][0]["outHandle"] = {"x": 0.7, "y": 0.1}
                _edit(service, "effect.update", {
                    "clipId": "overlay", "effectId": "cutvoke.fx.mask", "params": changed,
                }, 4)
                _edit(service, "history.undo", {}, 5)
                undone = service.get_project("alpha-mask").sequence.tracks[1].clips[0].effects[0]["params"]
                self.assertEqual(undone["pathMode"], "bezier")
                self.assertEqual(undone["points"], points)
                self.assertIs(undone["points"][0]["handlesLinked"], False)
                _edit(service, "history.redo", {}, 6)
                redone = service.get_project("alpha-mask").sequence.tracks[1].clips[0].effects[0]["params"]
                self.assertEqual(redone["points"], changed["points"])


if __name__ == "__main__":
    unittest.main()
