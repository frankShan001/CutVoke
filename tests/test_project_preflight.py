"""User-facing readiness and recovery contracts, with no media subprocesses."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cutvoke.core.model import AssetReference, Clip, Sequence, Track
from cutvoke.core.project_health import project_preflight
from cutvoke.core.protocol import Command
from cutvoke.core.rational import Rational
from cutvoke.core.service import EditError, EditService
from cutvoke.core.store import ProjectStore


def clip(identifier, path, start=0, end=2):
    return Clip(identifier, AssetReference(identifier, str(path)),
                Rational.of(start), Rational.of(end), Rational.of(0))


class ProjectPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "available.png"
        self.source.write_bytes(b"not a decoded media probe")
        self.service = EditService()
        self.project = self.service.create_project("readiness")

    def test_repeated_sources_checked_once_and_command_does_not_edit(self):
        self.project.sequence.tracks = [Track("video", "video", [
            clip(f"clip-{i}", self.source, i * 2, i * 2 + 2) for i in range(1000)])]
        before = json.dumps(self.project.to_dict(), sort_keys=True)
        from cutvoke.core import project_health
        with patch.object(project_health.os, "stat", wraps=project_health.os.stat) as probe:
            result = self.service.execute(Command("project.preflight", {},
                                                 project_id=self.project.project_id))
        report = result.changed_entities[0]["report"]
        self.assertTrue(report["readyToRender"])
        self.assertEqual(1, report["checkedFileCount"])
        self.assertEqual(1, probe.call_count)
        self.assertEqual(1000, report["clipCount"])
        self.assertEqual(before, json.dumps(self.project.to_dict(), sort_keys=True))
        self.assertEqual([], self.service._undo_stack[self.project.project_id])

    def test_missing_source_locates_all_affected_clips(self):
        missing = self.root / "moved.png"
        self.project.sequence.tracks = [Track("pictures", "video", [
            clip("first", missing), clip("second", missing, 2, 4)])]
        report = project_preflight(self.project)
        self.assertFalse(report["readyToRender"])
        self.assertEqual(["first", "second"], report["errors"][0]["clipIds"])
        self.assertEqual(["pictures"], report["errors"][0]["trackIds"])
        missing.write_bytes(b"restored")
        self.assertTrue(project_preflight(self.project)["readyToRender"])

    def test_hidden_muted_and_outside_range_missing_media_only_warn(self):
        hidden = clip("hidden", self.root / "hidden.png")
        hidden.hidden = True
        self.project.sequence.tracks = [
            Track("base", "video", [clip("good", self.source),
                                     clip("later", self.root / "later.png", 2, 4)]),
            Track("hidden-track", "video", [hidden]),
            Track("muted", "audio", [clip("silent", self.root / "silent.wav")], muted=True)]
        report = project_preflight(self.project, output_range=(0, 2))
        self.assertTrue(report["readyToRender"])
        self.assertEqual(3, len(report["warnings"]))
        self.assertFalse(project_preflight(self.project)["readyToRender"])

    def test_muted_video_still_requires_its_picture(self):
        self.project.sequence.tracks = [Track("v", "video", [
            clip("missing", self.root / "gone.mp4")], muted=True)]
        self.assertFalse(project_preflight(self.project)["readyToRender"])

    def test_range_in_a_transition_still_checks_outgoing_source(self):
        incoming = clip("incoming", self.source, 2, 4)
        incoming.effects = [{"effectId": "cutvoke.transition.fade",
                             "params": {"duration": 0.5}}]
        self.project.sequence.tracks = [Track("v", "video", [
            clip("outgoing", self.root / "gone.png"), incoming])]
        self.assertFalse(project_preflight(self.project, output_range=(2.1, 3))["readyToRender"])
        self.assertTrue(project_preflight(self.project, output_range=(2.6, 3))["readyToRender"])
        incoming.effects[0]["params"]["duration"] = {"num": "1", "den": "2"}
        self.assertFalse(project_preflight(self.project, output_range=(2.1, 3))["readyToRender"])
        incoming.effects[0]["params"]["duration"] = 10
        self.assertFalse(project_preflight(self.project, output_range=(2.9, 4))["readyToRender"])
        self.assertTrue(project_preflight(self.project, output_range=(3.1, 4))["readyToRender"])

    def test_custom_lut_and_bypass_are_respected(self):
        media = clip("image", self.source)
        media.effects = [{"effectId": "cutvoke.fx.lut", "params": {
            "file": str(self.root / "gone.cube")}}]
        self.project.sequence.tracks = [Track("v", "video", [media])]
        report = project_preflight(self.project)
        self.assertEqual("lut", report["errors"][0]["resourceKind"])
        media.effects[0]["enabled"] = False
        self.assertTrue(project_preflight(self.project)["readyToRender"])

    def test_compound_clip_needs_children_not_a_synthetic_source(self):
        parent = clip("compound", "")
        parent.nested = Sequence("inner", 320, 180, Rational.of(25), tracks=[
            Track("inner-video", "video", [clip("child", self.source)])])
        self.project.sequence.tracks = [Track("outer", "video", [parent])]
        self.assertTrue(project_preflight(self.project)["readyToRender"])
        parent.nested.tracks[0].clips[0].asset_ref.source_path = str(self.root / "gone.png")
        report = project_preflight(self.project)
        self.assertEqual(["child"], report["errors"][0]["clipIds"])

    def test_empty_file_and_directory_are_rejected_without_decode(self):
        empty = self.root / "empty.png"
        empty.touch()
        self.project.sequence.tracks = [Track("v", "video", [
            clip("empty", empty), clip("directory", self.root, 2, 4)])]
        self.assertEqual({"EMPTY_FILE", "NOT_A_FILE"},
                         {item["code"] for item in project_preflight(self.project)["errors"]})

    def test_empty_timeline_is_actionable(self):
        report = project_preflight(self.project)
        self.assertFalse(report["readyToRender"])
        self.assertEqual("EMPTY_TIMELINE", report["errors"][0]["code"])

    def test_command_catalog_has_unique_parameters_for_every_command(self):
        for command in self.service.command_catalog():
            with self.subTest(command=command["type"]):
                names = [item["name"] for item in command["params"]]
                self.assertEqual(len(names), len(set(names)))
                self.assertNotEqual("(未描述)", command["description"])

    def test_dry_run_does_not_export_files_or_cancel_jobs(self):
        output = self.root / "keep.vtt"
        output.write_text("existing user output", encoding="utf-8")
        for kind, payload in (("caption.exportVtt", {"outPath": str(output)}),
                              ("export.enqueue", {"outPath": str(self.root / "video.mp4")}),
                              ("export.cancel", {"jobId": "active-job"}),
                              ("caption.autoSegment", {"applyToProject": True})):
            with self.subTest(command=kind):
                with patch.dict(self.service._handlers, {kind: lambda *_: self.fail("dry-run handler ran")}):
                    with self.assertRaises(EditError):
                        self.service.preview_command(Command(kind, payload,
                            project_id=self.project.project_id, expected_revision=self.project.revision))
        self.assertEqual("existing user output", output.read_text(encoding="utf-8"))
        self.assertFalse((self.root / "video.mp4").exists())
        capabilities = {item["type"]: item for item in self.service.command_catalog()}
        self.assertFalse(capabilities["export.enqueue"]["previewSupported"])
        self.assertTrue(capabilities["clip.insert"]["previewSupported"])

    def test_readonly_preflight_can_be_previewed_without_project_changes(self):
        self.project.sequence.tracks = [Track("v", "video", [clip("image", self.source)])]
        before = self.project.to_dict()
        result = self.service.preview_command(Command("project.preflight", {},
            project_id=self.project.project_id, expected_revision=self.project.revision))
        self.assertTrue(result["changedEntities"][0]["report"]["readyToRender"])
        self.assertEqual(before, self.project.to_dict())

    def test_asset_id_only_swap_resolves_persistent_ledger_and_undo(self):
        store = ProjectStore(str(self.root / "projects.sqlite"))
        self.addCleanup(store.close)
        service = EditService(store=store)
        project = service.create_project("recover reference")
        old = self.root / "old.png"
        old.write_bytes(b"old")
        project.sequence.tracks = [Track("v", "video", [clip("clip", old)])]
        store.save(project)
        store.add_asset(asset_id="replacement", name="new picture", path=str(self.source),
                        size=self.source.stat().st_size, kind="image")
        with patch.object(service, "_assert_clip_within_source"):
            result = service.execute(Command("asset.swap", {
                "clipIds": ["clip"], "assetId": "replacement"},
                project_id=project.project_id, expected_revision=project.revision))
        updated = service.get_project(project.project_id)
        self.assertEqual(str(self.source), updated.sequence.tracks[0].clips[0].asset_ref.source_path)
        service.execute(Command("history.undo", {}, project_id=project.project_id,
                                expected_revision=result.revision))
        self.assertEqual(str(old), service.get_project(project.project_id).sequence.tracks[0]
                         .clips[0].asset_ref.source_path)

    def test_unknown_asset_id_does_not_poison_project(self):
        self.project.sequence.tracks = [Track("v", "video", [clip("image", self.source)])]
        before = self.project.to_dict()
        with self.assertRaises(EditError):
            self.service.execute(Command("asset.swap", {"clipIds": ["image"], "assetId": "missing"},
                project_id=self.project.project_id, expected_revision=self.project.revision))
        self.assertEqual(before, self.service.get_project(self.project.project_id).to_dict())


if __name__ == "__main__":
    unittest.main()
