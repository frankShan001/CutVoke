"""A real stdio MCP client edits production media in an isolated project copy."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "output/playwright/agent-workflow-20261005"
SOURCE = Path.home() / ".cutvoke/data/projects.sqlite"


def fingerprint():
    with sqlite3.connect(f"file:{SOURCE.as_posix()}?mode=ro", uri=True) as connection:
        rows = connection.execute("SELECT * FROM projects ORDER BY project_id").fetchall()
    return {"projects": len(rows), "sha256": hashlib.sha256(repr(rows).encode()).hexdigest()}


def main():
    global EVIDENCE
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine")
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    args = parser.parse_args()
    EVIDENCE = args.evidence.resolve()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    database = EVIDENCE / "projects.sqlite"
    if database.exists():
        raise RuntimeError("QA database already exists; use the existing result or a new evidence directory")
    before = fingerprint()
    with sqlite3.connect(f"file:{SOURCE.as_posix()}?mode=ro", uri=True) as original, sqlite3.connect(database) as qa:
        original.backup(qa)
    command = [args.engine] if args.engine else [sys.executable, "-m", "cutvoke"]
    log = (EVIDENCE / "mcp-stderr.log").open("w", encoding="utf-8")
    process = subprocess.Popen([*command, "mcp", "--data", str(database)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8", cwd=ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    serial = 0
    transcript = []
    def rpc(method, params=None):
        nonlocal serial
        serial += 1
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": serial, "method": method,
                                       "params": params or {}}, ensure_ascii=False) + "\n")
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["result"]
    def call(tool, **arguments):
        tick = time.monotonic()
        result = rpc("tools/call", {"name": tool, "arguments": arguments})
        data = json.loads(result["content"][0]["text"])
        if result.get("isError"):
            raise RuntimeError({"tool": tool, "arguments": arguments, "error": data})
        transcript.append({"tool": tool, "seconds": round(time.monotonic() - tick, 3),
                           "result": {key: value for key, value in data.items() if key not in ("project", "presets", "commands")}})
        return data
    def wait(name, project_id, job_id, timeout=240):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = call(name, projectId=project_id, jobId=job_id)
            state = job.get("state", job.get("status"))
            if state in ("succeeded", "completed"):
                return job
            if state in ("failed", "cancelled", "interrupted"):
                raise RuntimeError(job)
            time.sleep(1)
        raise RuntimeError("job timed out")
    def rat(n, d=1):
        return {"num": str(n), "den": str(d)}
    try:
        initialized = rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "editing-verifier", "version": "1"}})
        discovered = rpc("tools/list")["tools"]
        projects = call("project_list", query="潮汐藏馆")
        original = call("project_query", projectId="tide-gallery-20260927")["project"]
        clone = call("project_clone", projectId=original["projectId"], expectedRevision=original["revision"],
                     newProjectId="agent-real-copy", name="Agent 验收｜原工程副本")
        call("preview_frames", projectId=clone["projectId"], expectedRevision="0", times=[3, 11, 19, 27, 35], width=960)
        visual = next(t for t in original["sequence"]["tracks"] if t["kind"] == "video")
        score = next(t for t in original["sequence"]["tracks"] if t["kind"] == "audio")
        paths = [c["assetRef"]["sourcePath"] for c in visual["clips"][:2]] + [score["clips"][0]["assetRef"]["sourcePath"]]
        imported = call("media_import", paths=paths)["assets"]
        inspected = call("media_inspect", assetId=imported[0]["assetId"])
        resources = call("resources_list", family="transition", query="叠化", limit=2)
        call("commands_list", type="edit.batch")
        project = call("create_project", projectId="agent-edited-sample", name="Agent 验收｜可编辑六秒短片", width=960, height=540, fps=30)
        pid, revision = project["projectId"], project["revision"]
        lease = call("edit_lock", projectId=pid, action="acquire", owner="external-mcp-verifier", ttlSeconds=120)["lease"]["leaseId"]
        batch = {"commands": [
            {"type": "clip.insert", "payload": {"trackId": "visual", "createTrackKind": "video", "clipId": "shot-a", "assetId": imported[0]["assetId"], "timelineStart": rat(0), "timelineEnd": rat(3)}},
            {"type": "clip.insert", "payload": {"trackId": "visual", "clipId": "shot-b", "assetId": imported[1]["assetId"], "timelineStart": rat(3), "timelineEnd": rat(6)}},
            {"type": "clip.insert", "payload": {"trackId": "music", "createTrackKind": "audio", "clipId": "score-clip", "assetId": imported[2]["assetId"], "timelineStart": rat(0), "timelineEnd": rat(6)}},
            {"type": "effect.setTransition", "payload": {"clipId": "shot-b", "effectId": "cutvoke.transition.crossfade", "params": {"duration": 0.6}}},
            {"type": "clip.keyframe", "payload": {"clipId": "shot-a", "action": "add", "param": "scale", "time": rat(0), "value": 1}},
            {"type": "clip.keyframe", "payload": {"clipId": "shot-a", "action": "add", "param": "scale", "time": rat(3), "value": 1.08}},
            {"type": "clip.insert", "payload": {"trackId": "titles", "createTrackKind": "text", "clipId": "headline", "text": {"content": "潮汐藏馆", "fontSize": 90, "color": "#ffe5b4", "x": 0.5, "y": 0.24}, "timelineStart": rat(0), "timelineEnd": rat(6)}},
            {"type": "caption.add", "payload": {"captionId": "agent-caption", "text": "外部 Agent 编辑 · 人工可继续修改", "fontSize": 50, "start": rat(0), "end": rat(6)}},
        ]}
        call("command_preview", projectId=pid, type="edit.batch", payload=batch, expectedRevision=revision)
        result = call("command_apply", projectId=pid, type="edit.batch", payload=batch, expectedRevision=revision,
                      editLeaseId=lease, commandId="agent-six-second-batch", actorId="external-mcp-verifier")
        repeated = call("command_apply", projectId=pid, type="edit.batch", payload=batch, expectedRevision=revision,
                        editLeaseId=lease, commandId="agent-six-second-batch", actorId="external-mcp-verifier")
        assert result["revision"] == repeated["revision"] == "1"
        call("project_lookup", projectId=pid, entityType="clip", entityId="headline", fields=["effects"])
        call("preview_frames", projectId=pid, expectedRevision="1", times=[1, 3.3, 5], width=960)
        prepared = call("preview_prepare", projectId=pid, expectedRevision="1")
        ready = wait("preview_job", pid, prepared["jobId"])
        job = call("command_apply", projectId=pid, type="export.enqueue", payload={"outPath": str(EVIDENCE / "agent-edited.mp4"), "quality": "high"}, expectedRevision="1", editLeaseId=lease)
        exported = wait("export_job", pid, job["changedEntities"][0]["jobId"])
        package = call("project_package", projectId=pid, expectedRevision="1", outPath=str(EVIDENCE / "agent-edited.cutvokepack.zip"))
        events = call("project_events", projectId=pid)
        call("edit_lock", projectId=pid, action="release", leaseId=lease)
        runtime = call("runtime")
        result = {"initialized": initialized, "tools": [t["name"] for t in discovered], "sourceBefore": before,
                  "sourceAfter": fingerprint(), "projectId": pid, "revision": "1", "preview": ready,
                  "export": exported, "package": package, "runtime": runtime, "events": events}
        assert result["sourceAfter"] == before
        (EVIDENCE / "agent-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"tools": len(discovered), "projectId": pid, "export": exported["result"]["output_path"], "sourceUnchanged": True}, ensure_ascii=False))
    finally:
        (EVIDENCE / "transcript.json").write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
        process.stdin.close()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        log.close()


if __name__ == "__main__":
    main()
