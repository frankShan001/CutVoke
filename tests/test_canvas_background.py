"""Canvas background settings persist and render through the normal preview path."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from PIL import Image

from cutvoke.core.protocol import Actor, Command
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditError, EditService
from cutvoke.core.store import ProjectStore


def _command(service: EditService, project_id: str, command_type: str,
             payload: dict, serial: int) -> None:
    project = service.get_project(project_id)
    service.execute(Command(
        type=command_type,
        payload=payload,
        command_id=f"canvas-background-{serial}",
        project_id=project_id,
        expected_revision=project.revision,
        actor=Actor("human", "canvas-background-test"),
    ))


def _frame(video: Path, at: float, output: Path) -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(at), "-i", str(video), "-frames:v", "1", str(output),
    ], check=True, capture_output=True)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class CanvasBackgroundTests(unittest.TestCase):
    def test_color_command_persists_and_undoes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "project.sqlite"
            with ProjectStore(str(database)) as store:
                service = EditService(store)
                service.create_project("canvas-background")
                _command(service, "canvas-background", "sequence.background",
                         {"color": "#efe7d8"}, 1)
                self.assertEqual(service.get_project("canvas-background")
                                 .sequence.background_color, "#EFE7D8")

                catalog = {item["type"]: item for item in service.command_catalog()}
                self.assertIn("sequence.background", catalog)
                self.assertIn("sequence.background", catalog["edit.batch"]["allowedCommands"])

                with self.assertRaises(EditError):
                    _command(service, "canvas-background", "sequence.background",
                             {"color": "#123456;movie=bad"}, 2)
                self.assertEqual(service.get_project("canvas-background")
                                 .sequence.background_color, "#EFE7D8")

                _command(service, "canvas-background", "history.undo", {}, 3)
                self.assertEqual(service.get_project("canvas-background")
                                 .sequence.background_color, "#000000")
                _command(service, "canvas-background", "history.redo", {}, 4)
                self.assertEqual(service.get_project("canvas-background")
                                 .sequence.background_color, "#EFE7D8")

            with ProjectStore(str(database)) as reopened:
                restored = EditService(reopened).get_project("canvas-background")
                self.assertEqual(restored.sequence.background_color, "#EFE7D8")
                self.assertEqual(restored.sequence.to_dict()["backgroundColor"], "#EFE7D8")

    def test_background_matches_preview_and_export_on_an_audio_only_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "silence.wav"
            with wave.open(str(audio), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(16000)
                stream.writeframes(b"\x00\x00" * 16000)

            with ProjectStore(str(root / "project.sqlite")) as store:
                service = EditService(store)
                service.create_project("canvas-render", width=96, height=64)
                _command(service, "canvas-render", "clip.insert", {
                    "trackId": "audio", "createTrackKind": "audio",
                    "clipId": "silence", "sourcePath": str(audio),
                    "timelineStart": {"num": "0", "den": "1"},
                    "timelineEnd": {"num": "1", "den": "1"},
                }, 1)
                _command(service, "canvas-render", "sequence.background",
                         {"color": "#29364A"}, 2)
                project = service.get_project("canvas-render")

            renderer = RenderService()
            exported, preview = root / "background-export.mp4", root / "background-preview.mp4"
            renderer.render(project, str(exported), quality="low")
            renderer.render_preview_window(project, str(preview), 0.1, 0.8)
            export_frame, preview_frame = root / "export.png", root / "preview.png"
            _frame(exported, 0.5, export_frame)
            _frame(preview, 0.4, preview_frame)
            expected = (41, 54, 74)
            with Image.open(export_frame) as image, Image.open(preview_frame) as other:
                pixel = image.convert("RGB").getpixel((48, 32))
                preview_pixel = other.convert("RGB").getpixel((48, 32))
                self.assertLess(max(abs(actual - wanted)
                                    for actual, wanted in zip(pixel, expected)), 12)
                self.assertLess(max(abs(actual - wanted)
                                    for actual, wanted in zip(preview_pixel, expected)), 12)

            transparent_video = root / "transparent-audio-only.mov"
            renderer.render(project, str(transparent_video), quality="low", alpha=True)
            alpha_frame = root / "transparent-audio-only.png"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", "0.5", "-i", str(transparent_video), "-frames:v", "1",
                "-vf", "format=rgba", str(alpha_frame),
            ], check=True, capture_output=True)
            with Image.open(alpha_frame) as image:
                self.assertEqual(image.convert("RGBA").getpixel((48, 32))[3], 0)

    def test_canvas_color_shows_through_alpha_mask_but_not_transparent_still(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            overlay = root / "blue-overlay.png"
            Image.new("RGB", (160, 90), (30, 70, 230)).save(overlay)
            with ProjectStore(str(root / "project.sqlite")) as store:
                service = EditService(store)
                service.create_project("canvas-mask", width=160, height=90)
                _command(service, "canvas-mask", "clip.insert", {
                    "trackId": "overlay", "createTrackKind": "video",
                    "createTrackRole": "sticker",
                    "clipId": "blue", "sourcePath": str(overlay),
                    "timelineStart": {"num": "0", "den": "1"},
                    "timelineEnd": {"num": "2", "den": "1"},
                }, 1)
                _command(service, "canvas-mask", "effect.add", {
                    "clipId": "blue", "effectId": "cutvoke.fx.mask",
                    "params": {"shape": "circle", "x": 0.5, "y": 0.5,
                               "w": 0.58, "h": 0.72, "feather": 0, "invert": False},
                }, 2)
                _command(service, "canvas-mask", "sequence.background",
                         {"color": "#29364A"}, 3)
                project = service.get_project("canvas-mask")

            renderer = RenderService()
            exported, preview = root / "masked-export.mp4", root / "masked-preview.mp4"
            renderer.render(project, str(exported), quality="low")
            renderer.render_preview_window(project, str(preview), 0.1, 0.8)
            export_frame, preview_frame = root / "masked-export.png", root / "masked-preview.png"
            _frame(exported, 0.5, export_frame)
            _frame(preview, 0.4, preview_frame)
            expected_background = (41, 54, 74)
            with Image.open(export_frame) as image, Image.open(preview_frame) as other:
                image, other = image.convert("RGB"), other.convert("RGB")
                center, corner = image.getpixel((80, 45)), image.getpixel((4, 4))
                self.assertGreater(center[2], 180)
                self.assertLess(max(abs(actual - wanted)
                                    for actual, wanted in zip(corner, expected_background)), 12,
                                f"custom canvas color was not visible through mask: {corner}")
                self.assertLessEqual(max(abs(actual - wanted) for actual, wanted in
                                         zip(center, other.getpixel((80, 45)))), 6)

            transparent_still = root / "transparent-still.png"
            renderer.extract_still(project, 0.5, str(transparent_still), alpha=True)
            with Image.open(transparent_still) as image:
                rgba = image.convert("RGBA")
                self.assertEqual(rgba.getpixel((4, 4))[3], 0)
                self.assertGreater(rgba.getpixel((80, 45))[3], 240)


if __name__ == "__main__":
    unittest.main()
