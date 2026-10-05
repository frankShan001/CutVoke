"""External editor workflow tools. No model or generation service is involved.

Both MCP and HTTP use these adapters and the same EditService/preview queue.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import tempfile
import threading
import uuid
from pathlib import Path

from .protocol import ErrorCode
from .service import EditError


def _tool(name, description, properties, required=()):
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": properties, "required": list(required),
        "additionalProperties": False}}


STRING = {"type": "string", "minLength": 1}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}
PAGE = {"offset": {"type": "integer", "minimum": 0}, "limit": LIMIT}
PROJECT = {"projectId": STRING}
REVISION = {**PROJECT, "expectedRevision": STRING}
AGENT_TOOLS = [
    _tool("editor_help", "START HERE if unfamiliar with CutVoke. Bundled workflow, units, recipes, command payload JSON schemas, effect schemas and error recovery; no external Skill/source-code access needed. Default {} returns a compact onboarding guide. Before editing, request topic=command with the command name; add effectId for exact effect params.",
          {"topic": {"type": "string", "enum": ["overview", "time", "editing", "errors", "recipes", "command"], "default": "overview"},
           "command": STRING, "effectId": STRING, "recipe": STRING}),
    _tool("project_list", "分页查找已有工程，返回 ID、名称、revision。先查找再打开。",
          {"query": {"type": "string"}, **PAGE}),
    _tool("project_clone", "复制当前 revision 为新的可编辑工程；原工程不变，副本从 revision 0 开始。",
          {**REVISION, "newProjectId": STRING, "name": {"type": "string"}},
          ("projectId", "expectedRevision")),
    _tool("commands_list", "按命令名检索编辑参数，返回每个命令的 payload inputSchema；先查询再填 payload。查具体效果的参数用 editor_help(topic=command, command=..., effectId=...)。批量编辑用 edit.batch。",
          {"type": STRING, "query": {"type": "string"}, **PAGE}),
    _tool("media_import", "流式复制本机素材到持久素材库，探测尺寸/声音/时长；同一内容重复导入返回同一 assetId。不会创建时间线片段。",
          {"paths": {"type": "array", "minItems": 1, "maxItems": 32, "items": STRING}}, ("paths",)),
    _tool("media_list", "分页检索持久素材库；可按类型、名称、工程已用/未用过滤。返回可直接用于 clip.insert 的 assetId。",
          {**PROJECT, "query": {"type": "string"}, "kind": {"type": "string", "enum": ["image", "video", "audio"]},
           "used": {"type": "boolean"}, **PAGE}),
    _tool("media_inspect", "探测一个已导入素材或本机源文件，返回视频/音频信息与可用时长。",
          {"assetId": STRING, "path": STRING}),
    _tool("resources_list", "检索可用的内置预制效果/标题/转场，返回 presetId、参数、适用对象；使用 builtinPreset.apply 提交。",
          {"query": {"type": "string"}, "family": {"type": "string", "enum": ["fx", "transition", "animation", "text", "filter", "sticker", "personFx"]},
           "appliesTo": {"type": "string", "enum": ["image", "video", "audio", "text"]},
           "presetId": STRING, **PAGE}),
    _tool("preview_frames", "查看工程或素材在指定秒数的实际画面，返回 PNG 图片供视觉检查。工程画面含文字轨和字幕，并标注渲染 revision；一次最多 6 帧。",
          {**REVISION, "assetId": STRING, "path": STRING,
           "times": {"type": "array", "minItems": 1, "maxItems": 6,
                     "items": {"type": "number", "minimum": 0}},
           "width": {"type": "integer", "minimum": 160, "maximum": 1920, "default": 960}}, ("times",)),
    _tool("preview_prepare", "异步准备完整的连续预览；返回 jobId，随后调用 preview_job 查询或取消。视频底层含文字轨，字幕由人类编辑器叠加；视觉验收用 preview_frames。",
          REVISION, ("projectId", "expectedRevision")),
    _tool("preview_job", "查询/取消预览任务；完成返回本机可播放文件路径。任务属于创建它的服务进程，服务重启后可重新提交并复用磁盘缓存。",
          {**PROJECT, "jobId": STRING, "action": {"type": "string", "enum": ["status", "cancel"], "default": "status"}},
          ("projectId", "jobId")),
    _tool("export_job", "非阻塞查询/取消导出任务。用 command_apply export.enqueue 创建任务，传 expectedRevision、outPath、quality、range；轮询本工具直到 succeeded，再读取 result.output_path。",
          {**PROJECT, "jobId": STRING, "action": {"type": "string", "enum": ["status", "cancel"], "default": "status"}},
          ("projectId", "jobId")),
    _tool("project_events", "分页读取实际操作者、修改对象与版本事件，用于核对 Agent 修改、人工接手和撤销历史。nextAfterSeq 可用于下次增量读取。",
          {**PROJECT, "afterSeq": {"type": "integer", "minimum": -1}, "limit": LIMIT}, ("projectId",)),
    _tool("project_package", "将工程与引用素材打包为可继续编辑的交接文件；默认拒绝覆盖已有文件。",
          {**REVISION, "outPath": STRING, "overwrite": {"type": "boolean", "default": False}},
          ("projectId", "expectedRevision", "outPath")),
]


class AgentTools:
    def __init__(self, api):
        self.api = api
        self.service = api.service
        self.render = api.render

    def call(self, name, arguments):
        handler = getattr(self, name, None)
        if not any(tool["name"] == name for tool in AGENT_TOOLS) or handler is None:
            raise ValueError(f"unknown agent tool: {name}")
        return {"ok": True, **handler(arguments)}

    def _project(self, a):
        project = copy.deepcopy(self.service.get_project(a["projectId"]))
        if a.get("expectedRevision") is not None and project.revision != a["expectedRevision"]:
            raise EditError(ErrorCode.REVISION_CONFLICT,
                            f"expected revision {a['expectedRevision']}, current {project.revision}", retryable=True)
        return project

    @staticmethod
    def _page(items, a, key):
        offset, limit = a.get("offset", 0), a.get("limit", 20)
        return {key: items[offset:offset + limit], "total": len(items),
                "nextOffset": offset + limit if offset + limit < len(items) else None}

    def project_list(self, a):
        store = self.service.store
        items = store.list_projects() if store else [
            {"id": p.project_id, "name": p.name, "revision": p.revision}
            for p in self.service._projects.values()]
        query = a.get("query", "").casefold()
        return self._page([p for p in items if query in (p["name"] + " " + p["id"]).casefold()], a, "projects")

    def project_clone(self, a):
        project = self._project(a)
        source_id, source_revision = project.project_id, project.revision
        project.revision = "0"
        project.name = a.get("name", project.name + "｜副本")
        imported = self.service.import_project(project, project_id=a.get("newProjectId"))
        return {"projectId": imported.project_id, "revision": imported.revision,
                "sourceProjectId": source_id, "sourceRevision": source_revision}

    def commands_list(self, a):
        entries = self.service.command_catalog()
        query = a.get("query", "").casefold()
        entries = [item for item in entries if (not a.get("type") or item["type"] == a["type"])
                   and query in (item["type"] + " " + item["description"]).casefold()]
        return self._page(entries, a, "commands")

    def editor_help(self, a):
        from .editor_guide import EditorGuide
        return {"help": EditorGuide(self.service).help(a)}

    def media_import(self, a):
        store = self.service.store
        if not store:
            raise ValueError("persistent storage is required for media_import")
        root = Path(self.api.media_dir) / "imports"
        root.mkdir(parents=True, exist_ok=True)
        assets = []
        for value in a["paths"]:
            source = Path(value).expanduser().resolve(strict=True)
            if not source.is_file():
                raise ValueError(f"not a media file: {source}")
            before = source.stat()
            fd, temporary = tempfile.mkstemp(prefix="import-", suffix=source.suffix.lower(), dir=root)
            try:
                digest = hashlib.sha256()
                with source.open("rb") as incoming, os.fdopen(fd, "wb") as outgoing:
                    while chunk := incoming.read(1024 * 1024):
                        digest.update(chunk)
                        outgoing.write(chunk)
                after = source.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError(f"media changed during import: {source}")
                asset_id = "asset_" + digest.hexdigest()
                existing = store.get_asset(asset_id)
                if existing and Path(existing["path"]).is_file():
                    assets.append(existing)
                    continue
                info = self.render.probe_media(temporary)
                if not info.get("has_video") and not info.get("has_audio"):
                    raise ValueError(f"no decodable media streams: {source}")
                target = root / (digest.hexdigest() + source.suffix.lower())
                os.replace(temporary, target)
                assets.append(store.add_asset(
                    asset_id=asset_id, name=source.name, path=str(target), size=after.st_size,
                    kind=self.api._infer_kind(source.name, info.get("has_video"), info.get("has_audio")),
                    duration=info.get("duration") or None, has_video=bool(info.get("has_video")),
                    has_audio=bool(info.get("has_audio")), width=info.get("width"), height=info.get("height")))
            finally:
                Path(temporary).unlink(missing_ok=True)
        return {"assets": assets, "count": len(assets)}

    def media_list(self, a):
        items = self.service.store.list_assets() if self.service.store else []
        used = set()
        revision = None
        if a.get("projectId"):
            project = self._project(a)
            revision = project.revision
            def collect(sequence):
                for track in sequence.tracks:
                    for clip in track.clips:
                        used.add(clip.asset_ref.asset_id)
                        used.add(clip.asset_ref.source_path)
                        if clip.nested:
                            collect(clip.nested)
            for sequence in project.sequences:
                collect(sequence)
        elif "used" in a:
            raise ValueError("projectId is required with used")
        query = a.get("query", "").casefold()
        filtered = []
        for item in items:
            if a.get("kind") and item["kind"] != a["kind"]:
                continue
            if query not in item["name"].casefold():
                continue
            is_used = item["assetId"] in used or item["path"] in used
            if "used" in a and a["used"] != is_used:
                continue
            filtered.append({**item, "used": is_used, "available": Path(item["path"]).is_file()})
        return {"revision": revision, **self._page(filtered, a, "assets")}

    def _source(self, a):
        if bool(a.get("assetId")) == bool(a.get("path")):
            raise ValueError("provide exactly one of assetId or path")
        if a.get("assetId"):
            asset = self.service.store.get_asset(a["assetId"]) if self.service.store else None
            if asset is None:
                raise EditError("NOT_FOUND", "asset not found")
            return Path(asset["path"]).resolve(strict=True)
        return Path(a["path"]).expanduser().resolve(strict=True)

    def media_inspect(self, a):
        path = self._source(a)
        return {"path": str(path), **self.render.probe_media(str(path))}

    def resources_list(self, a):
        from .preset_catalog import PresetCatalog
        from .resource_pack import active_resource_pack_root
        catalog = PresetCatalog.builtin(self.service._effects, package_root=active_resource_pack_root(self.api.media_dir))
        query = a.get("query", "").casefold()
        items = [p for p in catalog.all() if (not a.get("family") or p.family == a["family"])
                 and (not a.get("appliesTo") or a["appliesTo"] in p.applies_to)
                 and (not a.get("presetId") or a["presetId"] == p.id)
                 and query in (p.name + " " + p.id + " " + p.subcategory).casefold()]
        # Only inspect qualification/media for the requested page.
        page = self._page(items, a, "presets")
        page["presets"] = [p.to_dict() for p in page["presets"]]
        return page

    def preview_frames(self, a):
        from .preview_prepare import preview_identity
        from .model import Project, Sequence, Track, Clip, AssetReference
        from .rational import Rational as R
        target_count = sum(bool(a.get(key)) for key in ("projectId", "assetId", "path"))
        if target_count != 1:
            raise ValueError("provide exactly one of projectId, assetId or path")
        if a.get("projectId"):
            project = self._project(a)
        else:
            if a.get("expectedRevision"):
                raise ValueError("expectedRevision is only valid for a project")
            source = self._source(a)
            info = self.render.probe_media(str(source))
            if not info.get("has_video"):
                raise ValueError("media has no video frame; use media_inspect for audio")
            image = self.api._infer_kind(source.name, True, info.get("has_audio")) == "image"
            duration = max(a["times"]) + 1 if image else info.get("duration", 0)
            sequence = Sequence(id="source", width=info.get("width") or 1920, height=info.get("height") or 1080, fps=R.of(30))
            sequence.tracks = [Track(id="source-track", kind="video", clips=[Clip(
                id="source-clip", asset_ref=AssetReference("source-asset", str(source)),
                timeline_start=R.of(0), timeline_end=R.from_float(duration), source_start=R.of(0))])]
            project = Project("1", "source", "0", sequence)
        duration = self.render._sequence_duration(project.sequence)
        if any(t >= duration for t in a["times"]):
            raise ValueError(f"times must be before the timeline end ({duration}s)")
        width = a.get("width", 960)
        height = max(2, round(width * project.sequence.height / project.sequence.width))
        if height > 3840:
            raise ValueError("preview aspect ratio is too tall")
        # Unlike the Web base video, Agent pictures must include editable subtitles.
        identity = preview_identity(project, self.api._preview_preparation.renderer_version)
        subtitles = [s.to_dict().get("captions", []) for s in project.sequences]
        frames, images = [], []
        for at in a["times"]:
            key = hashlib.sha256(json.dumps(["agent-frame-v1", identity, subtitles, at, width, height],
                                            sort_keys=True).encode()).hexdigest()
            name = f"frame_{key}.png"
            cache = self.api.preview_cache
            path = cache.get(name, validate=lambda p: Path(p).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n")
            if not path:
                with self.api._preview_scheduler.slot(threading.Event()):
                    path = cache.get(name)
                    if not path:
                        temporary = str(cache.path(name)) + "." + uuid.uuid4().hex + ".building.png"
                        try:
                            result = self.render.extract_frame(project, at, temporary, size=(width, height), include_captions=True)
                            if result is None:
                                frames.append({"time": at, "empty": True})
                                continue
                            path = cache.publish(name, temporary)
                        finally:
                            Path(temporary).unlink(missing_ok=True)
            with cache.pin(path):
                data = Path(path).read_bytes()
            frames.append({"time": at, "path": path, "mimeType": "image/png", "empty": False})
            images.append({"type": "image", "mimeType": "image/png", "data": base64.b64encode(data).decode()})
        return {"projectId": a.get("projectId"), "revision": project.revision, "frames": frames, "_images": images}

    def preview_prepare(self, a):
        return self.api._preview_preparation.submit(self._project(a))

    def preview_job(self, a):
        queue = self.api._preview_preparation
        job = queue.get(a["jobId"])
        if job["projectId"] != a["projectId"]:
            raise EditError("NOT_FOUND", "preview job not found for this project")
        if a.get("action") == "cancel":
            job = queue.cancel(a["jobId"])
        if job["state"] == "completed":
            path, _identity, _duration = queue.media(a["jobId"])
            job["path"] = path
        return job

    def export_job(self, a):
        # Reading another interface's live ledger must not initialize a queue
        # (which also performs restart recovery of its own interrupted work).
        store = self.service.store
        job = store.get_export_job(a["jobId"]) if store else self.api._export_queue().get(a["jobId"])
        if job is None or job["projectId"] != a["projectId"]:
            raise EditError("NOT_FOUND", "export job not found for this project")
        if a.get("action") == "cancel":
            queue = self.api._export_queue()
            queue.cancel(a["jobId"])
            job = queue.get(a["jobId"])
        return job

    def project_events(self, a):
        project = self._project(a)
        after = a.get("afterSeq", -1)
        store = self.service.store
        events = store.events_since(project.project_id, after) if store else [
            event.to_dict() for event in self.service.events_since(project.project_id, "0")]
        events = events[:a.get("limit", 20)]
        return {"revision": project.revision, "events": events,
                "nextAfterSeq": events[-1].get("seq", after) if events else after,
                "lease": self.service.get_edit_lease(project.project_id)}

    def project_package(self, a):
        from .projectpack import pack_project
        project = self._project(a)
        path = Path(a["outPath"]).expanduser().resolve()
        if path.exists() and not a.get("overwrite", False):
            raise ValueError("output exists; choose another outPath or explicitly set overwrite")
        output = pack_project(project, str(path))
        return {"projectId": project.project_id, "revision": project.revision,
                "path": output, "sizeBytes": Path(output).stat().st_size}
