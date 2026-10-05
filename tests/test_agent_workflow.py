"""Exercise an external editor's real media, preview, export and handoff flow."""
import base64
import copy
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from cutvoke.core.export_queue import ExportQueue
from cutvoke.core.httpapi import HttpApi
from cutvoke.core.mcp_server import MCPServer
from cutvoke.core.projectpack import unpack_project
from cutvoke.core.render import RenderCancelled, RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore


def rat(t):
    return {"num": str(t), "den": "1"}


class AgentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = ProjectStore(str(self.root / "projects.sqlite"))
        self.service = EditService(self.store)
        self.server = MCPServer(self.service)
        self.image = self.root / "blue.png"
        Image.new("RGB", (320, 180), (15, 35, 65)).save(self.image)

    def tearDown(self):
        self.server.close()
        self.store.close()
        self.temporary.cleanup()

    def call(self, tool, **arguments):
        result = self.server.call_tool(tool, arguments)
        self.assertTrue(result["ok"], result)
        return result

    def apply(self, kind, payload, *, revision=None, **extra):
        return self.call("command_apply", projectId="original", type=kind, payload=payload,
                         expectedRevision=revision or self.service.get_project("original").revision, **extra)

    def wait_job(self, tool, job_id):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = self.call(tool, projectId="original", jobId=job_id)
            status = job.get("state", job.get("status"))
            if status in ("completed", "succeeded", "failed", "cancelled"):
                self.assertIn(status, ("completed", "succeeded"), job)
                return job
            time.sleep(0.05)
        self.fail("job did not complete")

    def test_real_external_editor_round_trip(self):
        self.call("create_project", projectId="original", name="外部 Agent 流程", width=320, height=180, fps=15)
        imported = self.call("media_import", paths=[str(self.image)])["assets"][0]
        self.assertEqual(self.call("media_import", paths=[str(self.image)])["assets"][0]["assetId"], imported["assetId"])
        self.assertEqual(self.call("media_inspect", assetId=imported["assetId"])["width"], 320)
        info = self.call("commands_list", type="clip.insert")["commands"][0]
        self.assertTrue(info["previewSupported"])
        lease = self.call("edit_lock", projectId="original", action="acquire", owner="workflow-test")["lease"]["leaseId"]
        batch = {"commands": [
            {"type": "clip.insert", "payload": {"trackId": "picture", "createTrackKind": "video",
                "clipId": "picture-clip", "assetId": imported["assetId"], "timelineStart": rat(0), "timelineEnd": rat(3)}},
            {"type": "clip.insert", "payload": {"trackId": "titles", "createTrackKind": "text", "clipId": "title",
                "text": {"content": "AGENT", "fontSize": 180, "color": "#ffd040", "x": 0.5, "y": 0.4},
                "timelineStart": rat(0), "timelineEnd": rat(3)}},
            {"type": "caption.add", "payload": {"captionId": "caption", "text": "可继续编辑", "fontSize": 180,
                "start": rat(0), "end": rat(3)}},
        ]}
        self.call("command_preview", projectId="original", type="edit.batch", payload=batch, expectedRevision="0")
        self.assertEqual(self.service.get_project("original").revision, "0")
        change = self.apply("edit.batch", batch, revision="0", editLeaseId=lease, commandId="stable-batch", actorId="workflow-test")
        repeat = self.apply("edit.batch", batch, revision="0", editLeaseId=lease, commandId="stable-batch", actorId="workflow-test")
        self.assertEqual(change["revision"], repeat["revision"])
        self.assertEqual(change["revision"], "1")
        conflict = self.server.call_tool("command_apply", {"projectId": "original", "type": "caption.update",
            "payload": {"captionId": "caption", "text": "must not apply"}, "expectedRevision": "0", "editLeaseId": lease})
        self.assertEqual(conflict["error"]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.call("media_list", projectId="original", used=True)["assets"][0]["assetId"], imported["assetId"])
        frames = self.call("preview_frames", projectId="original", expectedRevision="1", times=[1], width=320)
        self.assertTrue(base64.b64decode(frames["_images"][0]["data"]).startswith(b"\x89PNG"))
        # Agent images contain both titles and editable subtitles.
        with Image.open(frames["frames"][0]["path"]) as rendered, Image.open(self.image) as source:
            difference = ImageChops.difference(rendered.convert("RGB"), source)
            self.assertGreater(max(ImageStat.Stat(difference).mean), 1)
        wire = self.server._handle({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "preview_frames", "arguments": {"projectId": "original", "times": [1], "width": 320}}})
        self.assertEqual([c["type"] for c in wire["result"]["content"]], ["text", "image"])
        self.assertNotIn("_images", json.loads(wire["result"]["content"][0]["text"]))
        prepared = self.call("preview_prepare", projectId="original", expectedRevision="1")
        ready = self.wait_job("preview_job", prepared["jobId"])
        self.assertTrue(Path(ready["path"]).is_file())
        job = self.apply("export.enqueue", {"outPath": str(self.root / "result.mp4"), "quality": "low",
                                             "range": {"start": 0.5, "end": 2.5}}, editLeaseId=lease,
                         commandId="export-once")
        exported = self.wait_job("export_job", job["changedEntities"][0]["jobId"])
        self.assertEqual("1", job["revision"])
        self.assertEqual("1", job["previousRevision"])
        self.assertAlmostEqual(RenderService().probe_media(exported["result"]["output_path"])["duration"], 2, delta=0.15)
        retry = self.apply("export.enqueue", {"outPath": str(self.root / "result.mp4"), "quality": "low",
                                             "range": {"start": 0.5, "end": 2.5}}, revision="0",
                           commandId="export-once")
        self.assertEqual(job["changedEntities"], retry["changedEntities"])
        self.assertEqual("1", retry["revision"])
        self.assertEqual(len(self.store.list_export_jobs("original")), 1)
        stale = self.server.call_tool("command_apply", {"projectId": "original", "type": "export.enqueue",
            "expectedRevision": "0", "payload": {"outPath": str(self.root / "stale.mp4")}})
        self.assertEqual(stale["error"]["code"], "REVISION_CONFLICT")
        mismatch = self.server.call_tool("command_apply", {"projectId": "original", "type": "export.enqueue",
            "expectedRevision": "1", "commandId": "export-once", "payload": {"outPath": str(self.root / "other.mp4")}})
        self.assertEqual(mismatch["error"]["code"], "IDEMPOTENCY_MISMATCH")
        with ProjectStore(str(self.root / "projects.sqlite")) as retry_store:
            retry_service = EditService(retry_store)
            retry_server = MCPServer(retry_service)
            try:
                repeated = retry_server.call_tool("command_apply", {"projectId": "original", "type": "export.enqueue",
                    "expectedRevision": "0", "commandId": "export-once", "payload": {"outPath": str(self.root / "result.mp4"),
                        "quality": "low", "range": {"start": 0.5, "end": 2.5}}})
                self.assertEqual(job["changedEntities"], repeated["changedEntities"])
                self.assertEqual("1", repeated["revision"])
                self.assertEqual(len(retry_store.list_export_jobs("original")), 1)
            finally:
                retry_server.close()
        before = copy.deepcopy(self.service.get_project("original").to_dict())
        clone = self.call("project_clone", projectId="original", expectedRevision="1", newProjectId="copy", name="交接副本")
        self.assertEqual(clone["revision"], "0")
        self.assertEqual(before, self.service.get_project("original").to_dict())
        rejected = self.server.call_tool("project_clone", {"projectId": "original", "expectedRevision": "1", "newProjectId": "original"})
        self.assertFalse(rejected["ok"])
        package = self.call("project_package", projectId="original", expectedRevision="1", outPath=str(self.root / "handoff.zip"))
        restored, _, warnings = unpack_project(package["path"], str(self.root / "restored"))
        self.assertEqual(restored.sequence.tracks[1].clips[0].effects[0]["params"]["content"], "AGENT")
        self.assertEqual(restored.sequence.captions[0].text, "可继续编辑")
        events = self.call("project_events", projectId="original")
        self.assertTrue(events["events"])
        self.assertEqual(self.call("project_list", query="交接")["projects"][0]["id"], "copy")
        self.call("edit_lock", projectId="original", action="release", leaseId=lease)
        self.apply("history.undo", {})
        self.assertEqual(len(self.service.get_project("original").sequence.tracks), 0)
        self.apply("history.redo", {})
        with ProjectStore(str(self.root / "projects.sqlite")) as human_store:
            human = EditService(human_store)
            self.assertEqual(human.get_project("original").sequence.captions[0].text, "可继续编辑")
            self.assertIsNone(human.get_edit_lease("original"))
            human.close()

    def test_validation_and_http_parity(self):
        for values in ([0] * 7, [], [float("nan")], [-1]):
            result = self.server.call_tool("preview_frames", {"path": str(self.image), "times": values})
            self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT", result)
        broken = self.root / "broken.mp4"
        broken.write_text("not media")
        result = self.server.call_tool("media_import", {"paths": [str(broken)]})
        self.assertFalse(result["ok"])
        self.assertEqual([a for a in self.store.list_assets() if not a["builtin"]], [])
        source = self.call("preview_frames", path=str(self.image), times=[0], width=320)
        self.assertFalse(source["frames"][0]["empty"])
        api = self.server._agent_tools().api
        status, bad = api.handle("POST", "/api/v1/agent/preview_frames", {"path": str(self.image), "times": [0] * 7})
        self.assertEqual(status, 400)
        status, good = api.handle("POST", "/api/v1/agent/preview_frames", {"path": str(self.image), "times": [0], "width": 320})
        self.assertEqual(status, 200)
        self.assertEqual(good["images"][0]["mimeType"], "image/png")

    def test_live_export_survives_another_queue_and_can_be_cancelled(self):
        project = self.service.create_project("original")
        entered, stopped = threading.Event(), threading.Event()
        class BlockingRenderer:
            def render(self, project, out_path, **options):
                entered.set()
                event = options["cancel_event"]
                while not event.is_set():
                    time.sleep(0.01)
                stopped.set()
                raise RenderCancelled("cancelled")
        first = ExportQueue(self.store, self.service, render_factory=BlockingRenderer)
        second_store = ProjectStore(str(self.root / "projects.sqlite"))
        second = None
        try:
            job = first.submit(project, {"outPath": str(self.root / "cancelled.mp4")})
            self.assertTrue(entered.wait(3))
            second = ExportQueue(second_store, self.service, autostart=False)
            self.assertEqual(second_store.get_export_job(job["jobId"])["status"], "running")
            self.assertTrue(second.cancel(job["jobId"])["cancelled"])
            self.assertTrue(stopped.wait(3))
            self.assertEqual(self.store.get_export_job(job["jobId"])["status"], "cancelled")
            self.assertFalse((self.root / "cancelled.mp4").exists())
        finally:
            first.shutdown()
            if second:
                second.shutdown()
            second_store.close()


if __name__ == "__main__":
    unittest.main()
