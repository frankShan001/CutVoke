import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sqlite3

from PIL import Image

from cutvoke.domain.protocol import Command
from cutvoke.editing.service import EditError, EditService
from cutvoke.storage.store import ProjectStore
from cutvoke.storage.workspace_paths import path_key, path_values, replace_paths


class WorkspacePathRelocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "old.png"
        self.target = self.root / "new.png"
        Image.new("RGB", (16, 16), "red").save(self.source)
        self.target.write_bytes(self.source.read_bytes())
        self.store = ProjectStore(str(self.root / "projects.sqlite"))
        self.addCleanup(self.store.close)
        self.service = EditService(store=self.store)
        self.addCleanup(self.service.close)
        self.service.create_project("p")
        self.apply("track.add", {"trackId": "v", "kind": "video"})
        self.apply("clip.insert", {"trackId": "v", "clipId": "c", "sourcePath": str(self.source),
                                   "timelineStart": {"num": "0", "den": "1"},
                                   "timelineEnd": {"num": "1", "den": "1"}})
        self.store.add_asset(asset_id="a", name="old.png", path=str(self.source),
                             size=self.source.stat().st_size, kind="image")

    def apply(self, kind, payload):
        return self.service.execute(Command(type=kind, payload=payload, project_id="p",
                                            expected_revision=self.service.get_project("p").revision))

    def migration(self, command_id="move"):
        return Command(type="workspace.relocatePaths", command_id=command_id, payload={
            "expectedProjects": {"p": self.service.get_project("p").revision},
            "moves": [{"from": str(self.source), "to": str(self.target),
                       "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest()}]})

    def test_history_library_receipts_and_frozen_exports_follow_relocation(self):
        before = self.service.get_project("p").to_dict()
        self.store.create_export_job(job_id="done", project_id="p", revision=before["revision"],
                                     request_hash="frozen", out_path=str(self.source), quality="high",
                                     status="succeeded", project_snapshot=before)
        self.apply("clip.move", {"clipId": "c", "timelineStart": {"num": "1", "den": "1"}})
        command = self.migration()
        result = self.service.execute(command)
        self.source.unlink()
        self.assertEqual(self.store.get_asset("a")["path"], str(self.target))
        self.assertEqual(self.store.get_export_job("done")["outPath"], str(self.target))
        self.assertIn(str(self.target), list(path_values(self.store.get_export_project_snapshot("done"))))
        self.assertEqual(self.service.execute(command).to_dict(), result.to_dict())
        other = EditService(store=ProjectStore(self.store.path))
        self.addCleanup(other.store.close)
        self.addCleanup(other.close)
        self.assertIn(str(self.target), list(path_values(other.get_project("p").to_dict())))
        self.apply("history.undo", {})
        restored = self.service.get_project("p").to_dict()
        self.assertIn(str(self.target), list(path_values(restored)))
        self.assertNotIn(str(self.source), list(path_values(restored)))
        self.assertEqual(self.store.get_export_job("done")["revision"], before["revision"])

    def test_content_mismatch_and_stale_revision_leave_entire_database_unchanged(self):
        before = list(self.store._conn.iterdump())
        command = self.migration()
        self.target.write_bytes(b"different content")
        with self.assertRaises(EditError):
            self.service.execute(command)
        self.assertEqual(list(self.store._conn.iterdump()), before)
        self.target.write_bytes(self.source.read_bytes())
        command.payload["expectedProjects"]["p"] = "0"
        with self.assertRaises(EditError):
            self.service.execute(command)
        self.assertEqual(list(self.store._conn.iterdump()), before)

    def test_active_lease_and_export_reject_relocation(self):
        command = self.migration()
        self.service.acquire_edit_lease("p", owner="test", lease_id="lease", ttl_seconds=30)
        with self.assertRaises(EditError):
            self.service.execute(command)
        self.service.release_edit_lease("p", "lease")
        self.store.create_export_job(job_id="pending", project_id="p", revision=command.payload["expectedProjects"]["p"],
                                     request_hash="pending", out_path=str(self.root / "export.mp4"), quality="high")
        with self.assertRaises(EditError):
            self.service.execute(command)

    def test_path_like_caption_text_is_preserved(self):
        source = str(self.source)
        value = {"text": source, "sourcePath": source, "params": {"maskPath": source}}
        rewritten, count = replace_paths(value, {path_key(str(self.source)): str(self.target)})
        self.assertEqual(rewritten["text"], source)
        self.assertEqual(rewritten["sourcePath"], str(self.target))
        self.assertEqual(count, 2)

    def test_late_sql_failure_rolls_back_history_library_and_project(self):
        self.store._conn.execute("CREATE TRIGGER reject_move BEFORE UPDATE ON projects "
                                 "BEGIN SELECT RAISE(ABORT, 'late failure'); END")
        before = list(self.store._conn.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.execute(self.migration())
        self.assertEqual(list(self.store._conn.iterdump()), before)

    def test_preexisting_missing_reference_can_move_without_fabricating_media(self):
        command = self.migration()
        self.source.unlink()
        self.target.unlink()
        command.payload["moves"][0].pop("sha256")
        command.payload["moves"][0]["missing"] = True
        self.service.execute(command)
        self.assertFalse(self.target.exists())
        self.assertIn(str(self.target), list(path_values(self.service.get_project("p").to_dict())))


if __name__ == "__main__":
    unittest.main()
