"""CutVoke MCP Server（任务书 T26 / 第 9.4 章）。

把核心引擎暴露为 MCP 工具，供 Claude Code / Codex / Cursor 等 Agent 调用。
复用 EditService 的同一套能力，与 CLI/HTTP/Web UI 行为一致。

M1 落地 stdio 传输（Line-delimited JSON-RPC 2.0），无第三方依赖。
工具集：create_project / project_query / command_apply / export / capabilities。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

from .service import EditService, EditError
from .protocol import Command, CommandResult, ErrorCode
from .rational import Rational
from .render import RenderService, RenderError

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "cutvoke"
SERVER_VERSION = "0.1.0"


class MCPServer:
    """MCP 服务端（stdio）。"""

    def __init__(self, service: Optional[EditService] = None,
                 render: Optional[RenderService] = None) -> None:
        self.service = service or EditService()
        self.render = render or RenderService()

    # ------------------------------------------------------------------
    # 工具定义
    # ------------------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "create_project",
                "description": "创建一个新的视频编辑工程，返回 projectId 和初始 revision。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectId": {"type": "string", "description": "可选，工程 ID（缺省自动生成）"},
                        "width": {"type": "integer", "default": 1920},
                        "height": {"type": "integer", "default": 1080},
                        "fps": {"type": "number", "default": 30},
                    },
                },
            },
            {
                "name": "project_query",
                "description": "读取工程当前状态（JSON），含轨道、片段、revision。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectId": {"type": "string"},
                    },
                    "required": ["projectId"],
                },
            },
            {
                "name": "command_apply",
                "description": "提交一条编辑命令（支持的命令类型以 capabilities 工具的 commands 列表为准）。返回新 revision。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectId": {"type": "string"},
                        "type": {"type": "string", "description": "命令类型"},
                        "payload": {"type": "object", "description": "命令参数，时间用有理数 {\"num\":\"1\",\"den\":\"2\"}"},
                        "expectedRevision": {"type": "string", "description": "当前 revision（乐观并发）"},
                    },
                    "required": ["projectId", "type", "payload", "expectedRevision"],
                },
            },
            {
                "name": "edit_lock",
                "description": "获取、续租或释放工程编辑租约。租约期间用户仍可查看工程变化，但不带该 leaseId 的写操作会被拒绝。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectId": {"type": "string"},
                        "action": {"type": "string", "enum": ["acquire", "renew", "release"]},
                        "leaseId": {"type": "string"},
                        "owner": {"type": "string", "default": "agent"},
                        "ttlSeconds": {"type": "number", "default": 30},
                    },
                    "required": ["projectId", "action"],
                },
            },
            {
                "name": "export",
                "description": "把工程渲染并导出为 MP4 视频文件（H.264 + AAC）。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectId": {"type": "string"},
                        "outPath": {"type": "string", "description": "输出文件绝对路径"},
                        "quality": {"type": "string", "enum": ["high", "medium", "low"], "default": "high"},
                    },
                    "required": ["projectId", "outPath"],
                },
            },
            {
                "name": "effects_list",
                "description": "列出当前可用的效果（转场/变换/调色等），含参数 schema、默认值与可动画参数。新增效果只需安装清单，无需改内核。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "可选，按分类过滤：transition / transform / color"},
                        "effectId": {"type": "string", "description": "可选，只查单个效果详情"},
                        "lang": {"type": "string", "default": "zh-CN"},
                    },
                },
            },
            {
                "name": "capabilities",
                "description": "查询当前支持的编辑能力（命令类型、效果清单、质量预设等）。",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

    # ------------------------------------------------------------------
    # 工具调用
    # ------------------------------------------------------------------

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "create_project":
                return self._create_project(arguments)
            if name == "project_query":
                return self._project_query(arguments)
            if name == "command_apply":
                return self._command_apply(arguments)
            if name == "edit_lock":
                return self._edit_lock(arguments)
            if name == "export":
                return self._export(arguments)
            if name == "effects_list":
                return self._effects_list(arguments)
            if name == "capabilities":
                return self._capabilities(arguments)
            return {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": f"unknown tool: {name}"}}
        except EditError as e:
            return {"ok": False, "error": {"code": e.code, "message": e.message}}
        except (RenderError, FileNotFoundError, ValueError) as e:
            return {"ok": False, "error": {"code": "EXECUTION_FAILED", "message": str(e)}}

    def _create_project(self, a: dict) -> dict:
        pid = a.get("projectId") or ""
        if not pid:
            import uuid
            pid = f"p_{uuid.uuid4().hex[:8]}"
        fps_raw = a.get("fps", 30)
        fps = Rational.of(30000, 1001) if fps_raw == 29.97 else Rational.of(int(fps_raw), 1)
        proj = self.service.create_project(pid, width=a.get("width", 1920),
                                           height=a.get("height", 1080), fps=fps)
        return {"ok": True, "projectId": proj.project_id, "revision": proj.revision,
                "width": proj.sequence.width, "height": proj.sequence.height,
                "fps": proj.sequence.fps.to_json()}

    def _project_query(self, a: dict) -> dict:
        proj = self.service.get_project(a["projectId"])
        return {"ok": True, "revision": proj.revision, "project": proj.to_dict()}

    def _command_apply(self, a: dict) -> dict:
        cmd = Command(
            type=a["type"], payload=a.get("payload", {}),
            project_id=a["projectId"], expected_revision=a["expectedRevision"],
            edit_lease_id=a.get("editLeaseId", ""),
        )
        result = self.service.execute(cmd)
        return {"ok": True, "revision": result.revision,
                "previousRevision": result.previous_revision,
                "changedEntities": result.changed_entities,
                "transactionId": result.transaction_id}

    def _edit_lock(self, a: dict) -> dict:
        action = a.get("action", "")
        pid = a["projectId"]
        if action == "acquire":
            lease = self.service.acquire_edit_lease(
                pid, owner=a.get("owner", "agent"),
                lease_id=a.get("leaseId", ""),
                ttl_seconds=a.get("ttlSeconds", 30),
            )
            return {"ok": True, "locked": True, "lease": lease}
        lease_id = a.get("leaseId", "")
        if not lease_id:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "leaseId is required for renew/release")
        if action == "renew":
            lease = self.service.renew_edit_lease(
                pid, lease_id, a.get("ttlSeconds", 30))
            return {"ok": True, "locked": True, "lease": lease}
        if action == "release":
            released = self.service.release_edit_lease(pid, lease_id)
            return {"ok": True, "locked": not released, "released": released}
        raise EditError(ErrorCode.INVALID_ARGUMENT,
                        f"unknown edit_lock action: {action}")

    def _export(self, a: dict) -> dict:
        proj = self.service.get_project(a["projectId"])
        r = self.render.render(proj, a["outPath"], quality=a.get("quality", "high"), overwrite=True)
        return {"ok": True, **r}

    def _effects_list(self, a: dict) -> dict:
        """效果能力查询（AC18：MCP 客户端可发现效果与参数）。"""
        from .effects import EffectNotFound
        lang = a.get("lang", "zh-CN")
        caps = self.service.effect_capabilities(lang)
        eid = a.get("effectId")
        if eid:
            try:
                spec = self.service._effects.get(eid)
            except EffectNotFound as e:
                return {"ok": False, "error": {"code": "EFFECT_UNAVAILABLE",
                                               "message": str(e)}}
            return {"ok": True, "effect": spec.to_dict(lang)}
        cat = a.get("category")
        if cat:
            items = [s.to_dict(lang) for s in self.service._effects.by_category(cat)]
            return {"ok": True, "category": cat, "effects": items, "count": len(items)}
        return {"ok": True, **caps}

    def _capabilities(self, a: dict) -> dict:
        return {
            "ok": True,
            "commands": self.service.command_catalog(),
            "effects": self.service.effect_capabilities()["effects"],
            "qualityPresets": ["high", "medium", "low"],
            "rationalTime": "num/den decimal strings",
        }

    # ------------------------------------------------------------------
    # JSON-RPC / MCP 协议
    # ------------------------------------------------------------------

    def _handle(self, msg: dict) -> Optional[dict]:
        method = msg.get("method")
        rid = msg.get("id")

        if method == "initialize":
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }}
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": self.list_tools()}}
        if method == "tools/call":
            params = msg.get("params", {})
            result = self.call_tool(params.get("name", ""), params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "isError": not result.get("ok", False),
            }}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}

    def run_stdio(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            resp = self._handle(msg)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()


def main() -> int:
    server = MCPServer()
    server.run_stdio()
    return 0


if __name__ == "__main__":
    sys.exit(main())
