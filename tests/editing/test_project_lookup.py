"""Small read contracts for an Agent changing one subtitle in a large project."""

from __future__ import annotations

import tempfile
import unittest
from http.server import ThreadingHTTPServer
from threading import Thread

from cutvoke.interfaces.client import CutVokeClient
from cutvoke.interfaces.httpapi import HttpApi, build_handler
from cutvoke.interfaces.mcp_server import MCPServer
from cutvoke.domain.model import AssetReference, Caption, Clip, Track
from cutvoke.domain.rational import Rational
from cutvoke.editing.service import EditError, EditService


def r(value: int) -> Rational:
    return Rational.of(value, 1)


class ProjectLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = EditService()
        self.project = self.service.create_project("字幕 工程 / 1")
        self.project.sequence.captions = [
            Caption(id=f"cap-{i}", text=f"普通字幕 {i}", start=r(i), end=r(i + 1))
            for i in range(30)
        ]
        self.project.sequence.captions[17].text = "需要调整的那一句"
        self.project.sequence.tracks = [Track(
            id="v1", kind="video", clips=[Clip(
                id="clip-one", asset_ref=AssetReference("asset-one", "C:/media/test.png"),
                timeline_start=r(0), timeline_end=r(30), source_start=r(0),
            )],
        )]

    def test_caption_lookup_returns_only_matching_fields_and_revision(self) -> None:
        result = self.service.project_lookup(
            self.project.project_id, entity_type="caption",
            text_contains="调整", fields=["text", "start", "end"],
        )
        self.assertEqual(result["revision"], "0")
        self.assertEqual(result["items"], [{
            "id": "cap-17", "text": "需要调整的那一句",
            "start": {"num": "17", "den": "1"},
            "end": {"num": "18", "den": "1"},
        }])
        self.assertFalse(result["hasMore"])
        self.assertEqual(
            self.service.project_lookup(
                self.project.project_id, entity_type="caption", at_seconds=17.5,
            )["items"][0]["id"],
            "cap-17",
        )

    def test_clip_lookup_hides_local_path_until_requested(self) -> None:
        result = self.service.project_lookup(
            self.project.project_id, entity_type="clip", entity_id="clip-one",
        )
        self.assertEqual(result["items"][0]["sourceName"], "test.png")
        self.assertNotIn("assetRef", result["items"][0])
        detailed = self.service.project_lookup(
            self.project.project_id, entity_type="clip", entity_id="clip-one",
            fields=["assetRef", "effects"],
        )
        self.assertEqual(detailed["items"][0]["assetRef"]["sourcePath"], "C:/media/test.png")

    def test_filters_limit_and_invalid_projection(self) -> None:
        result = self.service.project_lookup(
            self.project.project_id, entity_type="caption", limit=2,
            from_seconds=10, to_seconds=20,
        )
        self.assertEqual([item["id"] for item in result["items"]], ["cap-10", "cap-11"])
        self.assertTrue(result["hasMore"])
        with self.assertRaises(EditError):
            self.service.project_lookup(
                self.project.project_id, entity_type="caption", fields=["assetRef"],
            )
        with self.assertRaises(EditError):
            self.service.project_lookup(
                self.project.project_id, entity_type="caption",
                from_seconds=5, to_seconds=4,
            )

    def test_http_mcp_and_python_client_share_the_same_lookup(self) -> None:
        mcp = MCPServer(self.service)
        response = mcp.call_tool("project_lookup", {
            "projectId": self.project.project_id, "entityType": "caption",
            "textContains": "调整", "fields": ["text"],
        })
        self.assertTrue(response["ok"])
        self.assertEqual(response["items"], [{"id": "cap-17", "text": "需要调整的那一句"}])
        self.assertIn("project_lookup", {tool["name"] for tool in mcp.list_tools()})

        with tempfile.TemporaryDirectory() as media_dir:
            api = HttpApi(self.service, media_dir=media_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(api))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = CutVokeClient(f"http://127.0.0.1:{server.server_port}")
                selected = client.project_lookup(
                    self.project.project_id, "caption", entity_id="cap-17",
                    fields=["text"],
                )
                self.assertEqual(selected["items"], response["items"])
                self.assertEqual(selected["revision"], response["revision"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                api.close()

    def test_match_count_is_exact_before_limit_and_projection(self) -> None:
        for caption in self.project.sequence.captions:
            caption.text = "Click Save"
        self.project.sequence.captions[-1].text = "CLICK SAVE"
        for limit in (1, 10, 50):
            with self.subTest(limit=limit):
                result = self.service.project_lookup(
                    self.project.project_id, entity_type="caption",
                    text_contains="click save", fields=[], limit=limit,
                )
                self.assertEqual(result["matchCount"], 30)
                self.assertTrue(result["ambiguous"])
                self.assertEqual(len(result["items"]), min(limit, 30))
                self.assertEqual(result["hasMore"], limit < 30)
                self.assertEqual(result["items"][0], {"id": "cap-0"})

    def test_counting_does_not_serialize_truncated_matches(self) -> None:
        from unittest.mock import patch

        with patch.object(self.project.sequence.captions[-1], "to_dict",
                          side_effect=AssertionError("unreturned caption serialized")):
            result = self.service.project_lookup(
                self.project.project_id, entity_type="caption", limit=1,
            )
        self.assertEqual(result["matchCount"], 30)

    def test_unique_guard_cannot_be_bypassed_by_limit_or_projection(self) -> None:
        before = self.project.to_dict()
        for limit in (1, 10, 50):
            with self.subTest(limit=limit), self.assertRaises(EditError) as caught:
                self.service.project_lookup(
                    self.project.project_id, entity_type="caption",
                    text_contains="普通字幕", fields=[], limit=limit,
                    require_unique=True,
                )
            self.assertEqual(caught.exception.code, "AMBIGUOUS_MATCH")
            self.assertIn("29 caption", caught.exception.message)
            self.assertFalse(caught.exception.committed)
            self.assertFalse(caught.exception.retryable)
        self.assertEqual(self.project.to_dict(), before)

    def test_zero_and_one_match_are_unambiguous_with_or_without_guard(self) -> None:
        for require_unique in (False, True):
            for text, count in (("not present", 0), ("调整", 1)):
                with self.subTest(require_unique=require_unique, text=text):
                    result = self.service.project_lookup(
                        self.project.project_id, entity_type="caption",
                        text_contains=text, require_unique=require_unique,
                    )
                    self.assertEqual(result["matchCount"], count)
                    self.assertEqual(len(result["items"]), count)
                    self.assertFalse(result["ambiguous"])
                    self.assertFalse(result["hasMore"])

    def test_time_and_id_filters_disambiguate_with_existing_half_open_bounds(self) -> None:
        for caption in self.project.sequence.captions:
            caption.text = "Click Save"
        for filters in ({"at_seconds": 17}, {"from_seconds": 17, "to_seconds": 18},
                        {"entity_id": "cap-17"},
                        {"entity_id": "cap-17", "at_seconds": 17.5}):
            with self.subTest(filters=filters):
                result = self.service.project_lookup(
                    self.project.project_id, entity_type="caption",
                    text_contains="Click Save", require_unique=True, **filters,
                )
                self.assertEqual(result["matchCount"], 1)
                self.assertFalse(result["ambiguous"])
                self.assertEqual(result["items"][0]["id"], "cap-17")
        excluded = self.service.project_lookup(
            self.project.project_id, entity_type="caption", entity_id="cap-17",
            at_seconds=18, require_unique=True,
        )
        self.assertEqual(excluded["matchCount"], 0)
        with self.assertRaises(EditError) as caught:
            self.service.project_lookup(
                self.project.project_id, entity_type="caption", text_contains="Save",
                from_seconds=17, to_seconds=19, require_unique=True,
            )
        self.assertEqual(caught.exception.code, "AMBIGUOUS_MATCH")

    def test_repeated_clip_names_across_tracks_have_same_contract(self) -> None:
        self.project.sequence.tracks.append(Track(id="v2", kind="video", clips=[Clip(
            id="clip-two", asset_ref=AssetReference("asset-two", "/other/TEST.png"),
            timeline_start=r(0), timeline_end=r(30), source_start=r(0),
        )]))
        result = self.service.project_lookup(
            self.project.project_id, entity_type="clip", text_contains="test.png", limit=1,
        )
        self.assertEqual(result["matchCount"], 2)
        self.assertTrue(result["ambiguous"])
        self.assertTrue(result["hasMore"])
        self.assertNotIn("assetRef", result["items"][0])
        with self.assertRaises(EditError) as caught:
            self.service.project_lookup(
                self.project.project_id, entity_type="clip", text_contains="test.png",
                at_seconds=1, limit=1, require_unique=True,
            )
        self.assertEqual(caught.exception.code, "AMBIGUOUS_MATCH")
        resolved = self.service.project_lookup(
            self.project.project_id, entity_type="clip", entity_id="clip-two",
            require_unique=True, fields=[],
        )
        self.assertEqual(resolved["items"], [{"id": "clip-two", "trackId": "v2"}])
        self.assertEqual(resolved["matchCount"], 1)

    def test_invalid_guard_values_are_not_truthy_coerced(self) -> None:
        mcp = MCPServer(self.service)
        self.addCleanup(mcp.close)
        for value in (None, 0, 1, "false", "true", "yes", [], {}):
            with self.subTest(value=value):
                with self.assertRaises(EditError) as caught:
                    self.service.project_lookup(
                        self.project.project_id, entity_type="caption", require_unique=value,
                    )
                self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
                response = mcp.call_tool("project_lookup", {
                    "projectId": self.project.project_id, "entityType": "caption",
                    "requireUnique": value,
                })
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT")

    def test_guard_is_exposed_through_mcp_http_and_real_client(self) -> None:
        from cutvoke.interfaces.client import CutVokeApiError

        mcp = MCPServer(self.service)
        self.addCleanup(mcp.close)
        schema = next(t for t in mcp.list_tools() if t["name"] == "project_lookup")["inputSchema"]
        self.assertEqual(schema["properties"]["requireUnique"]["type"], "boolean")
        arguments = {"projectId": self.project.project_id, "entityType": "caption",
                     "textContains": "普通字幕", "limit": 1, "requireUnique": True}
        response = mcp.call_tool("project_lookup", arguments)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "AMBIGUOUS_MATCH")
        self.assertFalse(response["error"]["committed"])
        self.assertIn("entityId", response["error"]["recovery"]["message"])
        recipe = mcp.call_tool("editor_help", {"topic": "recipes", "recipe": "caption-fix"})["help"]
        self.assertTrue(recipe["steps"][0]["arguments"]["requireUnique"])
        self.assertIn("matchCount=1", recipe["steps"][1]["reuse"])

        with tempfile.TemporaryDirectory() as media_dir:
            api = HttpApi(self.service, media_dir=media_dir)
            status, rest_response = api.handle("GET",
                CutVokeClient._project_path(self.project.project_id) + "/lookup", arguments)
            self.assertEqual(status, 409)
            self.assertEqual(rest_response["error"]["code"], response["error"]["code"])
            self.assertFalse(rest_response["error"]["committed"])
            server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(api))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = CutVokeClient(f"http://127.0.0.1:{server.server_port}")
                with self.assertRaises(CutVokeApiError) as caught:
                    client.project_lookup(self.project.project_id, "caption",
                                          text_contains="普通字幕", limit=1, require_unique=True)
                self.assertEqual(caught.exception.status, 409)
                self.assertEqual(caught.exception.code, "AMBIGUOUS_MATCH")
                for value in ("false", "False"):
                    listed = client._request("GET", client._project_path(self.project.project_id)
                                             + "/lookup?entityType=caption&requireUnique=" + value)
                    self.assertEqual(listed["matchCount"], 30)
                for value in ("yes", "0", "1", "null"):
                    with self.subTest(value=value), self.assertRaises(CutVokeApiError) as invalid:
                        client._request("GET", client._project_path(self.project.project_id)
                                        + "/lookup?entityType=caption&requireUnique=" + value)
                    self.assertEqual(invalid.exception.code, "INVALID_ARGUMENT")
                resolved = client.project_lookup(
                    self.project.project_id, "caption", text_contains="普通字幕",
                    at_seconds=19, fields=["text"], require_unique=True,
                )
                self.assertEqual(resolved["items"], [{"id": "cap-19", "text": "普通字幕 19"}])
                self.assertEqual(resolved["matchCount"], 1)
                self.assertFalse(resolved["ambiguous"])
                empty = client.project_lookup(self.project.project_id, "caption",
                                              text_contains="not present", require_unique=True)
                self.assertEqual(empty["matchCount"], 0)
                with self.assertRaises(ValueError):
                    client.project_lookup(self.project.project_id, "caption", require_unique="false")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                api.close()

    def test_persisted_lookup_and_targeted_write_keep_revision_and_undo_contract(self) -> None:
        from contextlib import ExitStack
        from pathlib import Path
        from cutvoke.domain.protocol import Command
        from cutvoke.storage.store import ProjectStore

        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            store = ProjectStore(str(Path(directory) / "projects.sqlite"))
            reader_store = ProjectStore(str(Path(directory) / "projects.sqlite"))
            cleanup.callback(store.close)
            cleanup.callback(reader_store.close)
            writer, reader = EditService(store), EditService(reader_store)
            project = writer.create_project("repeated")
            # Prime the independent reader before writes to verify live storage reads.
            self.assertEqual(reader.project_lookup(project.project_id, entity_type="caption")["matchCount"], 0)
            for index in (0, 1):
                writer.execute(Command(type="caption.add", project_id=project.project_id,
                    expected_revision=str(index), payload={"captionId": f"repeat-{index}",
                    "text": "Click Save", "start": {"num": str(index), "den": "1"},
                    "end": {"num": str(index + 1), "den": "1"}}))
            with self.assertRaises(EditError) as caught:
                reader.project_lookup(project.project_id, entity_type="caption",
                                      text_contains="Save", limit=1, require_unique=True)
            self.assertEqual(caught.exception.code, "AMBIGUOUS_MATCH")
            resolved = reader.project_lookup(project.project_id, entity_type="caption",
                text_contains="Save", at_seconds=1, require_unique=True)
            self.assertEqual(resolved["revision"], "2")
            self.assertEqual(resolved["items"][0]["id"], "repeat-1")
            command = Command(type="caption.update", project_id=project.project_id,
                expected_revision=resolved["revision"], command_id="resolved-update",
                payload={"captionId": resolved["items"][0]["id"], "text": "Click Export"})
            result = reader.execute(command)
            self.assertEqual(result.to_dict(), reader.execute(command).to_dict())
            current = writer.get_project(project.project_id)
            self.assertEqual(current.revision, "3")
            self.assertEqual([c.text for c in current.sequence.captions], ["Click Save", "Click Export"])
            with self.assertRaises(EditError) as stale:
                writer.execute(Command(type="caption.update", project_id=project.project_id,
                    expected_revision=resolved["revision"], payload={"captionId": "repeat-1", "text": "stale"}))
            self.assertEqual(stale.exception.code, "REVISION_CONFLICT")
            writer.execute(Command(type="history.undo", project_id=project.project_id,
                                   expected_revision="3", payload={}))
            after_undo = reader.project_lookup(project.project_id, entity_type="caption", text_contains="Save")
            self.assertEqual(after_undo["matchCount"], 2)
            self.assertEqual(after_undo["revision"], "4")
