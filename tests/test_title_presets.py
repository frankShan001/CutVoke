"""Built-in text styles create or replace editable title clips atomically."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from cutvoke.core.protocol import Actor, Command
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditError, EditService
from cutvoke.core.store import ProjectStore


def seconds(value: int) -> dict[str, str]:
    return {"num": str(value), "den": "1"}


class TitlePresetTests(unittest.TestCase):
    def test_insert_replace_undo_reload_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            background = root / "background.png"
            Image.new("RGB", (320, 180), (20, 30, 60)).save(background)
            database = str(root / "projects.sqlite")

            def execute(service: EditService, kind: str, payload: dict, serial: int) -> None:
                project = service.get_project("title-presets")
                service.execute(Command(
                    type=kind, payload=payload, command_id=f"title-preset-{serial}",
                    project_id="title-presets", expected_revision=project.revision,
                    actor=Actor("agent", "title-preset-test"),
                ))

            with ProjectStore(database) as store:
                service = EditService(store)
                service.create_project("title-presets", width=320, height=180)
                execute(service, "clip.insert", {
                    "trackId": "video", "createTrackKind": "video", "clipId": "picture",
                    "sourcePath": str(background), "timelineStart": seconds(0),
                    "timelineEnd": seconds(3),
                }, 1)
                execute(service, "clip.insert", {
                    "trackId": "titles", "createTrackKind": "text", "clipId": "headline",
                    "textPresetId": "cutvoke.preset.text.flower_gold",
                    "text": {"content": "真正的标题"},
                    "timelineStart": seconds(0), "timelineEnd": seconds(2),
                }, 2)
                project = service.get_project("title-presets")
                title = project.sequence.tracks[1].clips[0]
                self.assertEqual(title.effects[0]["params"]["content"], "真正的标题")
                self.assertEqual(title.effects[0]["params"]["fontSize"], 148)
                self.assertEqual(title.effects[0]["presetId"], "cutvoke.preset.text.flower_gold")
                self.assertEqual(len(project.sequence.captions), 0)
                before = project.revision
                for index, (kind, payload) in enumerate((
                    ("builtinPreset.apply", {"clipId": "picture", "presetId": "cutvoke.preset.text.flower_neon"}),
                    ("clip.insert", {"trackId": "other", "createTrackKind": "text",
                                     "textPresetId": "cutvoke.preset.filter.noirContrast",
                                     "text": {"content": "不能应用"}}),
                    ("effect.add", {"clipId": "headline", "effectId": "cutvoke.text",
                                    "params": {"content": "重复"}}),
                ), start=3):
                    with self.assertRaises(EditError):
                        execute(service, kind, payload, index)
                    self.assertEqual(service.get_project("title-presets").revision, before)
                execute(service, "builtinPreset.apply", {
                    "clipId": "headline", "presetId": "cutvoke.preset.text.flower_neon",
                }, 6)
                title = service.get_project("title-presets").sequence.tracks[1].clips[0]
                self.assertEqual(len(title.effects), 1)
                self.assertEqual(title.effects[0]["params"]["content"], "真正的标题")
                self.assertEqual(title.effects[0]["params"]["color"], "#30f4ff")
                execute(service, "history.undo", {}, 7)
                self.assertEqual(service.get_project("title-presets").sequence.tracks[1]
                                 .clips[0].effects[0]["params"]["color"], "#ffd24a")
                execute(service, "history.redo", {}, 8)

            with ProjectStore(database) as reopened:
                service = EditService(reopened)
                project = service.get_project("title-presets")
                title = project.sequence.tracks[1].clips[0]
                self.assertEqual(title.effects[0]["params"]["content"], "真正的标题")
                self.assertEqual(title.effects[0]["presetId"], "cutvoke.preset.text.flower_neon")
                renderer = RenderService()
                visible, empty = root / "visible.png", root / "empty.png"
                renderer.extract_frame(project, 1.0, str(visible))
                renderer.extract_frame(project, 2.5, str(empty))
                with Image.open(visible) as a, Image.open(empty) as b:
                    difference = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
                    self.assertGreater(max(ImageStat.Stat(difference).mean), 1.0)
                report = renderer.render(project, str(root / "title.mp4"), quality="low")
                self.assertAlmostEqual(report["duration"], 3, delta=0.1)


if __name__ == "__main__":
    unittest.main()
