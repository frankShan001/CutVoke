"""Built-in resource pack inventory must pin its offline media to file hashes."""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cutvoke.core import resource_pack
from cutvoke.core.httpapi import HttpApi, build_handler
from cutvoke.core.resource_pack import (
    MANIFEST_PATH,
    PACKAGE_ROOT,
    install_immutable_resource_file,
    active_resource_pack_root,
    install_resource_pack_archive,
    resource_pack_manager_status,
    resource_pack_summary,
)
from cutvoke.core.service import EditService
from scripts.build_builtin_resource_pack import build_manifest


class ResourcePackManifestTests(unittest.TestCase):
    def test_inventory_covers_current_presets_and_stickers_with_verified_media(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(manifest, build_manifest())
        self.assertEqual(manifest["packId"], "cutvoke.builtin-resources")
        self.assertEqual(manifest["version"], "1.38.1")
        resources = manifest["resources"]
        presets = [item for item in resources if item["kind"] == "preset"]
        self.assertEqual(len(presets), 189)
        self.assertEqual(sum(item["qualified"] for item in presets), 189)
        person_fx_presets = [item for item in presets if item["family"] == "personFx"]
        self.assertEqual(len(person_fx_presets), 24)
        self.assertTrue(all(item["status"] == "approved" and item["qualified"]
                            for item in person_fx_presets))
        self.assertEqual(sum(item["kind"] == "sticker" for item in resources), 612)
        energy_light = [item for item in resources
                        if "energy-light-overlay-atlas-20260927.png"
                        in item.get("source", "")]
        self.assertEqual(len(energy_light), 16)
        self.assertTrue(all(item["subcategory"] == "能量光效" and
                            item["defaultScale"] == 0.92 and item["qualified"] and
                            item["status"] == "approved" and item["license"] and
                            any(path.endswith(".visual.json") for path in item["mediaFiles"])
                            for item in energy_light))
        material_backgrounds = [item for item in resources
                                if item.get("subcategory") == "标题卡材质背景"]
        self.assertEqual(len(material_backgrounds), 16)
        self.assertTrue(all(
            item["kind"] == "background" and item["status"] == "approved" and
            item["license"] and "video-background-material-atlas-20260927.png"
            in item["source"] and any(path.endswith(".jpg") for path in item["mediaFiles"])
            for item in material_backgrounds))
        celebration = [item for item in resources
                       if "celebration-party-sticker-atlas-20260927.png"
                       in item.get("source", "")]
        self.assertEqual(len(celebration), 16)
        self.assertTrue(all(item["subcategory"] == "庆祝派对" and
                            item["defaultScale"] == 0.46 and item["qualified"] and
                            item["status"] == "approved" and item["license"] and
                            any(path.endswith(".visual.json") for path in item["mediaFiles"])
                            for item in celebration))
        product_promo = [item for item in resources
                         if "product-promo-overlay-atlas-20260927.png"
                         in item.get("source", "")]
        self.assertEqual(len(product_promo), 16)
        self.assertTrue(all(item["subcategory"] == "产品广告" and
                            item["defaultScale"] == 0.86 and item["qualified"] and
                            item["status"] == "approved" and item["license"] and
                            any(path.endswith(".visual.json") for path in item["mediaFiles"])
                            for item in product_promo))
        video_guides = [item for item in resources
                        if "video-annotation-guide-sticker-atlas-20260927.png"
                        in item.get("source", "")]
        self.assertEqual(len(video_guides), 16)
        self.assertTrue(all(item["subcategory"] == "指引" and item["qualified"] and
                            item["status"] == "approved" and item["license"] and
                            any(path.endswith(".visual.json") for path in item["mediaFiles"])
                            for item in video_guides))
        music_rhythm = [item for item in resources if item.get("subcategory") == "音乐节奏"]
        self.assertEqual(len(music_rhythm), 16)
        self.assertTrue(all(item["qualified"] and item["status"] == "approved" and
                            item["license"] and "music-rhythm-overlay-atlas-20260926.png"
                            in item.get("source", "") and
                            any(path.endswith(".visual.json") for path in item["mediaFiles"])
                            for item in music_rhythm))
        manga_motion = [item for item in resources
                        if item.get("subcategory") == "漫画动感" and
                        "manga-motion-atlas-v2-20260926.png" in item.get("source", "")]
        self.assertEqual(len(manga_motion), 16)
        self.assertTrue(all(item["qualified"] and item["status"] == "approved" and
                            item["license"] and any(path.endswith(".visual.json")
                                                     for path in item["mediaFiles"])
                            for item in manga_motion))
        backgrounds = [item for item in resources if item["kind"] == "background"]
        self.assertEqual(len(backgrounds), 42)
        self.assertTrue(all(item["name"].startswith("内置·背景·") and
                            len(item["mediaFiles"]) == 1 and
                            item["mediaFiles"][0].startswith("assets/backgrounds/")
                            for item in backgrounds))
        generated_texture_backgrounds = [item for item in backgrounds
                                         if item["resourceId"].startswith(
                                             "builtin_background_bgfx_")]
        self.assertEqual(len(generated_texture_backgrounds), 16)
        self.assertTrue(all(item["subcategory"] == "氛围纹理" and
                            "AI-generated" in item["license"] and
                            "cinematic-effect-texture-atlas-20260927.png" in item["source"] and
                            "extraction evidence" in item["source"] and
                            item["mediaFiles"][0].endswith(".jpg")
                            for item in generated_texture_backgrounds))
        education = [item for item in resources if item.get("subcategory") == "学习科普"]
        self.assertEqual(len(education), 16)
        self.assertTrue(all(item["qualified"] and item.get("source") for item in education))
        person_fx = [item for item in resources if item.get("subcategory") == "人物氛围"]
        self.assertEqual(len(person_fx), 16)
        self.assertTrue(all(item["qualified"] and "person-fx-overlay-atlas-20260925.png"
                            in item.get("source", "") for item in person_fx))
        generated_overlays = [item for item in resources
                              if "editor-effect-overlay-atlas-20260925.png"
                              in item.get("source", "")]
        self.assertEqual(len(generated_overlays), 16)
        self.assertTrue(all(item["status"] == "approved" and item["qualified"]
                            and item["license"] and
                            any(path.endswith(".visual.json")
                                for path in item["mediaFiles"])
                            for item in generated_overlays))
        self.assertEqual({item["subcategory"] for item in generated_overlays}, {"特效贴图"})
        cinematic_optical = [item for item in resources
                             if "cinematic-optical-overlay-atlas-20260926.png"
                             in item.get("source", "")]
        self.assertEqual(len(cinematic_optical), 16)
        self.assertTrue(all(item["subcategory"] == "镜头光效" and
                            item["status"] == "approved" and item["qualified"] and
                            item["license"] and
                            any(path.endswith(".visual.json") for path in item["mediaFiles"])
                            for item in cinematic_optical))
        vintage_print_candidates = [item for item in resources
                                    if "vintage-film-print-texture-atlas-20260926.png"
                                    in item.get("source", "")]
        self.assertEqual(len(vintage_print_candidates), 16)
        self.assertTrue(all(item["subcategory"] == "电影叠加" and
                            item["status"] == "approved" and item["qualified"] and
                            item["license"] and item.get("defaultScale") == 0.92 and
                            any(path.endswith(".visual.json") for path in item["mediaFiles"])
                            for item in vintage_print_candidates))
        scrapbook_decor = [item for item in resources
                           if "scrapbook-decor-atlas-20260927-v2.png" in
                           item.get("source", "")]
        self.assertEqual(len(scrapbook_decor), 16)
        self.assertTrue(all(item["subcategory"] == "装饰" and
                            item["defaultScale"] == 0.28 and
                            item["status"] == "approved" and item["qualified"] and
                            item["license"] and
                            any(path.endswith(".visual.json") for path in item["mediaFiles"])
                            for item in scrapbook_decor))
        natural_light = [item for item in resources
                         if "natural-light-shadow-overlay-atlas-20260927.png" in
                         item.get("source", "")]
        self.assertEqual(len(natural_light), 16)
        self.assertTrue(all(item["subcategory"] == "自然光影" and
                            item["defaultScale"] == 0.92 and
                            item["status"] == "approved" and item["qualified"] and
                            item["license"] and
                            any(path.endswith(".visual.json") for path in item["mediaFiles"])
                            for item in natural_light))
        pixel_game_overlays = [item for item in resources
                               if "pixel-game-effect-atlas-20260926.png"
                               in item.get("source", "")]
        self.assertEqual(len(pixel_game_overlays), 16)
        self.assertTrue(all(item["subcategory"] == "特效贴图" and item["qualified"]
                            and item["status"] == "approved" and item["license"]
                            and any(path.endswith(".visual.json")
                                    for path in item["mediaFiles"])
                            for item in pixel_game_overlays))
        weather_overlays = [item for item in resources
                            if "weather-atmosphere-overlay-atlas-20260926.png"
                            in item.get("source", "")]
        self.assertEqual(len(weather_overlays), 15)
        self.assertTrue(all(item["subcategory"] == "特效贴图" and item["qualified"]
                            and item["status"] == "approved" and item["license"]
                            and item.get("defaultScale") == 1.1 and
                            any(path.endswith(".visual.json") for path in item["mediaFiles"])
                            for item in weather_overlays))
        ink_overlays = [item for item in resources
                        if "east-asian-ink-motion-overlay-atlas-20260926.png"
                        in item.get("source", "")]
        self.assertEqual(len(ink_overlays), 16)
        self.assertTrue(all(item["subcategory"] == "特效贴图" and item["qualified"]
                            and item["status"] == "approved" and item["license"]
                            and item.get("defaultScale") == 1.1 and
                            any(path.endswith(".visual.json") for path in item["mediaFiles"])
                            for item in ink_overlays))
        glitches = [item for item in resources if item.get("subcategory") == "数码故障"]
        self.assertEqual(len(glitches), 16)
        self.assertTrue(all(item["qualified"] and item.get("source") for item in glitches))
        backgrounds = [item for item in resources if item.get("subcategory") == "背景纹理"]
        self.assertEqual(len(backgrounds), 16)
        self.assertTrue(all(item.get("source") for item in backgrounds))
        self.assertTrue(all(item["version"] and item["license"] for item in resources))
        self.assertTrue(all(item.get("source") for item in resources))
        self.assertTrue(all(item["qualified"] for item in presets))
        self.assertTrue(all(item["qualified"] for item in resources
                            if item["kind"] == "sticker" and
                            item not in generated_overlays and
                            item not in vintage_print_candidates))
        preset_resources = [item for item in resources if item["kind"] == "preset"]
        self.assertTrue(all(item["source"] for item in preset_resources))
        self.assertTrue(all("builtin_presets.json" in item["source"]
                            for item in preset_resources if item["family"] != "personFx"))
        self.assertTrue(all(item["source"].startswith("CutVoke")
                            for item in person_fx_presets))
        self.assertTrue(all(item["offlineAvailable"] for item in resources))

        for entry in manifest["files"]:
            path = (PACKAGE_ROOT / entry["path"]).resolve()
            self.assertTrue(path.is_relative_to(PACKAGE_ROOT.resolve()))
            content = path.read_bytes()
            self.assertEqual(len(content), entry["sizeBytes"], entry["path"])
            self.assertEqual(hashlib.sha256(content).hexdigest(), entry["sha256"], entry["path"])

    def test_api_summary_reports_current_pack_and_offline_media_state(self) -> None:
        summary = resource_pack_summary()
        self.assertEqual(summary["version"], "1.38.1")
        self.assertEqual(summary["resourceCount"], 843)
        self.assertEqual(summary["presetCount"], 189)
        self.assertEqual(summary["stickerCount"], 612)
        self.assertEqual(summary["backgroundCount"], 42)
        self.assertEqual(summary["provenanceCoverage"]["licenseDeclaredCount"], 843)
        self.assertEqual(summary["provenanceCoverage"]["sourceDeclaredCount"], 843)
        self.assertEqual(summary["provenanceCoverage"]["completeCount"], 843)
        self.assertEqual(summary["provenanceCoverage"]["incompleteCount"], 0)
        self.assertTrue(summary["offlineAvailable"])
        self.assertEqual(summary["missingFiles"], [])
        self.assertEqual(len(summary["manifestSha256"]), 64)

    def test_summary_reports_files_missing_from_an_offline_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps({
                "schemaVersion": 1,
                "packId": "test.resources",
                "version": "0.0.1",
                "resources": [],
                "files": [{"path": "assets/missing.png", "sizeBytes": 4}],
            }), encoding="utf-8")
            with patch.object(resource_pack, "MANIFEST_PATH", manifest_path), \
                    patch.object(resource_pack, "PACKAGE_ROOT", root):
                summary = resource_pack.resource_pack_summary()
            self.assertFalse(summary["offlineAvailable"])
            self.assertEqual(summary["missingFiles"], ["assets/missing.png"])
            self.assertEqual(summary["invalidFiles"], [])

    def test_summary_detects_same_size_corruption_by_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = root / "assets" / "sample.png"
            media.parent.mkdir()
            media.write_bytes(b"bad!")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps({
                "schemaVersion": 1,
                "packId": "test.resources",
                "version": "0.0.1",
                "resources": [],
                "files": [{
                    "path": "assets/sample.png", "sizeBytes": 4,
                    "sha256": hashlib.sha256(b"good").hexdigest(),
                }],
            }), encoding="utf-8")
            with patch.object(resource_pack, "MANIFEST_PATH", manifest_path), \
                    patch.object(resource_pack, "PACKAGE_ROOT", root):
                summary = resource_pack.resource_pack_summary()
            self.assertFalse(summary["offlineAvailable"])
            self.assertEqual(summary["missingFiles"], [])
            self.assertEqual(summary["invalidFiles"], ["assets/sample.png"])

    def test_installing_a_new_pack_version_keeps_the_previous_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            cache = root / "resources"
            source.write_bytes(b"sticker version one")
            first_bytes = source.read_bytes()
            first_hash = hashlib.sha256(first_bytes).hexdigest()
            first_path = install_immutable_resource_file(
                source, cache, pack_id="cutvoke.builtin-resources",
                pack_version="1.3.0", relative_path="assets/stickers/sample.png",
                expected_sha256=first_hash, expected_size=len(first_bytes),
            )

            source.write_bytes(b"sticker version two")
            second_bytes = source.read_bytes()
            second_hash = hashlib.sha256(second_bytes).hexdigest()
            second_path = install_immutable_resource_file(
                source, cache, pack_id="cutvoke.builtin-resources",
                pack_version="1.4.0", relative_path="assets/stickers/sample.png",
                expected_sha256=second_hash, expected_size=len(second_bytes),
            )

            self.assertNotEqual(first_path, second_path)
            self.assertEqual(first_path.read_bytes(), first_bytes)
            self.assertEqual(second_path.read_bytes(), second_bytes)

    def test_immutable_install_rejects_paths_outside_the_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            source.write_bytes(b"verified")
            with self.assertRaisesRegex(ValueError, "resource pack path"):
                install_immutable_resource_file(
                    source, root / "resources", pack_id="test.resources",
                    pack_version="1.0.0", relative_path="../escape.png",
                    expected_sha256=hashlib.sha256(b"verified").hexdigest(),
                )

    @staticmethod
    def _small_pack_archive(path: Path, *, corrupt_sticker: bool = False,
                            add_traversal: bool = False,
                            include_visual: bool = True) -> None:
        files = {
            "core/builtin_presets.json": json.dumps({"schemaVersion": 1, "presets": []}).encode(),
            "core/builtin_stickers.json": json.dumps({
                "schemaVersion": 1, "version": "1.0.0", "license": "CC0-1.0",
                "stickers": [{"stem": "sample", "name": "Sample", "subcategory": "decor",
                              "keywords": []}],
                "motionVariants": [],
            }).encode(),
            "assets/stickers/sample.png": b"sample-png",
            "assets/sticker_previews/sample.mp4": b"sample-preview",
            "assets/sticker_previews/sample.qa.json": b"{}",
            "assets/sticker_previews/sample.audit.json": b"{}",
            "assets/sticker_previews/sample.audit.mp4": b"sample-audit",
            "assets/sticker_previews/sample.visual.json": b"{}",
        }
        if not include_visual:
            del files["assets/sticker_previews/sample.visual.json"]
        records = [{"path": name, "sizeBytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "mediaType": "application/octet-stream"}
                   for name, content in sorted(files.items())]
        manifest = {
            "schemaVersion": 1,
            "packId": "test.resources",
            "version": "1.0.0",
            "resources": [{
                "resourceId": "cutvoke.sticker.sample", "kind": "sticker",
                "name": "Sample", "family": "sticker", "subcategory": "decor",
                "version": "1.0.0", "license": "CC0-1.0", "status": "candidate",
                "qualified": False, "downloadState": "bundled",
                "mediaFiles": [name for name in files if name.startswith("assets/")],
                "offlineAvailable": True,
            }],
            "files": records,
        }
        if corrupt_sticker:
            files["assets/stickers/sample.png"] = b"tampered!!"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest).encode())
            for name, content in files.items():
                archive.writestr(name, content)
            if add_traversal:
                archive.writestr("../escape.txt", b"outside")

    def test_offline_pack_install_activation_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "resources.zip"
            self._small_pack_archive(archive)
            installed = install_resource_pack_archive(archive.read_bytes(), root / "media")
            self.assertTrue(installed["offlineAvailable"])
            self.assertEqual((installed["packId"], installed["version"]),
                             ("test.resources", "1.0.0"))
            self.assertEqual(installed["provenanceCoverage"]["licenseDeclaredCount"], 1)
            self.assertEqual(installed["provenanceCoverage"]["sourceDeclaredCount"], 0)
            self.assertEqual(installed["provenanceCoverage"]["incompleteCount"], 1)

            status = resource_pack_manager_status(root / "media")
            self.assertEqual(len(status["installed"]), 2)
            self.assertFalse(status["canRollback"])
            api = HttpApi(EditService(), media_dir=str(root / "media"))
            self.addCleanup(api.close)
            code, status = api.handle("GET", "/api/v1/resource-packs", {})
            self.assertEqual(code, 200)
            self.assertEqual(status["active"]["packId"], "cutvoke.builtin-resources")
            code, status = api.handle("POST", "/api/v1/resource-packs/activate", {
                "packId": "test.resources", "version": "1.0.0",
            })
            self.assertEqual(code, 200)
            self.assertEqual(status["active"]["packId"], "test.resources")
            self.assertEqual(active_resource_pack_root(root / "media"),
                             root / "media" / ".resource-packs" / "test.resources" / "1.0.0")
            code, presets = api.handle("GET", "/api/v1/presets", {})
            self.assertEqual(code, 200)
            self.assertEqual(presets["count"], 0)
            self.assertEqual(presets["resourcePack"]["packId"], "test.resources")

            active_resource_pack_root(root / "media").joinpath(
                "assets", "stickers", "sample.png").write_bytes(b"corrupt-png")
            self.assertEqual(active_resource_pack_root(root / "media"), PACKAGE_ROOT)
            code, presets = api.handle("GET", "/api/v1/presets", {})
            self.assertEqual(code, 200)
            self.assertEqual(presets["count"], 189)

            code, status = api.handle("POST", "/api/v1/resource-packs/rollback", {})
            self.assertEqual(code, 200)
            self.assertEqual(status["active"]["packId"], "cutvoke.builtin-resources")
            self.assertEqual(active_resource_pack_root(root / "media"), PACKAGE_ROOT)
            self.assertFalse(resource_pack_manager_status(root / "media")["canRollback"])

    def test_candidate_pack_can_install_without_a_visual_approval_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "candidate-pack.zip"
            self._small_pack_archive(archive, include_visual=False)
            installed = install_resource_pack_archive(archive.read_bytes(), root / "media")
            with zipfile.ZipFile(archive) as packed:
                manifest = json.loads(packed.read("manifest.json"))
            sticker = next(item for item in manifest["resources"]
                           if item["kind"] == "sticker")
            self.assertTrue(installed["offlineAvailable"])
            self.assertEqual(sticker["status"], "candidate")
            self.assertFalse(sticker["qualified"])
            self.assertNotIn("assets/sticker_previews/sample.visual.json",
                             sticker["mediaFiles"])

    def test_install_rejects_corrupt_files_and_zip_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corrupt = root / "corrupt.zip"
            self._small_pack_archive(corrupt, corrupt_sticker=True)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                install_resource_pack_archive(corrupt.read_bytes(), root / "media")

            traversal = root / "traversal.zip"
            self._small_pack_archive(traversal, add_traversal=True)
            with self.assertRaisesRegex(ValueError, "invalid file path"):
                install_resource_pack_archive(traversal.read_bytes(), root / "media")

    def test_http_upload_route_installs_an_offline_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "resource-pack.zip"
            self._small_pack_archive(archive_path)
            api = HttpApi(EditService(), media_dir=str(root / "media"))
            server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(api))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                endpoint = f"http://127.0.0.1:{server.server_address[1]}"
                request = Request(
                    endpoint + "/api/v1/resource-packs/install",
                    data=archive_path.read_bytes(), method="POST",
                    headers={"Content-Type": "application/zip"},
                )
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 201)
                    installed = json.loads(response.read())
                self.assertEqual(installed["packId"], "test.resources")
                with urlopen(endpoint + "/api/v1/resource-packs", timeout=5) as response:
                    status = json.loads(response.read())
                self.assertTrue(any(item["packId"] == "test.resources"
                                    for item in status["installed"]))
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)
                api.close()

    def test_http_upload_route_rejects_corrupt_zip_and_recovers_for_valid_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid_archive = root / "resource-pack.zip"
            self._small_pack_archive(valid_archive)
            api = HttpApi(EditService(), media_dir=str(root / "media"))
            server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(api))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                endpoint = f"http://127.0.0.1:{server.server_address[1]}"
                invalid_request = Request(
                    endpoint + "/api/v1/resource-packs/install",
                    data=b"this is not a zip archive", method="POST",
                    headers={"Content-Type": "application/zip"},
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(invalid_request, timeout=5)
                self.assertEqual(raised.exception.code, 422)
                error = json.loads(raised.exception.read())
                self.assertEqual(error["error"]["code"], "RESOURCE_PACKAGE_INVALID")
                self.assertIn("zip", error["error"]["message"].lower())

                manager_root = root / "media" / ".resource-packs"
                self.assertEqual(list(manager_root.glob(".staging-*")), [])
                with urlopen(endpoint + "/api/v1/resource-packs", timeout=5) as response:
                    status = json.loads(response.read())
                self.assertEqual(len(status["installed"]), 1)
                self.assertEqual(status["active"]["packId"], "cutvoke.builtin-resources")

                valid_request = Request(
                    endpoint + "/api/v1/resource-packs/install",
                    data=valid_archive.read_bytes(), method="POST",
                    headers={"Content-Type": "application/zip"},
                )
                with urlopen(valid_request, timeout=5) as response:
                    self.assertEqual(response.status, 201)
                    installed = json.loads(response.read())
                self.assertEqual(installed["packId"], "test.resources")
                self.assertTrue(installed["offlineAvailable"])
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)
                api.close()


if __name__ == "__main__":
    unittest.main()
