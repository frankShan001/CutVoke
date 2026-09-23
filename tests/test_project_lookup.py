"""Small read contracts for an Agent changing one subtitle in a large project."""

from __future__ import annotations

import tempfile
import unittest
from http.server import ThreadingHTTPServer
from threading import Thread

from cutvoke.client import CutVokeClient
from cutvoke.core.httpapi import HttpApi, build_handler
from cutvoke.core.mcp_server import MCPServer
from cutvoke.core.model import AssetReference, Caption, Clip, Track
from cutvoke.core.rational import Rational
from cutvoke.core.service import EditError, EditService


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
