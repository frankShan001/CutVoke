"""Atomic multi-command edits for external agents and the manual editor."""

from __future__ import annotations

import tempfile
import unittest
import os

from cutvoke.core.httpapi import HttpApi
from cutvoke.core.protocol import Actor, Command, ErrorCode
from cutvoke.core.service import EditError, EditService
from cutvoke.core.store import ProjectStore


def make_command(project, command_type: str, payload: dict, command_id: str) -> Command:
    return Command(
        type=command_type,
        payload=payload,
        command_id=command_id,
        project_id=project.project_id,
        expected_revision=project.revision,
        actor=Actor("agent", "batch-test"),
    )


class AtomicEditBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = EditService()
        self.project = self.service.create_project("batch-test")
        self.operations = [
            {"type": "track.add", "payload": {"trackId": "v1", "kind": "video"}},
            {"type": "clip.insert", "payload": {
                "trackId": "v1", "clipId": "opening", "sourcePath": "opening.png",
                "timelineStart": {"num": 0, "den": 1},
                "timelineEnd": {"num": 4, "den": 1},
            }},
            {"type": "caption.add", "payload": {
                "captionId": "title", "text": "片头", "start": {"num": 0, "den": 1},
                "end": {"num": 2, "den": 1},
            }},
            {"type": "effect.add", "payload": {
                "clipId": "opening", "effectId": "cutvoke.fx.vibrance",
                "params": {},
            }},
        ]

    def test_preview_commit_and_one_undo_cover_all_operations(self) -> None:
        request = make_command(
            self.project, "edit.batch", {"commands": self.operations}, "batch-once"
        )
        preview = self.service.preview_command(request)
        self.assertEqual([item["batchIndex"] for item in preview["changedEntities"]],
                         [0, 1, 2, 3])
        self.assertEqual(self.service.get_project(self.project.project_id).revision, "0")
        self.assertEqual(self.service.get_project(self.project.project_id).sequence.tracks, [])

        committed = self.service.execute(request)
        self.assertEqual(committed.revision, "1")
        self.assertEqual([item["commandType"] for item in committed.changed_entities],
                         ["track.add", "clip.insert", "caption.add", "effect.add"])
        current = self.service.get_project(self.project.project_id)
        self.assertEqual([clip.id for clip in current.sequence.tracks[0].clips], ["opening"])
        self.assertEqual(current.sequence.tracks[0].clips[0].effects[0]["effectId"],
                         "cutvoke.fx.vibrance")
        self.assertEqual([caption.text for caption in current.sequence.captions], ["片头"])

        self.service.execute(make_command(current, "history.undo", {}, "batch-undo"))
        restored = self.service.get_project(self.project.project_id)
        self.assertEqual(restored.sequence.tracks, [])
        self.assertEqual(restored.sequence.captions, [])

    def test_late_failure_and_unsafe_commands_leave_no_partial_change(self) -> None:
        failing = self.operations[:1] + [{"type": "clip.insert", "payload": {
            "trackId": "v1", "clipId": "bad-audio", "sourcePath": "music.wav",
            "timelineEnd": {"num": 2, "den": 1},
        }}]
        for operations in (
            failing,
            self.operations[:1] + [{"type": "history.undo", "payload": {}}],
            self.operations[:1] + [{"type": "export.video", "payload": {}}],
            self.operations[:1] + [{"type": "edit.batch", "payload": {"commands": []}}],
        ):
            with self.subTest(command=operations[-1]["type"]):
                request = make_command(
                    self.project, "edit.batch", {"commands": operations},
                    "bad-" + operations[-1]["type"],
                )
                with self.assertRaises(EditError) as caught:
                    self.service.execute(request)
                self.assertEqual(caught.exception.code, ErrorCode.INVALID_ARGUMENT)
                current = self.service.get_project(self.project.project_id)
                self.assertEqual(current.revision, "0")
                self.assertEqual(current.sequence.tracks, [])

    def test_http_agent_dry_run_and_command_catalog(self) -> None:
        catalog = {item["type"]: item for item in self.service.command_catalog()}
        self.assertIn("edit.batch", catalog)
        self.assertIn("commands", {item["name"] for item in catalog["edit.batch"]["params"]})
        self.assertIn("caption.patch", catalog["edit.batch"]["allowedCommands"])
        self.assertNotIn("export.video", catalog["edit.batch"]["allowedCommands"])
        self.assertEqual(catalog["edit.batch"]["maxCommands"], 32)
        with tempfile.TemporaryDirectory() as media_dir:
            api = HttpApi(self.service, None, media_dir=media_dir)
            self.addCleanup(api.close)
            route = f"/api/v1/projects/{self.project.project_id}/commands"
            body = {
                "type": "edit.batch", "payload": {"commands": self.operations},
                "expectedRevision": "0", "commandId": "http-batch",
                "actor": {"kind": "agent", "id": "batch-test"},
            }
            status, preview = api.handle("POST", route, {**body, "dryRun": True})
            self.assertEqual(status, 200)
            self.assertTrue(preview["valid"])
            self.assertEqual(self.service.get_project(self.project.project_id).revision, "0")
            status, committed = api.handle("POST", route, body)
            self.assertEqual(status, 200)
            self.assertEqual(committed["revision"], "1")

    def test_persisted_batch_keeps_one_undo_point_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = os.path.join(temp_dir, "projects.sqlite3")
            with ProjectStore(database) as store:
                service = EditService(store)
                project = service.create_project("persistent-batch")
                request = make_command(
                    project, "edit.batch", {"commands": self.operations}, "persist-batch"
                )
                result = service.execute(request)
                self.assertEqual(result.revision, "1")
                # depth 是零起始的最后一项索引：0 代表恰好一个撤销点。
                self.assertEqual(store.history_depth(project.project_id), 0)
            with ProjectStore(database) as reopened:
                service = EditService(reopened)
                current = service.get_project("persistent-batch")
                self.assertEqual([track.id for track in current.sequence.tracks], ["v1"])
                self.assertEqual(service.execute(request).revision, result.revision)
                self.assertEqual(reopened.history_depth(project.project_id), 0)
                service.execute(make_command(current, "history.undo", {}, "persist-undo"))
                restored = service.get_project("persistent-batch")
                self.assertEqual(restored.sequence.tracks, [])
                self.assertEqual(restored.sequence.captions, [])


if __name__ == "__main__":
    unittest.main()
