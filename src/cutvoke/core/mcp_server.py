"""CutVoke MCP Server（任务书 T26 / 第 9.4 章）。

把核心引擎暴露为 MCP 工具，供 Claude Code / Codex / Cursor 等 Agent 调用。
复用 EditService 的同一套能力，与 CLI/HTTP/Web UI 行为一致。

M1 落地 stdio 传输（Line-delimited JSON-RPC 2.0），无第三方依赖。
工具集：create_project / project_query / project_summary / command_apply / export / capabilities。
"""

from __future__ import annotations

import json
import math
import re
import sys
from fractions import Fraction
from typing import Any, Optional

from .service import EditService, EditError
from .protocol import Actor, Command, CommandResult, ErrorCode
from .rational import Rational
from .render import RenderService, RenderError
from .editor_guide import EditorGuide, INSTRUCTIONS, recovery

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "cutvoke"
SERVER_VERSION = "0.3.1"


class MCPServer:
    """MCP 服务端（stdio）。"""

    def __init__(self, service: Optional[EditService] = None,
                 render: Optional[RenderService] = None) -> None:
        self.service = service or EditService()
        self.render = render or RenderService()
        from ..runtime import runtime_identity
        store_path = str(getattr(self.service.store, "path", "")) if self.service.store else None
        self.runtime_info = runtime_identity(store_path)
        self._agent_api = None

    def _agent_tools(self):
        if self._agent_api is None:
            from pathlib import Path
            from .httpapi import HttpApi
            from .agent_tools import AgentTools
            store_path = getattr(self.service.store, "path", None)
            media = Path(store_path).resolve().parent / "media" if store_path and store_path != ":memory:" else Path.home() / ".cutvoke" / "data" / "media"
            self._agent_api = AgentTools(HttpApi(self.service, self.render, media_dir=str(media)))
        return self._agent_api

    def close(self):
        if self._agent_api:
            self._agent_api.api.close()
        else:
            self.service.close()

    # ------------------------------------------------------------------
    # 工具定义
    # ------------------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        tools = [
            {
                "name": "create_project",
                "description": "创建一个新的视频编辑工程，返回 projectId 和初始 revision。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectId": {"type": "string", "description": "可选，工程 ID（缺省自动生成）"},
                        "name": {"type": "string", "description": "工程名称"},
                        "width": {"type": "integer", "minimum": 1, "default": 1920},
                        "height": {"type": "integer", "minimum": 1, "default": 1080},
                        "fps": {"type": "number", "exclusiveMinimum": 0, "default": 30},
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
                "description": "提交一条编辑命令。先用 editor_help(topic=command,command=命令名) 获取 payload JSON Schema；效果命令同时提供 effectId。编辑返回新 revision；只读分析保持 revision。支持预演时先 command_preview；带稳定 commandId 与 editLeaseId，出错看 error.recovery。",
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
                "description": "先查询 capabilities.commands 中该命令的 previewSupported；只有 true 才可预演。在当前 revision 上试算编辑，不写工程、不占 revision、不渲染。导出、音频处理、任务创建或取消等外部副作用命令会被拒绝，应明确使用 command_apply。",
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
                "description": "同步导出短片；长工程优先 command_apply export.enqueue + export_job。默认不覆盖已有文件。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectId": {"type": "string"},
                        "outPath": {"type": "string", "description": "输出文件绝对路径"},
                        "quality": {"type": "string", "enum": ["high", "medium", "low"], "default": "high"},
                        "overwrite": {"type": "boolean", "default": False},
                        "expectedRevision": {"type": "string"},
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
                "name": "runtime",
                "description": "读取此MCP进程启动时的版本、源码指纹与持久化数据目录；源码更新后需重连MCP客户端。",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "preview_cache_status",
                "description": "读取持久预览缓存的保存目录、容量、用量和分类，不改动工程或原素材。",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "capabilities",
                "description": "查询当前支持的编辑能力（命令类型、效果清单、质量预设等）。",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]
        from .agent_tools import AGENT_TOOLS
        tools.extend(AGENT_TOOLS)
        for tool in tools:
            tool["inputSchema"]["additionalProperties"] = False
        return tools

    @staticmethod
    def _validate_arguments(value: Any, schema: dict, path: str = "arguments") -> None:
        for branch in schema.get("allOf", []):
            MCPServer._validate_arguments(value, branch, path)
        if "anyOf" in schema:
            errors = []
            for branch in schema["anyOf"]:
                try:
                    MCPServer._validate_arguments(value, branch, path)
                    break
                except ValueError as exc:
                    errors.append(str(exc))
            else:
                raise ValueError(f"{path} must match an allowed schema: {'; '.join(errors)}")
        expected = schema.get("type")
        if expected is None and isinstance(value, dict):
            expected = "object"
        valid = {"object": isinstance(value, dict), "array": isinstance(value, list),
                 "string": isinstance(value, str),
                 "integer": isinstance(value, int) and not isinstance(value, bool),
                 "number": isinstance(value, (int, float)) and not isinstance(value, bool),
                 "boolean": isinstance(value, bool), "null": value is None}
        if isinstance(expected, list):
            if not any(valid.get(kind, False) for kind in expected):
                raise ValueError(f"{path} must be one of types {expected}")
            expected = next(kind for kind in expected if valid.get(kind, False))
        if expected in valid and not valid[expected]:
            raise ValueError(f"{path} must be {expected}")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(f"{path} must be one of {schema['enum']}")
        if expected == "object":
            if len(value) < schema.get("minProperties", 0):
                raise ValueError(f"{path} must contain more properties")
            for key in schema.get("required", []):
                if key not in value:
                    raise ValueError(f"{path}.{key} is required")
            properties = schema.get("properties", {})
            for key, child in value.items():
                if key in properties:
                    MCPServer._validate_arguments(child, properties[key], f"{path}.{key}")
                elif schema.get("additionalProperties") is False:
                    raise ValueError(f"unknown parameter: {path}.{key}")
                elif isinstance(schema.get("additionalProperties"), dict):
                    MCPServer._validate_arguments(child, schema["additionalProperties"], f"{path}.{key}")
        elif expected == "array" and "items" in schema:
            if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
                raise ValueError(f"{path} has invalid item count")
            for index, child in enumerate(value):
                MCPServer._validate_arguments(child, schema["items"], f"{path}[{index}]")
        elif expected == "string":
            if len(value) < schema.get("minLength", 0):
                raise ValueError(f"{path} must not be empty")
            if "pattern" in schema and re.search(schema["pattern"], value) is None:
                raise ValueError(f"{path} does not match {schema['pattern']}")
        elif expected in ("number", "integer"):
            if not math.isfinite(value):
                raise ValueError(f"{path} must be finite")
            for key, compare in (("minimum", lambda v, bound: v >= bound),
                                 ("maximum", lambda v, bound: v <= bound),
                                 ("exclusiveMinimum", lambda v, bound: v > bound)):
                if key in schema and not compare(value, schema[key]):
                    raise ValueError(f"{path} violates {key}={schema[key]}")

    # ------------------------------------------------------------------
    # 工具调用
    # ------------------------------------------------------------------

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            tool = next((tool for tool in self.list_tools() if tool["name"] == name), None)
            if tool is None:
                raise ValueError(f"unknown tool: {name}")
            self._validate_arguments(arguments, tool["inputSchema"])
            if name == "editor_help":
                return {"ok": True, "help": EditorGuide(self.service).help(arguments)}
            from .agent_tools import AGENT_TOOLS
            if any(tool["name"] == name for tool in AGENT_TOOLS):
                return self._agent_tools().call(name, arguments)
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
            if name == "runtime":
                return {"ok": True, **self.runtime_info}
            if name == "preview_cache_status":
                from pathlib import Path
                from .preview_cache import PreviewDiskCache
                store_path = getattr(self.service.store, "path", None)
                media = Path(store_path).resolve().parent / "media" if store_path and store_path != ":memory:" else Path.home() / ".cutvoke" / "data" / "media"
                cache = PreviewDiskCache(str(media))
                try:
                    return {"ok": True, **cache.status()}
                finally:
                    cache.close()
            return {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": f"unknown tool: {name}"}}
        except EditError as e:
            return {"ok": False, "error": {"code": e.code, "message": e.message,
                                            "retryable": e.retryable, "committed": e.committed,
                                            "recovery": recovery(e.code, arguments)}}
        except (KeyError, TypeError, ValueError, OverflowError) as e:
            return {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": str(e),
                                            "recovery": recovery("INVALID_ARGUMENT", arguments)}}
        except (RenderError, OSError) as e:
            return {"ok": False, "error": {"code": "EXECUTION_FAILED", "message": str(e),
                                            "recovery": recovery("EXECUTION_FAILED", arguments)}}
        except Exception as e:  # Keep the stdio connection alive after one failed tool.
            print(f"CutVoke MCP tool {name} failed: {type(e).__name__}: {e}", file=sys.stderr)
            return {"ok": False, "error": {"code": "INTERNAL", "message": str(e)}}

    def _create_project(self, a: dict) -> dict:
        pid = a.get("projectId") or ""
        if not pid:
            import uuid
            pid = f"p_{uuid.uuid4().hex[:8]}"
        fps_raw = a.get("fps", 30)
        value = Fraction(str(fps_raw)).limit_denominator(1_000_000)
        fps = Rational.of(30000, 1001) if fps_raw == 29.97 else Rational.of(value.numerator, value.denominator)
        proj = self.service.create_project(pid, width=a.get("width", 1920),
                                           height=a.get("height", 1080), fps=fps,
                                           name_hint=a.get("name", ""))
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
        return {"ok": True, **result.to_dict()}

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
        if a.get("expectedRevision") is not None and a["expectedRevision"] != proj.revision:
            raise EditError(ErrorCode.REVISION_CONFLICT, f"current revision {proj.revision}", retryable=True)
        r = self.render.render(proj, a["outPath"], quality=a.get("quality", "high"), overwrite=a.get("overwrite", False))
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
            "runtime": self.runtime_info,
            "workflowTools": [tool["name"] for tool in self.list_tools()],
            "workflow": ["project_list/create_project/project_clone", "media_import/media_list/media_inspect",
                         "commands_list/resources_list/effects_list", "edit_lock acquire",
                         "command_preview", "command_apply (edit.batch, revision, commandId, editLeaseId)",
                         "preview_frames", "command_apply export.enqueue + export_job",
                         "project_package", "edit_lock release"],
            "modelServices": False,
            "onboarding": {"tool": "editor_help", "resource": "cutvoke://guide/overview", "externalSkillRequired": False},
        }

    # ------------------------------------------------------------------
    # JSON-RPC / MCP 协议
    # ------------------------------------------------------------------

    def _handle(self, msg: dict) -> Optional[dict]:
        if (not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or
                not isinstance(msg.get("method"), str) or
                ("id" in msg and (isinstance(msg["id"], bool) or
                                  not isinstance(msg["id"], (str, int, type(None)))))):
            return {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32600, "message": "invalid JSON-RPC request"}}
        method = msg.get("method")
        rid = msg.get("id")
        # JSON-RPC notifications never receive a response.
        if "id" not in msg:
            return None
        if not isinstance(msg.get("params", {}), dict):
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": "params must be an object"}}

        if method == "initialize":
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            }}
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": self.list_tools()}}
        if method == "resources/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"resources": EditorGuide(self.service).resources()}}
        if method == "resources/templates/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"resourceTemplates": [{
                "uriTemplate": "cutvoke://command/{command}", "name": "Command payload schema",
                "description": "Use a command type from cutvoke://commands or commands_list.", "mimeType": "application/json"}]}}
        if method == "resources/read":
            try:
                result = EditorGuide(self.service).read(msg.get("params", {}).get("uri"))
            except (ValueError, KeyError) as exc:
                return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": str(exc)}}
            return {"jsonrpc": "2.0", "id": rid, "result": result}
        if method == "tools/call":
            params = msg.get("params", {})
            arguments = params.get("arguments", {})
            result = self.call_tool(params.get("name", ""), arguments)
            images = result.pop("_images", [])
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}, *images],
                "isError": not result.get("ok", False),
            }}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}

    def run_stdio(self) -> None:
        # MCP uses UTF-8 regardless of the Windows console code page. Frozen
        # Python ignores PYTHONIOENCODING; explicitly configure pipe wrappers.
        for stream in (sys.stdin, sys.stdout):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="strict")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                resp = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "invalid JSON"}}
            else:
                resp = self._handle(msg)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()


def main() -> int:
    # The console entry point must use the same persistent store as the CLI.
    from ..main import main as cli_main
    return cli_main(["mcp", *sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
