"""Circular presets stay round while legacy normalized masks remain ellipses."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from cutvoke.core.protocol import Actor, Command
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class MaskAspectTests(unittest.TestCase):
    def test_default_circular_presets_and_legacy_ellipse_after_reload(self) -> None:
        cases = [None, "circleMask", "centerCutout", "circleAperture"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for size in ((160, 90), (90, 160)):
                for preset in cases:
                    with self.subTest(size=size, preset=preset):
                        stem = f"{size[0]}-{size[1]}-{preset or 'legacy'}"
                        source, database = root / f"{stem}.png", root / f"{stem}.sqlite"
                        Image.new("RGB", size, "white").save(source)
                        with ProjectStore(str(database)) as store:
                            service = EditService(store)
                            service.create_project("mask-aspect", width=size[0], height=size[1])

                            def edit(kind: str, payload: dict, suffix: str) -> None:
                                project = service.get_project("mask-aspect")
                                service.execute(Command(
                                    type=kind, payload=payload, command_id=f"{stem}-{suffix}",
                                    project_id=project.project_id, expected_revision=project.revision,
                                    actor=Actor("agent", "mask-aspect-test")))

                            edit("clip.insert", {
                                "trackId": "visual", "createTrackKind": "video",
                                "clipId": "source", "sourcePath": str(source),
                                "timelineStart": {"num": "0", "den": "1"},
                                "timelineEnd": {"num": "1", "den": "1"},
                            }, "insert")
                            if preset:
                                edit("builtinPreset.apply", {
                                    "clipId": "source", "presetId": f"cutvoke.preset.fx.{preset}",
                                }, "preset")
                            else:
                                edit("effect.add", {
                                    "clipId": "source", "effectId": "cutvoke.fx.mask",
                                    "params": {"shape": "circle", "x": 0.5, "y": 0.5,
                                               "w": 0.6, "h": 0.6, "feather": 0},
                                }, "mask")
                        with ProjectStore(str(database)) as reopened:
                            project = EditService(reopened).get_project("mask-aspect")
                            frame = root / f"{stem}-mask.png"
                            RenderService().extract_still(project, 0.5, str(frame), alpha=True)
                        with Image.open(frame) as image:
                            alpha = image.convert("RGBA").getchannel("A")
                            if preset == "centerCutout":
                                alpha = alpha.point(lambda value: 255 - value)
                            box = alpha.point(lambda value: 255 if value >= 128 else 0).getbbox()
                            self.assertIsNotNone(box, "mask must have a visible area")
                            width, height = box[2] - box[0], box[3] - box[1]
                            if preset:
                                self.assertLessEqual(abs(width - height), 2,
                                                     "named circle must be round in pixel coordinates")
                            else:
                                self.assertGreater(abs(width - height), 30,
                                                   "old bounds keep their independent percentages")


if __name__ == "__main__":
    unittest.main()
