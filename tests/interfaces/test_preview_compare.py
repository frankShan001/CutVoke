"""Same-snapshot visual dry-runs over synthetic images and real PNG rendering."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from cutvoke.editing.service import EditService
from cutvoke.interfaces.mcp_server import MCPServer
from cutvoke.storage.store import ProjectStore


def rat(n):
    return {"num": str(n), "den": "1"}


class PreviewCompareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ProjectStore(str(self.root / "projects.sqlite"))
        self.service = EditService(self.store)
        self.server = MCPServer(self.service)
        self.call("create_project", projectId="p", width=320, height=180, fps=15)
        self.assets = []
        for color in ("red", "blue"):
            path = self.root / f"{color}.png"
            Image.new("RGB", (320, 180), color).save(path)
            self.assets.append(self.call("media_import", paths=[str(path)], projectId="p")["assets"][0]["assetId"])
        self.apply("clip.insert", {"trackId": "v", "createTrackKind": "video", "clipId": "c",
            "assetId": self.assets[0], "timelineStart": rat(0), "timelineEnd": rat(3)})

    def tearDown(self):
        self.server.close()
        self.store.close()
        self.temp.cleanup()

    def call(self, name, **args):
        result = self.server.call_tool(name, args)
        self.assertTrue(result["ok"], result)
        return result

    def apply(self, kind, payload):
        return self.call("command_apply", projectId="p", expectedRevision=self.service.get_project("p").revision,
                         type=kind, payload=payload)

    def args(self, **extra):
        return {"projectId": "p", "expectedRevision": self.service.get_project("p").revision,
                "type": "asset.swap", "payload": {"clipIds": ["c"], "assetId": self.assets[1]},
                "times": [0.5, 1.5, 2.5], "width": 320, **extra}

    def test_real_pixels_wire_order_and_no_persistent_writes(self):
        snapshot = copy.deepcopy(self.service.get_project("p").to_dict())
        events = self.call("project_events", projectId="p")["events"]
        response = self.server._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "preview_compare", "arguments": self.args()}})["result"]
        self.assertFalse(response["isError"], response)
        self.assertEqual(["text"] + ["image"] * 6, [block["type"] for block in response["content"]])
        data = json.loads(response["content"][0]["text"])
        self.assertEqual(data["revision"], snapshot["revision"])
        self.assertTrue(data["candidate"])
        self.assertNotIn("project", data)
        self.assertNotIn("beforeProject", data)
        for index, pair in enumerate(data["pairs"]):
            for offset, label, channel in ((0, "before", 0), (1, "after", 2)):
                self.assertEqual(pair[label]["imageIndex"], index * 2 + offset)
                with Image.open(pair[label]["path"]) as frame:
                    pixel = frame.convert("RGB").getpixel((160, 90))
                    self.assertGreater(pixel[channel], 240)
                    self.assertLess(pixel[2 - channel], 15)
        self.assertEqual(snapshot, self.service.get_project("p").to_dict())
        self.assertEqual(events, self.call("project_events", projectId="p")["events"])
        reader = ProjectStore(str(self.root / "projects.sqlite"))
        try:
            self.assertEqual(snapshot, EditService(reader).get_project("p").to_dict())
        finally:
            reader.close()
        # Applying the exact command yields the same cached candidate pixels.
        self.apply("asset.swap", self.args()["payload"])
        applied = self.call("preview_frames", projectId="p", times=[0.5], width=320)
        self.assertEqual(Path(data["pairs"][0]["after"]["path"]).read_bytes(),
                         Path(applied["frames"][0]["path"]).read_bytes())

    def test_validation_rejects_stale_side_effects_and_bounds(self):
        cases = [self.args(expectedRevision="0"), self.args(times=[]), self.args(times=[0, 1, 2, 2.5]),
                 self.args(times=[-1]), self.args(type="export.enqueue", payload={"outPath": str(self.root / "bad.mp4")}),
                 self.args(type="clip.remove", payload={"clipId": "c"}), self.args(times=[3]),
                 self.args(width=0), self.args(times=[float("nan")]),
                 self.args(type="edit.batch", payload={"commands": [{"type": "export.enqueue", "payload": {"outPath": str(self.root / "bad.mp4")}}]}), self.args(payload={"clipIds": ["missing"], "assetId": self.assets[1]})]
        snapshot = copy.deepcopy(self.service.get_project("p").to_dict())
        with patch("cutvoke.rendering.render.RenderService.extract_frame") as renderer:
            for args in cases:
                with self.subTest(args=args):
                    self.assertFalse(self.server.call_tool("preview_compare", args)["ok"])
                    self.assertEqual(snapshot, self.service.get_project("p").to_dict())
            renderer.assert_not_called()
        self.assertFalse((self.root / "bad.mp4").exists())
        self.assertIsNone(self.service._export_queue)

    def test_before_and_candidate_share_snapshot_even_if_edit_occurs_after_dry_run(self):
        original = self.service.preview_command
        def intervening(command, **kwargs):
            result = original(command, **kwargs)
            self.apply("clip.remove", {"clipId": "c"})
            return result
        with patch.object(self.service, "preview_command", side_effect=intervening):
            result = self.call("preview_compare", **self.args(times=[1]))
        self.assertEqual("1", result["revision"])
        self.assertEqual("2", self.service.get_project("p").revision)
        self.assertEqual(2, len(result["_images"]))

    def test_discovery_and_http_share_contract(self):
        schema = next(t for t in self.server.list_tools() if t["name"] == "preview_compare")["inputSchema"]
        self.assertIn("expectedRevision", schema["required"])
        self.assertEqual(3, schema["properties"]["times"]["maxItems"])
        status, result = self.server._agent_api.api.handle("POST", "/api/v1/agent/preview_compare", self.args(times=[1]))
        self.assertEqual(200, status, result)
        self.assertEqual(2, len(result["images"]))
        self.assertNotIn("_images", result)

    def test_caption_only_candidate_has_distinct_pixels_and_unchanged_timing(self):
        self.apply("caption.add", {"captionId": "cap", "text": "BEFORE", "fontSize": 80,
                                   "start": rat(0), "end": rat(3)})
        original = copy.deepcopy(self.service.get_project("p").to_dict())
        result = self.call("preview_compare", **self.args(type="caption.update",
            payload={"captionId": "cap", "text": "AFTER"}, times=[1]))
        pair = result["pairs"][0]
        self.assertNotEqual(Path(pair["before"]["path"]).read_bytes(), Path(pair["after"]["path"]).read_bytes())
        self.assertEqual(original, self.service.get_project("p").to_dict())
        self.assertEqual(result["changedEntities"][0]["timelineRange"]["before"],
                         result["changedEntities"][0]["timelineRange"]["after"])

    def test_empty_frame_mapping_does_not_shift_following_image(self):
        # A synthetic render stub tests the protocol's empty-picture branch only.
        tools = self.server._agent_api
        def render(args, _project_override=None):
            if _project_override.sequence.tracks[0].clips[0].asset_ref.asset_id == self.assets[0]:
                return {"frames": [{"time": args["times"][0], "empty": True}], "_images": []}
            return {"frames": [{"time": args["times"][0], "empty": False}],
                    "_images": [{"type": "image", "mimeType": "image/png", "data": "stub"}]}
        with patch.object(tools, "preview_frames", side_effect=render):
            result = self.call("preview_compare", **self.args(times=[1, 2]))
        self.assertEqual([None, None], [p["before"]["imageIndex"] for p in result["pairs"]])
        self.assertEqual([0, 1], [p["after"]["imageIndex"] for p in result["pairs"]])
