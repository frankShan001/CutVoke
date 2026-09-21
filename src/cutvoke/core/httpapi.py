"""CutVoke HTTP API 服务（任务书 T12 HTTP 接口族 + T31 本地访问治理）。

用 Python 标准库 http.server，零第三方运行时依赖。
默认只监听回环地址，并对来源、路径与资源做边界控制（T31 / AC22）。

接口（第 9.2 章的能力子集）：
  GET  /api/v1/capabilities                能力清单（含效果）
  GET  /api/v1/effects[?category=&effectId=] 效果发现（AC18）
  GET  /api/v1/projects                    列出工程
  POST /api/v1/projects                    创建工程
  GET  /api/v1/projects/{id}               读工程 JSON
  GET  /api/v1/projects/{id}/summary       工程摘要
  POST /api/v1/projects/{id}/commands      提交编辑命令
  POST /api/v1/projects/{id}/export        触发导出（同步阻塞，等结果）
  POST /api/v1/projects/{id}/exports       提交异步导出任务（V02，返回 202+jobId）
  GET  /api/v1/projects/{id}/exports       该工程的导出任务列表
  GET  /api/v1/jobs[?projectId=&status=]   导出任务队列视图（V02）
  GET  /api/v1/jobs/{id}                   单个任务状态
  POST /api/v1/jobs/{id}/cancel            取消导出任务（V02）
  GET  /api/v1/templates[?category=&templateId=] 工程模板列表（J12）
  POST /api/v1/projects/{id}/templates/apply     套用模板生成时间线（J12）
  GET  /api/v1/projects/{id}/events?since= 事件查询
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import urlparse, parse_qs
import uuid

from .service import EditService, EditError
from .protocol import Command, Actor, ErrorCode, ExitCode
from .rational import Rational
from .render import RenderService
from .projectpack import (
    pack_project, unpack_project, inspect_package, ProjectPackError,
    SUPPORTED_PACKAGE_FORMAT,
)
from .security import (ORIGIN_MESSAGES, GovernanceError, Limits, ResourceLimiter,
                       SessionToken, PathEscapeError, check_origin,
                       check_request_token, safe_path, resolve_binding)
from .diagnostics import JsonlLogger


class HttpApi:
    """HTTP API 应用（绑定 EditService + RenderService + 访问治理）。"""

    def __init__(self, service: EditService, render: Optional[RenderService] = None,
                 *, logger: Optional[JsonlLogger] = None,
                 limits: Optional[Limits] = None,
                 session: Optional[SessionToken] = None,
                 media_dir: Optional[str] = None) -> None:
        self.service = service
        self.render = render or RenderService()
        # 结构化诊断日志（T31）：默认关闭（避免库导入即写用户目录），
        # 由 CLI / 服务启动时显式注入 JsonlLogger() 写入 ~/.cutvoke/logs/。
        self.logger = logger
        # 资源上限（T31）：请求体大小、并发导出数
        self.limiter = ResourceLimiter(limits)
        # 会话令牌（T31）：None 表示仅回环匿名访问（默认）；存在则要求令牌
        self.session = session
        # 素材导入落盘目录（WP-03/A07）：默认 ~/.cutvoke/media/
        self.media_dir = media_dir or os.path.join(
            os.path.expanduser("~"), ".cutvoke", "media")
        # 预览视频缓存按“去掉可编辑字幕后的画面工程”寻址：字幕修改只更新
        # 前端叠层，不重新编码基础视频；画面/音频/效果变化才生成新缓存。
        self._preview_lock = threading.RLock()
        self._preview_cache: dict[str, dict[str, Any]] = {}
        self._preview_cache_limit = 8
        # V02 异步导出队列：**挂在 EditService 上**（架构矫正）。本 HttpApi 不再
        # 自持队列实例——否则会与 service 命令各持一份，导致两个后台线程重复
        # 消费任务。HttpApi 只做薄封装，转调 service._get_export_queue()，
        # 并传入 `lambda: self.render` 让队列复用本接口的 RenderService（便于
        # 测试注入假渲染器）。队列的懒创建/后台线程由 EditService.close() 收尾。

    def _export_queue(self) -> "ExportQueue":
        """取本进程唯一的异步导出队列（实际由 EditService 持有）。

        传入 `lambda: self.render`，使队列复用 HttpApi 的 RenderService
        （与同步导出/预览共享并发限流与取消看门狗）；仅首次创建时生效。
        """
        return self.service._get_export_queue(render_factory=lambda: self.render)

    def close(self) -> None:
        """停掉异步导出队列的后台线程（复用实例 / 测试收尾时必须调用）。"""
        self.service.close()

    def _preview_media_file(self, project) -> tuple[str, str, Optional[float]]:
        """返回无字幕基础预览文件、ETag 和时长。

        预览媒体是可重建缓存，不属于工程数据；缓存键不包含 revision/name/
        favorites 等非画面字段，因此字幕小改不会触发完整 MP4 重编码。
        """
        preview_project = copy.deepcopy(project)
        for sequence in preview_project.sequences:
            sequence.captions = []
        preview_project.sequence.captions = []
        snapshot = preview_project.to_dict()
        for key in ("revision", "name", "favorites", "recentEffects", "presets"):
            snapshot.pop(key, None)
        digest = hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:32]
        with self._preview_lock:
            cached = self._preview_cache.get(digest)
            if cached and os.path.isfile(cached["path"]):
                self._preview_cache.pop(digest, None)
                self._preview_cache[digest] = cached
                return cached["path"], digest, cached.get("duration")

            cache_dir = os.path.join(self.media_dir, ".preview-cache")
            os.makedirs(cache_dir, exist_ok=True)
            out_path = os.path.join(cache_dir, f"preview_{digest}.mp4")
            if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
                entry = {"path": out_path, "duration": None}
                self._preview_cache[digest] = entry
                return out_path, digest, None
            tmp_path = out_path + ".building.mp4"
            for stale in (tmp_path,):
                with contextlib.suppress(OSError):
                    os.remove(stale)
            try:
                result = self.render.render(
                    preview_project, tmp_path, quality="low", overwrite=True)
                if not os.path.isfile(tmp_path):
                    raise RuntimeError("preview renderer returned without an output file")
                os.replace(tmp_path, out_path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.remove(tmp_path)
                raise
            entry = {"path": out_path, "duration": result.get("duration")}
            self._preview_cache[digest] = entry
            while len(self._preview_cache) > self._preview_cache_limit:
                old_key, old = next(iter(self._preview_cache.items()))
                if old_key == digest:
                    break
                self._preview_cache.pop(old_key, None)
                with contextlib.suppress(OSError):
                    os.remove(old["path"])
            return out_path, digest, result.get("duration")

    def save_asset(self, asset_id: str, filename: str, raw: bytes) -> str:
        """把上传的素材字节落到媒体目录，返回落盘绝对路径。

        安全的文件名：只保留 basename，扩展名白名单由调用方（前端）负责
        合法性，服务端按后缀存储。目录不存在则创建（含中文路径安全）。
        """
        os.makedirs(self.media_dir, exist_ok=True)
        # 保留原始扩展名用于 ffmpeg 正确识别；文件名用 assetId 避免注入
        ext = os.path.splitext(filename)[1][:10].lower() or ".bin"
        dest = os.path.join(self.media_dir, f"{asset_id}{ext}")
        # 先写临时再 rename，避免半截文件被 probe/render 读到
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, dest)
        return dest

    @staticmethod
    def _infer_kind(name: str, has_video: bool, has_audio: bool) -> str:
        """由文件名扩展名与探测结果推断素材类别（与前端 inferKind 同口径）。"""
        lower = name.lower()
        if ".png" in lower or ".jpg" in lower or ".jpeg" in lower or ".gif" in lower or \
           ".webp" in lower or ".bmp" in lower or ".apng" in lower or ".avif" in lower:
            return "image"
        if has_video:
            return "video"
        if has_audio:
            return "audio"
        if ".mp3" in lower or ".wav" in lower or ".flac" in lower or \
           ".aac" in lower or ".m4a" in lower or ".ogg" in lower:
            return "audio"
        return "unknown"

    def import_asset(self, filename: str, raw: bytes) -> dict:
        """导入一个素材：落盘 → probe 固化元数据 → 写入资产账本。

        返回富描述（assetId/name/path/size/kind/duration/hasVideo/hasAudio/
        width/height）。probe 失败不阻塞导入：kind 按扩展名推断、duration 为
        None，元数据后续可被访问时再补齐（降级策略，见 D02 素材工作流）。
        账本仅在持久化服务（store 存在）下可用；内存态退化为仅落盘。
        """
        asset_id = f"asset_{uuid.uuid4().hex[:10]}"
        stored = self.save_asset(asset_id, filename, raw)

        kind = "unknown"
        duration = None
        width = None
        height = None
        has_video = False
        has_audio = False
        try:
            info = self.render.probe_media(stored)
            duration = info.get("duration") if info.get("duration", 0) > 0 else None
            width = info.get("width")
            height = info.get("height")
            has_video = bool(info.get("has_video"))
            has_audio = bool(info.get("has_audio"))
        except Exception:  # noqa: BLE001 — probe 失败降级，不阻断导入
            pass
        kind = self._infer_kind(filename, has_video, has_audio)

        store = self.service.store
        if store is not None:
            return store.add_asset(
                asset_id=asset_id, name=filename, path=stored, size=len(raw),
                kind=kind, duration=duration, has_video=has_video,
                has_audio=has_audio, width=width, height=height,
            )

        # 无持久化 store：仍返回富描述，但不落账本（内存态服务）
        return {
            "assetId": asset_id,
            "name": filename,
            "path": stored,
            "size": len(raw),
            "kind": kind,
            "duration": duration,
            "hasVideo": has_video,
            "hasAudio": has_audio,
            "width": width,
            "height": height,
        }

    def _log(self, level: str, event: str, **kw: Any) -> None:
        if self.logger is None:
            return
        try:
            self.logger.log(level, event, **kw)
        except Exception:  # noqa: BLE001 — 日志失败不能影响主流程
            pass

    # ------------------------------------------------------------------
    # 路由处理（返回 (status, dict)）
    # ------------------------------------------------------------------

    def handle(self, method: str, path: str, body: dict,
               *, inline_media: bool = True) -> tuple[int, dict]:
        try:
            return self._dispatch(method, path, body, inline_media=inline_media)
        except EditError as e:
            return 400 if e.code == ErrorCode.INVALID_ARGUMENT else 409, {
                "error": {"code": e.code, "message": e.message,
                          "retryable": e.retryable, "committed": e.committed},
            }
        except ProjectPackError as e:
            # 资源包硬错误：缺失素材 / 包格式过新 / 目标目录非空等，统一 422。
            # 包格式过新是「明确拒绝」语义，给更具体的错误码便于前端提示升级。
            code = ("PACKAGE_FORMAT_UNSUPPORTED"
                    if "高于当前支持" in str(e) else "PACKAGE_ERROR")
            return 422, {"error": {"code": code, "message": str(e)}}
        except Exception as e:  # noqa: BLE001
            return 500, {"error": {"code": "INTERNAL", "message": str(e)}}

    def _dispatch(self, method: str, path: str, body: dict,
                  *, inline_media: bool = True) -> tuple[int, dict]:
        parts = [p for p in path.split("/") if p]
        # parts 形如 ['api','v1','projects',...]

        # ---- 能力与效果发现（AC18：UI/CLI/HTTP/MCP 都能列出效果）----
        if method == "GET" and parts == ["api", "v1", "capabilities"]:
            lang = body.get("lang", "zh-CN")
            return 200, {
                "effects": self.service.effect_capabilities(lang),
                "qualityPresets": ["high", "medium", "low"],
                "rationalTime": "num/den decimal strings",
                "commands": self.service.command_catalog(lang),
            }

        # ---- 命令目录（D09：开发者接入，自动派生自 EditService._handlers）----
        if method == "GET" and parts == ["api", "v1", "commands"]:
            lang = body.get("lang", "zh-CN")
            catalog = self.service.command_catalog(lang)
            return 200, {"commands": catalog, "count": len(catalog)}

        # ---- 机器可读 schema（P4 SDK）：命令目录 + 效果的 JSON Schema 形态 ----
        if method == "GET" and parts == ["api", "v1", "schema"]:
            return 200, self.service.machine_schema(body.get("lang", "zh-CN"))

        if method == "GET" and parts == ["api", "v1", "effects"]:
            lang = body.get("lang", "zh-CN")
            eid = body.get("effectId")
            registry = self.service._effects
            if eid:
                from .effects import EffectNotFound
                try:
                    spec = registry.get(eid)
                except EffectNotFound as e:
                    return 404, {"error": {"code": "EFFECT_UNAVAILABLE",
                                           "message": str(e)}}
                return 200, {"effect": spec.to_dict(lang)}
            cat = body.get("category")
            if cat:
                items = [s.to_dict(lang) for s in registry.by_category(cat)]
                return 200, {"category": cat, "effects": items, "count": len(items)}
            # J01 资源检索（计划 5.1）：支持关键词与适用对象筛选
            q = body.get("q")
            applies_to = body.get("appliesTo")
            if q or applies_to:
                items = [s.to_dict(lang) for s in registry.search(
                    q=q, applies_to=applies_to)]
                return 200, {"q": q or "", "appliesTo": applies_to or "",
                             "effects": items, "count": len(items)}
            return 200, self.service.effect_capabilities(lang)

        # ---- J12 工程模板库：一键套用完整时间线（横/竖/方画幅模板）----
        if method == "GET" and parts == ["api", "v1", "templates"]:
            from .templates import load_templates
            specs, skipped = load_templates()
            items = [s.to_dict() for s in specs]
            tid = body.get("templateId")
            if tid:
                items = [i for i in items if i["id"] == tid]
                if not items:
                    return 404, {"error": {"code": "NOT_FOUND",
                                           "message": f"unknown template: {tid}"}}
            cat = body.get("category")
            if cat:
                items = [i for i in items if i.get("category") == cat]
            # skipped 一并回报：写坏的模板 JSON 不静默吞掉
            return 200, {"templates": items, "count": len(items),
                         "skipped": skipped}

        if method == "GET" and parts == ["api", "v1", "projects"]:
            # 富信息：优先走持久层（含 updatedAt）；内存态无 updatedAt 回退。
            store = self.service.store
            if store is not None:
                return 200, {"projects": store.list_projects()}
            items = []
            for pid, proj in self.service._projects.items():
                items.append({
                    "id": pid,
                    "name": getattr(proj, "name", ""),
                    "revision": proj.revision,
                    "updatedAt": "",
                })
            return 200, {"projects": items}

        # 导出任务查询（WP-03：导出任务 UI 的状态入口）
        if method == "GET" and len(parts) == 4 and parts[:3] == ["api", "v1", "jobs"]:
            store = self.service.store
            if store is None:
                return 404, {"error": {"code": "NOT_FOUND",
                                       "message": "job ledger requires persistent store"}}
            job = store.get_export_job(parts[3])
            if job is None:
                return 404, {"error": {"code": "NOT_FOUND",
                                       "message": f"unknown job: {parts[3]}"}}
            return 200, job

        # 导出任务队列视图（V02）：GET /api/v1/jobs[?projectId=&status=]
        # 账本为准（队列的每次状态迁移都落账），所以重启后历史仍可查。
        if method == "GET" and parts == ["api", "v1", "jobs"]:
            store = self.service.store
            if store is None:
                return 200, {"jobs": []}
            pid = body.get("projectId") or None
            want_status = body.get("status") or None
            jobs = store.list_export_jobs(pid, limit=200)
            if want_status:
                jobs = [j for j in jobs if j.get("status") == want_status]
            return 200, {"jobs": jobs}

        # 取消异步导出任务（V02）：POST /api/v1/jobs/{id}/cancel
        # queued 直接出队（一次都不渲染）；running 由渲染看门狗 terminate。
        # 已完成/已取消的任务如实回报 cancelled=false，不假装取消成功。
        if method == "POST" and len(parts) == 5 and parts[:3] == ["api", "v1", "jobs"] \
                and parts[4] == "cancel":
            store = self.service.store
            row = store.get_export_job(parts[3]) if store is not None else None
            if row is None:
                return 404, {"error": {"code": "NOT_FOUND",
                                       "message": f"unknown job: {parts[3]}"}}
            try:
                r = self._export_queue().cancel(parts[3])
            except KeyError as e:
                # 任务在账本里但不在本进程队列（例如服务重启后遗留）→ 不能取消
                return 409, {"error": {"code": "NOT_CANCELLABLE", "message": str(e)}}
            return 200, {"jobId": parts[3],
                         "cancelled": bool(r.get("cancelled", False)),
                         "status": r.get("status"),
                         "message": r.get("message", "")}

        # 素材探测（导入 UX：GET /api/v1/probe?path=<绝对路径>）
        if method == "GET" and parts == ["api", "v1", "probe"]:
            src = body.get("path", "")
            if not src:
                return 400, {"error": {"code": "INVALID_ARGUMENT",
                                       "message": "probe requires path"}}
            from .render import RenderError
            try:
                return 200, self.render.probe_media(src)
            except RenderError as e:
                return 422, {"error": {"code": "PROBE_FAILED", "message": str(e)}}

        # 素材库列表（D02 素材工作流）：GET /api/v1/assets → 服务端资产账本
        if method == "GET" and parts == ["api", "v1", "assets"]:
            store = self.service.store
            if store is None:
                return 404, {"error": {"code": "NOT_FOUND",
                                       "message": "asset ledger requires persistent store"}}
            return 200, {"assets": store.list_assets()}

        # 素材缩略图（D02 缩略图）：GET /api/v1/assets/{id}/thumbnail?w=320
        # 从素材源文件抽首帧 PNG；图片可直接抽、视频按 0 时刻抽帧。
        if method == "GET" and len(parts) == 5 and parts[:3] == ["api", "v1", "assets"] \
                and parts[4] == "thumbnail":
            store = self.service.store
            if store is None:
                return 404, {"error": {"code": "NOT_FOUND",
                                       "message": "asset ledger requires persistent store"}}
            asset = store.get_asset(parts[3])
            if not asset:
                return 404, {"error": {"code": "NOT_FOUND",
                                       "message": f"unknown asset: {parts[3]}"}}
            src = asset.get("path", "")
            if not src:
                return 404, {"error": {"code": "NOT_FOUND",
                                       "message": "asset missing path"}}
            try:
                w = int(body.get("w") if body.get("w") else 320)
            except (TypeError, ValueError):
                w = 320
            w = max(64, min(960, w))
            import tempfile as _tempfile
            import os as _os
            fd, tmp = _tempfile.mkstemp(suffix=".png")
            _os.close(fd)
            from .render import RenderError
            try:
                self.render.thumbnail_media(src, tmp, width=w)
                with open(tmp, "rb") as fh:
                    png = fh.read()
            except RenderError as e:
                return 422, {"error": {"code": "THUMBNAIL_FAILED",
                                       "message": str(e)}}
            finally:
                with contextlib.suppress(OSError):
                    _os.unlink(tmp)
            return 200, {"__png__": png}

        if method == "POST" and parts == ["api", "v1", "projects"]:
            pid = body.get("projectId")
            if not pid:
                # 注意：这里不要写 `import uuid`——函数内任何局部 import 都会
                # 让 uuid 在整个 handle() 作用域变成局部名，遮蔽模块级导入，
                # 导致导出分支（先执行）拿不到 uuid 而 UnboundLocalError。
                pid = f"p_{uuid.uuid4().hex[:8]}"
            width = body.get("width", 1920)
            height = body.get("height", 1080)
            fps_raw = body.get("fps", 30)
            # fps 支持两个特殊值：29.97 -> 30000/1001（NTSC）；其它按整数帧率
            if fps_raw == 29.97:
                fps = Rational.of(30000, 1001)
            else:
                fps = Rational.of(int(fps_raw), 1)
            name_hint = body.get("name", "")
            proj = self.service.create_project(
                pid, name_hint=name_hint, width=width, height=height, fps=fps,
            )
            return 201, proj.to_dict()

        if len(parts) >= 4 and parts[:3] == ["api", "v1", "projects"]:
            pid = parts[3]

            # 重命名工程（仅 name）：缺失返回 404；非法 name 返回 400。
            if method == "PATCH" and len(parts) == 4:
                name = body.get("name")
                if not isinstance(name, str) or name == "":
                    return 400, {"error": {"code": "INVALID_ARGUMENT",
                                           "message": "name must be a non-empty string"}}
                if len(name) > 200:
                    return 400, {"error": {"code": "INVALID_ARGUMENT",
                                           "message": "name too long (max 200)"}}
                ok = self.service.rename_project(
                    pid, name, edit_lease_id=body.get("editLeaseId", ""))
                if not ok:
                    return 404, {"error": {"code": "NOT_FOUND",
                                           "message": f"unknown project: {pid}"}}
                return 200, {"id": pid, "name": name}

            # Agent 编辑租约：页面继续展示工程变化，但写入在租约期间只
            # 接受持有者的 editLeaseId。租约有 TTL，Agent 异常退出也会自动解锁。
            if method == "GET" and len(parts) == 5 and parts[4] == "edit-lock":
                lease = self.service.get_edit_lease(pid)
                return 200, {"locked": lease is not None, "lease": lease}

            if method == "POST" and len(parts) == 5 and parts[4] == "edit-lock":
                lease = self.service.acquire_edit_lease(
                    pid, owner=body.get("owner", "agent"),
                    lease_id=body.get("leaseId", ""),
                    ttl_seconds=body.get("ttlSeconds", 30),
                )
                return 200, {"locked": True, "lease": lease}

            if method == "POST" and len(parts) == 6 \
                    and parts[4] == "edit-lock" and parts[5] == "renew":
                lease = self.service.renew_edit_lease(
                    pid, body.get("leaseId", ""), body.get("ttlSeconds", 30))
                return 200, {"locked": True, "lease": lease}

            if method == "POST" and len(parts) == 6 \
                    and parts[4] == "edit-lock" and parts[5] == "release":
                released = self.service.release_edit_lease(
                    pid, body.get("leaseId", ""))
                return 200, {"locked": not released,
                             "released": released}

            if method == "GET" and len(parts) == 4:
                proj = self.service.get_project(pid)
                return 200, proj.to_dict()

            if method == "GET" and len(parts) == 5 and parts[4] == "summary":
                proj = self.service.get_project(pid)
                return 200, {
                    "projectId": proj.project_id,
                    "revision": proj.revision,
                    "trackCount": len(proj.sequence.tracks),
                    "clipCount": sum(len(t.clips) for t in proj.sequence.tracks),
                    "width": proj.sequence.width,
                    "height": proj.sequence.height,
                    "fps": proj.sequence.fps.to_json(),
                }

            if method == "POST" and len(parts) == 5 and parts[4] == "commands":
                cmd = self._make_command(pid, body)
                if body.get("dryRun"):
                    return 200, self.service.preview_command(cmd)
                result = self.service.execute(cmd)
                return 200, result.to_dict()

            # J12 模板套用：POST /projects/{id}/templates/apply
            # body {templateId, sources?, clearExisting?, expectedRevision?}
            # 未提供素材的槽位用内置资产占位（占位素材是真实文件，套后可直接导出成片）
            if method == "POST" and len(parts) == 6 \
                    and parts[4] == "templates" and parts[5] == "apply":
                template_id = body.get("templateId")
                if not isinstance(template_id, str) or not template_id:
                    return 400, {"error": {
                        "code": "INVALID_ARGUMENT",
                        "message": "templates/apply requires a non-empty "
                                   "'templateId'"}}
                cmd = self._make_command(pid, {
                    "type": "template.apply",
                    "payload": {
                        "templateId": template_id,
                        "sources": body.get("sources"),
                        "clearExisting": bool(body.get("clearExisting", False)),
                    },
                    "expectedRevision": body.get("expectedRevision", ""),
                    "actor": body.get("actor", {}),
                    "editLeaseId": body.get("editLeaseId", ""),
                })
                return 200, self.service.execute(cmd).to_dict()

            # SRT 导出（D05 F23）：GET /projects/{id}/captions.srt → 纯文本
            if method == "GET" and len(parts) == 5 and parts[4] == "captions.srt":
                proj = self.service.get_project(pid)
                return 200, {"__text__": self.service.export_srt(pid)}

            # SRT 导入（D05 F23）：POST /projects/{id}/captions/import，body 为 SRT 文本
            if method == "POST" and len(parts) == 5 and parts[4] == "captions":
                srt_text = body.get("srt", "")
                if not srt_text.strip():
                    return 400, {"error": {"code": "INVALID_ARGUMENT",
                                           "message": "captions import requires 'srt' text"}}
                actor = Actor("http", "srt-import")
                n = self.service.import_srt(
                    pid, srt_text, actor=actor,
                    edit_lease_id=body.get("editLeaseId", ""))
                return 200, {"imported": n}

            if method == "POST" and len(parts) == 5 and parts[4] == "export":
                import os as _os
                from .diagnostics import DiskBudgetError, check_disk_budget
                from .store import ExportJobMismatch
                proj = self.service.get_project(pid)
                out = body.get("outPath")
                if not out:
                    return 400, {"error": {"code": "INVALID_ARGUMENT", "message": "outPath required"}}
                quality = body.get("quality", "high")
                out_abs = _os.path.abspath(out)

                # 导出任务幂等（指导第 9 章：导出任务同样要有幂等保证）。
                # 账本只在持久化服务下可用；内存态退化为直接渲染。
                claim = None
                job_id = body.get("jobId") or f"export_{uuid.uuid4().hex[:12]}"
                store = self.service.store
                if store is not None:
                    import hashlib as _hl
                    req_hash = _hl.sha256(
                        f"{pid}|{proj.revision}|{out_abs}|{quality}".encode("utf-8")
                    ).hexdigest()
                    try:
                        claim = store.claim_export_job(
                            job_id=job_id, project_id=pid, revision=proj.revision,
                            request_hash=req_hash, out_path=out_abs, quality=quality)
                    except ExportJobMismatch as e:
                        return 409, {"error": {"code": "IDEMPOTENCY_MISMATCH",
                                               "message": str(e)}}
                    outcome = claim["outcome"]
                    if outcome == "cached":
                        prev = dict(claim["job"]["result"] or {})
                        prev["jobId"] = claim["job"]["jobId"]
                        prev["cached"] = True
                        return 200, prev
                    if outcome == "in_flight":
                        return 202, {"jobId": claim["job"]["jobId"],
                                     "status": "running",
                                     "message": "same export already running"}
                    job_id = claim["job"]["jobId"]

                def _fail(code: str, msg: str, http_status: int):
                    if store is not None and claim is not None:
                        store.finish_export_job(job_id, status="failed",
                                                error={"code": code, "message": msg})
                    return http_status, {"error": {"code": code, "message": msg}}

                # 目标目录可写 + 磁盘余量（AC22：磁盘不足给可诊断错误而非伪成品）
                out_dir = _os.path.dirname(out_abs) or "."
                try:
                    check_disk_budget(out_dir, 64 * 1024 * 1024)
                except DiskBudgetError as e:
                    return _fail("DISK_INSUFFICIENT", str(e), 507)
                # 并发导出上限（T31）：超限直接拒绝，不无界排队
                try:
                    with self.limiter.export_slot():
                        r = self.render.render(proj, out, quality=quality, overwrite=True)
                except GovernanceError as e:
                    code = getattr(e, "code", "LIMIT_EXCEEDED")
                    return _fail(code, str(e), 429)
                except Exception as e:  # noqa: BLE001
                    return _fail("RENDER_FAILED", str(e), 500)
                r = dict(r)
                r["jobId"] = job_id
                if store is not None and claim is not None:
                    store.finish_export_job(job_id, status="succeeded", result=r)
                return 200, r

            # 封面导出（J10）：POST /api/v1/projects/{id}/cover
            #   body {outPath, t?, width?} → 抽帧（可选缩放）写 PNG/JPEG
            if method == "POST" and len(parts) == 5 and parts[4] == "cover":
                out = body.get("outPath")
                if not out:
                    return 400, {"error": {"code": "INVALID_ARGUMENT",
                                           "message": "outPath required"}}
                t = body.get("t")
                width = body.get("width")
                result = self.service.export_cover(pid, out, t=t, width=width)
                return 200, result

            # 工程打包（J10 资源包 Web 化）：POST /api/v1/projects/{id}/package
            #   body {outPath?} → 把工程 + 其引用的素材打包成自包含 zip 包。
            #   缺素材时 pack_project 抛 ProjectPackError → 由 handle() 转 422。
            if method == "POST" and len(parts) == 5 and parts[4] == "package":
                try:
                    proj = self.service.get_project(pid)
                except EditError:
                    return 404, {"error": {"code": "NOT_FOUND",
                                           "message": f"unknown project: {pid}"}}
                out = body.get("outPath")
                if not out:
                    # 缺省：写到 media_dir 的上级目录（同级），命名 {projectId}.cutvokepack.zip
                    out = os.path.join(os.path.dirname(self.media_dir),
                                       f"{pid}.cutvokepack.zip")
                out = os.path.abspath(out)
                dest = pack_project(proj, out)
                # 统计片段数 / 去重素材数（来自工程当前状态，不重复读包）。
                clip_count = 0
                asset_ids: set[str] = set()
                for track in proj.sequence.tracks:
                    for clip in track.clips:
                        clip_count += 1
                        asset_ids.add(clip.asset_ref.asset_id)
                return 200, {
                    "outPath": dest,
                    "format": SUPPORTED_PACKAGE_FORMAT,
                    "size": os.path.getsize(dest),
                    "clipCount": clip_count,
                    "assetCount": len(asset_ids),
                }

            if method == "GET" and len(parts) == 5 and parts[4] == "events":
                since = body.get("since", "0")
                evs = self.service.events_since(pid, since)
                return 200, {"events": [e.to_dict() for e in evs]}

            # 该工程的导出任务列表（导出任务 UI 用）
            if method == "GET" and len(parts) == 5 and parts[4] == "exports":
                store = self.service.store
                if store is None:
                    return 200, {"jobs": []}
                return 200, {"jobs": store.list_export_jobs(pid)}

            # 异步导出队列入队（V02）：POST /api/v1/projects/{id}/exports
            #   body {outPath, quality?, versioned?, transparent?,
            #         videoBitrateKbps?, audioBitrateKbps?}
            #   立刻返回 202 + jobId，渲染在后台**串行**执行：可排队、可取消
            #   （POST /api/v1/jobs/{id}/cancel），versioned=true 时永不覆盖
            #   已有成片（name.mp4 → name_v2.mp4 → …）。
            #   与同步 POST .../export 的分工：同步路径适合"等一个结果"，
            #   本端点适合"提交多个导出、边等边干别的"。
            if method == "POST" and len(parts) == 5 and parts[4] == "exports":
                out = body.get("outPath")
                if not out:
                    return 400, {"error": {"code": "INVALID_ARGUMENT",
                                           "message": "outPath required"}}
                try:
                    proj = self.service.get_project(pid)
                except EditError:
                    return 404, {"error": {"code": "NOT_FOUND",
                                           "message": f"unknown project: {pid}"}}
                job = self._export_queue().submit(proj, body)
                return 202, {"jobId": job["jobId"], "status": job["status"],
                             "projectId": job["projectId"],
                             "outPath": job["outPath"],
                             "versioned": job["versioned"]}

            # J01 资源检索（含收藏/最近使用标记）
            #   GET /api/v1/projects/{id}/resources?q=&category=&appliesTo=
            #       &favoritesOnly=1&recentOnly=1
            if method == "GET" and len(parts) == 5 and parts[4] == "resources":
                lang = body.get("lang", "zh-CN")
                proj = self.service.get_project(pid)
                fav = list(proj.favorites)
                rec = list(proj.recent_effects)
                truthy = ("1", "true", "True", "yes")
                fav_only = str(body.get("favoritesOnly", "")) in truthy
                rec_only = str(body.get("recentOnly", "")) in truthy
                scope = fav if fav_only else (rec if rec_only else None)
                specs = self.service._effects.search(
                    q=body.get("q"), category=body.get("category"),
                    applies_to=body.get("appliesTo"), ids=scope)
                items = []
                for s in specs:
                    d = s.to_dict(lang)
                    d["favorite"] = s.id in fav
                    d["recent"] = s.id in rec
                    items.append(d)
                return 200, {"effects": items, "count": len(items),
                             "favorites": fav, "recent": rec}

            # J01 个人预设列表
            if method == "GET" and len(parts) == 5 and parts[4] == "presets":
                proj = self.service.get_project(pid)
                return 200, {"presets": [dict(x) for x in proj.presets]}

            if method == "GET" and len(parts) == 5 and parts[4] == "preview-frame":
                # F32 准确预览（单帧）：GET .../preview-frame?t=<sec>&size=<w>x<h>
                # 成功返回 PNG 二进制（标记 __png__ 由 do_GET 处理）。
                # 无覆盖片段返回 204（黑帧语义）；预览语义近似（只取主层画面）。
                from .render import RenderError
                try:
                    t = float(body.get("t", "0"))
                except (TypeError, ValueError):
                    return 400, {"error": {"code": "INVALID_ARGUMENT",
                                           "message": "preview-frame requires numeric t (seconds)"}}
                size: Optional[tuple[int, int]] = None
                s = body.get("size")
                if s:
                    try:
                        w, h = s.lower().split("x")
                        size = (int(w), int(h))
                    except (ValueError, AttributeError):
                        size = None
                import os as _os
                import tempfile
                fd, tmp_png = tempfile.mkstemp(suffix=".png")
                _os.close(fd)
                try:
                    proj = self.service.get_project(pid)
                    # 字幕由 Web 的可编辑 Canvas 叠层负责显示；服务端只抽取
                    # 无字幕基础画面，避免字幕修改触发重复烧录。
                    out = self.render.extract_frame(
                        proj, t, tmp_png, size=size, include_captions=False)
                    if out is None:
                        return 204, {}
                    with open(tmp_png, "rb") as f:
                        data = f.read()
                    return 200, {"__png__": data}
                except RenderError as e:
                    return 422, {"error": {"code": "PREVIEW_FAILED", "message": str(e)}}
                except Exception as e:  # noqa: BLE001
                    return 500, {"error": {"code": "PREVIEW_ERROR", "message": str(e)}}
                finally:
                    try:
                        _os.remove(tmp_png)
                    except OSError:
                        pass

            if method == "GET" and len(parts) == 5 and parts[4] == "preview-media":
                # WP-03/A08：可播放的工程预览（含音频）。
                # 只缓存无字幕基础视频；字幕由前端叠层显示，因此字幕编辑
                # 不会重复渲染整片。实际字节由 HTTP 层按 Range 流式返回。
                from .render import RenderError as _RE
                try:
                    proj = self.service.get_project(pid)
                    requested_rev = body.get("rev")
                    if requested_rev and str(requested_rev) != proj.revision:
                        raise EditError(
                            ErrorCode.REVISION_CONFLICT,
                            f"requested revision {requested_rev}, current {proj.revision}",
                            retryable=True,
                        )
                    path, etag, duration = self._preview_media_file(proj)
                    result = {"__video_path__": path,
                              "__video_etag__": etag,
                              "duration": duration}
                    # 保留 Python 端旧的 handle() 数据契约，便于插件/测试直接
                    # 调用 API；真实 HTTP 请求显式关闭内联，走 Range 流式传输。
                    if inline_media:
                        with open(path, "rb") as f:
                            result["__video__"] = f.read()
                    return 200, result
                except EditError:
                    raise
                except _RE as e:
                    return 422, {"error": {"code": "PREVIEW_FAILED",
                                           "message": str(e)}}
                except Exception as e:  # noqa: BLE001
                    return 500, {"error": {"code": "PREVIEW_MEDIA_ERROR",
                                           "message": str(e)}}

        # ------------------------------------------------------------------
        # 资源包：打开 / 只读检查（J10 资源包 Web 化）
        # 这两类端点的路径前缀是 /api/v1/packages，与 /projects 平级，
        # 因为「打开一个包」不依赖某个已存在的工程 id（异机恢复场景）。
        # ------------------------------------------------------------------

        # 解包打开（J10）：POST /api/v1/packages/open
        #   body {packagePath, targetDir?} → 解包出工程 + 素材 → 作为新工程导入服务。
        #   targetDir 受限在 media_dir 上级目录内（防路径穿越），缺省自动生成唯一目录。
        if method == "POST" and parts == ["api", "v1", "packages", "open"]:
            pkg = body.get("packagePath")
            if not pkg:
                return 400, {"error": {"code": "INVALID_ARGUMENT",
                                       "message": "open requires packagePath"}}
            try:
                target_dir = self._resolve_unpack_target(body.get("targetDir"))
            except GovernanceError as e:
                return 400, {"error": {"code": e.code, "message": str(e)}}
            project, mapping, warnings = unpack_project(pkg, target_dir)
            # 导入为服务内的新工程（生成新 id，避免与现有工程冲突）。
            imported = self.service.import_project(project)
            # 统计该工程引用的去重素材数。
            asset_ids: set[str] = set()
            for track in imported.sequence.tracks:
                for clip in track.clips:
                    asset_ids.add(clip.asset_ref.asset_id)
            return 200, {
                "projectId": imported.project_id,
                "name": imported.name,
                "importedAssets": len(asset_ids),
                "warnings": warnings,
            }

        # 只读检查（J10）：GET /api/v1/packages/inspect?path=<pkg>
        #   不写任何东西，仅返回 manifest 摘要 + 缺失素材列表。
        if method == "GET" and parts == ["api", "v1", "packages", "inspect"]:
            pkg = body.get("path")
            if not pkg:
                return 400, {"error": {"code": "INVALID_ARGUMENT",
                                       "message": "inspect requires path"}}
            info = inspect_package(pkg)
            manifest = info["manifest"]
            compatibility = info["compatibility"]
            # 片段数：读 project.json（仅在内存解析，不落盘）。
            clip_count = 0
            try:
                import zipfile as _zf
                with _zf.ZipFile(pkg, "r") as _z:
                    proj_dict = json.loads(_z.read("project.json").decode("utf-8"))
                clip_count = sum(len(t.get("clips", []))
                                 for t in proj_dict.get("sequence", {}).get("tracks", []))
            except Exception:  # noqa: BLE001 — 取不到片段数不影响摘要主体
                pass
            missing = [a.get("originalPath", "")
                       for a in manifest.get("assets", [])
                       if a.get("present") is False]
            return 200, {
                "format": manifest.get("packageFormat"),
                "version": manifest.get("schemaVersion"),
                "projectId": manifest.get("projectId"),
                "clipCount": clip_count,
                "assetCount": len(manifest.get("assets", [])),
                "missing": missing,
                "compatibility": compatibility,
            }

        # 音频分析（J09）：POST /api/v1/audio/analyze body {audioPath} →
        #   {duration, sampleRate, channels, loudnessI, bpm}
        if method == "POST" and parts == ["api", "v1", "audio", "analyze"]:
            audio_path = body.get("audioPath")
            if not audio_path:
                return 400, {"error": {"code": "INVALID_ARGUMENT",
                                       "message": "audioPath required"}}
            return 200, self.service.audio_analyze(audio_path)

        return 404, {"error": {"code": "NOT_FOUND", "message": f"unknown: {method} {path}"}}

    def _resolve_unpack_target(self, target_dir: Optional[str]) -> str:
        """解析并约束解包目标目录，防路径穿越（J10 安全边界）。

        允许根 = media_dir 的上级目录（如 ~/.cutvoke）。规则：
          - target_dir 为 None → 在允许根下生成唯一子目录（unpacked/<uuid>）。
          - 否则把 target_dir 当作「相对允许根」的路径解析（绝对路径会覆盖前
            缀，等同要求它落在允许根内）；解析后用 pathlib 判定是否仍在允许根
            之内。任何通过 ../ 或绝对路径逃逸出允许根的情形都抛 PathEscapeError。
        返回最终绝对目标目录（确保已创建）。
        """
        allowed = Path(os.path.dirname(self.media_dir)).resolve()
        try:
            allowed.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if not target_dir:
            return str((allowed / "unpacked" / uuid.uuid4().hex).resolve())
        candidate = (allowed / target_dir).resolve()
        if candidate != allowed and allowed not in candidate.parents:
            raise PathEscapeError(
                "PATH_ESCAPE",
                f"解包目标目录越界 {target_dir!r}：解析为 {candidate}，"
                f"必须位于允许根目录 {allowed} 之内（禁止路径穿越）。",
            )
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise PathEscapeError(
                "PATH_ESCAPE",
                f"无法创建解包目标目录 {candidate}：{e}",
            ) from e
        return str(candidate)

    def _make_command(self, pid: str, body: dict) -> Command:
        kwargs: dict[str, Any] = {
            "type": body["type"],
            "payload": body.get("payload", {}),
            "project_id": pid,
            "expected_revision": body.get("expectedRevision", ""),
            "actor": Actor(kind=body.get("actor", {}).get("kind", "http"),
                           id=body.get("actor", {}).get("id", "client")),
            "edit_lease_id": body.get("editLeaseId", ""),
        }
        # 只有显式提供 commandId 才传，否则用默认生成的（幂等键由服务端或客户端提供）
        cid = body.get("commandId")
        if cid:
            kwargs["command_id"] = cid
        return Command(**kwargs)


# ---------------------------------------------------------------------------
# HTTP 服务器封装
# ---------------------------------------------------------------------------

def build_handler(api: HttpApi, web_dir: Optional[str] = None):
    import os

    class Handler(BaseHTTPRequestHandler):
        server_version = "CutVoke/0.1"

        # ---------------- 响应 ----------------

        def _cors_headers(self) -> dict[str, str]:
            """CORS 收敛：只回显回环来源，不再使用通配符 *（T31）。"""
            origin = self.headers.get("Origin")
            if not origin:
                return {}
            ok, _ = check_origin(self.headers)
            if not ok:
                return {}
            return {
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Headers":
                    "Content-Type, X-CutVoke-Token",
                "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
                "Vary": "Origin",
            }

        def _respond(self, status: int, data: dict) -> None:
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            for k, v in self._cors_headers().items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(payload)

        def _respond_bytes(self, status: int, data: bytes,
                           ctype: str = "application/octet-stream") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in self._cors_headers().items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _respond_video_file(self, path: str, etag: str = "") -> None:
            """按浏览器 Range 请求流式返回预览 MP4，不把整片读入内存。"""
            try:
                total = os.path.getsize(path)
                if total <= 0:
                    raise OSError("empty preview file")
            except OSError:
                self._respond(404, {"error": {"code": "NOT_FOUND",
                                               "message": "preview file not found"}})
                return

            start, end = 0, total - 1
            status = 200
            range_header = self.headers.get("Range", "")
            if range_header:
                try:
                    unit, value = range_header.split("=", 1)
                    if unit.strip().lower() != "bytes" or "," in value:
                        raise ValueError
                    left, right = value.strip().split("-", 1)
                    if left:
                        start = int(left)
                        end = int(right) if right else total - 1
                    else:
                        suffix = int(right)
                        if suffix <= 0:
                            raise ValueError
                        start = max(0, total - suffix)
                    if start < 0 or start >= total or end < start:
                        raise ValueError
                    end = min(end, total - 1)
                    status = 206
                except (TypeError, ValueError):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{total}")
                    self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()
                    return

            length = end - start + 1
            if etag and self.headers.get("If-None-Match") == f'"{etag}"':
                self.send_response(304)
                self.send_header("ETag", f'"{etag}"')
                self.send_header("Cache-Control", "no-cache")
                for k, v in self._cors_headers().items():
                    self.send_header(k, v)
                self.end_headers()
                return
            self.send_response(status)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-cache")
            if etag:
                self.send_header("ETag", f'"{etag}"')
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            for k, v in self._cors_headers().items():
                self.send_header(k, v)
            self.end_headers()
            try:
                with open(path, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining:
                        chunk = f.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except OSError:
                # 客户端提前断开时无需再写错误 JSON；响应已经开始发送。
                return

        def _respond_file(self, path: str) -> None:
            try:
                with open(path, "rb") as f:
                    data = f.read()
                ctype = {
                    ".html": "text/html; charset=utf-8",
                    ".js": "application/javascript",
                    ".mjs": "application/javascript",
                    ".css": "text/css",
                    ".json": "application/json",
                    ".svg": "image/svg+xml",
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                    ".ico": "image/x-icon",
                    ".woff": "font/woff",
                    ".woff2": "font/woff2",
                    ".ttf": "font/ttf",
                    ".map": "application/json",
                }.get(os.path.splitext(path)[1].lower(),
                      "application/octet-stream")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except OSError:
                self._respond(404, {"error": {"code": "NOT_FOUND", "message": "file not found"}})

        # ---------------- 治理 ----------------

        def _reject_if_forbidden(self) -> bool:
            """来源检查（T31）：非授权网页请求被拒绝。返回 True 表示已拒绝。"""
            ok, reason = check_origin(self.headers)
            if ok:
                return False
            msg = ORIGIN_MESSAGES.get(reason, reason)
            api._log("WARNING", "http.rejected", detail={"reason": reason, "path": self.path})
            self._respond(403, {"error": {"code": reason, "message": msg}})
            return True

        def _reject_if_unauthorized(self, query: Optional[dict] = None) -> bool:
            """令牌校验（T31）：只在启用令牌后生效；未启用则放行本地匿名访问。"""
            ok, reason = check_request_token(self.headers, query, api.session)
            if ok:
                return False
            code = 401
            api._log("WARNING", "http.unauthorized", detail={"reason": reason, "path": self.path})
            self._respond(code, {"error": {
                "code": reason,
                "message": "缺少或无效的会话令牌。请在请求头带 X-CutVoke-Token，"
                           "或运行 `cutvoke token show` 查看令牌。"}})
            return True

        def _resolve_static(self) -> Optional[str]:
            if not web_dir:
                return None
            req = urlparse(self.path).path
            if req in ("/", "/index.html"):
                req = "/index.html"
            try:
                p = safe_path(web_dir, req.lstrip("/"))
                if req == "/index.html" or p.is_file():
                    return str(p)
            except GovernanceError as e:
                api._log("WARNING", "http.path_rejected",
                         detail={"reason": e.code, "path": self.path})
                self._respond(403, {"error": {"code": e.code, "message": str(e)}})
                return None
            return None

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            # 请求体上限（T31）：超限直接拒绝，不读入内存
            if length > api.limiter.limits.max_body_bytes:
                raise GovernanceError(
                    "BODY_TOO_LARGE",
                    f"请求体 {length} 字节超过上限 "
                    f"{api.limiter.limits.max_body_bytes} 字节，已拒绝。")
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return {}

        # ---------------- 方法 ----------------

        def do_OPTIONS(self):
            if self._reject_if_forbidden():
                return
            self._respond(204, {})

        def do_GET(self):
            if self._reject_if_forbidden():
                return
            parsed = urlparse(self.path)
            # 静态文件优先：根 / /index.html 或 web/dist 下确实存在的资源
            # （经 safe_path 路径边界约束；不存在则落回 API 路由，避免把
            # 未知路径 404 误判为静态资源命中）。
            target = self._resolve_static()
            if target is not None:
                self._respond_file(target)
                return
            qs = parse_qs(parsed.query)
            body: dict = {}
            if "since" in qs:
                body["since"] = qs["since"][0]
            if "t" in qs:
                body["t"] = qs["t"][0]
            if "size" in qs:
                body["size"] = qs["size"][0]
            for key in ("category", "effectId", "lang", "path", "w",
                        "q", "appliesTo", "favoritesOnly", "recentOnly", "rev"):
                if key in qs:
                    body[key] = qs[key][0]
            if self._reject_if_unauthorized(qs):
                return
            status, data = api.handle("GET", parsed.path, body,
                                      inline_media=False)
            # 预览帧返回二进制 PNG（__png__ 标记，媒体走单独数据通道）
            if isinstance(data, dict) and "__png__" in data:
                self._respond_bytes(200, data["__png__"], "image/png")
                return
            # 工程预览媒体：可播放 mp4（含音频）
            if isinstance(data, dict) and "__video_path__" in data:
                self._respond_video_file(data["__video_path__"],
                                         data.get("__video_etag__", ""))
                return
            # SRT 导出：纯文本（UTF-8）
            if isinstance(data, dict) and "__text__" in data:
                self._respond_bytes(200, data["__text__"].encode("utf-8"),
                                    "text/plain; charset=utf-8")
                return
            if status == 204 and isinstance(data, dict) and not data:
                self.send_response(204)
                self.end_headers()
                return
            self._respond(status, data)

        def do_POST(self):
            if self._reject_if_forbidden():
                return
            parsed = urlparse(self.path)
            if self._reject_if_unauthorized(parse_qs(parsed.query)):
                return
            # 文件导入（WP-03/A07）：POST /api/v1/assets?name=<filename> 读原始字节
            # 存到服务媒体目录，返回稳定 assetId + 落盘路径（前端不再填绝对路径）。
            if parsed.path == "/api/v1/assets":
                qs = parse_qs(parsed.query)
                fname = (qs.get("name") or [""])[0].strip()
                if not fname:
                    self._respond(400, {"error": {"code": "INVALID_ARGUMENT",
                                                  "message": "import requires ?name=<filename>"}})
                    return
                length = int(self.headers.get("Content-Length", 0))
                limit = api.limiter.limits.max_body_bytes
                if length <= 0:
                    self._respond(400, {"error": {"code": "INVALID_ARGUMENT",
                                                  "message": "empty body"}})
                    return
                if length > limit:
                    self._respond(413, {"error": {"code": "BODY_TOO_LARGE",
                                                  "message": f"文件 {length} 字节超过上限 {limit} 字节"}})
                    return
                raw = self.rfile.read(length)
                try:
                    asset = api.import_asset(fname, raw)
                except Exception as e:  # noqa: BLE001
                    self._respond(500, {"error": {"code": "ASSET_SAVE_FAILED",
                                                  "message": str(e)}})
                    return
                self._respond(201, asset)
                return
            try:
                body = self._read_body()
            except GovernanceError as e:
                self._respond(413, {"error": {"code": e.code, "message": str(e)}})
                return
            status, data = api.handle("POST", parsed.path, body)
            self._respond(status, data)

        def do_PATCH(self):
            if self._reject_if_forbidden():
                return
            parsed = urlparse(self.path)
            if self._reject_if_unauthorized(parse_qs(parsed.query)):
                return
            try:
                body = self._read_body()
            except GovernanceError as e:
                self._respond(413, {"error": {"code": e.code, "message": str(e)}})
                return
            status, data = api.handle("PATCH", parsed.path, body)
            self._respond(status, data)

        def log_message(self, *args):  # 静默标准日志，避免污染；结构化日志走 JsonlLogger
            pass

    return Handler


def serve(api: HttpApi, host: str = "127.0.0.1", port: int = 8787,
          web_dir: Optional[str] = None,
          allow_non_loopback: bool = False) -> None:
    """启动 HTTP 服务（阻塞）。web_dir 提供静态 Web UI 文件。

    本地访问边界（T31）：默认只允许回环地址绑定；显式 allow_non_loopback=True
    才允许对外监听（同时会要求会话令牌）。
    """
    bound = resolve_binding(host, allow_non_loopback=allow_non_loopback)
    handler = build_handler(api, web_dir)
    server = ThreadingHTTPServer((bound, port), handler)
    api._log("INFO", "http.serve_start",
             detail={"host": bound, "port": port, "tokenRequired": api.session is not None})
    print(f"CutVoke server listening on http://{bound}:{port}")
    if api.session is None:
        print("  访问边界：仅回环地址；未启用会话令牌（本地匿名访问）")
    else:
        print("  访问边界：已启用会话令牌，API 请求需带 X-CutVoke-Token")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
