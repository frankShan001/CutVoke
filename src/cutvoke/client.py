"""CutVoke 本地 HTTP 客户端（P4 SDK，最小可用）。

把一个可运行的 Python 客户端交给开发者 / Agent：封装 HTTP API 的核心动作
（工程 CRUD / 命令 / dry-run / 导出 / 素材），全部返回结构化结果，
异常统一为 CutVokeApiError。零第三方依赖（urllib）。

用法：
    from cutvoke.client import CutVokeClient
    api = CutVokeClient()                      # 默认 http://127.0.0.1:8787
    proj = api.create_project(name="我的片子")
    api.command(proj["projectId"], "track.add", {"kind": "video"})
    preview = api.dry_run(proj["projectId"], "clip.insert", {...})   # 试算不提交
    api.export(proj["projectId"], out="/tmp/out.mp4")

这是「可运行」的 SDK，不是只暴露一个自由文本入口——每个能力都是显式方法。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional


class CutVokeApiError(Exception):
    """统一 API 错误：携带 HTTP 状态码与错误码。"""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"[{code}] {message} (HTTP {status})")
        self.status = status
        self.code = code
        self.message = message


class CutVokeClient:
    """CutVoke 本地 HTTP API 客户端（P4 SDK）。"""

    def __init__(self, base_url: str = "http://127.0.0.1:8787"):
        self.base = base_url.rstrip("/")

    # ---- 底层 ----
    def _request(self, method: str, path: str, body: Any = None) -> Any:
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            try:
                d = json.loads(e.read().decode("utf-8"))
                err = d.get("error", {})
                raise CutVokeApiError(e.code, err.get("code", "UNKNOWN"),
                                      err.get("message", str(d))) from e
            except json.JSONDecodeError:
                raise CutVokeApiError(e.code, "HTTP", str(e)) from e
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    # ---- 工程 ----
    def list_projects(self) -> list[dict]:
        return self._request("GET", "/api/v1/projects").get("projects", [])

    def create_project(self, name: str = "", project_id: Optional[str] = None,
                       width: int = 1920, height: int = 1080,
                       fps: int = 30) -> dict:
        body: dict[str, Any] = {"name": name, "width": width,
                                "height": height, "fps": fps}
        if project_id:
            body["projectId"] = project_id
        return self._request("POST", "/api/v1/projects", body)

    def get_project(self, project_id: str) -> dict:
        return self._request("GET", f"/api/v1/projects/{project_id}")

    def rename_project(self, project_id: str, name: str,
                       edit_lease_id: Optional[str] = None) -> dict:
        body: dict[str, Any] = {"name": name}
        if edit_lease_id:
            body["editLeaseId"] = edit_lease_id
        return self._request("PATCH", f"/api/v1/projects/{project_id}",
                             body)

    # ---- 命令 ----
    def command(self, project_id: str, cmd_type: str, payload: dict,
                expected_revision: Optional[str] = None,
                edit_lease_id: Optional[str] = None) -> dict:
        if expected_revision is None:
            expected_revision = self.get_project(project_id).get("revision", "")
        body = {"type": cmd_type, "payload": payload,
                "expectedRevision": expected_revision}
        if edit_lease_id:
            body["editLeaseId"] = edit_lease_id
        return self._request("POST", f"/api/v1/projects/{project_id}/commands",
                             body)

    def edit_lock(self, project_id: str, action: str = "acquire",
                  lease_id: Optional[str] = None, owner: str = "agent",
                  ttl_seconds: float = 30) -> dict:
        """获取/续租/释放 Agent 编辑租约。"""
        body: dict[str, Any] = {"action": action, "owner": owner,
                                "ttlSeconds": ttl_seconds}
        if lease_id:
            body["leaseId"] = lease_id
        return self._request("POST", f"/api/v1/projects/{project_id}/edit-lock", body)

    def dry_run(self, project_id: str, cmd_type: str, payload: dict,
                expected_revision: Optional[str] = None) -> dict:
        """试算命令（不提交），返回会发生什么（changedEntities）。"""
        if expected_revision is None:
            expected_revision = self.get_project(project_id).get("revision", "")
        body = {"type": cmd_type, "payload": payload,
                "expectedRevision": expected_revision, "dryRun": True}
        return self._request("POST", f"/api/v1/projects/{project_id}/commands",
                             body)

    # ---- 导出 ----
    def export(self, project_id: str, out: str, quality: str = "high") -> dict:
        return self._request("POST", f"/api/v1/projects/{project_id}/export",
                             {"outPath": out, "quality": quality})

    # ---- 能力发现 ----
    def capabilities(self) -> dict:
        return self._request("GET", "/api/v1/capabilities")

    def commands_catalog(self) -> list[dict]:
        return self._request("GET", "/api/v1/commands").get("commands", [])

    def schema(self) -> dict:
        return self._request("GET", "/api/v1/schema")

    def effects(self) -> list[dict]:
        return self._request("GET", "/api/v1/effects").get("effects", [])

    # ---- 素材 ----
    def list_assets(self) -> list[dict]:
        return self._request("GET", "/api/v1/assets").get("assets", [])

    def probe(self, path: str) -> dict:
        return self._request("GET", f"/api/v1/probe?path={path}")
