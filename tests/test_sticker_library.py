"""Built-in stickers are distinct editable visual overlays, not flat video images."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
import shutil
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from cutvoke.core.builtin_stickers import PREVIEW_ROOT, _quality, load_builtin_stickers
from cutvoke.core.httpapi import HttpApi
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.resource_pack import install_immutable_resource_file
from cutvoke.core.service import EditError, EditService
from cutvoke.core.store import ProjectStore


def _rat(value: int) -> dict[str, str]:
    return {"num": str(value), "den": "1"}


def _command(service: EditService, project_id: str, name: str, payload: dict,
             serial: int) -> None:
    project = service.get_project(project_id)
    service.execute(Command(type=name, payload=payload, command_id=f"sticker-{serial}",
                            project_id=project_id, expected_revision=project.revision,
                            actor=Actor("agent", "sticker-test")))


class StickerLibraryTests(unittest.TestCase):
    def test_background_texture_uses_canvas_filling_default_scale(self) -> None:
        sticker = next(item for item in load_builtin_stickers()
                       if item["stickerId"] == "cutvoke.sticker.texture_bg_underwater_caustics")
        self.assertEqual(sticker["defaultScale"], 2.22)
        service = EditService()
        project = service.create_project("background-texture-scale", width=320, height=180)
        _command(service, project.project_id, "clip.insert", {
            "trackId": "texture", "createTrackKind": "video",
            "createTrackRole": "sticker", "role": "sticker",
            "clipId": "background-texture", "assetId": sticker["assetId"],
            "sourcePath": sticker["path"], "stickerScale": sticker["defaultScale"],
            "timelineStart": _rat(0), "timelineEnd": _rat(2),
        }, 1)
        clip = service.get_project(project.project_id).sequence.tracks[0].clips[0]
        transform = next(effect["params"] for effect in clip.effects
                         if effect["effectId"] == "cutvoke.transform")
        self.assertEqual(transform["scale"], 2.22)
        self.assertEqual(transform["position"], {"x": -40, "y": -110})

    def test_sticker_favorite_uses_project_command_and_undo(self) -> None:
        sticker_id = load_builtin_stickers()[0]["stickerId"]
        service = EditService()
        project = service.create_project("sticker-favorite")
        _command(service, project.project_id, "resource.favorite", {"stickerId": sticker_id}, 1)
        self.assertEqual(service.get_project(project.project_id).favorites, [sticker_id])
        with self.assertRaises(EditError):
            _command(service, project.project_id, "resource.favorite",
                     {"stickerId": "cutvoke.sticker.missing"}, 2)
        self.assertEqual(service.get_project(project.project_id).favorites, [sticker_id])
        _command(service, project.project_id, "history.undo", {}, 3)
        self.assertEqual(service.get_project(project.project_id).favorites, [])

    def test_catalog_has_real_transparent_categories(self) -> None:
        catalog = load_builtin_stickers()
        self.assertEqual(len(catalog), 612)
        self.assertEqual(len({item["stickerId"] for item in catalog}), 612)
        self.assertEqual(Counter(item["kind"] for item in catalog),
                         {"static": 604, "dynamic": 8})
        self.assertTrue(all(item.get("source") for item in catalog))
        self.assertTrue(all(item.get("version") == (
                                "1.1.1" if item["stickerId"] in {
                                    "cutvoke.sticker.gift", "cutvoke.sticker.gift_float"}
                                else "1.1.0")
                            for item in catalog
                            if item["source"].startswith("OpenAI ImageGen atlas") or
                               item["source"].startswith("CutVoke motion variant")
                               if not item["stickerId"].startswith(("cutvoke.sticker.sports_",
                                                                 "cutvoke.sticker.learn_",
                                                                 "cutvoke.sticker.personfx_",
                                                                 "cutvoke.sticker.fxoverlay_",
                                                                 "cutvoke.sticker.manga_v2_",
                                                                 "cutvoke.sticker.audiofx_",
                                                                 "cutvoke.sticker.weatherfx_",
                                                                 "cutvoke.sticker.inkfx_",
                                                                 "cutvoke.sticker.cinematic_optical_",
                                                                 "cutvoke.sticker.printfx_",
                                                                 "cutvoke.sticker.scrapbook_",
                                                                 "cutvoke.sticker.natural_light_",
                                                                 "cutvoke.sticker.party_",
                                                                 "cutvoke.sticker.guide_",
                                                                 "cutvoke.sticker.promo_",
                                                                 "cutvoke.sticker.energy_"))))
        self.assertGreaterEqual(min(Counter(item["subcategory"] for item in catalog).values()), 8)
        product_promo = [item for item in catalog
                         if item["stickerId"].startswith("cutvoke.sticker.promo_")]
        self.assertEqual(len(product_promo), 16)
        self.assertTrue(all(item["subcategory"] == "产品广告" and
                            item["defaultScale"] == 0.86 and item["version"] == "1.0.0" and
                            item["qualified"] and item["status"] == "approved" and
                            "product-promo-overlay-atlas-20260927.png" in item["source"]
                            for item in product_promo))
        self.assertEqual(Counter(item["subcategory"] for item in catalog)["氛围纹理"], 16)
        self.assertEqual(Counter(item["subcategory"] for item in catalog)["氛围纹理·鲜明"], 16)
        self.assertEqual(Counter(item["subcategory"] for item in catalog)["漫画动感"], 33)
        energy_light = [item for item in catalog
                        if item["stickerId"].startswith("cutvoke.sticker.energy_")]
        self.assertEqual(len(energy_light), 16)
        self.assertTrue(all(item["subcategory"] == "能量光效" and
                            item["defaultScale"] == 0.92 and item["version"] == "1.0.0" and
                            item["qualified"] and item["status"] == "approved" and
                            "energy-light-overlay-atlas-20260927.png" in item["source"]
                            for item in energy_light))
        cinematic_optical = [item for item in catalog
                             if item["stickerId"].startswith(
                                 "cutvoke.sticker.cinematic_optical_")]
        self.assertEqual(len(cinematic_optical), 16)
        self.assertTrue(all(item["subcategory"] == "镜头光效" and
                            item["version"] == "1.0.0" and item["qualified"] and
                            item["status"] == "approved" for item in cinematic_optical))
        pixel_game = [item for item in catalog
                      if item["stickerId"].startswith("cutvoke.sticker.fxoverlay_pixel_")]
        self.assertEqual(len(pixel_game), 16)
        self.assertTrue(all(item["subcategory"] == "特效贴图" and
                            item["defaultScale"] == 0.92 and
                            item["version"] == "1.0.0" and item["qualified"] and
                            item["status"] == "approved" and
                            "pixel-game-effect-atlas-20260926.png" in item["source"]
                            for item in pixel_game))
        ink_overlays = [item for item in catalog
                        if item["stickerId"].startswith("cutvoke.sticker.inkfx_")]
        self.assertEqual(len(ink_overlays), 16)
        self.assertTrue(all(item["subcategory"] == "特效贴图" and
                            item["defaultScale"] == 1.1 and
                            item["version"] == "1.0.0" and item["license"] and
                            item["qualified"] and item["status"] == "approved" and
                            "east-asian-ink-motion-overlay-atlas-20260926.png" in item["source"]
                            for item in ink_overlays))
        self.assertEqual(Counter(item["subcategory"] for item in catalog)["特效贴图"], 95)
        refraction = [item for item in catalog
                      if item["stickerId"].startswith("cutvoke.sticker.fxoverlay_refraction_")]
        self.assertEqual(len(refraction), 16)
        self.assertTrue(all(item["subcategory"] == "特效贴图" and
                            item["defaultScale"] == 0.92 and
                            item["version"] == "1.0.0" and item["license"] and
                            item["qualified"] and item["status"] == "approved" and
                            "glass-liquid-refraction-atlas-20260927.png" in item["source"]
                            for item in refraction))
        weather = [item for item in catalog
                   if item["stickerId"].startswith("cutvoke.sticker.weatherfx_")]
        self.assertEqual(len(weather), 15)
        self.assertTrue(all(item["subcategory"] == "特效贴图" and
                            item["defaultScale"] == 1.1 and item["qualified"] and
                            item["status"] == "approved" and item["license"] and
                            "weather-atmosphere-overlay-atlas-20260926.png" in item["source"]
                            for item in weather))
        manga_v2 = [item for item in catalog
                    if item["stickerId"].startswith("cutvoke.sticker.manga_v2_")]
        self.assertEqual(len(manga_v2), 16)
        self.assertTrue(all(item["subcategory"] == "漫画动感" and item["defaultScale"] == 0.92
                            and item["version"] == "1.0.0" and item["license"]
                            and item["qualified"] and item["status"] == "approved"
                            and "manga-motion-atlas-v2-20260926.png" in item["source"]
                            for item in manga_v2))
        self.assertEqual(Counter(item["subcategory"] for item in catalog)["花草"], 16)
        self.assertEqual(Counter(item["subcategory"] for item in catalog)["材质纹理"], 16)
        self.assertEqual(Counter(item["subcategory"] for item in catalog)["旅行手帐"], 16)
        self.assertEqual(Counter(item["subcategory"] for item in catalog)["美食饮品"], 16)
        self.assertEqual(Counter(item["subcategory"] for item in catalog)["电影叠加"], 30)
        vintage_print = [item for item in catalog
                         if item["stickerId"].startswith("cutvoke.sticker.printfx_")]
        self.assertEqual(len(vintage_print), 16)
        self.assertTrue(all(item["subcategory"] == "电影叠加" and
                            item["defaultScale"] == 0.92 and
                            item["version"] == "1.0.0" and item["license"] and
                            item["status"] == "approved" and item["qualified"] and
                            "vintage-film-print-texture-atlas-20260926.png" in item["source"] and
                            Path(item["previewPath"]).is_file()
                            for item in vintage_print))
        scrapbook_decor = [item for item in catalog
                           if item["stickerId"].startswith("cutvoke.sticker.scrapbook_")]
        self.assertEqual(len(scrapbook_decor), 16)
        self.assertTrue(all(item["subcategory"] == "装饰" and
                            item["defaultScale"] == 0.28 and
                            item["status"] == "approved" and item["qualified"] and
                            item["license"] and
                            "scrapbook-decor-atlas-20260927-v2.png" in item["source"]
                            for item in scrapbook_decor))
        natural_light = [item for item in catalog
                         if item["stickerId"].startswith("cutvoke.sticker.natural_light_")]
        self.assertEqual(len(natural_light), 16)
        self.assertTrue(all(item["subcategory"] == "自然光影" and
                            item["defaultScale"] == 0.92 and
                            item["version"] == "1.0.0" and
                            item["status"] == "approved" and item["qualified"] and
                            item["license"] and
                            "natural-light-shadow-overlay-atlas-20260927.png" in item["source"] and
                            Path(item["previewPath"]).is_file()
                            for item in natural_light))
        celebration = [item for item in catalog
                       if item["stickerId"].startswith("cutvoke.sticker.party_")]
        self.assertEqual(len(celebration), 16)
        self.assertTrue(all(item["subcategory"] == "庆祝派对" and
                            item["defaultScale"] == 0.46 and
                            item["version"] == "1.0.0" and item["license"] and
                            item["status"] == "approved" and item["qualified"] and
                            "celebration-party-sticker-atlas-20260927.png" in item["source"]
                            for item in celebration))
        generated_markers = [item for item in catalog
                             if item["stickerId"].startswith("cutvoke.sticker.marker_doodle_")]
        self.assertEqual(len(generated_markers), 16)
        self.assertTrue(all(item["qualified"] and item["status"] == "approved"
                            for item in generated_markers))
        video_guides = [item for item in catalog
                        if item["stickerId"].startswith("cutvoke.sticker.guide_")]
        self.assertEqual(len(video_guides), 16)
        self.assertTrue(all(item["subcategory"] == "指引" and
                            item["version"] == "1.0.0" and item["license"] and
                            item["qualified"] and item["status"] == "approved" and
                            "video-annotation-guide-sticker-atlas-20260927.png" in item["source"]
                            for item in video_guides))
        self.assertEqual(Counter(item["subcategory"] for item in catalog)["背景纹理"], 16)
        self.assertEqual(Counter(item["subcategory"] for item in catalog)["镜头光效"], 32)
        self.assertEqual(Counter(item["subcategory"] for item in catalog)["宠物日常"], 16)
        sports = [item for item in catalog if item["subcategory"] == "运动"]
        self.assertEqual(len(sports), 16)
        self.assertTrue(all(item["qualified"] and item["status"] == "approved"
                            and item["version"] == "1.0.0"
                            and "sports-sticker-atlas-20260925.png" in item["source"]
                            for item in sports))
        self.assertEqual(sum(item["subcategory"] == "运动" and item["qualified"]
                             for item in catalog), 16)
        education = [item for item in catalog if item["subcategory"] == "学习科普"]
        self.assertEqual(len(education), 16)
        self.assertTrue(all(item["qualified"] and item["status"] == "approved"
                            and item["version"] == "1.0.0"
                            and "education-science-sticker-atlas-20260925-v2.png" in item["source"]
                            and item["license"] for item in education))
        person_fx = [item for item in catalog if item["subcategory"] == "人物氛围"]
        self.assertEqual(len(person_fx), 16)
        self.assertTrue(all(item["qualified"] and item["status"] == "approved"
                            and item["version"] == "1.0.0"
                            and "person-fx-overlay-atlas-20260925.png" in item["source"]
                            and item["license"] for item in person_fx))
        generated_overlays = [item for item in catalog
                              if item["stickerId"].startswith("cutvoke.sticker.fxoverlay_")]
        self.assertEqual(len(generated_overlays), 64)
        self.assertTrue(all(item["subcategory"] == "特效贴图" and
                            item["status"] == "approved" and item["qualified"] and
                            item["version"] == "1.0.0" and item["license"]
                            for item in generated_overlays))
        self.assertEqual(sum("editor-effect-overlay-atlas-20260925.png" in item["source"]
                             for item in generated_overlays), 16)
        self.assertEqual(sum("pixel-game-effect-atlas-20260926.png" in item["source"]
                             for item in generated_overlays), 16)
        self.assertEqual(sum("glass-liquid-refraction-atlas-20260927.png" in item["source"]
                             for item in generated_overlays), 16)
        music_rhythm = [item for item in catalog
                        if item["stickerId"].startswith("cutvoke.sticker.audiofx_")]
        self.assertEqual(len(music_rhythm), 16)
        self.assertTrue(all(item["subcategory"] == "音乐节奏" and
                            item["defaultScale"] == 0.92 and item["qualified"] and
                            item["status"] == "approved" and item["license"] and
                            "music-rhythm-overlay-atlas-20260926.png" in item["source"]
                            for item in music_rhythm))
        digital_glitch = [item for item in catalog if item["subcategory"] == "数码故障"]
        self.assertEqual(len(digital_glitch), 16)
        self.assertTrue(all(item["qualified"] and item["status"] == "approved"
                            and item["license"] == "MIT" and "OpenAI" in item["source"]
                            for item in digital_glitch))
        self.assertEqual(sum(item["subcategory"] == "材质纹理" and item["qualified"]
                             for item in catalog), 16)
        self.assertEqual(sum(item["subcategory"] == "背景纹理" and item["qualified"]
                             for item in catalog), 16)
        self.assertEqual(sum(item["subcategory"] == "镜头光效" and item["qualified"]
                             for item in catalog), 32)
        self.assertEqual(sum(item["subcategory"] == "氛围纹理" and item["qualified"]
                             for item in catalog), 16)
        self.assertEqual(sum(item["subcategory"] == "旅行手帐" and item["qualified"]
                             for item in catalog), 16)
        self.assertEqual(sum(item["subcategory"] == "宠物日常" and item["qualified"]
                             for item in catalog), 16)
        self.assertEqual(sum(item["subcategory"] == "美食饮品" and item["qualified"]
                             for item in catalog), 16)
        for sticker in catalog:
            with Image.open(sticker["path"]) as image:
                self.assertIn("A", image.getbands(), sticker["stickerId"])
                alpha = image.getchannel("A")
                self.assertEqual(alpha.getextrema()[0], 0, sticker["stickerId"])
                self.assertGreater(alpha.getextrema()[1], 0, sticker["stickerId"])
            if sticker["kind"] == "dynamic":
                self.assertTrue(Path(sticker["previewPath"]).is_file(), sticker["stickerId"])
        self.assertEqual(Counter(item["subcategory"] for item in catalog if item["qualified"]),
                         {"指引": 29, "氛围": 17, "标记": 28, "装饰": 28,
                          "反应": 16, "光效纹理": 16, "氛围纹理·鲜明": 16,
                          "氛围纹理": 16, "漫画动感": 33, "花草": 16,
                          "材质纹理": 16, "旅行手帐": 16, "美食饮品": 16,
                          "宠物日常": 16,
                          "电影叠加": 30, "背景纹理": 16, "镜头光效": 32,
                          "数码故障": 16, "运动": 16, "学习科普": 16,
                          "人物氛围": 16, "特效贴图": 95, "音乐节奏": 16,
                          "自然光影": 16, "庆祝派对": 16, "舞台灯光": 16,
                          "产品广告": 16, "能量光效": 16})

    def test_replacement_manga_stickers_keep_high_contrast_ink_on_dark_footage(self) -> None:
        catalog = {item["stickerId"].rsplit(".", 1)[-1]: item
                   for item in load_builtin_stickers()}
        for stem in ("manga_ink_splatter", "manga_motion_arc",
                     "manga_crosshatch_burst", "manga_speed_impact"):
            with self.subTest(sticker=stem), Image.open(catalog[stem]["path"]) as opened:
                image = opened.convert("RGBA")
                pixels = image.get_flattened_data()
                bright = sum(1 for red, green, blue, alpha in pixels
                             if alpha >= 128 and max(red, green, blue) >= 170)
                light_ink = sum(1 for red, green, blue, alpha in pixels
                                if alpha >= 128 and min(red, green, blue) >= 205)
                area = image.width * image.height
                self.assertGreater(bright, area // 10)
                self.assertGreater(light_ink, area // 40)

    def test_approved_sticker_expires_when_artwork_changes(self) -> None:
        source = next(item for item in load_builtin_stickers()
                      if item["stickerId"].endswith("heart_pulse"))
        self.assertTrue(source["qualified"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "heart.png"
            preview = root / "heart_pulse.mp4"
            shutil.copyfile(source["path"], image)
            shutil.copyfile(source["previewPath"], preview)
            for suffix in ("qa.json", "audit.json", "audit.mp4", "visual.json"):
                shutil.copyfile(PREVIEW_ROOT / f"heart_pulse.{suffix}",
                                root / f"heart_pulse.{suffix}")
            item = {**source, "path": str(image), "previewPath": str(preview)}
            with patch("cutvoke.core.builtin_stickers.PREVIEW_ROOT", root):
                _quality(item)
                self.assertTrue(item["qualified"])
                image.write_bytes(image.read_bytes() + b"changed")
                _quality(item)
                self.assertFalse(item["qualified"])
                self.assertEqual(item["status"], "candidate")

    def test_static_sticker_has_real_preview_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            api = HttpApi(EditService(), RenderService(), media_dir=media_dir)
            try:
                for sticker_id in ("cutvoke.sticker.cross",
                                   "cutvoke.sticker.texture_v2_rain_drops",
                                   "cutvoke.sticker.manga_speedline_cobalt",
                                   "cutvoke.sticker.manga_speed_impact",
                                   "cutvoke.sticker.glitch_horizontal_tear",
                                   "cutvoke.sticker.learn_microscope",
                                   "cutvoke.sticker.fxoverlay_cyan_aura_ring",
                                   "cutvoke.sticker.inkfx_bronze_teal_ring",
                                   "cutvoke.sticker.party_balloon_bouquet",
                                   "cutvoke.sticker.party_fireworks"):
                    status, response = api.handle(
                        "GET", f"/api/v1/stickers/{sticker_id}/preview", {})
                    self.assertEqual(status, 200)
                    self.assertTrue(Path(response["__video_path__"]).is_file())
            finally:
                api.close()

    def test_persistent_api_seeds_new_builtin_stickers_as_available_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = ProjectStore(str(root / "projects.sqlite"))
            api = HttpApi(EditService(store=store), RenderService(),
                          media_dir=str(root / "media"))
            try:
                status, response = api.handle("GET", "/api/v1/stickers", {})
                self.assertEqual(status, 200)
                item = next(sticker for sticker in response["stickers"]
                            if sticker["stickerId"] == "cutvoke.sticker.botanical_cherry_blossom")
                self.assertTrue(item["available"])
                self.assertTrue(item["previewAvailable"])
                self.assertEqual(item["resourceRef"]["packId"], "cutvoke.builtin-resources")
                self.assertEqual(item["resourceRef"]["packVersion"], "1.38.1")
                self.assertEqual(len(item["resourceRef"]["sha256"]), 64)
                generated = next(sticker for sticker in response["stickers"]
                                 if sticker["stickerId"] ==
                                 "cutvoke.sticker.marker_doodle_circle")
                self.assertTrue(generated["available"])
                self.assertTrue(generated["previewAvailable"])
                self.assertTrue(generated["qualified"])
                self.assertEqual(generated["status"], "approved")
                video_guides = [sticker for sticker in response["stickers"]
                                if sticker["stickerId"].startswith("cutvoke.sticker.guide_")]
                self.assertEqual(len(video_guides), 16)
                self.assertTrue(all(sticker["available"] and sticker["previewAvailable"]
                                    and sticker["qualified"] and sticker["status"] == "approved"
                                    and sticker["subcategory"] == "指引"
                                    for sticker in video_guides))
                optical = [sticker for sticker in response["stickers"]
                           if sticker["subcategory"] == "镜头光效"]
                self.assertEqual(len(optical), 32)
                self.assertTrue(all(sticker["available"] and sticker["previewAvailable"]
                                    and sticker["qualified"] for sticker in optical))
                person_fx = [sticker for sticker in response["stickers"]
                             if sticker["subcategory"] == "人物氛围"]
                self.assertEqual(len(person_fx), 16)
                self.assertTrue(all(sticker["available"] and sticker["previewAvailable"]
                                    and sticker["qualified"] for sticker in person_fx))
                celebration = [sticker for sticker in response["stickers"]
                               if sticker["subcategory"] == "庆祝派对"]
                self.assertEqual(len(celebration), 16)
                self.assertTrue(all(sticker["available"] and sticker["previewAvailable"]
                                    and sticker["qualified"] and sticker["status"] == "approved"
                                    for sticker in celebration))
                generated_overlays = [sticker for sticker in response["stickers"]
                                      if sticker["stickerId"].startswith(
                                          "cutvoke.sticker.fxoverlay_")]
                self.assertEqual(len(generated_overlays), 64)
                self.assertTrue(all(sticker["available"] and sticker["previewAvailable"]
                                    and sticker["status"] == "approved" and
                                    sticker["qualified"] for sticker in generated_overlays))
                scrapbook_decor = [sticker for sticker in response["stickers"]
                                   if sticker["stickerId"].startswith(
                                       "cutvoke.sticker.scrapbook_")]
                self.assertEqual(len(scrapbook_decor), 16)
                self.assertTrue(all(sticker["available"] and sticker["previewAvailable"]
                                    and sticker["subcategory"] == "装饰" and
                                    sticker["qualified"] for sticker in scrapbook_decor))
                weather = [sticker for sticker in response["stickers"]
                           if sticker["stickerId"].startswith("cutvoke.sticker.weatherfx_")]
                self.assertEqual(len(weather), 15)
                self.assertTrue(all(sticker["available"] and sticker["previewAvailable"]
                                    and sticker["qualified"] for sticker in weather))
                ink_overlays = [sticker for sticker in response["stickers"]
                                if sticker["stickerId"].startswith("cutvoke.sticker.inkfx_")]
                self.assertEqual(len(ink_overlays), 16)
                self.assertTrue(all(sticker["available"] and sticker["previewAvailable"]
                                    and sticker["qualified"] and
                                    sticker["subcategory"] == "特效贴图"
                                    for sticker in ink_overlays))
                pixel_game = [sticker for sticker in generated_overlays
                              if sticker["stickerId"].startswith(
                                  "cutvoke.sticker.fxoverlay_pixel_")]
                self.assertEqual(len(pixel_game), 16)
                self.assertTrue(all(sticker["available"] and sticker["previewAvailable"]
                                    and sticker["qualified"] for sticker in pixel_game))
                sports = [sticker for sticker in response["stickers"]
                          if sticker["subcategory"] == "运动"]
                self.assertEqual(len(sports), 16)
                self.assertTrue(all(sticker["available"] and sticker["previewAvailable"]
                                    and sticker["qualified"] for sticker in sports))
                asset = store.get_asset("builtin_sticker_botanical_cherry_blossom")
                self.assertTrue(asset["builtin"])
                self.assertTrue(Path(asset["path"]).is_file())
                self.assertIn(".builtin-resources", Path(asset["path"]).parts)
            finally:
                api.close()
                store.close()

    def test_saved_project_keeps_its_sticker_when_a_new_pack_version_is_installed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = ProjectStore(str(root / "projects.sqlite"))
            api = HttpApi(EditService(store=store), RenderService(),
                          media_dir=str(root / "media"))
            try:
                status, response = api.handle("GET", "/api/v1/stickers", {})
                self.assertEqual(status, 200)
                sticker = next(item for item in response["stickers"]
                               if item["stickerId"] == "cutvoke.sticker.botanical_cherry_blossom")
                old_asset = store.get_asset(sticker["assetId"])
                project = api.service.create_project("pinned-sticker-version")
                _command(api.service, project.project_id, "clip.insert", {
                    "trackId": "sticker", "createTrackKind": "video",
                    "createTrackRole": "sticker", "role": "sticker",
                    "assetId": sticker["assetId"], "sourcePath": old_asset["path"],
                    "resourceRef": sticker["resourceRef"],
                    "timelineStart": _rat(0), "timelineEnd": _rat(1),
                }, 1)
                original = api.service.get_project(project.project_id).sequence.tracks[0].clips[0]
                original_path = Path(original.asset_ref.source_path)
                original_bytes = original_path.read_bytes()

                replacement = root / "replacement.png"
                replacement.write_bytes(b"new pack image content")
                replacement_bytes = replacement.read_bytes()
                replacement_hash = hashlib.sha256(replacement_bytes).hexdigest()
                replacement_path = install_immutable_resource_file(
                    replacement, root / "media" / ".builtin-resources",
                    pack_id="cutvoke.builtin-resources", pack_version="1.4.0",
                    relative_path="assets/stickers/botanical_cherry_blossom.png",
                    expected_sha256=replacement_hash,
                    expected_size=len(replacement_bytes),
                )
                store.add_asset(
                    asset_id=sticker["assetId"], name="内置·贴纸·樱花枝",
                    path=str(replacement_path), size=len(replacement_bytes), kind="image",
                    duration=None, has_audio=False, builtin=True,
                )

                reopened = api.service.get_project(project.project_id).sequence.tracks[0].clips[0]
                self.assertEqual(reopened.asset_ref.source_path, str(original_path))
                self.assertEqual(reopened.asset_ref.resource_ref, sticker["resourceRef"])
                self.assertEqual(original_path.read_bytes(), original_bytes)
                self.assertNotEqual(original_path, replacement_path)
            finally:
                api.close()
                store.close()

    def test_sticker_api_explains_when_a_cached_resource_is_missing_or_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = ProjectStore(str(root / "projects.sqlite"))
            api = HttpApi(EditService(store=store), RenderService(),
                          media_dir=str(root / "media"))
            try:
                sticker_id = "cutvoke.sticker.botanical_cherry_blossom"
                asset = store.get_asset("builtin_sticker_botanical_cherry_blossom")
                cached_path = Path(asset["path"])
                cached_path.write_bytes(b"damaged resource")
                status, response = api.handle("GET", "/api/v1/stickers", {})
                self.assertEqual(status, 200)
                item = next(entry for entry in response["stickers"]
                            if entry["stickerId"] == sticker_id)
                self.assertFalse(item["available"])
                self.assertEqual(item["downloadState"], "missing")
                self.assertIn("恢复/下载", item["availabilityMessage"])

                cached_path.unlink()
                status, response = api.handle("GET", "/api/v1/stickers", {})
                self.assertEqual(status, 200)
                item = next(entry for entry in response["stickers"]
                            if entry["stickerId"] == sticker_id)
                self.assertFalse(item["available"])
                self.assertTrue(item["availabilityMessage"])
            finally:
                api.close()
                store.close()

    def test_dynamic_sticker_is_editable_and_undoes_as_one_insert(self) -> None:
        sticker = next(item for item in load_builtin_stickers() if item["stickerId"].endswith("heart_pulse"))
        service = EditService()
        project = service.create_project("motion-sticker")
        _command(service, project.project_id, "clip.insert", {
            "trackId": "stickers", "createTrackKind": "video", "createTrackRole": "sticker",
            "role": "sticker", "assetId": sticker["assetId"], "sourcePath": sticker["path"],
            "stickerAnimation": {"effectId": sticker["effectId"], "params": sticker["params"]},
            "timelineStart": _rat(0), "timelineEnd": _rat(2),
        }, 1)
        clip = service.get_project(project.project_id).sequence.tracks[0].clips[0]
        self.assertEqual([item["effectId"] for item in clip.effects],
                         ["cutvoke.transform", "cutvoke.anim.breathe"])
        self.assertEqual(clip.effects[1]["params"], sticker["params"])
        _command(service, project.project_id, "history.undo", {}, 2)
        self.assertEqual(service.get_project(project.project_id).sequence.tracks, [])

    def test_dynamic_sticker_keeps_its_base_transform_during_animation(self) -> None:
        sticker = next(item for item in load_builtin_stickers() if item["stickerId"].endswith("heart_pulse"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            background = root / "background.png"
            Image.new("RGB", (320, 180), (235, 239, 245)).save(background)
            service = EditService()
            project = service.create_project("sticker-transform", width=320, height=180,
                                             fps=Rational.of(15))
            _command(service, project.project_id, "clip.insert", {
                "trackId": "base", "createTrackKind": "video", "sourcePath": str(background),
                "timelineStart": _rat(0), "timelineEnd": _rat(2),
            }, 1)
            _command(service, project.project_id, "clip.insert", {
                "trackId": "stickers", "createTrackKind": "video", "createTrackRole": "sticker",
                "role": "sticker", "clipId": "animated", "assetId": sticker["assetId"],
                "sourcePath": sticker["path"],
                "stickerAnimation": {"effectId": sticker["effectId"], "params": sticker["params"]},
                "timelineStart": _rat(0), "timelineEnd": _rat(2),
            }, 2)
            _command(service, project.project_id, "effect.update", {
                "clipId": "animated", "effectId": "cutvoke.transform",
                "params": {"scale": 0.46, "position": {"x": 119, "y": 49}},
            }, 3)
            rendered = root / "animated.png"
            RenderService().extract_frame(service.get_project(project.project_id), 0.55,
                                          str(rendered))
            with Image.open(rendered) as frame:
                rgb = frame.convert("RGB")
                visible = [(x, y) for y in range(180) for x in range(320)
                           if max(abs(rgb.getpixel((x, y))[channel] - (235, 239, 245)[channel])
                                  for channel in range(3)) > 20]
            self.assertGreater(len(visible), 500)
            self.assertGreater(min(x for x, _ in visible), 80)
            self.assertLess(max(x for x, _ in visible), 240)
            self.assertGreater(min(y for _, y in visible), 30)
            self.assertLess(max(y for _, y in visible), 165)

    def test_sticker_insert_is_one_undo_and_preserves_overlay_identity(self) -> None:
        sticker = next(item for item in load_builtin_stickers() if item["stickerId"].endswith("heart"))
        service = EditService()
        project = service.create_project("sticker")
        _command(service, project.project_id, "clip.insert", {
            "trackId": "stickers", "createTrackKind": "video", "createTrackRole": "sticker",
            "role": "sticker", "assetId": sticker["assetId"], "sourcePath": sticker["path"],
            "timelineStart": _rat(1), "timelineEnd": _rat(3),
        }, 1)
        track = service.get_project(project.project_id).sequence.tracks[0]
        self.assertEqual((track.kind, track.role, track.clips[0].role),
                         ("video", "sticker", "sticker"))
        self.assertEqual(track.clips[0].asset_ref.asset_id, sticker["assetId"])
        self.assertEqual(track.clips[0].effects[0]["effectId"], "cutvoke.transform")
        restored = type(project).from_dict(service.get_project(project.project_id).to_dict())
        self.assertEqual(restored.sequence.tracks[0].clips[0].role, "sticker")
        _command(service, project.project_id, "history.undo", {}, 2)
        self.assertEqual(service.get_project(project.project_id).sequence.tracks, [])

    def test_sticker_preserves_base_video_and_disappears_on_time(self) -> None:
        sticker = next(item for item in load_builtin_stickers() if item["stickerId"].endswith("heart"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            background = root / "background.png"
            Image.new("RGB", (160, 90), (20, 180, 60)).save(background)
            service = EditService()
            project = service.create_project("sticker-render", width=160, height=90,
                                             fps=Rational.of(15))
            _command(service, project.project_id, "clip.insert", {
                "trackId": "base", "createTrackKind": "video",
                "sourcePath": str(background), "timelineStart": _rat(0),
                "timelineEnd": _rat(3),
            }, 1)
            _command(service, project.project_id, "clip.insert", {
                "trackId": "stickers", "createTrackKind": "video", "createTrackRole": "sticker",
                "role": "sticker", "assetId": sticker["assetId"], "sourcePath": sticker["path"],
                "timelineStart": _rat(1), "timelineEnd": _rat(2),
            }, 2)
            current = service.get_project(project.project_id)
            renderer = RenderService()
            colors = {}
            for label, time in (("before", 0.5), ("during", 1.5), ("after", 2.5)):
                target = root / f"{label}.png"
                renderer.extract_frame(current, time, str(target))
                with Image.open(target) as frame:
                    rgb = frame.convert("RGB")
                    colors[label] = (rgb.getpixel((80, 45)), rgb.getpixel((3, 3)))
            for label in colors:
                self.assertGreater(colors[label][1][1], 140, label)
            self.assertGreater(colors["before"][0][1], 140)
            self.assertGreater(colors["after"][0][1], 140)
            self.assertGreater(colors["during"][0][0], colors["during"][0][1] + 30)
            renderer.render(current, str(root / "sticker.mp4"))
            self.assertGreater((root / "sticker.mp4").stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
