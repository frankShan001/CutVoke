"""CutVoke MCP Server（任务书 T26 / 第 9.4 章）。

把核心引擎暴露为 MCP 工具，供 Claude Code / Codex / Cursor 等 Agent 调用。
复用 EditService 的同一套能力，与 CLI/HTTP/Web UI 行为一致。

M1 落地 stdio 传输（Line-delimited JSON-RPC 2.0），无第三方依赖。
工具集：create_project / project_query / project_summary / command_apply / export / capabilities。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

from .service import EditService, EditError
from .protocol import Actor, Command, CommandResult, ErrorCode
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
                "name": "project_summary",
                "description": "读取适合 Agent 规划的小型工程摘要：revision、画布、租约、轨道与片段时间、效果 ID。不会返回完整字幕或效果参数；需要细节时再用 project_query，可显著减少上下文开销。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectId": {"type": "string"},
                    },
                    "required": ["projectId"],
                },
            },
            {
                "name": "project_lookup",
                "description": "按 ID、文字或时间范围读取少量字幕/片段，返回 revision 与指定字段。修改一句字幕时先用此工具定位，无需读取整个工程。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectId": {"type": "string"},
                        "entityType": {"type": "string", "enum": ["caption", "clip"]},
                        "entityId": {"type": "string"},
                        "textContains": {"type": "string", "description": "字幕正文或素材文件名包含的文字"},
                        "atSeconds": {"type": "number"},
                        "fromSeconds": {"type": "number"},
                        "toSeconds": {"type": "number"},
                        "fields": {"type": "array", "items": {"type": "string"},
                                   "description": "可选；只返回指定字段，id 与片段 trackId 始终返回"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50,
                                  "default": 10},
                    },
                    "required": ["projectId", "entityType"],
                },
            },
            {
                "name": "command_apply",
                "description": "提交一条编辑命令（支持的命令类型以 capabilities 工具的 commands 列表为准）。返回新 revision。对会明显改变时间线的操作，先使用 command_preview 再提交。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectId": {"type": "string"},
                        "type": {"type": "string", "description": "命令类型"},
                        "payload": {"type": "object", "description": "命令参数，时间用有理数 {\"num\":\"1\",\"den\":\"2\"}"},
                        "expectedRevision": {"type": "string", "description": "当前 revision（乐观并发）"},
                        "editLeaseId": {"type": "string", "description": "通过 edit_lock 获取的租约 ID；Agent 持锁编辑时必填"},
                        "actorId": {"type": "string", "description": "可选，写入活动记录的 Agent 标识"},
                        "commandId": {"type": "string", "description": "可选幂等命令 ID；网络重试时复用同一值"},
                    },
                    "required": ["projectId", "type", "payload", "expectedRevision"],
                },
            },
            {
                "name": "command_preview",
                "description": "在当前 revision 上试算一条编辑命令，不写工程、不占 revision、不产生渲染。返回会改变的对象；适合 Agent 在锁定前或批量编辑前检查时间线、素材时长和参数。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectId": {"type": "string"},
                        "type": {"type": "string", "description": "命令类型"},
                        "payload": {"type": "object", "description": "命令参数；时间用有理数 {\"num\":\"1\",\"den\":\"2\"}"},
                        "expectedRevision": {"type": "string", "description": "当前 revision（乐观并发）"},
                        "actorId": {"type": "string", "description": "可选，仅用于诊断身份"},
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
                "description": "列出当前可用的效果（转场/变换/调色等），含参数 schema、默认值与可动画参数。可按分类或适用对象过滤，避免 Agent 读取无关效果。新增效果只需安装清单，无需改内核。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "可选，按分类过滤：transition / animation / fx / color / transform / text"},
                        "appliesTo": {"type": "string", "description": "可选，按适用对象过滤：image / video / audio / text"},
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
            if name == "project_summary":
                return self._project_summary(arguments)
            if name == "project_lookup":
                return self._project_lookup(arguments)
            if name == "command_apply":
                return self._command_apply(arguments)
            if name == "command_preview":
                return self._command_preview(arguments)
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

    def _project_summary(self, a: dict) -> dict:
        return {"ok": True, **self.service.project_summary(a["projectId"])}

    def _project_lookup(self, a: dict) -> dict:
        return {"ok": True, **self.service.project_lookup(
            a["projectId"], entity_type=a["entityType"],
            entity_id=a.get("entityId", ""),
            text_contains=a.get("textContains", ""),
            at_seconds=a.get("atSeconds"),
            from_seconds=a.get("fromSeconds"),
            to_seconds=a.get("toSeconds"),
            fields=a.get("fields"), limit=a.get("limit", 10),
        )}

    def _command_apply(self, a: dict) -> dict:
        command_kwargs: dict[str, Any] = {
            "type": a["type"], "payload": a.get("payload", {}),
            "project_id": a["projectId"], "expected_revision": a["expectedRevision"],
            "actor": Actor("mcp", str(a.get("actorId") or "agent")[:200]),
            "edit_lease_id": a.get("editLeaseId", ""),
        }
        if a.get("commandId"):
            command_kwargs["command_id"] = str(a["commandId"])
        cmd = Command(**command_kwargs)
        result = self.service.execute(cmd)
        return {"ok": True, "revision": result.revision,
                "previousRevision": result.previous_revision,
                "changedEntities": result.changed_entities,
                "transactionId": result.transaction_id}

    def _command_preview(self, a: dict) -> dict:
        """MCP 的无副作用命令试算，复用 HTTP dry-run 与核心 preview_command 语义。"""
        cmd = Command(
            type=a["type"], payload=a.get("payload", {}),
            project_id=a["projectId"], expected_revision=a["expectedRevision"],
            actor=Actor("mcp", str(a.get("actorId") or "agent")[:200]),
        )
        return {"ok": True, **self.service.preview_command(cmd)}

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
        target = a.get("appliesTo")
        if cat or target:
            items = [
                spec.to_dict(lang)
                for spec in self.service._effects.search(category=cat, applies_to=target)
            ]
            return {
                "ok": True,
                "category": cat,
                "appliesTo": target,
                "effects": items,
                "count": len(items),
            }
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
