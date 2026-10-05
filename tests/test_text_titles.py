"""Editable title clips are distinct from subtitles and render from the project graph."""

from __future__ import annotations

import tempfile
import subprocess
import re
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from cutvoke.core.caption_render import build_ass
from cutvoke.core.invariants import validate_project
from cutvoke.core.httpapi import HttpApi
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.model import Caption
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditError, EditService
from cutvoke.core.store import ProjectStore


def _rat(seconds: int) -> dict[str, str]:
    return {"num": str(seconds), "den": "1"}


def _command(service: EditService, name: str, payload: dict, serial: int) -> None:
    project = service.get_project("titles")
    service.execute(Command(type=name, payload=payload, command_id=f"title-{serial}",
                            project_id="titles", expected_revision=project.revision,
                            actor=Actor("agent", "title-test")))


class TextTitleTests(unittest.TestCase):
    def test_ass_styles_precede_events_so_libass_uses_title_styling(self) -> None:
        ass = build_ass([
            Caption("styled", "金色标题", Rational.of(0), Rational.of(2),
                    fontSize=64, color="#ffd24a", bold=True),
        ], 1920, 1080)
        self.assertLess(ass.index("Style: C0,"), ass.index("[Events]"))
        self.assertIn("Style: C0,Noto Sans SC,64,&H004AD2FF", ass)

    def test_long_typewriter_cue_keeps_loop_animation_running_and_editable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = str(Path(temporary) / "projects.sqlite")
            with ProjectStore(database) as store:
                service = EditService(store)
                service.create_project("titles", width=320, height=180)
                _command(service, "caption.add", {
                    "captionId": "long-loop", "text": "逐字出现后持续循环的标题",
                    "start": _rat(0), "end": _rat(60),
                }, 1)
                _command(service, "caption.update", {
                    "captionId": "long-loop", "animIn": 5000,
                    "animInStyle": "typewriter", "animLoopStyle": "pulse",
                    "animLoopMs": 300,
                }, 2)
                caption = service.get_project("titles").sequence.captions[0]
                self.assertEqual((caption.animIn, caption.animLoopStyle, caption.animLoopMs),
                                 (5000, "pulse", 300))
                with self.assertRaises(EditError):
                    _command(service, "caption.update", {
                        "captionId": "long-loop", "animLoopStyle": "spin",
                    }, 3)
                with self.assertRaises(EditError):
                    _command(service, "caption.update", {
                        "captionId": "long-loop", "animLoopMs": 200,
                    }, 4)

            with ProjectStore(database) as reopened:
                caption = EditService(reopened).get_project("titles").sequence.captions[0]
                ass = build_ass([caption], 320, 180)
                # At a 300 ms cycle, a 60-second cue needs far more than the old
                # 64-cycle cap to keep its pulse active through the full title.
                loop_ends = [int(end) for _, end in
                             re.findall(r"\\t\((\d+),(\d+),", ass)]
                self.assertGreater(len(loop_ends), 128)
                self.assertGreater(max(loop_ends), 54000)
                self.assertIn("0:01:00.00", ass)

    def test_title_insert_edit_undo_reload_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            background = root / "background.png"
            Image.new("RGB", (320, 180), (20, 30, 60)).save(background)
            with ProjectStore(str(root / "projects.sqlite")) as store:
                service = EditService(store)
                service.create_project("titles", width=320, height=180,
                                       fps=Rational.of(15))
                _command(service, "clip.insert", {
                    "trackId": "video", "createTrackKind": "video",
                    "sourcePath": str(background), "timelineStart": _rat(0),
                    "timelineEnd": _rat(3),
                }, 1)
                revision = service.get_project("titles").revision
                with self.assertRaises(EditError):
                    _command(service, "clip.insert", {
                        "trackId": "titles", "createTrackKind": "text",
                        "text": {"content": "  "}, "timelineStart": _rat(0),
                        "timelineEnd": _rat(2),
                    }, 2)
                self.assertEqual(service.get_project("titles").revision, revision)
                _command(service, "clip.insert", {
                    "trackId": "titles", "createTrackKind": "text", "clipId": "headline",
                    "text": {"content": "可编辑标题", "fontSize": 160,
                             "color": "#ffda45", "bold": True, "x": 0.5, "y": 0.4,
                             "animIn": 300, "animOut": 300},
                    "timelineStart": _rat(0), "timelineEnd": _rat(2),
                }, 3)
                project = service.get_project("titles")
                self.assertTrue(validate_project(project).renderable)
                self.assertEqual(len(project.sequence.captions), 0)
                self.assertEqual(project.sequence.tracks[1].kind, "text")
                self.assertEqual(project.sequence.tracks[1].clips[0].effects[0]["params"]["content"],
                                 "可编辑标题")
                _command(service, "effect.update", {
                    "clipId": "headline", "effectId": "cutvoke.text",
                    "params": {"x": 0.65, "rotation": -10.0},
                }, 4)

            with ProjectStore(str(root / "projects.sqlite")) as reopened:
                service = EditService(reopened)
                project = service.get_project("titles")
                params = project.sequence.tracks[1].clips[0].effects[0]["params"]
                self.assertEqual((params["x"], params["rotation"]), (0.65, -10.0))
                _command(service, "history.undo", {}, 5)
                project = service.get_project("titles")
                self.assertEqual(project.sequence.tracks[1].clips[0].effects[0]["params"]["x"], 0.5)
                _command(service, "history.redo", {}, 6)
                project = service.get_project("titles")
                renderer = RenderService()
                inside = root / "inside.png"
                outside = root / "outside.png"
                renderer.extract_frame(project, 1.0, str(inside))
                renderer.extract_frame(project, 2.5, str(outside))
                with Image.open(inside) as a, Image.open(outside) as b:
                    difference = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
                    self.assertGreater(max(ImageStat.Stat(difference).mean), 0.8)
                report = renderer.render(project, str(root / "export.mp4"))
                self.assertAlmostEqual(report["duration"], 3, delta=0.12)
                api = HttpApi(service, renderer, media_dir=str(root))
                try:
                    preview, _, _ = api._preview_window_file(project, 0)
                    preview_frame = root / "preview-title.png"
                    subprocess.run([
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", "1", "-i", preview, "-frames:v", "1", str(preview_frame),
                    ], check=True, capture_output=True)
                    with Image.open(preview_frame) as a, Image.open(outside) as b:
                        difference = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
                        self.assertGreater(max(ImageStat.Stat(difference).mean), 0.8)
                finally:
                    api.close()
