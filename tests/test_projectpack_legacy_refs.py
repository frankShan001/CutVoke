"""Portable packages preserve distinct media in older clips without asset IDs."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from cutvoke.core.projectpack import pack_project, unpack_project
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore


class LegacyAssetReferencePackageTests(unittest.TestCase):
    def test_distinct_sources_with_empty_asset_ids_survive_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = [root / "red image.png", root / "blue image.png"]
            Image.new("RGB", (32, 18), "red").save(sources[0])
            Image.new("RGB", (32, 18), "blue").save(sources[1])
            with ProjectStore(str(root / "projects.sqlite")) as store:
                service = EditService(store)
                service.create_project("legacy-package", width=32, height=18,
                                       fps=Rational.of(15))
                for index, source in enumerate(sources):
                    project = service.get_project("legacy-package")
                    service.execute(Command(
                        type="clip.insert",
                        payload={
                            "trackId": "video", "clipId": f"scene-{index}",
                            **({"createTrackKind": "video"} if index == 0 else {}),
                            "sourcePath": str(source),
                            "timelineStart": {"num": str(index), "den": "1"},
                            "timelineEnd": {"num": str(index + 1), "den": "1"},
                        },
                        command_id=f"legacy-package-{index}", project_id=project.project_id,
                        expected_revision=project.revision,
                        actor=Actor("agent", "legacy-package-test"),
                    ))
                project = service.get_project("legacy-package")

            package = root / "project.cutvokepack.zip"
            pack_project(project, str(package))
            with zipfile.ZipFile(package) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual({item["originalPath"] for item in manifest["assets"]},
                                 {str(source) for source in sources})

            imported, mapping, warnings = unpack_project(str(package), str(root / "unpacked"))
            self.assertEqual(warnings, [])
            self.assertEqual(set(mapping), {str(source) for source in sources})
            restored = [clip.asset_ref.source_path
                        for clip in imported.sequence.tracks[0].clips]
            self.assertEqual(len(set(restored)), 2)
            self.assertEqual([Path(path).read_bytes() for path in restored],
                             [source.read_bytes() for source in sources])

            # Simulate an old broken manifest that recorded only the first blank ID.
            broken = root / "old-broken.cutvokepack.zip"
            with zipfile.ZipFile(package) as source_zip, zipfile.ZipFile(broken, "w") as target_zip:
                for name in source_zip.namelist():
                    data = source_zip.read(name)
                    if name == "manifest.json":
                        old_manifest = json.loads(data)
                        old_manifest["assets"] = old_manifest["assets"][:1]
                        data = json.dumps(old_manifest).encode("utf-8")
                    target_zip.writestr(name, data)
            old_project, _, old_warnings = unpack_project(str(broken), str(root / "old-unpacked"))
            self.assertTrue(any(str(sources[1]) in warning for warning in old_warnings))
            self.assertEqual(old_project.sequence.tracks[0].clips[1].asset_ref.source_path,
                             str(sources[1]))


if __name__ == "__main__":
    unittest.main()
