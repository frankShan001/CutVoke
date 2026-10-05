"""Imported .cube LUTs are validated, editable, rendered and portable."""

from __future__ import annotations

import json
import io
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from cutvoke.core.effects import EffectParamInvalid, default_registry
from cutvoke.core.httpapi import HttpApi, build_handler
from cutvoke.core.luts import CubeInvalid, validate_cube
from cutvoke.core.projectpack import pack_project, unpack_project
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore


def _cube() -> bytes:
    return ("TITLE \"CutVoke constant teal\"\n"
            "DOMAIN_MIN 0.0 0.0 0.0\n"
            "DOMAIN_MAX 1.0 1.0 1.0\n"
            "LUT_3D_SIZE 2\n" + "0.10 0.75 0.25\n" * 8).encode("utf-8")


def _apply(service: EditService, kind: str, payload: dict, serial: int) -> None:
    project = service.get_project("lut-import")
    service.execute(Command(
        type=kind, payload=payload, command_id=f"lut-import-{serial}",
        project_id=project.project_id, expected_revision=project.revision,
        actor=Actor("agent", "lut-import-test"),
    ))


def _frame(video: Path, at: float, output: Path) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", str(at), "-i", str(video), "-frames:v", "1", str(output)],
                   check=True, capture_output=True, timeout=30)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class LutImportTests(unittest.TestCase):
    def test_http_import_render_and_project_package_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "O'Brien LUT 空格"
            root.mkdir()
            media_dir = root / "managed media"
            database = root / "projects.sqlite"
            source = root / "colors.png"
            Image.new("RGB", (160, 90), (220, 50, 30)).save(source)
            with ProjectStore(str(database)) as store:
                service = EditService(store)
                renderer = RenderService()
                api = HttpApi(service, renderer, media_dir=str(media_dir))
                server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(api))
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{server.server_port}/api/v1/luts?"
                        + urllib.parse.urlencode({"name": "我的暖调.cube"}),
                        data=_cube(), method="POST",
                        headers={"Content-Type": "application/octet-stream"},
                    )
                    with urllib.request.urlopen(request, timeout=10) as response:
                        self.assertEqual(response.status, 201)
                        uploaded = json.load(response)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=10)

                lut_file = Path(uploaded["path"])
                self.assertTrue(lut_file.is_file())
                self.assertEqual(uploaded["edgeSize"], 2)
                self.assertEqual(uploaded["inputColorSpace"], "Rec.709/sRGB")
                self.assertEqual(lut_file.read_bytes(), _cube())
                service.create_project("lut-import", width=160, height=90,
                                       fps=Rational.of(15))
                _apply(service, "clip.insert", {
                    "trackId": "video", "createTrackKind": "video", "clipId": "scene",
                    "sourcePath": str(source),
                    "timelineStart": {"num": "0", "den": "1"},
                    "timelineEnd": {"num": "2", "den": "1"},
                }, 1)
                _apply(service, "effect.add", {
                    "clipId": "scene", "effectId": "cutvoke.fx.lut",
                    "params": {"file": str(lut_file), "name": uploaded["name"]},
                }, 2)
                current = service.get_project("lut-import")
                self.assertEqual(current.sequence.tracks[0].clips[0].effects[0]["params"]["file"],
                                 str(lut_file))
                self.assertEqual(current.sequence.tracks[0].clips[0].effects[0]["params"]["name"],
                                 "我的暖调.cube")
                compare_server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(api))
                compare_thread = threading.Thread(target=compare_server.serve_forever, daemon=True)
                compare_thread.start()
                try:
                    frame_url = (f"http://127.0.0.1:{compare_server.server_port}"
                                 "/api/v1/projects/lut-import/preview-frame?t=0.5")
                    with urllib.request.urlopen(frame_url + "&compareClipId=scene", timeout=10) as response:
                        ungraded = response.read()
                    with urllib.request.urlopen(frame_url, timeout=10) as response:
                        graded = response.read()
                finally:
                    compare_server.shutdown()
                    compare_server.server_close()
                    compare_thread.join(timeout=10)
                with Image.open(io.BytesIO(ungraded)) as image:
                    ungraded_rgb = ImageStat.Stat(image.convert("RGB")).mean
                with Image.open(io.BytesIO(graded)) as image:
                    graded_rgb = ImageStat.Stat(image.convert("RGB")).mean
                self.assertGreater(ungraded_rgb[0], 180)
                self.assertLess(ungraded_rgb[1], 80)
                self.assertLess(graded_rgb[0], 60)
                self.assertGreater(graded_rgb[1], 130)
                self.assertEqual(service.get_project("lut-import").revision, current.revision)
                status, invalid = api.handle("GET", "/api/v1/projects/lut-import/preview-frame",
                                              {"t": "0.5", "compareClipId": "missing"})
                self.assertEqual(status, 400)
                self.assertEqual(invalid["error"]["code"], "INVALID_ARGUMENT")
                preview, export = root / "preview.mp4", root / "export.mp4"
                renderer.render_preview_window(current, str(preview), 0, 1)
                renderer.render(current, str(export), quality="low")
                before, after = root / "preview.png", root / "export.png"
                _frame(preview, 0.5, before)
                _frame(export, 0.5, after)
                with Image.open(before) as image:
                    means = ImageStat.Stat(image.convert("RGB")).mean
                    self.assertLess(means[0], 60)
                    self.assertGreater(means[1], 130)
                    self.assertLess(means[2], 110)
                with Image.open(before) as a, Image.open(after) as b:
                    diff = ImageStat.Stat(ImageChops.difference(a.convert("RGB"),
                                                              b.convert("RGB"))).mean
                    self.assertLess(sum(diff) / 3, 5)

                package = root / "portable.cvkpkg"
                pack_project(current, str(package))
                imported, mapping, warnings = unpack_project(str(package), str(root / "unpacked"))
                self.assertEqual(warnings, [])
                self.assertIn(str(lut_file), mapping)
                moved_lut = Path(imported.sequence.tracks[0].clips[0].effects[0]["params"]["file"])
                self.assertEqual(imported.sequence.tracks[0].clips[0].effects[0]["params"]["name"],
                                 "我的暖调.cube")
                self.assertTrue(moved_lut.is_file())
                self.assertNotEqual(moved_lut, lut_file)
                lut_file.unlink()
                portable_export = root / "portable.mp4"
                renderer.render(imported, str(portable_export), quality="low")
                portable_frame = root / "portable.png"
                _frame(portable_export, 0.5, portable_frame)
                with Image.open(after) as a, Image.open(portable_frame) as b:
                    diff = ImageStat.Stat(ImageChops.difference(a.convert("RGB"),
                                                              b.convert("RGB"))).mean
                    self.assertLess(sum(diff) / 3, 5)
                api.close()

    def test_invalid_cubes_and_missing_files_are_rejected(self) -> None:
        valid = _cube()
        self.assertEqual(validate_cube(valid), 2)
        with self.assertRaises(CubeInvalid):
            validate_cube(valid.replace(b"0.10 0.75 0.25\n", b"", 1))
        with self.assertRaises(CubeInvalid):
            validate_cube(valid.replace(b"0.10 0.75 0.25", b"nan 0.75 0.25", 1))
        with self.assertRaises(CubeInvalid):
            validate_cube(valid.replace(b"LUT_3D_SIZE 2", b"LUT_1D_SIZE 2"))
        with tempfile.TemporaryDirectory() as temporary:
            api = HttpApi(EditService(), media_dir=temporary)
            with self.assertRaises(CubeInvalid):
                api.import_lut("bad.txt", valid)
            with self.assertRaises(EffectParamInvalid):
                default_registry().validate_params("cutvoke.fx.lut", {
                    "file": str(Path(temporary) / "missing.cube")})
            api.close()


if __name__ == "__main__":
    unittest.main()
