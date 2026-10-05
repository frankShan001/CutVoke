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
  GET  /api/v1/projects/{id}/lookup        按需读取字幕或片段
  POST /api/v1/projects/{id}/commands      提交编辑命令
  GET  /api/v1/asr/status                   本地语音识别组件状态
  POST /api/v1/projects/{id}/asr-jobs      提交片段识别任务
  GET  /api/v1/asr-jobs/{id}               识别进度与候选字幕
  POST /api/v1/asr-jobs/{id}/cancel        取消识别任务
  GET  /api/v1/person-cutout/status        本地人物抠像组件状态
  POST /api/v1/projects/{id}/person-cutout-jobs  提交人物抠像任务
  GET  /api/v1/person-cutout-jobs/{id}     人物抠像进度
  POST /api/v1/person-cutout-jobs/{id}/cancel  取消人物抠像任务
  POST /api/v1/projects/{id}/person-cutout-jobs/{job}/apply  可撤销地应用抠像结果
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
import zipfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import urlparse, parse_qs, unquote
import uuid

from .service import EditService, EditError
from .protocol import Command, Actor, ErrorCode, ExitCode
from .rational import Rational
from .effects import find_transition
from .render import RenderService, _TRANSITION_HANDLE_STATIC_FX
from .preview_prepare import PreviewPreparation
from .preview_cache import PreviewDiskCache, PreviewRenderScheduler
from .projectpack import (
    pack_project, unpack_project, inspect_package, ProjectPackError,
    SUPPORTED_PACKAGE_FORMAT,
)
from .security import (ORIGIN_MESSAGES, GovernanceError, Limits, ResourceLimiter,
                       SessionToken, PathEscapeError, check_origin,
                       check_request_token, safe_path, resolve_binding)
from .diagnostics import JsonlLogger
from .asr_jobs import AsrJobManager
from .speech_recognition import (SpeechRecognitionError,
                                 availability as asr_availability,
                                 source_signature)
from .person_cutout import (PersonCutoutError, PersonCutoutJobManager,
                            availability as person_cutout_availability,
                            find_video_clip)
from .luts import CubeInvalid, MAX_CUBE_BYTES, validate_cube


# Bump when preview rendering changes so neither disk cache nor browser ETag can
# treat media produced by an older renderer as current for the same project data.
PREVIEW_RENDER_CACHE_VERSION = 17
PREVIEW_WINDOW_SECONDS = 8
ASSET_MEDIA_TYPES = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
    ".aac": "audio/aac", ".m4a": "audio/mp4", ".ogg": "audio/ogg",
    ".opus": "audio/ogg", ".mp4": "video/mp4", ".m4v": "video/mp4",
    ".webm": "video/webm", ".mov": "video/quicktime",
    ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
}


class HttpApi:
    """HTTP API 应用（绑定 EditService + RenderService + 访问治理）。"""

    def __init__(self, service: EditService, render: Optional[RenderService] = None,
                 *, logger: Optional[JsonlLogger] = None,
                 limits: Optional[Limits] = None,
                 session: Optional[SessionToken] = None,
        media_dir: Optional[str] = None) -> None:
        self.service = service
        self.media_dir = media_dir or os.path.join(
            os.path.expanduser("~"), ".cutvoke", "media")
        if service.store is not None:
            self._ensure_active_stickers()
        self.render = render or RenderService()
        if service.store is not None:
            self._ensure_builtin_backgrounds()
        # 结构化诊断日志（T31）：默认关闭（避免库导入即写用户目录），
        # 由 CLI / 服务启动时显式注入 JsonlLogger() 写入 ~/.cutvoke/logs/。
        self.logger = logger
        # 资源上限（T31）：请求体大小、并发导出数
        self.limiter = ResourceLimiter(limits)
        # 会话令牌（T31）：None 表示仅回环匿名访问（默认）；存在则要求令牌
        self.session = session
        # The CLI injects a boot-time snapshot; library users have identity too.
        from ..runtime import runtime_identity
        store_path = str(getattr(service.store, "path", "")) if service.store else None
        self.runtime_info = runtime_identity(store_path)
        # 预览视频缓存按“去掉可编辑字幕后的画面工程”寻址：字幕修改只更新
        # 前端叠层，不重新编码基础视频；画面/音频/效果变化才生成新缓存。
        self._preview_lock = threading.RLock()
        self.preview_cache = PreviewDiskCache(self.media_dir)
        self._preview_scheduler = PreviewRenderScheduler()
        self._preview_jobs_lock = threading.Lock()
        self._preview_active_jobs: dict[str, tuple[str, threading.Event]] = {}
        self._preview_preparation = PreviewPreparation(
            os.path.join(self.media_dir, ".preview-cache"), self.render,
            self._preview_window_file, self._preview_lock,
            renderer_version=PREVIEW_RENDER_CACHE_VERSION,
            window_seconds=PREVIEW_WINDOW_SECONDS, disk_cache=self.preview_cache)
        self._asr_jobs = AsrJobManager()
        self._person_cutout_jobs = PersonCutoutJobManager()
        # V02 异步导出队列：**挂在 EditService 上**（架构矫正）。本 HttpApi 不再
        # 自持队列实例——否则会与 service 命令各持一份，导致两个后台线程重复
        # 消费任务。HttpApi 只做薄封装，转调 service._get_export_queue()，
        # 并传入 `lambda: self.render` 让队列复用本接口的 RenderService（便于
        # 测试注入假渲染器）。队列的懒创建/后台线程由 EditService.close() 收尾。

    def _ensure_active_stickers(self) -> None:
        """Register active pack stickers while retaining versioned project media."""
        if self.service.store is None:
            return
        from .builtin_assets import ensure_builtin_stickers
        from .resource_pack import active_resource_pack_root
        store_path = str(getattr(self.service.store, "path", ""))
        immutable_root = (None if store_path == ":memory:" or "mode=memory" in store_path
                          else Path(self.media_dir) / ".builtin-resources")
        ensure_builtin_stickers(
            self.service.store, immutable_root=immutable_root,
            package_root=active_resource_pack_root(self.media_dir))

    def _ensure_builtin_backgrounds(self) -> None:
        """Expose bundled composition backgrounds in the shared media library."""
        if self.service.store is None:
            return
        from .builtin_assets import ensure_builtin_backgrounds
        from .resource_pack import active_resource_pack_root
        store_path = str(getattr(self.service.store, "path", ""))
        immutable_root = (None if store_path == ":memory:" or "mode=memory" in store_path
                          else Path(self.media_dir) / ".builtin-resources")
        ensure_builtin_backgrounds(
            self.service.store, render=self.render, immutable_root=immutable_root,
            package_root=active_resource_pack_root(self.media_dir))

    def _export_queue(self) -> "ExportQueue":
        """取本进程唯一的异步导出队列（实际由 EditService 持有）。

        传入 `lambda: self.render`，使队列复用 HttpApi 的 RenderService
        （与同步导出/预览共享并发限流与取消看门狗）；仅首次创建时生效。
        """
        return self.service._get_export_queue(render_factory=lambda: self.render)

    def close(self) -> None:
        """停掉异步导出队列的后台线程（复用实例 / 测试收尾时必须调用）。"""
        self._preview_preparation.close()
        with self._preview_jobs_lock:
            for _identity, event in self._preview_active_jobs.values():
                event.set()
        self._asr_jobs.close()
        self._person_cutout_jobs.close()
        self.preview_cache.close()
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
        digest_source = {
            "rendererVersion": PREVIEW_RENDER_CACHE_VERSION,
            "project": snapshot,
        }
        from .preview_prepare import preview_identity
        digest_source["sourceIdentity"] = preview_identity(project, PREVIEW_RENDER_CACHE_VERSION)
        digest = hashlib.sha256(
            json.dumps(digest_source, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:32]
        name = f"preview_{digest}.mp4"
        metadata = self.preview_cache.metadata(name)
        cached = self.preview_cache.get(name, metadata)
        if cached:
            return cached, digest, metadata.get("duration")
        with self._preview_scheduler.slot(threading.Event()):
            metadata = self.preview_cache.metadata(name)
            cached = self.preview_cache.get(name, metadata)
            if cached:
                return cached, digest, metadata.get("duration")
            out_path = str(self.preview_cache.path(name))
            tmp_path = out_path + "." + uuid.uuid4().hex + ".building.mp4"
            try:
                result = self.render.render(
                    preview_project, tmp_path, quality="low", overwrite=True)
                if not os.path.isfile(tmp_path):
                    raise RuntimeError("preview renderer returned without an output file")
                out_path = self.preview_cache.publish(name, tmp_path, {"duration": result.get("duration")})
            except BaseException:
                with contextlib.suppress(OSError):
                    os.remove(tmp_path)
                raise
            return out_path, digest, result.get("duration")

    def _slice_preview_window_project(self, project, start: Rational, stop: Rational):
        """Build a bounded render-only project for timeline-safe clip features.

        Timeline edits remain untouched. Keyframed, animated, attached, or
        time-dependent clip features fall back to full-project compilation.
        """
        seq = project.sequence
        start_s = float(start.to_fraction())
        stop_s = float(stop.to_fraction())
        render_start_s, render_stop_s = start_s, stop_s
        multicam = seq.multicam if isinstance(seq.multicam, dict) else {}
        multicam_active = multicam.get("activeTrackId")

        video_tracks = [track for track in seq.tracks
                        if track.kind == "video" and track.visible and
                        (multicam_active is None or track.id == multicam_active) and
                        any(not clip.hidden for clip in track.clips)]
        video_tracks.sort(key=lambda track: track.role == "sticker")
        video_rank = {track.id: rank for rank, track in enumerate(video_tracks)}

        def transition_seconds(value: Any) -> float:
            if isinstance(value, Rational):
                return float(value.to_fraction())
            if isinstance(value, dict):
                try:
                    denominator = float(value.get("den", 1))
                    return float(value.get("num", 0)) / denominator
                except (TypeError, ValueError, ZeroDivisionError):
                    return 0.0
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return 0.0

        all_transitions: list[tuple[float, float]] = []
        target_transitions: list[tuple[float, float]] = []
        for track in video_tracks:
            clips = sorted((clip for clip in track.clips if not clip.hidden),
                           key=lambda clip: clip.timeline_start)
            for index in range(1, len(clips)):
                incoming, outgoing = clips[index], clips[index - 1]
                seam = float(incoming.timeline_start.to_fraction())
                if abs(float((incoming.timeline_start - outgoing.timeline_end).to_fraction())) > 1e-6:
                    continue
                transition = find_transition(incoming, getattr(self.render, "effects", None))
                if transition is None:
                    continue
                params = transition.get("params") or {}
                requested = transition_seconds(params.get("duration", 0))
                duration = min(requested,
                               float(outgoing.duration.to_fraction()) / 2,
                               float(incoming.duration.to_fraction()) / 2)
                if duration <= 0:
                    continue
                transition_window = (seam, duration)
                all_transitions.append(transition_window)
                if seam >= stop_s or seam + duration <= start_s:
                    continue
                target_transitions.append(transition_window)
                # Keep enough of both clips for _build_video_track to calculate
                # the same clamped transition duration after the preview trim.
                render_start_s = min(render_start_s, max(0.0, seam - 2 * duration))
                render_stop_s = max(render_stop_s, seam + 2 * duration)

        # If the required pre-roll itself crosses another transition, retain the
        # established full-project route rather than approximate its visual state.
        for seam, duration in all_transitions:
            if (seam < start_s and seam + duration > render_start_s and
                    (seam, duration) not in target_transitions):
                return None

        render_start = Rational.of(round(render_start_s * 1_000_000), 1_000_000)
        render_stop = Rational.of(round(render_stop_s * 1_000_000), 1_000_000)
        if render_stop <= render_start:
            return None

        sliced = copy.deepcopy(project)
        for sequence in sliced.sequences:
            sequence.captions = []
        sliced.sequence.captions = []
        selected_ids: set[str] = set()
        render_modes: list[dict[str, Any]] = []
        needs_canvas = getattr(self.render, "_clip_needs_canvas", None)

        for track in sliced.sequence.tracks:
            if track.kind == "video":
                active = (track.visible and
                          (multicam_active is None or track.id == multicam_active))
            elif track.kind == "audio":
                active = track.visible and not track.muted
            elif track.kind == "text":
                active = track.visible
            else:
                active = False
            if not active:
                track.clips = []
                continue
            overlapping = [clip for clip in track.clips if not clip.hidden and
                           clip.timeline_start < render_stop and
                           clip.timeline_end > render_start]
            if track.kind == "text" and overlapping:
                return None
            track.clips = overlapping
            selected_ids.update(clip.id for clip in overlapping)
            if track.kind == "video" and overlapping:
                original = next((item for item in video_tracks if item.id == track.id), None)
                if original is None:
                    return None
                rank = video_rank[track.id]
                render_modes.append({
                    "trackId": track.id,
                    "overlay": rank > 0 or track.role == "sticker",
                    "canvas": bool(needs_canvas and any(
                        not clip.hidden and needs_canvas(clip) for clip in original.clips)),
                })

        all_clips = [clip for track in sliced.sequence.tracks for clip in track.clips]
        selected_set = {clip.id for clip in all_clips}
        for track in seq.tracks:
            for clip in track.clips:
                if clip.id in selected_ids and clip.attached_to_clip_id:
                    return None
                if clip.attached_to_clip_id in selected_set and clip.id not in selected_set:
                    return None

        fx_key_params = getattr(self.render, "_fx_key_params", None)

        def stable_effect_stack(effects: list[dict]) -> bool:
            for effect in effects:
                if not isinstance(effect, dict):
                    return False
                effect_id = str(effect.get("effectId", ""))
                if (effect_id.startswith("cutvoke.transition.") or
                        effect_id in {"cutvoke.color", "cutvoke.transform"}):
                    continue
                if not effect_id.startswith("cutvoke.fx.") or fx_key_params is None:
                    return False
                key, _params = fx_key_params(effect)
                if key not in _TRANSITION_HANDLE_STATIC_FX:
                    return False
            return True

        for clip in all_clips:
            effects = clip.effects or []
            if (clip.nested is not None or clip.keyframes or
                    clip.fade_in != Rational.of(0) or clip.fade_out != Rational.of(0) or
                    clip.frame_interpolation not in (None, "none") or
                    clip.speed <= Rational.of(0) or
                    not stable_effect_stack(effects)):
                return None
            old_start, old_end = clip.timeline_start, clip.timeline_end
            new_start = max(old_start, render_start)
            new_end = min(old_end, render_stop)
            if new_end <= new_start:
                return None
            if clip.speed_curve is not None:
                EditService._crop_curve_clip(clip, new_start, new_end)
            else:
                EditService._rebase_effect_ranges(
                    clip, old_start, old_end, new_start, new_end)
                clip.source_start = clip.source_start + (new_start - old_start) * clip.speed
                clip.timeline_start, clip.timeline_end = new_start, new_end
            clip.timeline_start = new_start - render_start
            clip.timeline_end = new_end - render_start

        sliced.sequence.tracks = [track for track in sliced.sequence.tracks if track.clips]
        return sliced, float((start - render_start).to_fraction()), render_modes

    def _preview_window_file(self, project, index: int,
                             cancel_event: Optional[threading.Event] = None,
                             *, priority: int = 0) -> tuple[str, str, float]:
        """Render/cache one playback window instead of an entire changed project."""
        if index < 0:
            raise EditError(ErrorCode.INVALID_ARGUMENT, "preview window index must be >= 0")
        seq = project.sequence
        visible = [clip for track in seq.tracks if track.kind == "video" and track.visible
                   for clip in track.clips if not clip.hidden]
        audible = [clip for track in seq.tracks
                   if track.kind == "audio" and track.visible and not track.muted
                   for clip in track.clips if not clip.hidden]
        if not visible and not audible:
            raise EditError(ErrorCode.INVALID_ARGUMENT, "project has no visible video or audible audio")
        timeline_end = max(clip.timeline_end for clip in visible + audible)
        start = Rational.of(index * PREVIEW_WINDOW_SECONDS)
        if start >= timeline_end:
            raise EditError(ErrorCode.INVALID_ARGUMENT, "preview window is past the media end")
        stop = min(start + Rational.of(PREVIEW_WINDOW_SECONDS), timeline_end)
        duration = float((stop - start).to_fraction())
        preview_project = copy.deepcopy(project)
        for sequence in preview_project.sequences:
            sequence.captions = []
        preview_project.sequence.captions = []

        # The common case (one plain clip covering the whole window) can be
        # compiled from that source interval alone. A distant edit then keeps
        # this window's cache key and does not trigger another encode.
        active_video_tracks = [track for track in seq.tracks
                               if track.kind == "video" and track.visible and
                               any(not clip.hidden for clip in track.clips)]
        active_audio_tracks = [track for track in seq.tracks
                               if track.kind == "audio" and track.visible and
                               not track.muted and any(not clip.hidden for clip in track.clips)]
        active_text_tracks = [track for track in seq.tracks
                              if track.kind == "text" and track.visible and
                              any(not clip.hidden and clip.timeline_start < stop and
                                  clip.timeline_end > start for clip in track.clips)]
        local = False
        if (len(active_video_tracks) == 1 and not active_audio_tracks and
                not active_text_tracks and not seq.multicam):
            covering = [clip for clip in active_video_tracks[0].clips
                        if not clip.hidden and clip.timeline_start <= start and clip.timeline_end >= stop]
            if len(covering) == 1:
                clip = covering[0]
                if (not clip.effects and not clip.keyframes and not clip.nested and
                        clip.speed_curve is None and
                        clip.freeze_at is None and clip.fade_in == Rational.of(0) and
                        clip.fade_out == Rational.of(0) and clip.speed > Rational.of(0)):
                    sliced = copy.deepcopy(clip)
                    sliced.source_start = clip.source_start + (start - clip.timeline_start) * clip.speed
                    sliced.timeline_start = Rational.of(0)
                    sliced.timeline_end = stop - start
                    track = copy.deepcopy(active_video_tracks[0])
                    track.clips = [sliced]
                    preview_project.sequence.tracks = [track]
                    local = True

        digest_project = preview_project
        window_render_modes: list[dict[str, Any]] = []
        if not local:
            # Hash only material that can contribute pixels or samples to this
            # window. Rendering still uses the full project above; this copy is
            # solely the cache identity, so an edit in another interval reuses
            # already encoded windows.
            digest_project = copy.deepcopy(preview_project)
            digest_tracks = digest_project.sequence.tracks
            multicam_active = (seq.multicam.get("activeTrackId")
                               if isinstance(seq.multicam, dict) else None)
            renderable_video_tracks = [
                track for track in seq.tracks
                if track.kind == "video" and track.visible and
                (multicam_active is None or track.id == multicam_active) and
                any(not clip.hidden for clip in track.clips)
            ]
            renderable_video_tracks.sort(key=lambda track: track.role == "sticker")
            video_rank = {track.id: rank
                          for rank, track in enumerate(renderable_video_tracks)}

            def transition_seconds(value: Any) -> float:
                if isinstance(value, Rational):
                    return float(value.to_fraction())
                if isinstance(value, dict):
                    try:
                        denominator = float(value.get("den", 1))
                        return float(value.get("num", 0)) / denominator
                    except (TypeError, ValueError, ZeroDivisionError):
                        return 0.0
                try:
                    return float(value or 0)
                except (TypeError, ValueError):
                    return 0.0

            for track in digest_tracks:
                if track.kind == "video":
                    if (not track.visible or
                            (multicam_active is not None and
                             multicam_active != track.id)):
                        track.clips = []
                        continue
                    clips = sorted((clip for clip in track.clips if not clip.hidden),
                                   key=lambda clip: clip.timeline_start)
                    retained = {
                        clip.id for clip in clips
                        if clip.timeline_start < stop and clip.timeline_end > start
                    }
                    for index, clip in enumerate(clips):
                        if clip.id not in retained or index == 0:
                            continue
                        transition = find_transition(clip, getattr(self.render, "effects", None))
                        if transition is None:
                            continue
                        previous = clips[index - 1]
                        if abs(float((clip.timeline_start - previous.timeline_end).to_fraction())) > 1e-6:
                            continue
                        params = transition.get("params") or {}
                        transition_duration = transition_seconds(
                            params.get("duration", 0))
                        transition_duration = min(
                            transition_duration,
                            float(previous.duration.to_fraction()) / 2,
                            float(clip.duration.to_fraction()) / 2,
                        )
                        transition_start = float(clip.timeline_start.to_fraction())
                        if (transition_duration > 0 and
                                transition_start < float(stop.to_fraction()) and
                                transition_start + transition_duration >
                                float(start.to_fraction())):
                            retained.add(previous.id)
                    track.clips = [clip for clip in clips if clip.id in retained]
                    if track.clips:
                        rank = video_rank.get(track.id, 0)
                        needs_canvas = getattr(self.render, "_clip_needs_canvas", None)
                        window_render_modes.append({
                            "trackId": track.id,
                            "overlay": rank > 0 or track.role == "sticker",
                            # Track compilation chooses one path for all of its
                            # clips. A distant clip may switch that path even
                            # though its pixels are outside this window.
                            "canvas": bool(needs_canvas and any(
                                not clip.hidden and needs_canvas(clip)
                                for clip in next(
                                    full.clips for full in seq.tracks
                                    if full.id == track.id))),
                        })
                elif track.kind == "audio":
                    if not track.visible or track.muted:
                        track.clips = []
                    else:
                        track.clips = [clip for clip in track.clips
                                       if not clip.hidden and clip.timeline_start < stop
                                       and clip.timeline_end > start]
                elif track.kind == "text":
                    if not track.visible:
                        track.clips = []
                    else:
                        track.clips = [clip for clip in track.clips
                                       if not clip.hidden and clip.timeline_start < stop
                                       and clip.timeline_end > start]
                else:
                    track.clips = []
            digest_project.sequence.tracks = [track for track in digest_tracks if track.clips]

        render_project = preview_project
        render_window_start = float(start.to_fraction())
        render_modes_for_preview = None
        if not local:
            sliced = self._slice_preview_window_project(project, start, stop)
            if sliced is not None:
                render_project, render_window_start, render_modes_for_preview = sliced
                # The exact render-only snapshot is also the cache identity, so
                # a changed pre-roll clip or preserved track mode cannot reuse
                # a stale window.
                digest_project = copy.deepcopy(render_project)
                window_render_modes = render_modes_for_preview

        digest_sequence = digest_project.sequence
        snapshot = {
            "sequence": digest_sequence.to_dict(),
            "windowRenderModes": window_render_modes,
        }
        paths = {clip.asset_ref.source_path for track in digest_sequence.tracks
                 for clip in track.clips if clip.asset_ref.source_path}
        source_stats = []
        for path in sorted(paths):
            try:
                stat = os.stat(path)
                source_stats.append((path, stat.st_size, stat.st_mtime_ns))
            except OSError:
                source_stats.append((path, None, None))
        digest = hashlib.sha256(json.dumps({
            "rendererVersion": PREVIEW_RENDER_CACHE_VERSION,
            "window": index, "local": local, "duration": duration,
            "project": snapshot, "sourceStats": source_stats,
        }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:32]

        def cached_window_is_valid(path: str) -> bool:
            probe_media = getattr(self.render, "probe_media", None)
            if not callable(probe_media):
                return True
            try:
                info = probe_media(path)
                video_duration = float(info.get("video_duration") or
                                       info.get("duration") or 0.0)
                return (
                    bool(info.get("has_video")) and
                    int(info.get("width", 0)) == seq.width and
                    int(info.get("height", 0)) == seq.height and
                    abs(video_duration - duration) <= 0.1
                )
            except Exception:  # noqa: BLE001 — an unreadable cache is regenerated
                return False

        # A newer visual edit may arrive while the old immutable window is still
        # encoding under the cache lock. Signal that worker before waiting for
        # the lock; windows with identical visual dependencies remain queued.
        preview_cancel = cancel_event if cancel_event is not None else threading.Event()
        project_key = str(project.project_id)
        # Captions are drawn by the editor and already removed above. Metadata
        # such as project name/favorites/revision is not a visual dependency.
        # The active-job identity belongs to the whole visual timeline, before
        # slicing a plain clip. Different windows are seeks, not newer edits.
        from .preview_prepare import preview_identity
        visual_identity = preview_identity(project, PREVIEW_RENDER_CACHE_VERSION)
        # A late obsolete request must not cancel the newer active render.
        self._require_current_preview(project, visual_identity)
        with self._preview_jobs_lock:
            active = self._preview_active_jobs.get(project_key)
            if active and active[0] != visual_identity:
                active[1].set()
        name = f"window_{digest}.mp4"
        metadata = {"width": seq.width, "height": seq.height, "duration": duration}
        cached = self.preview_cache.get(name, metadata, cached_window_is_valid)
        if cached:
            return cached, digest, duration
        # The short disk-index lock never covers encoding. Recheck on admission
        # so duplicate requests share a render and cached reads bypass the queue.
        with self._preview_scheduler.slot(preview_cancel, priority):
            # Requests aborted by the browser can already be waiting here.
            # Do not encode obsolete snapshots ahead of a reopened project.
            self._require_current_preview(project, visual_identity)
            cached = self.preview_cache.get(name, metadata, cached_window_is_valid)
            if cached:
                return cached, digest, duration
            out_path = str(self.preview_cache.path(name))
            temp_path = out_path + "." + uuid.uuid4().hex + ".building.mp4"
            with self._preview_jobs_lock:
                self._preview_active_jobs[project_key] = (visual_identity, preview_cancel)
            try:
                if local:
                    options = {"cancel_event": preview_cancel} if isinstance(self.render, RenderService) else {}
                    self.render.render(preview_project, temp_path, quality="low", overwrite=True, **options)
                else:
                    options = {"cancel_event": preview_cancel} if isinstance(self.render, RenderService) else {}
                    self.render.render_preview_window(
                        render_project, temp_path, render_window_start, duration,
                        render_modes=render_modes_for_preview, **options)
                if preview_cancel.is_set():
                    from .render import RenderCancelled
                    raise RenderCancelled("preview superseded by a newer edit")
                out_path = self.preview_cache.publish(name, temp_path, metadata)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.remove(temp_path)
                raise
            finally:
                with self._preview_jobs_lock:
                    active = self._preview_active_jobs.get(project_key)
                    if active is not None and active[1] is preview_cancel:
                        self._preview_active_jobs.pop(project_key, None)
            return out_path, digest, duration

    def _require_current_preview(self, project, visual_identity: str) -> None:
        from .preview_prepare import preview_identity
        from .render import RenderCancelled
        current = self.service.get_project(str(project.project_id))
        if preview_identity(current, PREVIEW_RENDER_CACHE_VERSION) != visual_identity:
            raise RenderCancelled("preview superseded by a newer edit")

    def _preview_frame_file(self, project, at: float, size,
                            *, visual_identity: Optional[str] = None) -> Optional[str]:
        """Frame-aligned disk cache, with dependencies bounded to its interval."""
        import math
        from .preview_prepare import preview_identity
        if not math.isfinite(at) or at < 0:
            raise EditError(ErrorCode.INVALID_ARGUMENT, "预览时间必须是非负有限数")
        if size is not None and not all(0 < value <= 3840 for value in size):
            raise EditError(ErrorCode.INVALID_ARGUMENT, "预览尺寸需要在 1 到 3840 之间")
        fps = float(project.sequence.fps.to_fraction())
        frame = math.floor(at * fps + 1e-7)
        at = frame / fps
        start = Rational.of(int((frame / fps // PREVIEW_WINDOW_SECONDS) * PREVIEW_WINDOW_SECONDS))
        stop = start + Rational.of(PREVIEW_WINDOW_SECONDS)
        sliced = self._slice_preview_window_project(project, start, stop)
        dependencies = sliced[0] if sliced else project
        identity = preview_identity(dependencies, PREVIEW_RENDER_CACHE_VERSION)
        visual_identity = visual_identity or preview_identity(project, PREVIEW_RENDER_CACHE_VERSION)
        digest = hashlib.sha256(json.dumps([identity, frame, size]).encode()).hexdigest()[:32]
        name = f"frame_{digest}.png"
        metadata = {"frame": frame, "size": size}
        cached = self.preview_cache.get(name, metadata, lambda path: Path(path).stat().st_size > 0)
        if cached:
            return cached
        with self._preview_scheduler.slot(threading.Event()):
            self._require_current_preview(project, visual_identity)
            cached = self.preview_cache.get(name, metadata, lambda path: Path(path).stat().st_size > 0)
            if cached:
                return cached
            temporary = str(self.preview_cache.path(name)) + "." + uuid.uuid4().hex + ".png"
            try:
                result = self.render.extract_frame(project, at, temporary, size=size, include_captions=False)
                if result is None:
                    return None
                return self.preview_cache.publish(name, temporary, metadata)
            finally:
                with contextlib.suppress(OSError):
                    os.remove(temporary)

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

    def _asset_media_path(self, asset: dict) -> Optional[Path]:
        """Resolve an asset ledger entry without exposing arbitrary local files."""
        source = asset.get("path")
        if not isinstance(source, str) or not source:
            return None
        path = Path(source).resolve()
        media_root = Path(self.media_dir).resolve()
        builtin_root = Path(__file__).resolve().parent.parent / "assets"
        immutable_root = media_root / ".builtin-resources"
        imported = (path.parent == media_root and
                    path.stem == asset.get("assetId"))
        bundled = bool(asset.get("builtin") and
                       (path.is_relative_to(builtin_root) or path.is_relative_to(immutable_root)))
        if not imported and not bundled:
            return None
        return path if path.is_file() else None

    def relink_asset(self, asset_id: str, filename: str, raw: bytes) -> tuple[int, dict]:
        """Restore one missing uploaded file at its original project reference path."""
        store = self.service.store
        asset = store.get_asset(asset_id) if store is not None else None
        if not asset:
            return 404, {"error": {"code": "NOT_FOUND", "message": "素材不存在"}}
        source = Path(str(asset.get("path", ""))).resolve()
        media_root = Path(self.media_dir).resolve()
        if (asset.get("builtin") or source.parent != media_root or
                source.stem != asset_id):
            return 409, {"error": {"code": "ASSET_NOT_RELINKABLE",
                                   "message": "此素材不是可重新链接的用户导入文件"}}
        if source.exists():
            return 409, {"error": {"code": "ASSET_PRESENT",
                                   "message": "原素材仍可访问，无需重新链接"}}
        if Path(filename).suffix.lower() != source.suffix.lower():
            return 400, {"error": {"code": "TYPE_MISMATCH",
                                   "message": "请选择与原素材相同扩展名的文件"}}
        if not raw:
            return 400, {"error": {"code": "INVALID_ARGUMENT", "message": "文件为空"}}
        media_root.mkdir(parents=True, exist_ok=True)
        staged = media_root / f"{asset_id}-{uuid.uuid4().hex[:8]}{source.suffix.lower()}"
        try:
            staged.write_bytes(raw)
            info = self.render.probe_media(str(staged))
            kind = self._infer_kind(filename, bool(info.get("has_video")),
                                    bool(info.get("has_audio")))
            if (kind != asset.get("kind") or
                    (kind == "audio" and not info.get("has_audio")) or
                    (kind in ("video", "image") and not info.get("has_video"))):
                return 400, {"error": {"code": "TYPE_MISMATCH",
                                       "message": "新文件的媒体类型与原素材不一致"}}
            duration = info.get("duration") if info.get("duration", 0) > 0 else None
            previous = asset.get("duration")
            if (previous is not None and
                    (duration is None or duration + 0.01 < float(previous))):
                return 400, {"error": {"code": "DURATION_TOO_SHORT",
                                       "message": "新文件比原素材短，可能使已有片段越过素材末尾"}}
            os.replace(staged, source)
            restored = store.add_asset(
                asset_id=asset_id, name=filename, path=str(source), size=len(raw),
                kind=kind, duration=duration,
                has_video=bool(info.get("has_video")),
                has_audio=bool(info.get("has_audio")),
                width=info.get("width"), height=info.get("height"),
            )
            return 200, {**restored, "available": True}
        except Exception as error:  # noqa: BLE001 — caller needs a structured failure
            return 422, {"error": {"code": "RELINK_FAILED", "message": str(error)}}
        finally:
            with contextlib.suppress(OSError):
                staged.unlink()

    @staticmethod
    def _infer_kind(name: str, has_video: bool, has_audio: bool) -> str:
        """由文件名扩展名与探测结果推断素材类别（与前端 inferKind 同口径）。"""
        lower = name.lower()
        if lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
                           ".svg", ".apng", ".avif", ".heic", ".tif", ".tiff")):
            return "image"
        # 音频文件可能含封面图，ffprobe 会同时报告 video+audio。已知音频
        # 扩展名必须先于流类型判断，否则会被错误记入视频素材与视频轨。
        if lower.endswith((".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg",
                           ".opus", ".wma", ".aif", ".aiff", ".alac")):
            return "audio"
        if lower.endswith((".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
                           ".mpeg", ".mpg", ".wmv")):
            return "video"
        if has_video:
            return "video"
        if has_audio:
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

    def import_lut(self, filename: str, raw: bytes) -> dict:
        """Validate and keep a user 3D LUT under a content-addressed local path."""
        if Path(filename).suffix.lower() != ".cube":
            raise CubeInvalid("只支持 .cube 3D LUT 文件")
        edge = validate_cube(raw)
        digest = hashlib.sha256(raw).hexdigest()
        folder = Path(self.media_dir).resolve() / "luts"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{digest}.cube"
        if not target.is_file():
            temporary = folder / f"{digest}.{uuid.uuid4().hex}.part"
            try:
                temporary.write_bytes(raw)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return {"lutId": f"lut_{digest[:16]}", "name": Path(filename).name,
                "path": str(target), "sha256": digest, "size": len(raw),
                "edgeSize": edge, "inputColorSpace": "Rec.709/sRGB"}

    def _without_clip_color(self, project, clip_id: str):
        """Return a render-only snapshot before one clip's filters and color grade."""
        preview = copy.deepcopy(project)
        for track in preview.sequence.tracks:
            for clip in track.clips:
                if clip.id != clip_id:
                    continue
                if track.kind != "video":
                    raise EditError(ErrorCode.INVALID_ARGUMENT,
                                    "color comparison requires a video or image clip")
                retained = []
                for effect in clip.effects:
                    if effect.get("enabled", True) is False:
                        retained.append(effect)
                        continue
                    spec = self.service._effects.find(effect.get("effectId", ""))
                    if (spec is not None and
                            spec.to_dict()["browseCategory"] in ("filter", "color")):
                        continue
                    retained.append(effect)
                if len(retained) == len(clip.effects):
                    raise EditError(ErrorCode.INVALID_ARGUMENT,
                                    "selected clip has no filter or color effect to compare")
                clip.effects = retained
                return preview
        raise EditError(ErrorCode.INVALID_ARGUMENT,
                        f"comparison clip not found: {clip_id}")

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
        # JSON 允许顶层数组/字符串，但本 API 的每一条路由都使用命名字段。
        # 在统一入口拒绝非对象请求，避免各分支 body.get(...) 变成 500，给外部
        # Agent 一个可修复的参数错误。
        if not isinstance(body, dict):
            return 400, {"error": {
                "code": "INVALID_ARGUMENT",
                "message": "request body must be a JSON object",
            }}
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
        # BaseHTTPRequestHandler / urlparse 保留 percent-encoded path；若不在
        # 这里按段解码，前端对中文、空格或 % 的 projectId 使用
        # encodeURIComponent 后，后续 GET/commands 会找不到刚创建的工程。
        # 按段解码而不是解码整条路径，避免 %2F 被误当成路由分隔符。
        parts = [unquote(p) for p in path.split("/") if p]
        # parts 形如 ['api','v1','projects',...]

        if method == "POST" and len(parts) == 4 and parts[:3] == ["api", "v1", "agent"]:
            from .agent_tools import AgentTools, AGENT_TOOLS
            from .mcp_server import MCPServer
            spec = next((tool for tool in AGENT_TOOLS if tool["name"] == parts[3]), None)
            if spec is None:
                return 404, {"error": {"code": "NOT_FOUND", "message": "unknown Agent tool"}}
            try:
                MCPServer._validate_arguments(body, spec["inputSchema"])
                result = AgentTools(self).call(parts[3], body)
            except (ValueError, KeyError, TypeError) as error:
                return 400, {"error": {"code": "INVALID_ARGUMENT", "message": str(error)}}
            images = result.pop("_images", [])
            if images:
                result["images"] = images
            return 200, result

        if method == "GET" and parts == ["api", "v1", "agent", "tools"]:
            from .agent_tools import AGENT_TOOLS
            return 200, {"tools": AGENT_TOOLS, "modelServices": False}

        if method == "POST" and parts == ["api", "v1", "runtime", "shutdown"]:
            import hmac
            expected = os.environ.get("CUTVOKE_DESKTOP_KEY", "")
            supplied = body.get("key")
            stop = getattr(self, "shutdown_callback", None)
            if not expected or not isinstance(supplied, str) or not hmac.compare_digest(expected, supplied) or stop is None:
                return 403, {"error": {"code": "FORBIDDEN", "message": "此服务不属于当前客户端"}}
            threading.Thread(target=stop, name="desktop-shutdown", daemon=True).start()
            return 200, {"ok": True}

        if parts == ["api", "v1", "preview-cache"]:
            if method == "GET":
                return 200, self.preview_cache.status()
            if method == "POST":
                if self._preview_scheduler.active or self._preview_scheduler.queue or self._preview_preparation.busy():
                    return 409, {"error": {"code": "CACHE_BUSY", "message": "预览正在准备，请完成后再调整缓存设置"}}
                try:
                    return 200, self.preview_cache.configure(directory=body.get("directory"), max_bytes=body.get("maxBytes"))
                except (ValueError, OSError) as error:
                    return 400, {"error": {"code": "CACHE_CONFIG_INVALID", "message": str(error)}}
        if method == "POST" and parts == ["api", "v1", "preview-cache", "clear"]:
            return 200, {**self.preview_cache.trim(clear=True), **self.preview_cache.status()}

        if parts[:3] == ["api", "v1", "preview-jobs"] and len(parts) in (4, 5):
            job_id = parts[3]
            try:
                if method == "GET" and len(parts) == 4:
                    return 200, self._preview_preparation.get(job_id)
                if method == "POST" and len(parts) == 5 and parts[4] == "cancel":
                    return 200, self._preview_preparation.cancel(job_id)
                if method == "POST" and len(parts) == 5 and parts[4] == "keepalive":
                    self._preview_preparation.media(job_id)
                    return 200, {"ok": True}
                if method == "GET" and len(parts) == 5 and parts[4] == "media":
                    media, etag, duration = self._preview_preparation.media(job_id)
                    result = {"__video_path__": media, "__video_etag__": etag, "duration": duration}
                    if inline_media:
                        with open(media, "rb") as video:
                            result["__video__"] = video.read()
                    return 200, result
            except KeyError:
                return 404, {"error": {"code": "NOT_FOUND", "message": "preview job not found"}}
            except Exception as error:
                return 422, {"error": {"code": "PREVIEW_FAILED", "message": str(error)}}

        if (method == "POST" and parts[:3] == ["api", "v1", "projects"] and
                len(parts) == 5 and parts[4] == "preview-jobs"):
            project = self.service.get_project(parts[3])
            if body.get("revision") and str(body["revision"]) != project.revision:
                raise EditError(ErrorCode.REVISION_CONFLICT,
                                "工程已更新，请重新加载", retryable=True)
            try:
                return 202, self._preview_preparation.submit(project)
            except Exception as error:
                return 422, {"error": {"code": "PREVIEW_FAILED", "message": str(error)}}

        # ---- 能力与效果发现（AC18：UI/CLI/HTTP/MCP 都能列出效果）----
        if method == "GET" and parts == ["api", "v1", "runtime"]:
            return 200, {"ok": True, **self.runtime_info,
                         "previewPreparation": {"supported": True, "windowSeconds": PREVIEW_WINDOW_SECONDS}}

        # Fixed font names expose only the fonts used by caption export. This
        # shares the packaged files with Web instead of duplicating 43MB in Vite.
        if method == "GET" and len(parts) == 4 and parts[:3] == ["api", "v1", "fonts"]:
            from .caption_render import resolve_title_font, CaptionFontError
            families = {"NotoSansSC-VF.ttf": "Noto Sans SC",
                        "NotoSerifSC-VF.ttf": "Noto Serif SC"}
            family = families.get(parts[3])
            if family is None:
                return 404, {"error": {"code": "NOT_FOUND", "message": "unknown caption font"}}
            try:
                font_path = resolve_title_font(family)
                stat = os.stat(font_path)
            except (CaptionFontError, OSError) as error:
                return 422, {"error": {"code": "FONT_UNAVAILABLE", "message": str(error)}}
            return 200, {"__media_path__": font_path, "__media_type__": "font/ttf",
                         "__media_etag__": f"font-{parts[3]}-{stat.st_size}-{stat.st_mtime_ns}"}

        if method == "GET" and parts == ["api", "v1", "capabilities"]:
            lang = body.get("lang", "zh-CN")
            return 200, {
                "effects": self.service.effect_capabilities(lang),
                "qualityPresets": ["high", "medium", "low"],
                "rationalTime": "num/den decimal strings",
                "commands": self.service.command_catalog(lang),
                "runtime": self.runtime_info,
            }

        # ---- 命令目录（D09：开发者接入，自动派生自 EditService._handlers）----
        if method == "GET" and parts == ["api", "v1", "commands"]:
            lang = body.get("lang", "zh-CN")
            catalog = self.service.command_catalog(lang)
            return 200, {"commands": catalog, "count": len(catalog)}

        if method == "GET" and parts == ["api", "v1", "asr", "status"]:
            return 200, asr_availability()

        if method == "GET" and parts == ["api", "v1", "person-cutout", "status"]:
            return 200, person_cutout_availability()

        if method == "GET" and len(parts) == 4 and parts[:3] == ["api", "v1", "asr-jobs"]:
            job = self._asr_jobs.get(parts[3])
            if job is None:
                return 404, {"error": {"code": "NOT_FOUND", "message": "识别任务不存在"}}
            return 200, job

        if (method == "POST" and len(parts) == 5 and
                parts[:3] == ["api", "v1", "asr-jobs"] and parts[4] == "cancel"):
            job = self._asr_jobs.cancel(parts[3])
            if job is None:
                return 404, {"error": {"code": "NOT_FOUND", "message": "识别任务不存在"}}
            return 200, job

        if (method == "GET" and len(parts) == 4 and
                parts[:3] == ["api", "v1", "person-cutout-jobs"]):
            job = self._person_cutout_jobs.get(parts[3])
            if job is None:
                return 404, {"error": {"code": "NOT_FOUND", "message": "人物抠像任务不存在"}}
            return 200, job

        if (method == "POST" and len(parts) == 5 and
                parts[:3] == ["api", "v1", "person-cutout-jobs"] and parts[4] == "cancel"):
            job = self._person_cutout_jobs.cancel(parts[3])
            if job is None:
                return 404, {"error": {"code": "NOT_FOUND", "message": "人物抠像任务不存在"}}
            return 200, job

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
            subcategory = body.get("subcategory")
            # J01 资源检索（计划 5.1）：支持关键词、两级分类与适用对象筛选
            q = body.get("q")
            applies_to = body.get("appliesTo")
            if q or applies_to or cat or subcategory:
                items = [s.to_dict(lang) for s in registry.search(
                    q=q, category=cat, subcategory=subcategory,
                    applies_to=applies_to)]
                return 200, {"q": q or "", "appliesTo": applies_to or "",
                             "category": cat or "", "subcategory": subcategory or "",
                             "effects": items, "count": len(items)}
            return 200, self.service.effect_capabilities(lang)

        if method == "GET" and parts == ["api", "v1", "resource-packs"]:
            from .resource_pack import resource_pack_manager_status
            return 200, resource_pack_manager_status(self.media_dir)

        if method == "GET" and parts == ["api", "v1", "resource-packs", "registries"]:
            from .resource_pack_registry import list_registries
            try:
                return 200, list_registries(self.media_dir)
            except (OSError, ValueError) as error:
                return 500, {"error": {"code": "RESOURCE_REGISTRY_STATE_INVALID",
                                       "message": str(error)}}

        if method == "POST" and parts == ["api", "v1", "resource-packs", "registries", "add"]:
            url = body.get("url")
            if not isinstance(url, str):
                return 400, {"error": {"code": "INVALID_ARGUMENT",
                                       "message": "url is required"}}
            from .resource_pack_registry import add_registry
            try:
                return 201, add_registry(self.media_dir, url)
            except (OSError, ValueError) as error:
                return 422, {"error": {"code": "RESOURCE_REGISTRY_INVALID",
                                       "message": str(error)}}

        if method == "POST" and parts == ["api", "v1", "resource-packs", "registries", "refresh"]:
            url = body.get("url")
            if url is not None and not isinstance(url, str):
                return 400, {"error": {"code": "INVALID_ARGUMENT",
                                       "message": "url must be a string when provided"}}
            from .resource_pack_registry import refresh_registries
            try:
                return 200, refresh_registries(self.media_dir, url)
            except (OSError, ValueError) as error:
                return 422, {"error": {"code": "RESOURCE_REGISTRY_INVALID",
                                       "message": str(error)}}

        if method == "POST" and parts == ["api", "v1", "resource-packs", "registries", "remove"]:
            url = body.get("url")
            if not isinstance(url, str):
                return 400, {"error": {"code": "INVALID_ARGUMENT",
                                       "message": "url is required"}}
            from .resource_pack_registry import remove_registry
            try:
                return 200, remove_registry(self.media_dir, url)
            except KeyError as error:
                return 404, {"error": {"code": "NOT_FOUND", "message": str(error)}}
            except (OSError, ValueError) as error:
                return 422, {"error": {"code": "RESOURCE_REGISTRY_INVALID",
                                       "message": str(error)}}

        if method == "POST" and parts == ["api", "v1", "resource-packs", "registries", "download"]:
            url, pack_id, version = body.get("url"), body.get("packId"), body.get("version")
            if not all(isinstance(value, str) and value for value in (url, pack_id, version)):
                return 400, {"error": {"code": "INVALID_ARGUMENT",
                                       "message": "url, packId, and version are required"}}
            from .resource_pack_registry import download_registry_package
            try:
                return 201, download_registry_package(self.media_dir, url, pack_id, version)
            except KeyError as error:
                return 404, {"error": {"code": "NOT_FOUND", "message": str(error)}}
            except (OSError, ValueError) as error:
                return 422, {"error": {"code": "RESOURCE_PACKAGE_DOWNLOAD_FAILED",
                                       "message": str(error)}}

        if method == "POST" and parts == ["api", "v1", "resource-packs", "trust"]:
            publisher_id = body.get("publisherId")
            public_key_pem = body.get("publicKeyPem")
            fingerprint = body.get("fingerprint")
            if (not isinstance(publisher_id, str) or not isinstance(public_key_pem, str)
                    or len(public_key_pem.encode("utf-8")) > 16 * 1024
                    or not isinstance(fingerprint, str)):
                return 400, {"error": {"code": "INVALID_ARGUMENT",
                                       "message": "publisherId, publicKeyPem, and fingerprint are required"}}
            try:
                from cryptography.hazmat.primitives import serialization
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
                from .resource_pack_signing import add_trusted_publisher
                public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
                if not isinstance(public_key, Ed25519PublicKey):
                    raise ValueError("publisher key must be an Ed25519 public key")
                add_trusted_publisher(
                    self.media_dir, publisher_id, public_key,
                    expected_fingerprint=fingerprint,
                )
            except (TypeError, ValueError) as error:
                return 422, {"error": {"code": "PUBLISHER_KEY_INVALID", "message": str(error)}}
            from .resource_pack import resource_pack_manager_status
            return 200, resource_pack_manager_status(self.media_dir)

        if method == "POST" and parts == ["api", "v1", "resource-packs", "activate"]:
            pack_id, version = body.get("packId"), body.get("version")
            if not isinstance(pack_id, str) or not isinstance(version, str):
                return 400, {"error": {"code": "INVALID_ARGUMENT",
                                       "message": "packId and version are required"}}
            from .resource_pack import activate_resource_pack, resource_pack_manager_status
            try:
                activate_resource_pack(self.media_dir, pack_id, version)
                self._ensure_active_stickers()
            except (OSError, ValueError) as error:
                return 422, {"error": {"code": "RESOURCE_PACK_INVALID", "message": str(error)}}
            return 200, resource_pack_manager_status(self.media_dir)

        if method == "POST" and parts == ["api", "v1", "resource-packs", "rollback"]:
            from .resource_pack import resource_pack_manager_status, rollback_resource_pack
            try:
                rollback_resource_pack(self.media_dir)
                self._ensure_active_stickers()
            except (OSError, ValueError) as error:
                return 409, {"error": {"code": "RESOURCE_PACK_ROLLBACK_FAILED",
                                       "message": str(error)}}
            return 200, resource_pack_manager_status(self.media_dir)

        # 内置预设与效果算子分开计数；未通过完整验收的候选只供盘点，
        # 不能被客户端当作已交付的预设数量。
        if method == "GET" and parts == ["api", "v1", "presets"]:
            from .preset_catalog import PresetCatalog
            from .resource_pack import active_resource_pack_root
            package_root = active_resource_pack_root(self.media_dir)
            catalog = PresetCatalog.builtin(self.service._effects, package_root=package_root)
            family = str(body.get("family", ""))
            subcategory = str(body.get("subcategory", ""))
            qualified_only = str(body.get("qualifiedOnly", "")).lower() in ("1", "true", "yes")
            items = [preset.to_dict() for preset in catalog.all()
                     if (not family or preset.family == family)
                     and (not subcategory or preset.subcategory == subcategory)
                     and (not qualified_only or preset.qualified)]
            from .resource_pack import resource_pack_summary
            return 200, {"presets": items, "count": len(items),
                         "candidateCount": sum(p.status == "candidate" for p in catalog.all()),
                         "qualifiedCount": sum(p.qualified for p in catalog.all()),
                         "resourcePack": resource_pack_summary(package_root)}

        if method == "GET" and parts == ["api", "v1", "stickers"]:
            from .builtin_stickers import load_builtin_stickers
            from .resource_pack import (active_resource_pack_root, file_matches_sha256,
                                        load_resource_pack_manifest)

            package_root = active_resource_pack_root(self.media_dir)
            manifest = load_resource_pack_manifest(package_root)
            resource_entries = {item["resourceId"]: item for item in manifest["resources"]
                                if isinstance(item, dict) and isinstance(item.get("resourceId"), str)}
            file_entries = {item["path"]: item for item in manifest["files"]
                            if isinstance(item, dict) and isinstance(item.get("path"), str)}
            store = self.service.store
            items = []
            for sticker in load_builtin_stickers(package_root=package_root):
                asset = store.get_asset(sticker["assetId"]) if store is not None else None
                preview = Path(sticker["previewPath"]) if sticker["previewPath"] else None
                resource = resource_entries.get(sticker["stickerId"])
                image_path = next((item for item in (resource or {}).get("mediaFiles", [])
                                   if item.startswith("assets/stickers/") and item.lower().endswith(".png")), None)
                file_record = file_entries.get(image_path)
                asset_path = self._asset_media_path(asset) if asset is not None else None
                resource_available = bool(
                    asset_path is not None and isinstance(file_record, dict) and
                    file_matches_sha256(asset_path, file_record.get("sha256", ""),
                                        file_record.get("sizeBytes")))
                resource_ref = ({
                    "resourceId": sticker["stickerId"],
                    "packId": manifest["packId"],
                    "packVersion": manifest["version"],
                    "resourceVersion": sticker["version"],
                    "sha256": file_record["sha256"],
                } if isinstance(file_record, dict) and isinstance(file_record.get("sha256"), str) else None)
                preview_available = preview is not None and preview.is_file()
                availability_message = ""
                if not resource_available or not preview_available:
                    availability_message = "资源缺失或校验失败；请恢复/下载对应资源包，或选择其他贴纸。"
                items.append({key: value for key, value in sticker.items()
                              if key not in ("path", "previewPath")} | {
                    "resourceRef": resource_ref,
                    "available": resource_available and preview_available,
                    "previewAvailable": preview_available,
                    "availabilityMessage": availability_message,
                    "downloadState": "bundled" if resource_available and preview_available else "missing",
                })
            return 200, {"stickers": items, "count": len(items),
                         "qualifiedCount": sum(item["qualified"] for item in items)}

        if (method == "GET" and len(parts) == 5 and
                parts[:3] == ["api", "v1", "stickers"] and parts[4] == "preview"):
            from .builtin_stickers import load_builtin_stickers
            from .resource_pack import active_resource_pack_root
            package_root = active_resource_pack_root(self.media_dir)
            sticker = next((item for item in load_builtin_stickers(include_quality=False,
                                                                    package_root=package_root)
                            if item["stickerId"] == parts[3]), None)
            if sticker is None:
                return 404, {"error": {"code": "NOT_FOUND", "message": "sticker not found"}}
            source = Path(sticker["previewPath"]).resolve()
            if not source.is_relative_to(package_root.resolve()) or not source.is_file():
                return 404, {"error": {"code": "NOT_FOUND", "message": "sticker preview not found"}}
            return 200, {"__video_path__": str(source),
                         "__video_etag__": f"{sticker['stickerId']}-{sticker['version']}-{source.stat().st_size}"}

        if (method == "GET" and len(parts) == 5 and parts[:3] == ["api", "v1", "presets"]
                and parts[4] in ("cover", "preview")):
            from .preset_catalog import PresetCatalog
            from .resource_pack import active_resource_pack_root
            package_root = active_resource_pack_root(self.media_dir)
            preset = next((item for item in PresetCatalog.builtin(
                               self.service._effects, package_root=package_root).all()
                           if item.id == parts[3]), None)
            if preset is None:
                return 404, {"error": {"code": "NOT_FOUND", "message": "preset not found"}}
            reference = preset.cover if parts[4] == "cover" else preset.motion_preview
            media = (preset.asset_root / reference).resolve()
            # The manifest is bundled, but still prevent an accidental path
            # outside the package from becoming a local-file HTTP endpoint.
            if (not reference or not media.is_relative_to(package_root.resolve())
                    or not media.is_file()):
                return 404, {"error": {"code": "NOT_FOUND", "message": "preset media not found"}}
            if parts[4] == "cover":
                return 200, {"__png__": media.read_bytes()}
            return 200, {"__video_path__": str(media),
                         "__video_etag__": f"{preset.id}-{preset.version}-{media.stat().st_size}"}

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
            return 200, {"assets": [
                {**asset, "available": self._asset_media_path(asset) is not None}
                for asset in store.list_assets()
            ]}

        # 音频用途分类：用户显式区分背景音乐、音效和未分类；不从文件名猜测。
        if method == "PATCH" and len(parts) == 4 and parts[:3] == ["api", "v1", "assets"]:
            store = self.service.store
            if store is None:
                return 404, {"error": {"code": "NOT_FOUND",
                                       "message": "asset ledger requires persistent store"}}
            asset = store.get_asset(parts[3])
            if asset is None:
                return 404, {"error": {"code": "NOT_FOUND", "message": "asset not found"}}
            if asset.get("kind") != "audio":
                return 422, {"error": {"code": "INVALID_ARGUMENT",
                                       "message": "audioRole can only be set on audio assets"}}
            if asset.get("builtin"):
                return 409, {"error": {"code": "BUILTIN_ASSET_IMMUTABLE",
                                       "message": "built-in audio classification is maintained by the resource manifest"}}
            audio_role = body.get("audioRole")
            if audio_role not in ("music", "sound_effect", "unclassified"):
                return 400, {"error": {"code": "INVALID_ARGUMENT",
                                       "message": "audioRole must be music, sound_effect, or unclassified"}}
            updated = store.set_asset_audio_role(parts[3], audio_role)
            if updated is None:
                return 404, {"error": {"code": "NOT_FOUND", "message": "audio asset not found"}}
            return 200, {**updated, "available": self._asset_media_path(updated) is not None}

        if (method == "GET" and len(parts) == 5 and
                parts[:3] == ["api", "v1", "assets"] and
                parts[4] in ("media", "waveform")):
            store = self.service.store
            asset = store.get_asset(parts[3]) if store is not None else None
            if not asset:
                return 404, {"error": {"code": "NOT_FOUND", "message": "asset not found"}}
            source = self._asset_media_path(asset)
            if source is None:
                return 404, {"error": {"code": "NOT_FOUND", "message": "asset media not found"}}
            if parts[4] == "media":
                media_type = ASSET_MEDIA_TYPES.get(source.suffix.lower())
                if asset.get("kind") not in ("audio", "video", "image") or not media_type:
                    return 415, {"error": {"code": "UNSUPPORTED_MEDIA",
                                           "message": "asset format cannot be previewed in browser"}}
                stat = source.stat()
                return 200, {"__media_path__": str(source), "__media_type__": media_type,
                             "__media_etag__": f"{asset['assetId']}-{stat.st_size}-{stat.st_mtime_ns}"}
            if asset.get("kind") != "audio":
                return 415, {"error": {"code": "UNSUPPORTED_MEDIA",
                                       "message": "waveform requires an audio asset"}}
            try:
                width = max(160, min(960, int(body.get("w", 320))))
            except (TypeError, ValueError):
                width = 320
            from .render import RenderError
            stat = source.stat()
            digest = hashlib.sha256(
                f"{asset['assetId']}:{stat.st_size}:{stat.st_mtime_ns}:{width}".encode()
            ).hexdigest()[:24]
            cache_dir = Path(self.media_dir) / ".waveform-cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            output = cache_dir / f"{digest}.png"
            if not output.is_file():
                building = cache_dir / f"{digest}-{uuid.uuid4().hex[:8]}.png"
                try:
                    self.render.waveform_media(str(source), str(building), width=width)
                    os.replace(building, output)
                except RenderError as error:
                    return 422, {"error": {"code": "WAVEFORM_FAILED", "message": str(error)}}
                finally:
                    with contextlib.suppress(OSError):
                        building.unlink()
            return 200, {"__png__": output.read_bytes()}

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

            if (method == "GET" and len(parts) == 5 and
                    parts[4] == "person-cutout-preview"):
                clip_id = body.get("clipId")
                if not isinstance(clip_id, str) or not clip_id:
                    return 400, {"error": {"code": "INVALID_ARGUMENT",
                                           "message": "请选择要预览的视频片段"}}
                try:
                    import shutil
                    import subprocess
                    from math import isfinite
                    clip = find_video_clip(self.service.get_project(pid), clip_id)
                    ffmpeg = shutil.which("ffmpeg")
                    if not ffmpeg:
                        raise PersonCutoutError("找不到 ffmpeg；请先安装 FFmpeg 并加入 PATH")
                    source_start = clip.source_start.to_fraction()
                    preview_at = body.get("atSeconds", float(source_start))
                    if (isinstance(preview_at, bool) or
                            not isinstance(preview_at, (str, int, float))):
                        raise PersonCutoutError("预览时间必须是片段源素材范围内的秒数")
                    preview_at = float(preview_at)
                    source_end = source_start + clip.consumed_source_duration.to_fraction()
                    if (not isfinite(preview_at) or
                            not float(source_start) <= preview_at < float(source_end)):
                        raise PersonCutoutError("预览时间超出所选片段的源素材范围")
                    completed = subprocess.run(
                        [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss",
                         f"{preview_at:.6f}",
                         "-i", clip.asset_ref.source_path, "-frames:v", "1",
                         "-vf", "scale=640:360:force_original_aspect_ratio=decrease:flags=lanczos",
                         "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
                        capture_output=True, timeout=30,
                    )
                    if completed.returncode or not completed.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
                        detail = completed.stderr.decode("utf-8", errors="replace")[-600:]
                        raise PersonCutoutError(detail or "无法提取片段源入点预览帧")
                    if len(completed.stdout) > 8 * 1024 * 1024:
                        raise PersonCutoutError("预览帧超过 8 MiB 限制")
                    return 200, {"__png__": completed.stdout}
                except (PersonCutoutError, OSError, subprocess.SubprocessError,
                        EditError) as exc:
                    return 422, {"error": {"code": "PERSON_CUTOUT_PREVIEW_FAILED",
                                           "message": str(exc)}}

            if method == "POST" and len(parts) == 5 and parts[4] == "asr-jobs":
                clip_id = body.get("clipId")
                if not isinstance(clip_id, str) or not clip_id:
                    return 400, {"error": {"code": "INVALID_ARGUMENT",
                                           "message": "请选择有声视频或音频片段"}}
                try:
                    job = self._asr_jobs.submit(
                        self.service.get_project(pid), clip_id,
                        model=body.get("model", "base"),
                        language=body.get("language", "auto"))
                except SpeechRecognitionError as exc:
                    return 400, {"error": {"code": "ASR_UNAVAILABLE", "message": str(exc)}}
                return 202, job

            if method == "POST" and len(parts) == 5 and parts[4] == "person-cutout-jobs":
                clip_id = body.get("clipId")
                if not isinstance(clip_id, str) or not clip_id:
                    return 400, {"error": {"code": "INVALID_ARGUMENT",
                                           "message": "请选择要抠像的视频片段"}}
                try:
                    selection_mode = body.get("selectionMode", "all")
                    raw_point = body.get("selectionPoint")
                    point = ((raw_point.get("x"), raw_point.get("y"))
                             if isinstance(raw_point, dict) else None)
                    raw_points = body.get("selectionPoints")
                    selection_points = None
                    if isinstance(raw_points, list):
                        selection_points = [
                            (item.get("x"), item.get("y"), item.get("label"))
                            if isinstance(item, dict) else item
                            for item in raw_points
                        ]
                    raw_prompts = body.get("selectionPrompts")
                    selection_prompts = None
                    if isinstance(raw_prompts, list):
                        selection_prompts = []
                        for prompt in raw_prompts:
                            if not isinstance(prompt, dict):
                                selection_prompts.append(prompt)
                                continue
                            prompt_points = prompt.get("points")
                            if isinstance(prompt_points, list):
                                prompt_points = [
                                    (item.get("x"), item.get("y"), item.get("label"))
                                    if isinstance(item, dict) else item
                                    for item in prompt_points
                                ]
                            selection_prompts.append({
                                "atSeconds": prompt.get("atSeconds"),
                                "points": prompt_points,
                            })
                    elif "selectionPrompts" in body:
                        selection_prompts = raw_prompts
                    job = self._person_cutout_jobs.submit(
                        self.service.get_project(pid), clip_id, self.media_dir,
                        edge_softness=body.get("edgeSoftness", 1.2),
                        selection_mode=selection_mode,
                        selection_point=point,
                        selection_points=selection_points,
                        selection_at_seconds=body.get("selectionAtSeconds", 0.0),
                        selection_prompts=selection_prompts,
                        tracking_mode=body.get("trackingMode", "semantic"))
                except PersonCutoutError as exc:
                    return 400, {"error": {"code": "PERSON_CUTOUT_UNAVAILABLE",
                                           "message": str(exc)}}
                return 202, job

            if (method == "POST" and len(parts) == 7 and
                    parts[4] == "person-cutout-jobs" and parts[6] == "apply"):
                job_id = parts[5]
                job = self._person_cutout_jobs.claim_apply(job_id, pid)
                if job is None:
                    return 409, {"error": {"code": "PERSON_CUTOUT_NOT_READY",
                                           "message": "抠像任务不存在、尚未完成或属于其他工程"}}
                try:
                    project = self.service.get_project(pid)
                    clip = find_video_clip(project, job["clipId"])
                    if source_signature(clip) != job["sourceSignature"]:
                        raise EditError(ErrorCode.INVALID_ARGUMENT,
                                        "原片段在抠像期间发生变化；请重新生成抠像结果")
                    track = next((track for track in project.sequence.tracks
                                  if any(item.id == clip.id for item in track.clips)), None)
                    if track is None or track.locked:
                        raise EditError(ErrorCode.INVALID_ARGUMENT,
                                        "片段所在轨道已锁定或不存在，无法应用抠像")
                    result = job.get("result") or {}
                    output = Path(str(result.get("outputPath", ""))).resolve()
                    cutout_root = (Path(self.media_dir).resolve() / ".person-cutouts").resolve()
                    if (not output.is_relative_to(cutout_root) or not output.is_file()
                            or output.stat().st_size <= 0):
                        raise EditError(ErrorCode.INVALID_ARGUMENT,
                                        "透明视频文件缺失，请重新生成抠像结果")
                    command = self._make_command(pid, {
                        "type": "asset.swap",
                        "payload": {"clipIds": [clip.id], "sourcePath": str(output)},
                        "expectedRevision": project.revision,
                        "commandId": f"person_cutout_apply_{job_id}",
                        "actor": {"kind": "human", "id": "person-cutout"},
                    })
                    applied = self.service.execute(command)
                except Exception as exc:
                    self._person_cutout_jobs.finish_apply(job_id, error=str(exc))
                    raise
                finished = self._person_cutout_jobs.finish_apply(job_id)
                return 200, {"job": finished, "command": applied.to_dict()}

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
                # released=false 既可能是“别人的租约仍存在”，也可能只是本租约
                # 已过期。必须回读真实状态，不能把失败释放一律报告成仍被锁定。
                lease = self.service.get_edit_lease(pid)
                return 200, {"locked": lease is not None,
                             "lease": lease, "released": released}

            if method == "GET" and len(parts) == 4:
                proj = self.service.get_project(pid)
                return 200, proj.to_dict()

            if method == "GET" and len(parts) == 5 and parts[4] == "summary":
                return 200, self.service.project_summary(pid)

            if method == "GET" and len(parts) == 5 and parts[4] == "lookup":
                try:
                    limit = int(body.get("limit", 10))
                except (TypeError, ValueError):
                    raise EditError(ErrorCode.INVALID_ARGUMENT,
                                    "limit must be 1..50") from None
                raw_fields = body.get("fields")
                fields = raw_fields.split(",") if isinstance(raw_fields, str) else None
                return 200, self.service.project_lookup(
                    pid, entity_type=body.get("entityType", ""),
                    entity_id=body.get("entityId", ""),
                    text_contains=body.get("textContains", ""),
                    at_seconds=body.get("atSeconds"),
                    from_seconds=body.get("fromSeconds"),
                    to_seconds=body.get("toSeconds"),
                    fields=fields, limit=limit,
                )

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

            # SRT 导入：前端与公开约定使用 /captions/import；保留早期
            # /captions 作为兼容别名，避免已接入的外部 Agent 突然失效。
            is_srt_import = (
                len(parts) == 6 and parts[4] == "captions" and parts[5] == "import"
            ) or (len(parts) == 5 and parts[4] == "captions")
            if method == "POST" and is_srt_import:
                srt_text = body.get("srt", "")
                if not srt_text.strip():
                    return 400, {"error": {"code": "INVALID_ARGUMENT",
                                           "message": "captions import requires 'srt' text"}}
                actor = self._actor_from_body(
                    body, default_kind="http", default_id="srt-import")
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
                try:
                    output_range = (self.service._validated_export_range(
                        proj, body["range"])
                        if body.get("range") is not None else None)
                except EditError as e:
                    return 400, {"error": {"code": e.code,
                                           "message": e.message}}

                # 导出任务幂等（指导第 9 章：导出任务同样要有幂等保证）。
                # 账本只在持久化服务下可用；内存态退化为直接渲染。
                claim = None
                job_id = body.get("jobId") or f"export_{uuid.uuid4().hex[:12]}"
                store = self.service.store
                if store is not None:
                    import hashlib as _hl
                    request_key = f"{pid}|{proj.revision}|{out_abs}|{quality}"
                    if output_range is not None:
                        request_key += (f"|range:{output_range[0]:.9f}:"
                                        f"{output_range[1]:.9f}")
                    req_hash = _hl.sha256(request_key.encode("utf-8")).hexdigest()
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
                        render_options = dict(quality=quality, overwrite=True)
                        if output_range is not None:
                            start, end = output_range
                            render_options.update(output_start=start,
                                                  output_duration=end - start)
                        r = self.render.render(proj, out, **render_options)
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
                    return 200, {"jobs": self._export_queue().list(pid)}
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
                queue_payload = dict(body)
                if body.get("range") is not None:
                    try:
                        start, end = self.service._validated_export_range(
                            proj, body["range"])
                    except EditError as e:
                        return 400, {"error": {"code": e.code,
                                               "message": e.message}}
                    queue_payload["range"] = {"start": start, "end": end}
                job = self._export_queue().submit(proj, queue_payload)
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
                    subcategory=body.get("subcategory"),
                    applies_to=body.get("appliesTo"), ids=scope)
                items = []
                for s in specs:
                    d = s.to_dict(lang)
                    d["favorite"] = s.id in fav
                    d["recent"] = s.id in rec
                    items.append(d)
                return 200, {"effects": items, "count": len(items),
                             "favorites": fav, "recent": rec}

            # 对选中片段试用真实渲染路径。只修改工程深拷贝，绝不提交 revision。
            # UI 按需请求单卡预览，避免打开资源库时并发渲染整页效果。
            if method == "GET" and len(parts) == 5 and parts[4] == "resource-preview":
                from .render import RenderError
                effect_id = str(body.get("effectId", ""))
                clip_id = str(body.get("clipId", ""))
                if not effect_id or not clip_id:
                    return 400, {"error": {"code": "INVALID_ARGUMENT",
                                           "message": "effectId and clipId are required"}}
                spec = self.service._effects.find(effect_id)
                if spec is None:
                    return 404, {"error": {"code": "EFFECT_UNAVAILABLE",
                                           "message": f"unknown effect: {effect_id}"}}
                params: dict = {}
                if "duration" in body:
                    import math
                    try:
                        duration = float(body["duration"])
                    except (TypeError, ValueError):
                        duration = float("nan")
                    if spec.category != "transition" or not math.isfinite(duration) or not 0.1 <= duration <= 5:
                        return 400, {"error": {"code": "INVALID_ARGUMENT",
                                               "message": "transition preview duration must be 0.1..5 seconds"}}
                    params["duration"] = duration
                source = self.service.get_project(pid)
                preview = copy.deepcopy(source)
                target_track = next((track for track in preview.sequence.tracks
                                     if any(c.id == clip_id for c in track.clips)), None)
                clip = next((c for c in target_track.clips if c.id == clip_id), None) if target_track else None
                if clip is None:
                    return 404, {"error": {"code": "NOT_FOUND",
                                           "message": f"unknown clip: {clip_id}"}}
                if spec.category == "transition":
                    adjacent = sorted(target_track.clips,
                                      key=lambda c: c.timeline_start.to_fraction())
                    index = next(i for i, c in enumerate(adjacent) if c.id == clip_id)
                    if index == 0 or abs(float(clip.timeline_start.to_fraction() -
                                               adjacent[index - 1].timeline_end.to_fraction())) > 0.001:
                        return 422, {"error": {"code": "INVALID_TRANSITION_TARGET",
                                               "message": "转场需要前一段与当前片段首尾相接"}}
                try:
                    if spec.category == "transition":
                        self.service._h_effect_set_transition(
                            preview, {"clipId": clip_id, "effectId": effect_id,
                                      "params": params})
                    elif spec.category == "animation":
                        self.service._h_effect_set_animation(
                            preview, {"clipId": clip_id, "effectId": effect_id,
                                      "params": params})
                    else:
                        self.service._h_effect_add(preview, {"clipId": clip_id,
                                                             "effectId": effect_id,
                                                             "params": params})
                except EditError as e:
                    return 422, {"error": {"code": e.code, "message": e.message}}
                start = float(clip.timeline_start.to_fraction())
                end = float(clip.timeline_end.to_fraction())
                phase = spec.subcategory or spec.to_dict()["subcategory"]
                if spec.category == "transition":
                    t = start + min(float(params.get("duration", spec.default_params().get("duration", 0.5))) / 2,
                                    (end - start) / 2)
                elif spec.category == "animation" and phase == "出场":
                    t = end - min(0.25, (end - start) / 2)
                elif spec.category == "animation" and phase in {"入场", "组合"}:
                    t = start + min(0.25, (end - start) / 2)
                else:
                    t = start + (end - start) / 2
                import tempfile
                fd, tmp_png = tempfile.mkstemp(suffix=".png")
                os.close(fd)
                try:
                    out = self.render.extract_frame(preview, t, tmp_png,
                                                    size=(320, 180),
                                                    include_captions=False)
                    if out is None:
                        return 204, {}
                    with open(tmp_png, "rb") as f:
                        return 200, {"__png__": f.read()}
                except RenderError as e:
                    return 422, {"error": {"code": "PREVIEW_FAILED",
                                           "message": str(e)}}
                finally:
                    try:
                        os.remove(tmp_png)
                    except OSError:
                        pass

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
                    from .preview_prepare import preview_identity
                    visual_identity = preview_identity(proj, PREVIEW_RENDER_CACHE_VERSION)
                    compare_clip_id = body.get("compareClipId")
                    if compare_clip_id is not None:
                        if not isinstance(compare_clip_id, str) or not compare_clip_id:
                            raise EditError(ErrorCode.INVALID_ARGUMENT,
                                            "compareClipId must be a non-empty clip ID")
                        proj = self._without_clip_color(proj, compare_clip_id)
                    # 字幕由 Web 的可编辑 Canvas 叠层负责显示；服务端只抽取
                    # 无字幕基础画面，避免字幕修改触发重复烧录。
                    out = self._preview_frame_file(proj, t, size, visual_identity=visual_identity)
                    if out is None:
                        return 204, {}
                    with self.preview_cache.pin(out), open(out, "rb") as f:
                        data = f.read()
                    return 200, {"__png__": data}
                except RenderError as e:
                    return 422, {"error": {"code": "PREVIEW_FAILED", "message": str(e)}}
                except EditError:
                    raise
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

            if method == "GET" and len(parts) == 5 and parts[4] == "preview-window":
                try:
                    index = int(body.get("index", "0"))
                except (TypeError, ValueError):
                    raise EditError(ErrorCode.INVALID_ARGUMENT,
                                    "preview window index must be an integer") from None
                from .render import RenderError as _RE
                try:
                    proj = self.service.get_project(pid)
                    path, etag, duration = self._preview_window_file(proj, index)
                    result = {"__video_path__": path, "__video_etag__": etag,
                              "duration": duration,
                              "windowStart": index * PREVIEW_WINDOW_SECONDS}
                    if inline_media:
                        with open(path, "rb") as video:
                            result["__video__"] = video.read()
                    return 200, result
                except EditError:
                    raise
                except _RE as e:
                    return 422, {"error": {"code": "PREVIEW_FAILED", "message": str(e)}}
                except Exception as e:  # noqa: BLE001
                    return 500, {"error": {"code": "PREVIEW_WINDOW_ERROR", "message": str(e)}}

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

    @staticmethod
    def _actor_from_body(body: dict, *, default_kind: str = "http",
                         default_id: str = "client") -> Actor:
        """读取审计身份；认证不依赖此字段，畸形输入退化为端点默认身份。"""
        raw = body.get("actor")
        raw = raw if isinstance(raw, dict) else {}
        kind = raw.get("kind")
        actor_id = raw.get("id")
        return Actor(
            kind=str(kind or default_kind)[:40],
            id=str(actor_id or default_id)[:200],
        )

    def _make_command(self, pid: str, body: dict) -> Command:
        kwargs: dict[str, Any] = {
            "type": body["type"],
            "payload": body.get("payload", {}),
            "project_id": pid,
            "expected_revision": body.get("expectedRevision", ""),
            "actor": self._actor_from_body(body),
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

        def _write_body(self, data: bytes) -> None:
            # A browser may cancel an obsolete preview request while switching
            # windows. The response is already gone; this is not a server fault.
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

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
            self._write_body(payload)

        def _respond_bytes(self, status: int, data: bytes,
                           ctype: str = "application/octet-stream") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in self._cors_headers().items():
                self.send_header(k, v)
            self.end_headers()
            self._write_body(data)

        def _respond_video_file(self, path: str, etag: str = "",
                                media_type: str = "video/mp4") -> None:
            """按浏览器 Range 请求流式返回媒体，不把整片读入内存。"""
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
            self.send_header("Content-Type", media_type)
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
                self._write_body(data)
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
            for key in ("category", "subcategory", "family", "qualifiedOnly", "effectId", "clipId", "duration", "lang", "path", "w",
                        "q", "appliesTo", "favoritesOnly", "recentOnly", "rev",
                        "entityType", "entityId", "textContains", "atSeconds", "compareClipId",
                        "fromSeconds", "toSeconds", "fields", "limit", "index"):
                if key in qs:
                    body[key] = qs[key][0]
            # Like built Web files, these two public font assets need no session
            # token. CSS cannot set a custom token header; origin checks above
            # still apply, and the API's fixed-name whitelist forbids file paths.
            public_font = parsed.path in {"/api/v1/fonts/NotoSansSC-VF.ttf",
                                          "/api/v1/fonts/NotoSerifSC-VF.ttf"}
            if not public_font and self._reject_if_unauthorized(qs):
                return
            status, data = api.handle("GET", parsed.path, body,
                                      inline_media=False)
            # 预览帧返回二进制 PNG（__png__ 标记，媒体走单独数据通道）
            if isinstance(data, dict) and "__png__" in data:
                self._respond_bytes(200, data["__png__"], "image/png")
                return
            # 工程预览媒体：可播放 mp4（含音频）
            if isinstance(data, dict) and "__video_path__" in data:
                with api.preview_cache.pin(data["__video_path__"]):
                    self._respond_video_file(data["__video_path__"],
                                             data.get("__video_etag__", ""))
                return
            if isinstance(data, dict) and "__media_path__" in data:
                self._respond_video_file(data["__media_path__"],
                                         data.get("__media_etag__", ""),
                                         data.get("__media_type__", "application/octet-stream"))
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
            # Import and relink both carry raw media bytes and share body limits.
            path_parts = [unquote(p) for p in parsed.path.split("/") if p]
            if parsed.path == "/api/v1/resource-packs/install":
                length = int(self.headers.get("Content-Length", 0))
                limit = api.limiter.limits.max_body_bytes
                if length <= 0:
                    self._respond(400, {"error": {"code": "INVALID_ARGUMENT",
                                                  "message": "empty resource package"}})
                    return
                if length > limit:
                    self._respond(413, {"error": {"code": "BODY_TOO_LARGE",
                                                  "message": f"文件 {length} 字节超过上限 {limit} 字节"}})
                    return
                raw = self.rfile.read(length)
                try:
                    from .resource_pack import install_resource_pack_archive
                    result = install_resource_pack_archive(raw, api.media_dir)
                except (OSError, ValueError, zipfile.BadZipFile) as error:
                    self._respond(422, {"error": {"code": "RESOURCE_PACKAGE_INVALID",
                                                  "message": str(error)}})
                    return
                self._respond(201, result)
                return
            relink = (len(path_parts) == 5 and path_parts[:3] == ["api", "v1", "assets"]
                      and path_parts[4] == "relink")
            if parsed.path in ("/api/v1/assets", "/api/v1/luts") or relink:
                qs = parse_qs(parsed.query)
                fname = (qs.get("name") or [""])[0].strip()
                if not fname:
                    self._respond(400, {"error": {"code": "INVALID_ARGUMENT",
                                                  "message": "import requires ?name=<filename>"}})
                    return
                length = int(self.headers.get("Content-Length", 0))
                limit = (min(api.limiter.limits.max_body_bytes, MAX_CUBE_BYTES)
                         if parsed.path == "/api/v1/luts" else api.limiter.limits.max_body_bytes)
                if length <= 0:
                    self._respond(400, {"error": {"code": "INVALID_ARGUMENT",
                                                  "message": "empty body"}})
                    return
                if length > limit:
                    self._respond(413, {"error": {"code": "BODY_TOO_LARGE",
                                                  "message": f"文件 {length} 字节超过上限 {limit} 字节"}})
                    return
                raw = self.rfile.read(length)
                if parsed.path == "/api/v1/luts":
                    try:
                        self._respond(201, api.import_lut(fname, raw))
                    except CubeInvalid as error:
                        self._respond(400, {"error": {"code": "INVALID_LUT",
                                                      "message": str(error)}})
                    except OSError as error:
                        self._respond(500, {"error": {"code": "LUT_SAVE_FAILED",
                                                      "message": str(error)}})
                    return
                if relink:
                    status, result = api.relink_asset(path_parts[3], fname, raw)
                    self._respond(status, result)
                    return
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
    # Browsing a resource domain creates a burst of covers, media and API
    # connections. The older Python default backlog of five makes Linux TCP
    # retries delay unrelated API requests behind that burst.
    class LocalHTTPServer(ThreadingHTTPServer):
        request_queue_size = 128

    server = LocalHTTPServer((bound, port), handler)
    api.shutdown_callback = server.shutdown
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
    finally:
        server.server_close()
        api.shutdown_callback = None
