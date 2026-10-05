"""Discover help through the protocol and execute its returned recipe verbatim."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from cutvoke.core.httpapi import HttpApi
from cutvoke.core.mcp_server import MCPServer
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore
from cutvoke.core.protocol import Command


class MCPOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = ProjectStore(str(self.root / "projects.sqlite"))
        self.service = EditService(self.store)
        self.server = MCPServer(self.service)

    def tearDown(self):
        self.server.close()
        self.store.close()
        self.temporary.cleanup()

    def rpc(self, method, params=None):
        return self.server._handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})

    def call(self, name, arguments=None):
        response = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})["result"]
        self.assertFalse(response["isError"], response)
        return json.loads(response["content"][0]["text"])

    def test_initialize_discovery_and_resource_equivalence(self):
        initialized = self.rpc("initialize")["result"]
        self.assertIn("editor_help", initialized["instructions"])
        self.assertIn("resources", initialized["capabilities"])
        tools = self.rpc("tools/list")["result"]["tools"]
        helper = next(t for t in tools if t["name"] == "editor_help")
        self.assertIn("START HERE", helper["description"])
        help_value = self.call("editor_help")["help"]
        resource = self.rpc("resources/read", {"uri": "cutvoke://guide/overview"})["result"]
        self.assertEqual(help_value, json.loads(resource["contents"][0]["text"]))
        for item in self.rpc("resources/list")["result"]["resources"]:
            with self.subTest(uri=item["uri"]):
                self.assertIn("result", self.rpc("resources/read", {"uri": item["uri"]}))
        self.assertIn("resourceTemplates", self.rpc("resources/templates/list")["result"])
        for uri in (None, "file:///etc/passwd", "cutvoke://guide/../../other", "cutvoke://guide/command"):
            with self.subTest(uri=uri):
                self.assertEqual(-32602, self.rpc("resources/read", {"uri": uri})["error"]["code"])

    def test_all_registered_commands_have_typed_payload_schemas(self):
        for item in self.service.command_catalog():
            with self.subTest(command=item["type"]):
                schema = item["inputSchema"]
                self.assertEqual("object", schema["type"])
                self.assertTrue(set(schema["required"]).issubset(schema["properties"]))
                for field in schema["properties"].values():
                    self.assertTrue("type" in field or "anyOf" in field)
                uri_value = self.rpc("resources/read", {"uri": "cutvoke://command/" + item["type"]})["result"]
                self.assertEqual(schema, json.loads(uri_value["contents"][0]["text"])["inputSchema"])

    def test_effect_schema_and_batch_keyframes_are_exact_and_independent(self):
        info = self.call("editor_help", {"topic": "command", "command": "effect.add", "effectId": "cutvoke.transform"})["help"]
        self.assertIn("opacity", info["inputSchema"]["properties"]["params"]["properties"])
        plain = self.call("editor_help", {"topic": "command", "command": "effect.add"})["help"]
        self.assertNotIn("properties", plain["inputSchema"]["properties"]["params"])
        schema = self.call("editor_help", {"topic": "command", "command": "clip.keyframe"})["help"]["inputSchema"]
        valid = {"clipId": "c", "action": "batch", "keyframes": [{"param": "opacity", "time": 0, "value": 0.5}]}
        self.server._validate_arguments(valid, schema)
        with self.assertRaises(ValueError):
            self.server._validate_arguments({"clipId": "c", "action": "batch"}, schema)
        with self.assertRaises(ValueError):
            self.server._validate_arguments({"clipId": "c", "action": "add", "param": "opacity"}, schema)

    def test_errors_explain_recovery_and_do_not_change_state(self):
        self.call("create_project", {"projectId": "p"})
        invalid = self.server.call_tool("command_apply", {"projectId": "p", "expectedRevision": "0", "type": "caption.add", "payload": {}})
        self.assertFalse(invalid["ok"])
        self.assertEqual("caption.add", invalid["error"]["recovery"]["help"]["arguments"]["command"])
        self.assertEqual("0", self.service.get_project("p").revision)
        self.call("command_apply", {"projectId": "p", "expectedRevision": "0", "type": "track.add", "payload": {}})
        conflict = self.server.call_tool("command_apply", {"projectId": "p", "expectedRevision": "0", "type": "track.add", "payload": {}})
        self.assertEqual("REVISION_CONFLICT", conflict["error"]["code"])
        self.assertIn("reconcile", conflict["error"]["recovery"]["message"])
        self.assertEqual("1", self.service.get_project("p").revision)
        unknown = self.server.call_tool("editor_help", {"topic": "command", "command": "effect.add", "effectId": "invented"})
        self.assertEqual("INVALID_ARGUMENT", unknown["error"]["code"])

    def test_http_has_the_same_bundled_help(self):
        api = HttpApi(self.service, media_dir=str(self.root / "media"))
        try:
            status, result = api.handle("POST", "/api/v1/agent/editor_help", {})
            self.assertEqual(200, status)
            self.assertEqual(self.call("editor_help")["help"], result["help"])
        finally:
            api.close()

    def test_legacy_export_receipt_returns_snapshot_revision_after_live_edits(self):
        self.call("create_project", {"projectId": "p"})
        payload = {"outPath": str(self.root / "legacy.mp4")}
        command = Command("export.enqueue", payload, command_id="legacy-export", project_id="p", expected_revision="0")
        self.store.create_export_job(job_id="old-job", project_id="p", revision="0", request_hash="legacy-job",
            out_path=payload["outPath"], quality="high", status="succeeded", expected_revision="0",
            command_id=command.command_id, command_hash=self.service._request_hash(command),
            command_result={"commandId": command.command_id, "previousRevision": "", "revision": "", "transactionId": "",
                            "changedEntities": [{"type": "export_enqueued", "jobId": "old-job"}]})
        self.call("command_apply", {"projectId": "p", "expectedRevision": "0", "type": "track.add", "payload": {}})
        result = self.call("command_apply", {"projectId": "p", "expectedRevision": "0", "type": "export.enqueue", "payload": payload, "commandId": "legacy-export"})
        self.assertEqual("0", result["revision"])
        self.assertEqual("0", result["previousRevision"])
        self.assertEqual("1", self.service.get_project("p").revision)
        self.assertEqual(1, len(self.store.list_export_jobs("p")))

    def test_service_returned_recipe_produces_an_editable_project(self):
        image = self.root / "source.png"
        Image.new("RGB", (960, 540), (12, 45, 65)).save(image)
        recipe = self.call("editor_help", {"topic": "recipes", "recipe": "assemble"})["help"]
        bindings = {"<ABSOLUTE_MEDIA_PATH>": str(image)}

        def bind(value):
            if isinstance(value, dict):
                return {k: bind(v) for k, v in value.items()}
            if isinstance(value, list):
                return [bind(v) for v in value]
            return bindings.get(value, value) if isinstance(value, str) else value

        previous_preview = None
        for step in recipe["steps"]:
            name = step["tool"]
            if name == "command_apply":
                arguments = {**copy.deepcopy(previous_preview), "editLeaseId": bindings["<LEASE>"], "commandId": "recipe-edit"}
            else:
                arguments = bind(step["arguments"])
            if name in ("command_preview", "command_apply"):
                schema = self.call("editor_help", {"topic": "command", "command": arguments["type"]})["help"]["inputSchema"]
                self.server._validate_arguments(arguments["payload"], schema)
                for child in arguments["payload"]["commands"]:
                    schema = self.call("editor_help", {"topic": "command", "command": child["type"]})["help"]["inputSchema"]
                    self.server._validate_arguments(child["payload"], schema)
            if name == "preview_frames":
                rendered = self.rpc("tools/call", {"name": name, "arguments": arguments})["result"]
                self.assertFalse(rendered["isError"], rendered)
                self.assertEqual(3, sum(item["type"] == "image" for item in rendered["content"]))
                continue
            result = self.call(name, arguments)
            if name == "create_project":
                bindings.update({"<PROJECT>": result["projectId"], "<REVISION>": result["revision"]})
            elif name == "media_import":
                bindings["<ASSET>"] = result["assets"][0]["assetId"]
            elif name == "edit_lock" and arguments["action"] == "acquire":
                bindings["<LEASE>"] = result["lease"]["leaseId"]
            elif name == "command_preview":
                previous_preview = arguments
                self.assertEqual("0", self.service.get_project(bindings["<PROJECT>"]).revision)
            elif name == "command_apply":
                bindings["<REVISION>"] = result["revision"]
                self.assertEqual("1", result["revision"])
                self.assertEqual(result, self.call(name, arguments))  # Same ID does not duplicate the edit.
        project = self.service.get_project(bindings["<PROJECT>"])
        self.assertEqual(2, len(project.sequence.tracks))
        self.assertEqual("开始新的故事", project.sequence.captions[0].text)
        summary = self.call("project_summary", {"projectId": project.project_id})
        self.assertFalse(summary.get("editLease"))
        self.call("command_apply", {"projectId": project.project_id, "expectedRevision": "1", "type": "history.undo", "payload": {}})
        self.assertEqual([], self.service.get_project(project.project_id).sequence.tracks)


if __name__ == "__main__":
    unittest.main()
