"""Black-box stdio acceptance: execute recipes returned by the installed MCP.

This is a protocol/recipe test, not evidence of an unfamiliar LLM agent's skill.
No CutVoke source modules are imported. The target DB is created in a fresh
evidence directory; the user's project DB is never opened for writing.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path


class Client:
    def __init__(self, command, root):
        self.root = root
        self.log = (root / "stderr.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.log, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.messages = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.serial = 0
        self.transcript = []

    def _read(self):
        for line in self.process.stdout:
            self.messages.put(line)
        self.messages.put(None)

    def rpc(self, method, params=None):
        self.serial += 1
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.serial,
            "method": method, "params": params or {}}, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        raw = self.messages.get(timeout=90)
        if raw is None:
            raise RuntimeError("MCP process exited; inspect stderr.log")
        result = json.loads(raw)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result["result"]

    def call(self, name, arguments=None):
        started = time.monotonic()
        result = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        data = json.loads(result["content"][0]["text"])
        images = []
        for index, item in enumerate(result["content"][1:]):
            if item["type"] == "image":
                path = self.root / f"frame-{self.serial}-{index}.png"
                path.write_bytes(base64.b64decode(item["data"]))
                images.append(str(path))
        self.transcript.append({"tool": name, "arguments": arguments or {}, "seconds": round(time.monotonic()-started, 3), "result": data, "images": images})
        if result.get("isError"):
            raise RuntimeError(data)
        return data

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=10)
        self.process.stdout.close()
        self.log.close()
        (self.root / "transcript.json").write_text(json.dumps(self.transcript, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path)
    parser.add_argument("--media", type=Path, required=True, help="Image or a video at least three seconds long")
    parser.add_argument("--evidence", type=Path, required=True)
    options = parser.parse_args()
    root = options.evidence.resolve()
    root.mkdir(parents=True, exist_ok=True)
    database = root / "projects.sqlite"
    if database.exists():
        raise RuntimeError("Choose a fresh evidence directory; never overwrite a previous QA database")
    command = [str(options.engine.resolve())] if options.engine else [sys.executable, "-m", "cutvoke"]
    client = Client([*command, "mcp", "--data", str(database)], root)
    bindings = {"<ABSOLUTE_MEDIA_PATH>": str(options.media.resolve(strict=True)),
                "<ABSOLUTE_MP4_PATH>": str(root / "recipe.mp4"),
                "<ABSOLUTE_PACKAGE_PATH>": str(root / "recipe.cutvokepack.zip")}

    def bind(value):
        if isinstance(value, dict):
            return {key: bind(item) for key, item in value.items()}
        if isinstance(value, list):
            return [bind(item) for item in value]
        return bindings.get(value, value) if isinstance(value, str) else value

    lease = None
    try:
        initialized = client.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "onboarding-recipe-verifier", "version": "1"}})
        assert "editor_help" in initialized["instructions"]
        tools = client.rpc("tools/list")["tools"]
        assert any(tool["name"] == "editor_help" for tool in tools)
        resource = client.rpc("resources/read", {"uri": "cutvoke://guide/overview"})
        assert json.loads(resource["contents"][0]["text"]) == client.call("editor_help")["help"]
        for recipe_name in ("assemble", "export"):
            recipe = client.call("editor_help", {"topic": "recipes", "recipe": recipe_name})["help"]
            preview = None
            for step in recipe["steps"]:
                tool = step["tool"]
                bindings["<COMMAND_ID>"] = f"onboarding-{recipe_name}"
                arguments = bind(step.get("arguments", {}))
                if "reuse" in step:
                    arguments = {**copy.deepcopy(preview), "editLeaseId": bindings["<LEASE>"], "commandId": bindings["<COMMAND_ID>"]}
                if tool in ("command_apply", "command_preview"):
                    schema = client.call("editor_help", {"topic": "command", "command": arguments["type"]})["help"]["inputSchema"]
                    assert schema["type"] == "object"
                    for child in arguments["payload"].get("commands", []):
                        client.call("editor_help", {"topic": "command", "command": child["type"]})
                result = client.call(tool, arguments)
                if tool == "create_project":
                    bindings.update({"<PROJECT>": result["projectId"], "<REVISION>": result["revision"]})
                elif tool == "media_import":
                    bindings["<ASSET>"] = result["assets"][0]["assetId"]
                    client.call("media_inspect", {"assetId": bindings["<ASSET>"]})
                elif tool == "edit_lock":
                    if arguments["action"] == "acquire":
                        lease = result["lease"]["leaseId"]
                        bindings["<LEASE>"] = lease
                    else:
                        lease = None
                elif tool == "command_preview":
                    preview = arguments
                elif tool == "command_apply":
                    if result.get("revision"):
                        bindings["<REVISION>"] = result["revision"]
                    assert result == client.call(tool, arguments)
                    if arguments["type"] == "export.enqueue":
                        assert result["revision"] == arguments["expectedRevision"]
                        bindings["<JOB_ID>"] = result["changedEntities"][0]["jobId"]
                elif tool == "export_job":
                    deadline = time.monotonic() + 120
                    while result["status"] not in ("succeeded", "failed", "cancelled", "interrupted"):
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Export did not finish within 120 seconds")
                        time.sleep(1)
                        result = client.call(tool, arguments)
                    assert result["status"] == "succeeded", result
                    assert Path(result["result"]["output_path"]).is_file()
        project = client.call("project_query", {"projectId": bindings["<PROJECT>"]})["project"]
        assert len(project["sequence"]["tracks"]) == 2
        assert len(project["sequence"]["captions"]) == 1
        report = {"ok": True, "acceptance": "protocol-and-returned-recipes (not an unfamiliar LLM agent)",
                  "initialize": initialized, "toolCount": len(tools), "projectId": bindings["<PROJECT>"],
                  "revision": bindings["<REVISION>"], "video": bindings["<ABSOLUTE_MP4_PATH>"],
                  "package": bindings["<ABSOLUTE_PACKAGE_PATH>"], "database": str(database)}
        (root / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        if lease:
            client.call("edit_lock", {"projectId": bindings["<PROJECT>"], "action": "release", "leaseId": lease})
        client.close()


if __name__ == "__main__":
    main()
