"""Guard compact persistence and lightweight synchronization without timing assertions."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from urllib.request import urlopen

from cutvoke.domain.model import AssetReference, Clip, Project, Sequence, Track
from cutvoke.domain.protocol import Actor, Command, ErrorCode
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditError, EditService
from cutvoke.interfaces.httpapi import HttpApi, build_handler
from cutvoke.storage.store import ProjectStore


def fixture(count=1):
    sequence = Sequence(id="active", width=1920, height=1080, fps=R.of(30), tracks=[
        Track(id="video", kind="video", clips=[Clip(
            id=f"clip-{i}", asset_ref=AssetReference(asset_id="image", source_path="image.png"),
            timeline_start=R.of(i * 2), timeline_end=R.of(i * 2 + 1), source_start=R.of(0))
            for i in range(count)])])
    return Project(schema_version="1", project_id="performance", revision="0", sequence=sequence,
                   name="before", favorites=["keep"], presets=[{"name": "saved", "params": {"x": 1}}])


def command(project, kind, payload, serial):
    return Command(type=kind, payload=payload, project_id=project.project_id,
                   expected_revision=project.revision, command_id=f"command-{serial}",
                   actor=Actor("human", "performance-test"))


class ProjectStatePerformanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="cutvoke-state-perf-")
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "project.sqlite")
        self.store = ProjectStore(self.path)
        self.addCleanup(self.store.close)
        self.service = EditService(self.store)

    def test_all_three_project_layouts_parse_each_sequence_once(self):
        project = fixture()
        second = copy.deepcopy(project.sequence)
        second.id = "other"
        second.tracks[0].id = "other-track"
        second.tracks[0].clips[0].id = "other-clip"
        project.sequences.append(second)
        full = project.to_dict()
        legacy = {key: value for key, value in full.items() if key not in {"sequences", "activeSequenceId"}}
        for data, expected in ((full, 2), (project.to_dict(compact=True), 2), (legacy, 1)):
            with self.subTest(layout=list(data)):
                with patch.object(Sequence, "from_dict", wraps=Sequence.from_dict) as parse:
                    restored = Project.from_dict(data)
                self.assertEqual(parse.call_count, expected)
                self.assertIs(restored.sequence, restored.sequences[0])
                self.assertEqual(restored.favorites, ["keep"])

    def test_history_is_less_than_half_without_dropping_state_or_archive_contract(self):
        project = fixture(500)
        self.store.save(project)
        baseline = len(json.dumps(project.to_dict(), ensure_ascii=False).encode())
        self.service.execute(command(project, "clip.move", {
            "clipId": "clip-0", "timelineStart": R.of(1, 4).to_json()}, 1))
        snapshot = self.store.peek_history(project.project_id, 0)
        self.assertLess(len(snapshot.encode()), baseline / 2)
        self.assertEqual(Project.from_dict(json.loads(snapshot)).to_dict(), project.to_dict())
        stored = json.loads(self.store._conn.execute(
            "SELECT project_json FROM projects WHERE project_id=?", (project.project_id,)).fetchone()[0])
        self.assertNotIn("sequence", stored)
        self.assertEqual(len(stored["sequences"][0]["tracks"][0]["clips"]), 500)
        archived = self.store.production_snapshot(project.project_id)
        self.assertIn("sequence", archived["project"])
        self.assertIn("sequence", archived["history"][0]["project"])
        # A fresh process can undo a compact snapshot, retaining project resources.
        other = EditService(self.store)
        current = other.get_project(project.project_id)
        other.execute(command(current, "history.undo", {}, 2))
        restored = other.get_project(project.project_id)
        self.assertEqual(restored.sequence.to_dict(), project.sequence.to_dict())
        self.assertEqual(restored.favorites, project.favorites)
        other.execute(command(restored, "history.redo", {}, 3))
        self.assertEqual(other.get_project(project.project_id).sequence.tracks[0].clips[0].timeline_start, R.of(1, 4))

    def test_one_load_for_edit_no_load_for_lease_or_unchanged_sync_and_idempotency(self):
        project = fixture()
        self.store.save(project)
        first = self.service.project_sync(project.project_id)
        self.assertNotIn("sequence", first["project"])
        with patch.object(self.store, "load", wraps=self.store.load) as load:
            self.assertIsNone(self.service.get_edit_lease(project.project_id))
            same = self.service.project_sync(project.project_id, first["token"])
            self.assertTrue(same["unchanged"])
            self.assertNotIn("project", same)
            self.assertEqual(load.call_count, 0)
            request = command(project, "track.add", {"trackId": "audio", "kind": "audio"}, 1)
            result = self.service.execute(request)
            self.assertEqual(load.call_count, 1)
        replay = self.service.execute(request)
        self.assertEqual(replay.to_dict(), result.to_dict())
        self.assertEqual(self.store.history_depth(project.project_id), 0)

    def test_cross_connection_edits_rename_and_same_revision_save_invalidate_token(self):
        project = fixture()
        self.store.save(project)
        first = self.service.project_sync(project.project_id)
        external_store = ProjectStore(self.path)
        self.addCleanup(external_store.close)
        external = EditService(external_store)
        external.execute(command(external.get_project(project.project_id), "track.add", {
            "trackId": "external", "kind": "audio"}, 1))
        changed = self.service.project_sync(project.project_id, first["token"])
        self.assertFalse(changed["unchanged"])
        self.assertEqual(changed["revision"], "1")
        external.rename_project(project.project_id, "renamed")
        renamed = self.service.project_sync(project.project_id, changed["token"])
        self.assertFalse(renamed["unchanged"])
        self.assertEqual(renamed["revision"], "1")
        self.assertEqual(renamed["project"]["name"], "renamed")
        current = external.get_project(project.project_id)
        current.favorites.append("external favorite")
        external_store.save(current)
        saved = self.service.project_sync(project.project_id, renamed["token"])
        self.assertFalse(saved["unchanged"])
        self.assertIn("external favorite", saved["project"]["favorites"])
        with self.assertRaises(EditError) as caught:
            self.service.execute(command(project, "track.add", {"trackId": "stale", "kind": "audio"}, 9))
        self.assertEqual(caught.exception.code, ErrorCode.REVISION_CONFLICT)

    def test_old_dual_and_sequence_only_database_rows_remain_readable(self):
        for layout in ("dual", "legacy"):
            with self.subTest(layout=layout):
                project = fixture()
                self.store.save(project)
                self.store._conn.execute("UPDATE projects SET project_json=? WHERE project_id=?", (
                    json.dumps(project.to_dict()) if layout == "dual" else "", project.project_id))
                if layout == "legacy":
                    # Pre-project_json stores had no project-level resource fields.
                    project.favorites = []
                    project.presets = []
                self.assertEqual(self.store.load(project.project_id).to_dict(), project.to_dict())

    def test_sync_token_and_changed_content_share_one_read_snapshot(self):
        project = fixture()
        self.store.save(project)
        first = self.service.project_sync(project.project_id)
        external = ProjectStore(self.path)
        self.addCleanup(external.close)
        original_load = self.store.load

        def concurrent_write(project_id):
            # The other connection commits between the metadata and full read.
            changed = external.load(project_id)
            changed.name = "concurrent save"
            changed.revision = "1"
            external.save(changed)
            return original_load(project_id)

        with patch.object(self.store, "load", side_effect=concurrent_write):
            old = self.service.project_sync(project.project_id)
        self.assertEqual(old["token"], first["token"])
        self.assertEqual(old["revision"], old["project"]["revision"])
        self.assertEqual(old["project"]["name"], "before")
        changed = self.service.project_sync(project.project_id, old["token"])
        self.assertFalse(changed["unchanged"])
        self.assertEqual(changed["project"]["name"], "concurrent save")

    def test_conflicting_commit_keeps_authority_history_and_idempotency_atomic(self):
        project = fixture()
        self.store.save(project)
        external = ProjectStore(self.path)
        self.addCleanup(external.close)
        original_commit = self.store.commit

        def concurrent_write(candidate, **kwargs):
            changed = external.load(project.project_id)
            changed.revision = "1"
            changed.favorites.append("winner")
            external.save(changed)
            return original_commit(candidate, **kwargs)

        with patch.object(self.store, "commit", side_effect=concurrent_write):
            with self.assertRaises(EditError) as caught:
                self.service.execute(command(project, "track.add", {"trackId": "loser", "kind": "audio"}, 1))
        self.assertEqual(caught.exception.code, ErrorCode.REVISION_CONFLICT)
        self.assertEqual(self.store.history_depth(project.project_id), -1)
        self.assertIsNone(self.store.get_idempotent(project.project_id, "command-1"))
        authoritative = self.service.get_project(project.project_id)
        self.assertIn("winner", authoritative.favorites)
        self.assertEqual([track.id for track in authoritative.sequence.tracks], ["video"])

    def test_undo_redo_stack_survives_reopen_and_new_branch_clears_redo(self):
        self.store.save(fixture())
        serial = 0

        def reopen_apply(kind, payload):
            nonlocal serial
            serial += 1
            # Every command uses a new connection and service, without memory history.
            reopened = ProjectStore(self.path)
            try:
                service = EditService(reopened)
                project = service.get_project("performance")
                service.execute(command(project, kind, payload, serial))
                return service.get_project("performance")
            finally:
                reopened.close()

        reopen_apply("track.add", {"trackId": "a", "kind": "audio"})
        reopen_apply("track.add", {"trackId": "b", "kind": "audio"})
        reopen_apply("history.undo", {})
        state = reopen_apply("history.undo", {})
        self.assertEqual([track.id for track in state.sequence.tracks], ["video"])
        self.assertEqual(self.store.redo_depth("performance"), -2)
        state = reopen_apply("history.redo", {})
        self.assertEqual([track.id for track in state.sequence.tracks], ["video", "a"])
        state = reopen_apply("history.redo", {})
        self.assertEqual([track.id for track in state.sequence.tracks], ["video", "a", "b"])
        self.assertIsNone(self.store.redo_depth("performance"))
        reopen_apply("history.undo", {})
        state = reopen_apply("track.add", {"trackId": "c", "kind": "audio"})
        self.assertEqual([track.id for track in state.sequence.tracks], ["video", "a", "c"])
        self.assertIsNone(self.store.redo_depth("performance"))
        with self.assertRaises(EditError) as caught:
            reopen_apply("history.redo", {})
        self.assertEqual(caught.exception.code, ErrorCode.UNDO_CONFLICT)

    def test_rejected_undo_and_redo_commit_leave_both_persistent_stacks_intact(self):
        project = fixture()
        self.store.save(project)
        self.service.execute(command(project, "track.add", {"trackId": "a", "kind": "audio"}, 1))
        for kind, serial in (("history.undo", 2), ("history.redo", 4)):
            if kind == "history.redo":
                current = self.service.get_project(project.project_id)
                self.service.execute(command(current, "history.undo", {}, 3))
            current = self.service.get_project(project.project_id)
            before = self.store._conn.execute("SELECT depth,snapshot_json FROM history ORDER BY depth").fetchall()
            with patch.object(self.store, "commit", side_effect=RuntimeError("simulated failure")):
                with self.assertRaises(RuntimeError):
                    self.service.execute(command(current, kind, {}, serial))
            self.assertEqual(self.store._conn.execute("SELECT depth,snapshot_json FROM history ORDER BY depth").fetchall(), before)
            self.assertEqual(self.store.load(project.project_id).to_dict(), current.to_dict())

    def test_http_sync_query_token_and_legacy_get_contract(self):
        project = fixture()
        self.store.save(project)
        api = HttpApi(self.service)
        self.addCleanup(api.close)
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(api))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}/api/v1/projects/{project.project_id}"
            with urlopen(base + "/sync", timeout=5) as response:
                first = json.load(response)
            self.assertFalse(first["unchanged"])
            self.assertNotIn("sequence", first["project"])
            with urlopen(base + "/sync?token=" + first["token"], timeout=5) as response:
                unchanged = json.load(response)
            self.assertTrue(unchanged["unchanged"])
            self.assertNotIn("project", unchanged)
            with urlopen(base, timeout=5) as response:
                full = json.load(response)
            self.assertEqual(full["sequence"], full["sequences"][0])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
