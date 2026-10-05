"""Title animation lanes change exported pixels, not only editor controls."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from cutvoke.core.caption_render import build_ass
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore


def _command(service: EditService, kind: str, payload: dict, serial: int) -> None:
    project = service.get_project("animated-title")
    service.execute(Command(
        type=kind, payload=payload, command_id=f"animated-title-{serial}",
        project_id=project.project_id, expected_revision=project.revision,
        actor=Actor("agent", "animated-title-test"),
    ))


def _pixels(video: Path, at: float, output: Path, threshold: int = 90) -> int:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", str(at), "-i", str(video), "-frames:v", "1", str(output)],
                   check=True, capture_output=True, timeout=30)
    with Image.open(output) as image:
        rgb = image.convert("RGB").tobytes()
        return sum(rgb[index] > threshold and rgb[index + 1] > threshold
                   and rgb[index + 2] > threshold
                   for index in range(0, len(rgb), 3))


def _frame(video: Path, at: float, output: Path) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", str(at), "-i", str(video), "-frames:v", "1", str(output)],
                   check=True, capture_output=True, timeout=30)


def _red_pixels(video: Path, at: float, output: Path) -> int:
    _frame(video, at, output)
    with Image.open(output) as image:
        pixels = image.convert("RGB").tobytes()
        return sum(pixels[index] > 80
                   and pixels[index] > pixels[index + 1] * 1.35
                   and pixels[index] > pixels[index + 2] * 1.35
                   for index in range(0, len(pixels), 3))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class TitleAnimationLaneTests(unittest.TestCase):
    def test_typewriter_entrance_composes_with_word_highlight_in_export_and_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            background = root / "background.png"
            Image.new("RGB", (640, 360), (20, 30, 60)).save(background)
            with ProjectStore(str(root / "projects.sqlite")) as store:
                service = EditService(store)
                service.create_project("animated-title", width=640, height=360,
                                       fps=Rational.of(30))
                _command(service, "clip.insert", {
                    "trackId": "video", "createTrackKind": "video",
                    "sourcePath": str(background),
                    "timelineStart": {"num": "0", "den": "1"},
                    "timelineEnd": {"num": "2", "den": "1"},
                }, 1)
                _command(service, "caption.add", {
                    "captionId": "karaoke", "text": "HELLO WORLD",
                    "start": {"num": "0", "den": "1"},
                    "end": {"num": "2", "den": "1"},
                    "words": [
                        {"text": "HELLO", "start": {"num": "0", "den": "1"},
                         "end": {"num": "4", "den": "5"}},
                        {"text": "WORLD", "start": {"num": "4", "den": "5"},
                         "end": {"num": "3", "den": "2"}},
                    ],
                    "wordHighlightColor": "#ff0000", "animIn": 800,
                    "animInStyle": "typewriter", "fontSize": 96,
                    "strokeWidth": 0, "shadow": 0,
                }, 2)

            with ProjectStore(str(root / "projects.sqlite")) as reopened:
                service = EditService(reopened)
                project = service.get_project("animated-title")
                caption = project.sequence.captions[0]
                self.assertEqual(caption.animInStyle, "typewriter")
                self.assertEqual(caption.wordHighlightColor, "#FF0000")
                ass = build_ass(project.sequence.captions, 640, 360)
                first_event = next(line for line in ass.splitlines()
                                   if line.startswith(
                                       "Dialogue: 0,0:00:00.00,0:00:00.07,"))
                self.assertIn(r"\1c&H000000FF&", first_event)
                self.assertIn("H", first_event)
                self.assertNotIn("HELLO", first_event)

                renderer = RenderService()
                exported = root / "combined.mp4"
                preview = root / "combined-preview.mp4"
                renderer.render(project, str(exported), quality="low")
                renderer.render_preview_window(project, str(preview), 0, 1.2)
                early_red = _red_pixels(exported, 0.05, root / "early.png")
                middle_red = _red_pixels(exported, 0.45, root / "middle.png")
                second_word_red = _red_pixels(exported, 0.95, root / "second-word.png")
                self.assertGreater(early_red, 0)
                self.assertGreater(middle_red, early_red * 1.5)
                self.assertGreater(second_word_red, 0)

                _frame(exported, 0.95, root / "export-frame.png")
                _frame(preview, 0.95, root / "preview-frame.png")
                with Image.open(root / "export-frame.png") as export_frame, \
                        Image.open(root / "preview-frame.png") as preview_frame:
                    difference = ImageStat.Stat(ImageChops.difference(
                        export_frame.convert("RGB"), preview_frame.convert("RGB"))).mean
                    self.assertLess(sum(difference) / 3, 5)

    def test_typewriter_loop_and_scale_exit_survive_reload_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            background = root / "background.png"
            Image.new("RGB", (320, 180), (20, 30, 60)).save(background)
            with ProjectStore(str(root / "projects.sqlite")) as store:
                service = EditService(store)
                service.create_project("animated-title", width=320, height=180,
                                       fps=Rational.of(30))
                _command(service, "clip.insert", {
                    "trackId": "video", "createTrackKind": "video",
                    "sourcePath": str(background),
                    "timelineStart": {"num": "0", "den": "1"},
                    "timelineEnd": {"num": "2", "den": "1"},
                }, 1)
                _command(service, "clip.insert", {
                    "trackId": "title", "createTrackKind": "text", "clipId": "headline",
                    "text": {"content": "ABCD", "fontSize": 180,
                             "strokeWidth": 0, "shadow": 0,
                             "animIn": 800, "animInStyle": "typewriter",
                             "animOut": 400, "animOutStyle": "scale",
                             "animLoopStyle": "pulse", "animLoopMs": 400},
                    "timelineStart": {"num": "0", "den": "1"},
                    "timelineEnd": {"num": "2", "den": "1"},
                }, 2)
                project = service.get_project("animated-title")
                effect = project.sequence.tracks[1].clips[0].effects[0]
                self.assertEqual(effect["params"]["animInStyle"], "typewriter")
                _command(service, "history.undo", {}, 3)
                self.assertEqual(len(service.get_project("animated-title").sequence.tracks), 1)
                _command(service, "history.redo", {}, 4)

            with ProjectStore(str(root / "projects.sqlite")) as reopened:
                service = EditService(reopened)
                project = service.get_project("animated-title")
                effect = project.sequence.tracks[1].clips[0].effects[0]
                self.assertEqual(effect["params"]["animLoopStyle"], "pulse")
                renderer = RenderService()
                video = root / "animated.mp4"
                renderer.render(project, str(video), quality="low")
                early = _pixels(video, 0.05, root / "early.png")
                middle = _pixels(video, 0.45, root / "middle.png")
                full = _pixels(video, 0.95, root / "full.png")
                loop_peak = _pixels(video, 1.0, root / "peak.png")
                loop_valley = _pixels(video, 1.2, root / "valley.png")
                exit_frame = _pixels(video, 1.9, root / "exit.png")
                self.assertGreater(middle, early * 1.4)
                self.assertGreater(full, middle * 1.2)
                self.assertNotEqual(loop_peak, loop_valley)
                self.assertLess(exit_frame, full * 0.6)

                preview = root / "preview.mp4"
                renderer.render_preview_window(project, str(preview), 0, 1)
                preview_full = _pixels(preview, 0.95, root / "preview-full.png")
                self.assertAlmostEqual(preview_full, full, delta=full * 0.08)

                _command(service, "effect.update", {
                    "clipId": "headline", "effectId": "cutvoke.text",
                    "params": {"animInStyle": "scale", "animLoopStyle": "none",
                               "animOutStyle": "fade"},
                }, 5)
                scaled = root / "scaled.mp4"
                renderer.render(service.get_project("animated-title"), str(scaled),
                                quality="low")
                scale_early = _pixels(scaled, 0.05, root / "scale-early.png")
                scale_full = _pixels(scaled, 0.95, root / "scale-full.png")
                self.assertGreater(scale_full, scale_early * 1.5)

                _command(service, "effect.update", {
                    "clipId": "headline", "effectId": "cutvoke.text",
                    "params": {"animInStyle": "none", "animOutStyle": "none",
                               "animLoopStyle": "blink", "animLoopMs": 400},
                }, 6)
                blinking = root / "blinking.mp4"
                renderer.render(service.get_project("animated-title"), str(blinking),
                                quality="low")
                blink_low = _pixels(blinking, 0.2, root / "blink-low.png", 180)
                blink_high = _pixels(blinking, 0.4, root / "blink-high.png", 180)
                self.assertGreater(blink_high, blink_low * 1.5)


if __name__ == "__main__":
    unittest.main()
