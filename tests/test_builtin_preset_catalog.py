"""Built-in preset audit must not turn effect registrations into qualified content."""

from __future__ import annotations

import tempfile
import unittest
import json
import hashlib
from pathlib import Path

from cutvoke.core.effects import EffectRegistry
from cutvoke.core.httpapi import HttpApi
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.preset_catalog import CHECKS, PresetCatalog, PresetInvalid
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore


class BuiltinPresetCatalogTests(unittest.TestCase):
    def test_reviewed_presets_pass_the_qualification_gate(self) -> None:
        catalog = PresetCatalog.builtin()
        items = catalog.all()
        self.assertEqual(len(items), 189)
        self.assertEqual(len({item.id for item in items}), len(items))
        self.assertTrue(all(item.source for item in items))
        self.assertTrue(all("builtin_presets.json" in item.source
                            for item in items if item.family != "personFx"))
        self.assertEqual({family: sum(item.family == family for item in items)
                          for family in ("transition", "animation", "fx", "filter", "text",
                                         "personFx")},
                         {"transition": 41, "animation": 32, "fx": 36, "filter": 24,
                          "text": 32, "personFx": 24})
        self.assertEqual({category: sum(item.family == "text" and item.subcategory == category
                                        for item in items)
                          for category in ("简约", "花字", "信息条", "文字动画")},
                         {"简约": 8, "花字": 8, "信息条": 8, "文字动画": 8})
        self.assertEqual({slot: sum(item.family == "animation" and
                                    item.subcategory == slot for item in items)
                          for slot in ("入场", "出场", "循环", "组合")},
                         {"入场": 8, "出场": 8, "循环": 8, "组合": 8})
        compound = next(item for item in items if item.id == "cutvoke.preset.filter.noirContrast")
        self.assertEqual(compound.subcategory, "黑白")
        self.assertEqual([item["effectId"] for item in compound.effects],
                         ["cutvoke.fx.grayscale", "cutvoke.fx.curves"])
        self.assertEqual(compound.to_dict()["effects"][1]["effectId"], "cutvoke.fx.curves")
        self.assertTrue(all(item._exists(item.cover)
                            and item._exists(item.motion_preview) for item in items))
        self.assertTrue(all(item._exists(item.evidence["cover"])
                            and item._exists(item.evidence["motionPreview"])
                            for item in items))
        self.assertTrue(all(item.checks["cover"] and item.checks["motionPreview"]
                            for item in items))
        self.assertEqual(sum(item.qualified for item in items), 189)
        person_fx = [item for item in items if item.family == "personFx"]
        self.assertEqual(len(person_fx), 24)
        self.assertTrue(all(item.status == "approved" and item.qualified
                            and item.source.startswith("CutVoke") for item in person_fx))
        self.assertEqual(sum(item.qualified and item.family == "transition" and
                             item.subcategory == "叠化" for item in items), 8)
        self.assertEqual(sum(item.qualified and item.family == "transition" and
                             item.subcategory == "运镜" for item in items), 8)
        self.assertEqual(sum(item.qualified and item.family == "transition" and
                             item.subcategory == "故障" for item in items), 9)
        self.assertEqual(sum(item.family == "transition" and item.subcategory == "模糊"
                             and not item.qualified for item in items), 0)
        self.assertEqual(sum(item.qualified and item.family == "fx" and
                             item.subcategory == "构图" for item in items), 10)
        self.assertEqual(sum(item.qualified and item.family == "fx" and
                             item.subcategory == "光影" for item in items), 8)
        self.assertEqual(sum(item.qualified and item.family == "fx" and
                             item.subcategory == "基础" for item in items), 8)
        self.assertEqual(sum(item.qualified and item.family == "fx" and
                             item.subcategory == "动感" for item in items), 10)
        self.assertEqual({(item.family, item.subcategory) for item in items if item.qualified},
                         {("transition", "叠化"), ("transition", "擦除"), ("transition", "运镜"),
                          ("transition", "模糊"), ("transition", "故障"),
                          ("animation", "入场"),
                          ("animation", "出场"), ("animation", "循环"),
                          ("animation", "组合"), ("filter", "风格"),
                          ("filter", "黑白"), ("filter", "胶片"),
                          ("fx", "基础"), ("fx", "动感"),
                          ("fx", "光影"), ("fx", "构图"),
                          ("text", "花字"), ("text", "简约"),
                          ("text", "信息条"), ("text", "文字动画"),
                          ("personFx", "人物轮廓"), ("personFx", "人物残影"),
                          ("personFx", "人物色彩")})
        self.assertTrue(all(item.checks["visualDistinct"] == item.qualified for item in items))
        for item in (preset for preset in items if preset.family == "text"):
            audit_path = item.asset_root / item.evidence["export"]
            self.assertTrue(audit_path.is_file())
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(audit["presetId"], item.id)
            self.assertLess(audit["previewExportMeanRgbDifference"], 5)
            self.assertGreaterEqual(audit["visibleTitlePixels"], 80)
            export_path = audit_path.with_name(audit["auditExport"])
            self.assertEqual(hashlib.sha256(export_path.read_bytes()).hexdigest(),
                             audit["auditExportSha256"])
            for key in ("apply", "edit", "saveReload", "undo", "export"):
                self.assertTrue(item.checks[key])

    def test_invalid_operator_params_and_duplicate_id_are_rejected(self) -> None:
        effects = EffectRegistry()
        valid = {"presetId": "cutvoke.preset.transition.example",
                 "effectId": "cutvoke.transition.crossfade",
                 "params": {"duration": 0.5}}
        with self.assertRaises(PresetInvalid):
            PresetCatalog(effects, [valid, valid])
        with self.assertRaises(PresetInvalid):
            PresetCatalog(effects, [{**valid, "effectId": "cutvoke.transition.missing"}])
        with self.assertRaises(PresetInvalid):
            PresetCatalog(effects, [{**valid, "params": {"duration": 99}}])
        with self.assertRaises(PresetInvalid):
            PresetCatalog(effects, [{"presetId": valid["presetId"], "family": "filter",
                                     "effects": [{"effectId": valid["effectId"]}]}])
        with self.assertRaises(PresetInvalid):
            PresetCatalog(effects, [{"presetId": valid["presetId"], "effects": [
                {"effectId": "cutvoke.fx.grayscale"},
                {"effectId": "cutvoke.fx.grayscale"}]}])
        with self.assertRaises(PresetInvalid):
            PresetCatalog(effects, [{"presetId": "cutvoke.preset.animation.duplicate",
                                     "effects": [{"effectId": "cutvoke.anim.fadeIn"},
                                                 {"effectId": "cutvoke.anim.zoomIn"}]}])

    def test_approval_requires_every_evidence_gate(self) -> None:
        effects = EffectRegistry()
        with tempfile.TemporaryDirectory() as media_dir:
            root = Path(media_dir)
            entry = {"presetId": "cutvoke.preset.transition.example",
                     "effectId": "cutvoke.transition.crossfade",
                     "params": {"duration": 0.5}, "status": "approved",
                     "cover": "cover.png", "motionPreview": "preview.mp4",
                     "checks": {key: True for key in CHECKS},
                     "evidence": {key: f"{key}.json" for key in CHECKS}}
            self.assertFalse(PresetCatalog(effects, [entry], root).all()[0].qualified)
            self.assertFalse(PresetCatalog(effects, [entry], root).all()[0].to_dict()["mediaAvailable"])
            for name in ("cover.png", "preview.mp4", *(f"{key}.json" for key in CHECKS)):
                (root / name).write_bytes(b"evidence")
            spec = PresetCatalog(effects, [entry], root).all()[0]
            (root / "visualDistinct.json").write_text(json.dumps({
                "schemaVersion": 1, "decision": "approved", "presetId": spec.id,
                "version": spec.version, "effectId": spec.effect_id, "params": spec.params,
                "coverSha256": hashlib.sha256((root / "cover.png").read_bytes()).hexdigest(),
                "motionPreviewSha256": hashlib.sha256((root / "preview.mp4").read_bytes()).hexdigest(),
                "auditSha256": hashlib.sha256((root / "export.json").read_bytes()).hexdigest(),
            }), encoding="utf-8")
            self.assertTrue(PresetCatalog(effects, [entry], root).all()[0].qualified)
            self.assertTrue(PresetCatalog(effects, [entry], root).all()[0].to_dict()["mediaAvailable"])
            (root / "preview.mp4").write_bytes(b"changed")
            self.assertFalse(PresetCatalog(effects, [entry], root).all()[0].qualified)
            (root / "preview.mp4").write_bytes(b"evidence")
            missing_export = {**entry, "checks": {**entry["checks"], "export": False}}
            self.assertFalse(PresetCatalog(effects, [missing_export], root).all()[0].qualified)
            (root / "preview.mp4").unlink()
            self.assertFalse(PresetCatalog(effects, [entry], root).all()[0].qualified)

    def test_http_catalog_reports_candidates_separately(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            api = HttpApi(EditService(), RenderService(), media_dir=media_dir)
            try:
                status, body = api.handle("GET", "/api/v1/presets", {})
                self.assertEqual(status, 200)
                self.assertEqual(body["count"], 189)
                self.assertEqual(body["candidateCount"], 0)
                self.assertEqual(body["qualifiedCount"], 189)
                self.assertEqual(body["resourcePack"]["version"], "1.38.1")
                self.assertTrue(all(item["source"] for item in body["presets"]))
                self.assertTrue(all("builtin_presets.json" in item["source"]
                                    for item in body["presets"]
                                    if item["family"] != "personFx"))
                person_candidates = [item for item in body["presets"]
                                     if item["family"] == "personFx"]
                self.assertEqual(len(person_candidates), 24)
                self.assertTrue(all(item["status"] == "approved" and item["qualified"]
                                    for item in person_candidates))
                self.assertTrue(body["resourcePack"]["offlineAvailable"])
                status, animations = api.handle("GET", "/api/v1/presets", {"family": "animation"})
                self.assertEqual(status, 200)
                self.assertEqual(animations["count"], 32)
                status, picture_fx = api.handle("GET", "/api/v1/presets", {"family": "fx"})
                self.assertEqual(status, 200)
                self.assertEqual(picture_fx["count"], 36)
                status, person_fx = api.handle("GET", "/api/v1/presets", {"family": "personFx"})
                self.assertEqual(status, 200)
                self.assertEqual(person_fx["count"], 24)
                self.assertEqual(person_fx["candidateCount"], 0)
                self.assertEqual(person_fx["qualifiedCount"], 189)
                self.assertEqual(sum(item["qualified"] for item in person_fx["presets"]), 24)
                status, composition = api.handle("GET", "/api/v1/presets", {"family": "fx", "subcategory": "构图"})
                self.assertEqual(status, 200)
                self.assertEqual(sum(item["qualified"] for item in composition["presets"]), 10)
                status, filters = api.handle("GET", "/api/v1/presets", {"family": "filter"})
                self.assertEqual(status, 200)
                self.assertEqual(filters["count"], 24)
                status, titles = api.handle("GET", "/api/v1/presets", {"family": "text"})
                self.assertEqual(status, 200)
                self.assertEqual(titles["count"], 32)
                status, title_motions = api.handle("GET", "/api/v1/presets", {"family": "text", "subcategory": "文字动画"})
                self.assertEqual(status, 200)
                self.assertEqual(title_motions["count"], 8)
                self.assertEqual(sum(item["qualified"] for item in title_motions["presets"]), 8)
                self.assertEqual(title_motions["qualifiedCount"], 189)
                status, only = api.handle("GET", "/api/v1/presets", {"qualifiedOnly": "1"})
                self.assertEqual(status, 200)
                self.assertEqual(only["count"], 189)
                first = body["presets"][0]["presetId"]
                status, cover = api.handle("GET", f"/api/v1/presets/{first}/cover", {})
                self.assertEqual(status, 200)
                self.assertTrue(cover["__png__"].startswith(b"\x89PNG"))
                status, preview = api.handle("GET", f"/api/v1/presets/{first}/preview", {})
                self.assertEqual(status, 200)
                self.assertTrue(Path(preview["__video_path__"]).is_file())
                status, missing = api.handle("GET", "/api/v1/presets/no-such-preset/preview", {})
                self.assertEqual(status, 404)
                self.assertEqual(missing["error"]["code"], "NOT_FOUND")
            finally:
                api.close()

    def test_compound_apply_persists_as_one_undoable_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = str(Path(temporary) / "projects.db")
            with ProjectStore(database) as store:
                service = EditService(store)
                project = service.create_project("compound-preset")

                def execute(kind: str, payload: dict, command_id: str) -> None:
                    current = service.get_project(project.project_id)
                    service.execute(Command(type=kind, payload=payload,
                                            command_id=command_id,
                                            project_id=project.project_id,
                                            expected_revision=current.revision,
                                            actor=Actor("agent", "preset-test")))

                execute("track.add", {"trackId": "v1", "kind": "video"}, "add-track")
                execute("clip.insert", {"trackId": "v1", "clipId": "image",
                                        "sourcePath": "source.png",
                                        "timelineStart": {"num": 0, "den": 1},
                                        "timelineEnd": {"num": 4, "den": 1}}, "add-clip")
                before = service.get_project(project.project_id).revision
                execute("builtinPreset.apply", {"clipId": "image",
                                               "presetId": "cutvoke.preset.filter.noirContrast"},
                        "apply-compound")
                applied = service.get_project(project.project_id)
                self.assertEqual(int(applied.revision), int(before) + 1)
                effect_stack = applied.sequence.tracks[0].clips[0].effects
                self.assertEqual([effect["effectId"] for effect in effect_stack],
                                 ["cutvoke.fx.grayscale", "cutvoke.fx.curves"])
                self.assertEqual([effect["presetPartIndex"] for effect in effect_stack], [0, 1])
                self.assertTrue(all(effect["presetId"] ==
                                    "cutvoke.preset.filter.noirContrast"
                                    for effect in effect_stack))
                self.assertTrue(all(effect["presetVersion"] == "1.0.0"
                                    for effect in effect_stack))
            with ProjectStore(database) as reopened:
                service = EditService(reopened)
                current = service.get_project("compound-preset")
                self.assertEqual(current.sequence.tracks[0].clips[0].effects, effect_stack)
                service.execute(Command(type="history.undo", payload={},
                                        command_id="undo-compound", project_id=current.project_id,
                                        expected_revision=current.revision,
                                        actor=Actor("agent", "preset-test")))
                self.assertEqual(service.get_project(current.project_id)
                                 .sequence.tracks[0].clips[0].effects, [])


if __name__ == "__main__":
    unittest.main()
