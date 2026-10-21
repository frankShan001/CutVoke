"""Real persistent stdio connections, live source updates and draining jobs."""
import io
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest
from PIL import Image

from cutvoke.interfaces.mcp_supervisor import EngineWorker, MCPSupervisor, worker_command


class Client:
    def __init__(self, root):
        self.root = root
        self.package = root / "src" / "cutvoke"
        original = Path(__file__).resolve().parents[2] / "src" / "cutvoke"
        def copy(source, target):
            if "assets" in Path(source).relative_to(original).parts:
                try:
                    os.link(source, target)
                    return target
                except OSError:
                    pass
            return shutil.copy2(source, target)
        shutil.copytree(original, self.package, copy_function=copy,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        # Gate work deterministically; a slow CI machine must not finish the
        # job before a fresh interpreter has started and invalidate the test.
        self.gate = root / "hold-jobs"
        self.gate.touch()
        for filename, signature in (("export_queue.py", "    def _execute(self, job: dict, cancel_event: Optional[threading.Event]) -> None:\n"),
                                    ("preview_prepare.py", "    def _prepare(self, job_id, project, event):\n")):
            area = "storage" if filename == "export_queue.py" else "rendering"
            path = self.package / area / filename
            source = path.read_text(encoding="utf-8")
            assert signature in source
            path.write_text(source.replace(signature, signature +
                f"        while os.path.exists({str(self.gate)!r}):\n            time.sleep(.02)\n"), encoding="utf-8")
        self.server_file = self.package / "interfaces" / "mcp_server.py"
        self.original_server = self.server_file.read_text(encoding="utf-8")
        self.log = (root / "stderr.log").open("w", encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(root / "src"), PYTHONIOENCODING="utf-8")
        self.process = subprocess.Popen([sys.executable, "-m", "cutvoke", "mcp", "--data", str(root / "data")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, cwd=root,
            text=True, encoding="utf-8", env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.incoming = queue.Queue()
        self.notifications = []
        self.serial = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        initialized = self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                               "clientInfo": {"name": "reload-test", "version": "1"}})
        assert initialized["result"]["capabilities"]["tools"]["listChanged"]
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _read(self):
        try:
            for line in self.process.stdout:
                self.incoming.put(json.loads(line))  # stdout must remain pure JSON
        finally:
            self.incoming.put(None)

    def send(self, value):
        self.process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def rpc(self, method, params=None):
        self.serial += 1
        self.send({"jsonrpc": "2.0", "id": self.serial, "method": method, "params": params or {}})
        deadline = time.monotonic() + 45
        while True:
            value = self.incoming.get(timeout=max(.01, deadline - time.monotonic()))
            assert value is not None, (self.root / "stderr.log").read_text(encoding="utf-8")
            if "id" not in value:
                self.notifications.append(value)
            else:
                assert value["id"] == self.serial, value
                return value

    def call(self, tool_name, **arguments):
        response = self.rpc("tools/call", {"name": tool_name, "arguments": arguments})
        assert "error" not in response, response
        return json.loads(response["result"]["content"][0]["text"])

    def update(self, build=2, *, broken=False):
        source = self.original_server.replace('return {"ok": True, **status}',
            f'return {{"ok": True, "testBuild": {build}, **status}}')
        assert source != self.original_server
        source = source.replace('"description": "创建一个新的视频编辑工程，返回 projectId 和初始 revision。"',
            f'"description": "Fresh engine build {build}"')
        source = source.replace('        for tool in tools:\n',
            '        tools.append({"name": "reload_probe", "description": "Newly installed tool", '
            '"inputSchema": {"type": "object", "properties": {}}})\n        for tool in tools:\n')
        source = source.replace('            if name == "runtime":\n',
            f'            if name == "reload_probe":\n                return {{"ok": True, "build": {build}}}\n'
            '            if name == "runtime":\n')
        if broken:
            source += '\nraise RuntimeError("incomplete test installation")\n'
        self.server_file.write_text(source, encoding="utf-8")

    def wait_for(self, predicate, timeout=35):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(.2)
        pytest.fail("automatic update/job did not complete: " + (self.root / "stderr.log").read_text(encoding="utf-8"))

    def updated(self, build=2):
        return self.wait_for(lambda: (r if (r := self.call("runtime")).get("testBuild") == build else None))

    def project(self):
        result = self.call("create_project", projectId="reload", name="CHASE 光", width=320, height=180)
        assert result["ok"], result
        image = self.root / "source.png"
        Image.new("RGB", (320, 180), (20, 150, 220)).save(image)
        result = self.call("command_apply", projectId="reload", expectedRevision="0", commandId="insert-once",
            type="clip.insert", payload={"clipId": "shot", "trackId": "video", "createTrackKind": "video",
                "sourcePath": str(image), "timelineStart": {"num": "0", "den": "1"},
                "timelineEnd": {"num": "1", "den": "1"}})
        assert result["ok"], result
        return result

    def close(self):
        self.gate.unlink(missing_ok=True)
        self.process.stdin.close()
        try:
            self.process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.process.stdout.close()
        self.log.close()
        assert self.process.returncode == 0, (self.root / "stderr.log").read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path):
    client = Client(tmp_path)
    try:
        yield client
    finally:
        client.close()


def test_same_connection_activates_new_code_preserves_lease_history_and_replay(client):
    original = client.call("runtime")
    edit = client.project()
    lease = client.call("edit_lock", projectId="reload", action="acquire", owner="reload-test")
    assert lease["ok"], lease
    client.update()
    updated = client.updated()
    assert updated["processId"] != original["processId"]
    # Windows venv launchers can be a redirector parent. Compare the actual
    # transport identity, not Popen's possibly intermediary PID.
    assert updated["autoReload"]["supervisorProcessId"] == original["autoReload"]["supervisorProcessId"]
    assert updated["autoReload"]["generation"] == 2
    assert not updated["restartRequired"]
    tools = client.rpc("tools/list")["result"]["tools"]
    assert next(tool for tool in tools if tool["name"] == "create_project")["description"] == "Fresh engine build 2"
    assert any(tool["name"] == "reload_probe" for tool in tools)
    assert client.call("reload_probe")["build"] == 2
    assert client.call("project_query", projectId="reload")["project"]["name"] == "CHASE 光"
    replay = client.call("command_apply", projectId="reload", expectedRevision="0", commandId="insert-once",
            type="clip.insert", payload={"clipId": "shot", "trackId": "video", "createTrackKind": "video",
                "sourcePath": str(client.root / "source.png"), "timelineStart": {"num": "0", "den": "1"},
                "timelineEnd": {"num": "1", "den": "1"}})
    assert replay == edit
    # Lease is still stored; a different owner cannot steal it across restart.
    conflict = client.call("edit_lock", projectId="reload", action="acquire", owner="other-owner")
    assert not conflict["ok"], conflict
    released = client.call("edit_lock", projectId="reload", action="release", leaseId=lease["lease"]["leaseId"])
    assert released["ok"], released
    undone = client.call("command_apply", projectId="reload", expectedRevision=edit["revision"],
                         commandId="undo-once", type="history.undo", payload={})
    assert undone["ok"], undone
    assert {item["method"] for item in client.notifications} >= {
        "notifications/tools/list_changed", "notifications/resources/list_changed"}
    assert client.rpc("_cutvoke/status")["error"]["code"] == -32601
    client.process.stdin.write('{broken\n')
    client.process.stdin.flush()
    assert client.incoming.get(timeout=10)["error"]["code"] == -32700
    client.send([])
    assert client.incoming.get(timeout=10)["error"]["code"] == -32600
    assert client.rpc("ping")["result"] == {}


def test_active_export_and_preview_drain_then_original_preview_remains_playable(client):
    edit = client.project()
    preview = client.call("preview_prepare", projectId="reload", expectedRevision=edit["revision"])
    assert preview["ok"], preview
    export = client.call("command_apply", projectId="reload", expectedRevision=edit["revision"],
        commandId="export-once", type="export.enqueue", payload={"outPath": str(client.root / "result.mp4")})
    assert export["ok"], export
    export_id = export["changedEntities"][0]["jobId"]
    client.update()
    updated = client.updated()
    assert updated["autoReload"]["drainingWorkers"] == 1, updated
    # New edits are possible while the old immutable render snapshots finish.
    changed = client.call("command_apply", projectId="reload", expectedRevision=export["revision"],
                         commandId="new-generation-edit", type="track.add", payload={"trackId": "extra", "kind": "video"})
    assert changed["ok"], changed
    client.gate.unlink()
    def completed():
        job = client.call("export_job", projectId="reload", jobId=export_id)
        assert job.get("status") not in {"failed", "interrupted", "cancelled"}, job
        return job if job.get("status") == "succeeded" else None
    final_export = client.wait_for(completed)
    assert final_export["revision"] == edit["revision"]
    def preview_done():
        job = client.call("preview_job", projectId="reload", jobId=preview["jobId"])
        assert job.get("state") not in {"failed", "cancelled"}, json.dumps(job)
        return job if job.get("state") == "completed" else None
    client.wait_for(preview_done)
    client.wait_for(lambda: client.call("runtime")["autoReload"]["drainingWorkers"] == 0)
    job = client.call("preview_job", projectId="reload", jobId=preview["jobId"])
    assert job["ok"] and job["revision"] == edit["revision"], job
    from cutvoke.rendering.render import RenderService
    renderer = RenderService()
    renderer._verify(job["path"], 1, 320, 180)
    renderer._verify(str(client.root / "result.mp4"), 1, 320, 180)
    # A second generation also transfers previously inherited preview records.
    client.update(3)
    client.updated(3)
    assert client.call("preview_job", projectId="reload", jobId=preview["jobId"])["path"] == job["path"]


def test_cancel_old_jobs_during_update(client):
    edit = client.project()
    preview = client.call("preview_prepare", projectId="reload", expectedRevision=edit["revision"])
    export = client.call("command_apply", projectId="reload", expectedRevision=edit["revision"],
        commandId="cancel-export", type="export.enqueue", payload={"outPath": str(client.root / "cancelled.mp4")})
    export_id = export["changedEntities"][0]["jobId"]
    client.update()
    assert client.updated()["autoReload"]["drainingWorkers"] == 1
    cancelled = client.call("preview_job", projectId="reload", jobId=preview["jobId"], action="cancel")
    assert cancelled["state"] == "cancelled", cancelled
    cancelled = client.call("command_apply", projectId="reload", expectedRevision=export["revision"],
        commandId="cancel-once", type="export.cancel", payload={"jobId": export_id})
    assert cancelled["ok"], cancelled
    # Cancelled state alone must not prematurely kill still-executing cleanup.
    assert client.call("runtime")["autoReload"]["drainingWorkers"] == 1
    client.gate.unlink()
    client.wait_for(lambda: client.call("runtime")["autoReload"]["drainingWorkers"] == 0)
    assert client.call("export_job", projectId="reload", jobId=export_id)["status"] == "cancelled"
    assert client.call("preview_job", projectId="reload", jobId=preview["jobId"])["state"] == "cancelled"
    assert not (client.root / "cancelled.mp4").exists()


def test_incomplete_update_keeps_old_connection_and_retries_without_reconnect(client):
    client.project()
    original = client.call("runtime")
    client.update(broken=True)
    result = client.wait_for(lambda: (r if (r := client.call("runtime"))["autoReload"]["lastError"] else None))
    assert result["processId"] == original["processId"]
    assert client.call("project_summary", projectId="reload")["ok"]
    pending = client.call("command_apply", projectId="reload", expectedRevision="1", commandId="after-update",
        type="track.add", payload={"trackId": "after-update", "kind": "video"})
    assert pending["error"]["code"] == "AUTO_UPDATE_PENDING" and pending["error"]["committed"] is False
    client.update(3)
    client.updated(3)
    result = client.call("command_apply", projectId="reload", expectedRevision="1", commandId="after-update",
        type="track.add", payload={"trackId": "after-update", "kind": "video"})
    assert result["ok"], result


def test_worker_command_covers_python_and_frozen_launchers(monkeypatch):
    assert worker_command("test.db")[1:4] == ["-m", "cutvoke", "mcp"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert worker_command("test.db")[1:] == ["mcp", "--worker", "--data", "test.db"]


def test_lost_response_is_never_automatically_replayed(tmp_path):
    class LostWorker:
        def __init__(self):
            self.calls = 0
            self.closed = False
        def request(self, *args, **kwargs):
            self.calls += 1
            raise BrokenPipeError("write committed, response lost")
        def close(self):
            self.closed = True
    supervisor = MCPSupervisor(tmp_path / "data.db", output=io.StringIO())
    worker = supervisor.current = LostWorker()
    result = supervisor.handle({"jsonrpc": "2.0", "id": 99, "method": "tools/call",
        "params": {"name": "command_apply", "arguments": {"type": "track.add", "commandId": "same-id"}}})
    assert worker.calls == 1 and worker.closed
    assert result["error"]["data"]["committed"] is None
    assert supervisor.current is None


def test_rejects_bad_messages_and_private_controls_without_touching_worker(tmp_path):
    supervisor = MCPSupervisor(tmp_path / "data.db", output=io.StringIO())
    for message in ([], {"jsonrpc": "2.0", "id": True, "method": "ping"}):
        assert supervisor.handle(message)["error"]["code"] == -32600
    assert supervisor.handle({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": []})["error"]["code"] == -32602
    assert supervisor.handle({"jsonrpc": "2.0", "method": "_cutvoke/restore"}) is None


def test_rapid_updates_coalesce_and_bound_draining_generations(tmp_path):
    disk, now, workers = ["A"], [0.0], []
    class Worker:
        def __init__(self, _path):
            self.runtime = {"sourceFingerprint": disk[0]}
            self.busy = False
            self.closed = False
            self.jobs = []
            workers.append(self)
        def control(self, method, params=None):
            if method == "status":
                return {"runtime": self.runtime, "busy": self.busy,
                        "previewJobs": [], "exportJobs": self.jobs}
            return {"previews": []}
        def request(self, message, **kwargs):
            return {"jsonrpc": "2.0", "id": message["id"], "result": {"tools": [{"name": "runtime"}]}}
        def close(self):
            assert not self.busy  # a source update must never kill active work
            self.closed = True
    def status(loaded):
        return {**loaded, "currentSourceFingerprint": disk[0],
                "restartRequired": disk[0] != loaded["sourceFingerprint"]}
    supervisor = MCPSupervisor(tmp_path / "data.db", worker_factory=Worker, clock=lambda: now[0], output=io.StringIO())
    def advance(build):
        disk[0] = build
        supervisor.tick()
        now[0] += 1.1
        supervisor.tick()
    with patch("cutvoke.interfaces.mcp_supervisor.runtime_status", side_effect=status):
        supervisor.tick()
        workers[0].busy, workers[0].jobs = True, ["first-job"]
        advance("B")
        workers[1].busy = True
        advance("C")
        workers[2].busy = True
        advance("D")
        assert len(workers) == 3 and len(supervisor.draining) == 2
        assert supervisor.routes[("export_job", "first-job")] is workers[0]
        workers[0].busy = False
        supervisor.tick()
        assert workers[0].closed and supervisor.generation == 4
        assert len(supervisor.draining) == 2
        assert supervisor.current.runtime["sourceFingerprint"] == "D"


def test_late_control_response_does_not_poison_the_next_request():
    worker = EngineWorker.__new__(EngineWorker)
    worker.messages = queue.Queue()
    worker._abandoned = set()
    class Pipe:
        reply = False
        last = None
        def write(self, line):
            self.last = json.loads(line)
            if self.reply:
                worker.messages.put(json.dumps({"jsonrpc": "2.0", "id": self.last["id"], "result": {"ok": True}}))
        def flush(self):
            pass
    class Process:
        stdin = Pipe()
    worker.process = Process()
    message = {"jsonrpc": "2.0", "id": 1, "method": "_cutvoke/status"}
    with pytest.raises(TimeoutError):
        worker.request(message, timeout=0)
    old_id = worker.process.stdin.last["id"]
    worker.messages.put(json.dumps({"jsonrpc": "2.0", "id": old_id, "result": {"late": True}}))
    worker.process.stdin.reply = True
    assert worker.request(message, timeout=1)["result"] == {"ok": True}
    assert not worker._abandoned


@pytest.mark.parametrize("error", [None, "preview cancelled by user", {"code": "OTHER_ERROR"}])
def test_job_error_fields_are_not_confused_with_rpc_errors(tmp_path, error):
    supervisor = MCPSupervisor(tmp_path / "data.db", output=io.StringIO())
    class Worker:
        def request(self, message, **kwargs):
            return {"jsonrpc": "2.0", "id": message["id"], "result": {"content": [
                {"type": "text", "text": json.dumps({"ok": True, "error": error})}]}}
    supervisor.current = Worker()
    response = supervisor.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "export_job", "arguments": {"jobId": "job"}}})
    assert json.loads(response["result"]["content"][0]["text"])["error"] == error
