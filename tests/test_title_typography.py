"""Bundled title fonts and multiline spacing must survive project rendering."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from cutvoke.core.caption_render import build_ass, resolve_title_font
from cutvoke.core.model import Caption
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditError, EditService
from cutvoke.core.store import ProjectStore


def rat(value: int) -> dict[str, str]:
    return {"num": str(value), "den": "1"}


class TitleTypographyTests(unittest.TestCase):
    def test_independent_title_panel_spans_canvas_and_is_editable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            background = root / "background.png"
            Image.new("RGB", (320, 180), (20, 30, 45)).save(background)
            with ProjectStore(str(root / "project.sqlite")) as store:
                service = EditService(store)
                service.create_project("panel", width=320, height=180)

                def apply(name: str, payload: dict) -> None:
                    project = service.get_project("panel")
                    service.execute(Command(
                        type=name, payload=payload,
                        command_id=f"panel-{name}-{project.revision}",
                        project_id="panel", expected_revision=project.revision,
                        actor=Actor("human", "panel-test")))

                apply("clip.insert", {"trackId": "video", "createTrackKind": "video",
                                      "sourcePath": str(background), "timelineStart": rat(0),
                                      "timelineEnd": rat(2)})
                apply("clip.insert", {"trackId": "title", "createTrackKind": "text",
                                      "clipId": "headline", "text": {
                                          "content": "短题", "background": "#d12439",
                                          "panelWidth": 0.8, "panelHeight": 0.2,
                                          "fontSize": 120, "x": 0.5, "y": 0.75,
                                      }, "timelineStart": rat(0), "timelineEnd": rat(2)})
                renderer = RenderService()
                renderer.extract_frame(service.get_project("panel"), 1, str(root / "bar.png"))
                with Image.open(root / "bar.png") as image:
                    self.assertGreater(image.convert("RGB").getpixel((45, 120))[0], 150)
                    self.assertLess(image.convert("RGB").getpixel((10, 120))[0], 50)
                apply("effect.update", {"clipId": "headline", "effectId": "cutvoke.text",
                                        "params": {"panelWidth": 0}})
                renderer.extract_frame(service.get_project("panel"), 1, str(root / "box.png"))
                with Image.open(root / "box.png") as image:
                    self.assertLess(image.convert("RGB").getpixel((45, 120))[0], 50)
            with ProjectStore(str(root / "project.sqlite")) as reopened:
                params = EditService(reopened).get_project("panel").sequence.tracks[1].clips[0].effects[0]["params"]
                self.assertEqual(params["panelWidth"], 0)
                self.assertEqual(params["panelHeight"], 0.2)

    def test_ass_multiline_spacing_uses_two_distinct_positions(self) -> None:
        caption = Caption("title", "第一行\n第二行", Rational.of(0), Rational.of(2),
                          fontSize=120, fontFamily="Noto Serif SC", lineSpacing=1.6,
                          x=0.5, y=0.7)
        ass = build_ass([caption], 320, 180)
        lines = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        self.assertEqual(len(lines), 2)
        self.assertIn("Noto Serif SC", ass)
        self.assertIn("第一行", lines[0])
        self.assertIn("第二行", lines[1])
        self.assertNotEqual(lines[0].split("\\pos(")[1].split(")")[0],
                            lines[1].split("\\pos(")[1].split(")")[0])

    def test_packaged_serif_changes_real_render_and_survives_reload(self) -> None:
        self.assertTrue(Path(resolve_title_font("Noto Sans SC")).is_file())
        self.assertTrue(Path(resolve_title_font("Noto Serif SC")).is_file())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            still = root / "still.png"
            Image.new("RGB", (320, 180), (26, 36, 52)).save(still)
            with ProjectStore(str(root / "project.sqlite")) as store:
                service = EditService(store)
                service.create_project("typography", width=320, height=180)

                def apply(name: str, payload: dict) -> None:
                    project = service.get_project("typography")
                    service.execute(Command(type=name, payload=payload,
                        command_id=f"typography-{name}-{project.revision}",
                        project_id="typography", expected_revision=project.revision,
                        actor=Actor("human", "typography-test")))

                apply("clip.insert", {"trackId": "video", "createTrackKind": "video",
                                      "sourcePath": str(still), "timelineStart": rat(0),
                                      "timelineEnd": rat(2)})
                apply("clip.insert", {"trackId": "title", "createTrackKind": "text",
                                      "clipId": "headline", "text": {
                                          "content": "字形对比\n第二行", "fontSize": 160,
                                          "fontFamily": "Noto Serif SC", "lineSpacing": 1.6,
                                          "scale": 1.3, "x": 0.5, "y": 0.68,
                                      }, "timelineStart": rat(0), "timelineEnd": rat(2)})
                serif_project = service.get_project("typography")
                renderer = RenderService()
                renderer.extract_frame(serif_project, 1, str(root / "serif.png"))
                revision = serif_project.revision
                with self.assertRaises(EditError):
                    apply("effect.update", {"clipId": "headline",
                                            "effectId": "cutvoke.text",
                                            "params": {"fontFamily": "Missing Family"}})
                self.assertEqual(service.get_project("typography").revision, revision)
                apply("effect.update", {"clipId": "headline",
                                        "effectId": "cutvoke.text",
                                        "params": {"fontFamily": "Noto Sans SC"}})
                renderer.extract_frame(service.get_project("typography"), 1,
                                       str(root / "sans.png"))
                with Image.open(root / "serif.png") as serif, Image.open(root / "sans.png") as sans:
                    difference = ImageChops.difference(serif.convert("RGB"), sans.convert("RGB"))
                    self.assertGreater(max(ImageStat.Stat(difference).mean), 0.3)
            with ProjectStore(str(root / "project.sqlite")) as reopened:
                project = EditService(reopened).get_project("typography")
                params = project.sequence.tracks[1].clips[0].effects[0]["params"]
                self.assertEqual(params["fontFamily"], "Noto Sans SC")
                self.assertEqual(params["lineSpacing"], 1.6)


if __name__ == "__main__":
    unittest.main()
