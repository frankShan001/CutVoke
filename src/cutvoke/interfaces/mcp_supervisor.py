"""Stable MCP transport with fresh, independently draining engine processes.

No Python modules are hot-reloaded and no editor request is automatically replayed.
The supervisor owns only children it spawned; SQLite remains the editing authority.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from cutvoke.runtime import runtime_status


def worker_command(data_path: str) -> list[str]:
    prefix = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "cutvoke"]
    return [*prefix, "mcp", "--worker", "--data", data_path]


def _read_lines(stream, messages):
    try:
        for line in stream:
            messages.put(line)
    finally:
        messages.put(None)


class EngineWorker:
    """One sequential stdio engine. Control responses never reach the client."""

    def __init__(self, data_path):
        # A fresh cache also avoids Python's same-size, same-second .pyc reuse
        # during rapid source updates. PYTHONDONTWRITEBYTECODE alone cannot do it.
        self.cache = tempfile.TemporaryDirectory(prefix="cutvoke-mcp-python-")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPYCACHEPREFIX=self.cache.name)
        try:
            self.process = subprocess.Popen(
                worker_command(data_path), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=None, text=True, encoding="utf-8", env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except BaseException:
            self.cache.cleanup()
            raise
        self.messages = queue.Queue()
        self.reader = threading.Thread(target=_read_lines, args=(self.process.stdout, self.messages), daemon=True)
        self.reader.start()
        self.runtime = {}
        self._abandoned = set()

    def request(self, message, *, timeout=None, notify=lambda message: None):
        # Remap all request IDs so private controls can never collide with a
        # client-chosen ID. Notifications retain their notification semantics.
        forwarded = dict(message)
        request_id = "cutvoke-" + uuid.uuid4().hex
        if "id" in message:
            forwarded["id"] = request_id
        self.process.stdin.write(json.dumps(forwarded, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        if "id" not in message:
            return None
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            remaining = max(0, deadline - time.monotonic()) if deadline is not None else None
            try:
                line = self.messages.get(timeout=remaining)
            except queue.Empty as error:
                self._abandoned.add(request_id)
                raise TimeoutError("engine did not respond before its health-check deadline") from error
            if line is None:
                raise BrokenPipeError(f"CutVoke engine exited ({self.process.poll()})")
            response = json.loads(line)
            if response.get("id") in self._abandoned:
                self._abandoned.remove(response["id"])
                continue
            if response.get("id") == request_id:
                response["id"] = message["id"]
                return response
            if "method" in response and "id" not in response:
                notify(response)
            else:
                raise ValueError("unexpected engine response")

    def control(self, method, params=None):
        response = self.request({"jsonrpc": "2.0", "id": 0,
                                 "method": "_cutvoke/" + method, "params": params or {}}, timeout=30)
        if "error" in response:
            raise RuntimeError(response["error"]["message"])
        return response["result"]

    def close(self):
        # EOF invokes the child's ordinary queue/render cleanup. The supervisor
        # retires workers only after their actual background execution finishes.
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
        finally:
            self.process.stdout.close()
            self.reader.join(timeout=1)
            self.cache.cleanup()


class MCPSupervisor:
    POLL_SECONDS = 0.5
    SETTLE_SECONDS = 1.0
    RETRY_SECONDS = 3.0
    MAX_DRAINING = 2

    def __init__(self, data_path, *, worker_factory=EngineWorker, output=None, clock=time.monotonic):
        self.data_path = str(Path(data_path).expanduser().resolve())
        self.worker_factory = worker_factory
        self.output = output or sys.stdout
        self.clock = clock
        self.current = None
        self.draining = []
        self.routes = {}
        self.initialize = None
        self.initialized = False
        self.generation = 0
        self.pending = None
        self.pending_since = 0.0
        self.retry_at = 0.0
        self.last_error = None

    def emit(self, message):
        self.output.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.output.flush()

    def _start(self, expected=None):
        worker = self.worker_factory(self.data_path)
        try:
            state = worker.control("status")
            worker.runtime = state["runtime"]
            if expected is not None and worker.runtime["sourceFingerprint"] != expected:
                raise RuntimeError("source changed while the new engine was starting")
            if runtime_status(worker.runtime)["restartRequired"]:
                raise RuntimeError("source changed while the new engine was starting")
            if self.initialize is not None:
                response = worker.request(self.initialize, timeout=30)
                if "error" in response:
                    raise RuntimeError("new engine could not initialize")
                if self.initialized:
                    worker.request({"jsonrpc": "2.0", "method": "notifications/initialized"})
            # Force schema construction before publishing the new generation.
            response = worker.request({"jsonrpc": "2.0", "id": 0, "method": "tools/list"}, timeout=30)
            if "error" in response or not response.get("result", {}).get("tools"):
                raise RuntimeError("new engine failed its tool discovery check")
            return worker
        except BaseException:
            worker.close()
            raise

    def _state(self, worker):
        state = worker.control("status")
        for kind, key in (("preview_job", "previewJobs"), ("export_job", "exportJobs")):
            for job_id in state[key]:
                self.routes[(kind, job_id)] = worker
        return state

    def _retire(self):
        for worker in list(self.draining):
            try:
                state = self._state(worker)
            except TimeoutError as error:
                # A delayed health check is not proof that background rendering
                # stopped. Keep this owned worker and its cancellation route.
                self.last_error = str(error)
                continue
            except (OSError, ValueError, RuntimeError) as error:
                self.last_error = str(error)
                self.routes = {key: owner for key, owner in self.routes.items() if owner is not worker}
                self.draining.remove(worker)
                worker.close()
                continue
            if state["busy"]:
                continue
            # Prepared-preview files already live in the shared disk cache.
            # Transfer their original revision/identity; never re-render them
            # using a newer engine and silently change a submitted job.
            snapshot = worker.control("snapshot")
            if snapshot["previews"]:
                self.current.control("restore", snapshot)
            self.routes = {key: owner for key, owner in self.routes.items() if owner is not worker}
            self.draining.remove(worker)
            worker.close()

    def tick(self):
        if self.current is None:
            self.current = self._start()
            self.generation += 1
        self._retire()
        status = runtime_status(self.current.runtime)
        fingerprint = status["currentSourceFingerprint"]
        if not status["restartRequired"]:
            self.pending = None
            self.last_error = None
            return
        now = self.clock()
        if self.pending != fingerprint:
            self.pending, self.pending_since, self.retry_at = fingerprint, now, 0.0
        if fingerprint is None or now - self.pending_since < self.SETTLE_SECONDS or now < self.retry_at:
            return
        state = self._state(self.current)
        if len(self.draining) >= self.MAX_DRAINING and state["busy"]:
            return
        candidate = None
        try:
            candidate = self._start(fingerprint)
            # Includes terminal jobs belonging to the current generation, too.
            snapshot = self.current.control("snapshot")
            if snapshot["previews"]:
                candidate.control("restore", snapshot)
            if runtime_status(candidate.runtime)["restartRequired"]:
                raise RuntimeError("source changed during engine handoff")
            self.current.control("drain")
        except (OSError, ValueError, RuntimeError, TimeoutError) as error:
            if candidate is not None:
                candidate.close()
            self.last_error = str(error)
            self.retry_at = now + self.RETRY_SECONDS
            print(f"CutVoke MCP automatic update pending: {error}", file=sys.stderr)
            return
        previous, self.current = self.current, candidate
        self.draining.append(previous)
        self.generation += 1
        self.pending = None
        self.last_error = None
        if self.initialized:
            for method in ("notifications/tools/list_changed", "notifications/resources/list_changed"):
                self.emit({"jsonrpc": "2.0", "method": method})
        self._retire()

    @staticmethod
    def _error(rid, code, message, data=None):
        error = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": rid, "error": error}

    def handle(self, message):
        if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or
                not isinstance(message.get("method"), str) or
                ("id" in message and (isinstance(message["id"], bool) or
                 not isinstance(message["id"], (str, int, type(None)))))):
            return self._error(None, -32600, "invalid JSON-RPC request")
        rid = message.get("id")
        if not isinstance(message.get("params", {}), dict):
            return self._error(rid, -32602, "params must be an object") if "id" in message else None
        if message["method"].startswith("_cutvoke/"):
            return self._error(rid, -32601, "method not found") if "id" in message else None
        if message["method"] == "initialize":
            self.initialize = dict(message)
        if message["method"] == "notifications/initialized":
            self.initialized = True
        # A normal update race is queued briefly before dispatch, not exposed
        # as an error requiring a user/client retry. Nothing was sent to an
        # engine yet, so this cannot duplicate a committed write.
        if self.pending is not None and self.clock() >= self.retry_at:
            wait = self.SETTLE_SECONDS - (self.clock() - self.pending_since)
            if wait > 0:
                time.sleep(wait)
            self.tick()
        if (message["method"] == "tools/list" or message["method"].startswith("resources/")) and \
                runtime_status(self.current.runtime)["restartRequired"]:
            return (self._error(rid, -32001, "New engine capabilities are being prepared; retry shortly.",
                               {"code": "AUTO_UPDATE_PENDING", "retryable": True, "committed": False})
                    if "id" in message else None)
        params = message.get("params", {})
        arguments = params.get("arguments", {})
        worker = self.current
        if message["method"] == "tools/call" and isinstance(arguments, dict):
            kind, job_id = params.get("name"), arguments.get("jobId")
            if kind == "command_apply" and arguments.get("type") == "export.cancel":
                payload = arguments.get("payload", {})
                kind, job_id = "export_job", payload.get("jobId") if isinstance(payload, dict) else None
            worker = self.routes.get((kind, job_id), worker) if isinstance(kind, str) and isinstance(job_id, str) else worker
        try:
            response = worker.request(message, notify=self.emit)
        except (OSError, ValueError) as error:
            # A write may already have committed. Never replay a client request
            # just because the transport lost its response.
            self.last_error = str(error)
            if worker is self.current:
                self.routes = {key: owner for key, owner in self.routes.items() if owner is not worker}
                worker.close()
                self.current = None
            return (self._error(rid, -32002, "Engine connection lost; request was not replayed.",
                               {"code": "ENGINE_CONNECTION_LOST", "committed": None,
                                "recovery": "Read project revision and retry only with the original commandId."})
                    if "id" in message else None)
        if response is None:
            return None
        if message["method"] == "initialize" and "result" in response:
            response["result"]["capabilities"].update(tools={"listChanged": True}, resources={"listChanged": True})
        if message["method"] == "tools/call" and "result" in response:
            for content in response["result"].get("content", []):
                if content.get("type") != "text":
                    continue
                value = json.loads(content["text"])
                if params.get("name") == "runtime" and value.get("ok"):
                    value["autoReload"] = {"enabled": True, "generation": self.generation,
                        "state": "pending" if self.pending is not None else "current",
                        "supervisorProcessId": os.getpid(), "drainingWorkers": len(self.draining),
                        "lastError": self.last_error}
                if isinstance(value.get("error"), dict) and value["error"].get("code") == "RUNTIME_RESTART_REQUIRED":
                    value["error"].update(code="AUTO_UPDATE_PENDING", retryable=True,
                        message="A fresh CutVoke engine is being prepared. Retry after the automatic update.",
                        recovery={"action": "retry_after_update", "tools": ["runtime"], "retrySameCommand": True})
                content["text"] = json.dumps(value, ensure_ascii=False)
        return response

    def run(self):
        for stream in (sys.stdin, self.output):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="strict")
        incoming = queue.Queue(maxsize=64)
        threading.Thread(target=_read_lines, args=(sys.stdin, incoming), daemon=True).start()
        try:
            while True:
                try:
                    self.tick()
                except (OSError, ValueError, RuntimeError, TimeoutError) as error:
                    self.last_error = str(error)
                    print(f"CutVoke MCP lifecycle: {error}", file=sys.stderr)
                try:
                    line = incoming.get(timeout=self.POLL_SECONDS)
                except queue.Empty:
                    continue
                if line is None:
                    return 0
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    response = self._error(None, -32700, "invalid JSON")
                else:
                    if self.current is None:
                        response = self._error(message.get("id") if isinstance(message, dict) else None,
                                               -32002, "CutVoke engine is starting; retry shortly.")
                    else:
                        response = self.handle(message)
                if response is not None:
                    self.emit(response)
        finally:
            # Client disconnect is an explicit shutdown, unlike an upgrade.
            # Child EOF performs normal cancellation/cleanup of its queues.
            for worker in [*self.draining, *([self.current] if self.current else [])]:
                worker.close()
