"""CutVoke 编辑服务：统一命令执行 + 版本校验 + 幂等 + 事件（任务书第 8/10 章）。

所有工程修改经此服务，不区分 CLI/HTTP/MCP/UI 来源。
每次成功提交提升一次 revision，产生一条 project.changed 事件。

M0 原型：in-memory 状态 + 幂等记录；M1 T08/T09 接 SQLite 权威持久化。
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import math
import os
import subprocess
import tempfile
import threading
import time
import uuid
from typing import Callable, Optional

from .model import (Project, Sequence, Track, Clip, AssetReference, Caption, CaptionWord,
                   Marker, new_id, EFFECT_TRANSFORM)
from .keyframes import Keyframe
from .protocol import Command, CommandResult, Event, Actor, ErrorCode, bump_revision
from .rational import Rational
from .speed_curve import SpeedCurve
from .invariants import validate_project
from .store import EditLeaseConflict, ExportJobMismatch, ProjectStore, RevisionConflict
from .effects import (EffectRegistry, EffectNotFound, EffectParamInvalid,
                      default_registry, animation_slot)
from .preset_catalog import PresetCatalog, PresetInvalid
from .builtin_stickers import load_builtin_stickers
from .templates import (load_templates, get_template, build_plan,
                        canvas_for_aspect)


_IMAGE_SUFFIXES = frozenset({
    ".avif", ".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp",
})
_AUDIO_SUFFIXES = frozenset({
    ".aac", ".aif", ".aiff", ".alac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".wma",
})
_VIDEO_SUFFIXES = frozenset({
    ".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm", ".wmv",
})


def _source_track_kind(source_path: str) -> Optional[str]:
    """Return the required timeline track for a known media suffix.

    Unknown/custom containers remain allowed so the renderer can probe them;
    known audio files must never silently enter a video track (or vice versa).
    """
    suffix = os.path.splitext((source_path or "").split("?", 1)[0].split("#", 1)[0])[1].lower()
    if suffix in _AUDIO_SUFFIXES:
        return "audio"
    if suffix in _IMAGE_SUFFIXES or suffix in _VIDEO_SUFFIXES:
        return "video"
    return None


def _abs_rat(r: "Rational") -> "Rational":
    """返回有理数的绝对值（Rational 是 NamedTuple，无 __neg__）。"""
    return Rational.of(abs(r.num), r.den)


def _rat_seconds(v) -> "Rational":
    """秒（浮点/整数）→ 千分位有理数。

    不用 Rational.from_float：那会把 0.6 变成 5404319552844595/9007199254740992
    这种二进制脏分母，写进工程 JSON 后既不可读也无法人工核对。
    """
    return Rational.of(int(round(float(v) * 1000)), 1000)


def _parse_caption_words(value, start: "Rational", end: "Rational") -> list[CaptionWord]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 10000:
        raise EditError(ErrorCode.INVALID_ARGUMENT,
                        "caption words must be an array of at most 10000 items")
    words: list[CaptionWord] = []
    previous_end = start
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"caption word {index + 1} requires text/start/end")
        try:
            word = CaptionWord.from_dict(item)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"caption word {index + 1} has invalid time data") from exc
        if word.start < start or word.end > end or word.start < previous_end:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"caption word {index + 1} is outside or out of order")
        words.append(word)
        previous_end = word.end
    return words


def _rebase_caption_words(words: list[CaptionWord], old_start: "Rational",
                          old_end: "Rational", new_start: "Rational",
                          new_end: "Rational") -> list[CaptionWord]:
    old_duration, new_duration = old_end - old_start, new_end - new_start
    if old_duration <= Rational.of(0) or new_duration <= Rational.of(0):
        return []
    rebased = []
    for word in words:
        start_ratio = (word.start - old_start) / old_duration
        end_ratio = (word.end - old_start) / old_duration
        rebased.append(CaptionWord(
            word.text, new_start + new_duration * start_ratio,
            new_start + new_duration * end_ratio,
        ))
    return rebased


def _apply_caption_style(cap: "Caption", p: dict) -> None:
    """把 payload 中的字幕样式字段应用到 Caption（D05 F22/F24）。

    仅当字段存在才改（caption.add 走默认值、caption.update 走缺省不变）。
    支持：fontFamily / fontSize / color / strokeColor / strokeWidth / background / align / bold。
    """
    if "fontFamily" in p:
        family = p["fontFamily"]
        if not isinstance(family, str) or family not in ("Noto Sans SC", "Noto Serif SC"):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption fontFamily must be Noto Sans SC or Noto Serif SC")
        cap.fontFamily = family
    if "fontSize" in p:
        cap.fontSize = int(p["fontSize"])
    if "color" in p:
        cap.color = str(p["color"])
    if "wordHighlightColor" in p:
        color = str(p["wordHighlightColor"]).strip()
        if color and (len(color) != 7 or color[0] != "#" or
                      any(char not in "0123456789abcdefABCDEF" for char in color[1:])):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption wordHighlightColor must be #RRGGBB or empty")
        cap.wordHighlightColor = color.upper() if color else ""
    if "strokeColor" in p:
        cap.strokeColor = str(p["strokeColor"])
    if "strokeWidth" in p:
        cap.strokeWidth = int(p["strokeWidth"])
    if "background" in p:
        cap.background = str(p["background"])
    if "align" in p:
        align = str(p["align"])
        if align not in ("left", "center", "right"):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"caption align must be left/center/right, got {align}")
        cap.align = align
    if "bold" in p:
        cap.bold = bool(p["bold"])
    if "animIn" in p:
        cap.animIn = int(p["animIn"])
    if "animOut" in p:
        cap.animOut = int(p["animOut"])
    if "animInStyle" in p:
        anim_in_style = str(p["animInStyle"])
        if anim_in_style not in ("fade", "scale", "typewriter", "none"):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"caption animInStyle must be fade/scale/typewriter/none, got {anim_in_style}")
        cap.animInStyle = anim_in_style
    if "animLoopStyle" in p:
        anim_loop_style = str(p["animLoopStyle"])
        if anim_loop_style not in ("none", "pulse", "blink"):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"caption animLoopStyle must be none/pulse/blink, got {anim_loop_style}")
        cap.animLoopStyle = anim_loop_style
    if "animLoopMs" in p:
        anim_loop_ms = p["animLoopMs"]
        if (isinstance(anim_loop_ms, bool) or not isinstance(anim_loop_ms, int)
                or not 300 <= anim_loop_ms <= 3000):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"caption animLoopMs must be an integer from 300 to 3000, got {anim_loop_ms}")
        cap.animLoopMs = anim_loop_ms
    # 文字几何（H01 画布编辑写回）：x/y 归一化 0~1、scale 0.1~5、rotation -180~180。
    # 范围由契约锁定，越界抛 INVALID_ARGUMENT（含非法值整体回滚）。
    if "x" in p:
        x = float(p["x"])
        if not (0.0 <= x <= 1.0):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"caption x must be 0~1, got {x}")
        cap.x = x
    if "y" in p:
        y = float(p["y"])
        if not (0.0 <= y <= 1.0):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"caption y must be 0~1, got {y}")
        cap.y = y
    if "scale" in p:
        scale = float(p["scale"])
        if not (0.1 <= scale <= 5.0):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"caption scale must be 0.1~5, got {scale}")
        cap.scale = scale
    if "rotation" in p:
        rotation = float(p["rotation"])
        if not (-180.0 <= rotation <= 180.0):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"caption rotation must be -180~180, got {rotation}")
        cap.rotation = rotation
    if "shadow" in p:
        shadow = int(p["shadow"])
        if not (0 <= shadow <= 20):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"caption shadow must be 0~20, got {shadow}")
        cap.shadow = shadow


def _parse_speed(val) -> "Rational":
    """从命令 payload 的 speed 字段解析为有理数。

    支持两种形式：
      - 有理数对象 {"num": "2", "den": "1"} 或 {"num": 2, "den": 1}
      - 直接数值（int / float），经 Rational 精确构造（float 仅外部输入）
    不允许 speed 为 0（时间映射无法定义）。
    """
    if isinstance(val, dict):
        if "num" in val and "den" in val:
            return Rational.from_json(
                num=str(val["num"]), den=str(val["den"]))
        if "num" in val:  # 仅分子（整数倍速）
            return Rational.of(int(val["num"]), 1)
        raise EditError(ErrorCode.INVALID_ARGUMENT,
                        f"clip.speed: malformed rational {val}")
    if isinstance(val, bool):
        raise EditError(ErrorCode.INVALID_ARGUMENT,
                        "clip.speed: boolean is not a valid speed")
    if isinstance(val, int):
        return Rational.of(val, 1)
    if isinstance(val, float):
        return Rational.from_float(val)
    raise EditError(ErrorCode.INVALID_ARGUMENT,
                    f"clip.speed: unsupported speed value {val!r}")


class EditError(Exception):
    """业务错误，携带统一错误码（映射到 protocol.ErrorCode）。"""

    def __init__(self, code: str, message: str, retryable: bool = False,
                 committed: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.committed = committed


# 非改工程命令（分析/导出封面）：不进 revision/undo，经 _execute_nonmutating 直接执行，
# 但仍在 _handlers + COMMAND_PARAM_SPECS 中登记，保证 HTTP/CLI/MCP 三方一致。
_NONMUTATING_COMMANDS = frozenset({"export.cover", "export.video",
                                    "export.still",
                                    "audio.analyze",
                                    "caption.autoSegment", "caption.transcribe",
                                    "audio.splitSentences",
                                    "caption.exportVtt",
                                    "audio.denoise", "audio.vocalEnhance",
                                    "audio.normalize",
                                    "media.stabilize", "media.enhance",
                                    "media.gif2video", "media.video2gif",
                                    "audio.record", "audio.export",
                                    # 导出队列命令：不修改工程（只动队列/账本），
                                    # 故不提 revision、不记 undo，走 _execute_nonmutating。
                                    "export.enqueue", "export.cancel", "export.jobs",
                                    # J12 模板清单：纯目录扫描，不依赖也不修改工程
                                    "template.list", "project.preflight"})

# edit.batch 只组合候选工程上的纯编辑 handler。分析、导出、历史、录音等
# 会触及工程外状态的命令不能嵌入，否则预演或失败回滚会留下副作用。
_BATCH_EDIT_COMMANDS = frozenset({
    "track.add", "track.remove", "track.update",
    "sequence.background",
    "clip.insert", "clip.attach", "clip.trim", "clip.move", "clip.split", "clip.speed",
    "clip.remove", "clip.duplicate", "clip.audio", "clip.batch",
    "clip.keyframe", "clip.group", "clip.ungroup", "clip.rippleDelete",
    "clip.closeGap", "clip.align", "clip.distribute", "asset.swap",
    "effect.add", "effect.remove", "effect.update", "effect.copyVisual", "effect.setAnimation",
    "effect.setTransition", "effect.bypass",
    "effect.reorder",
    "caption.add", "caption.bulkAdd", "caption.update", "caption.patch", "caption.remove",
    "caption.batchStyle", "caption.shift", "caption.splitByWords",
    "marker.add", "marker.remove",
})
_BATCH_MAX_COMMANDS = 32

# Dry-run must not export, cancel jobs, download models, or write processed
# media. Only these nonmutating handlers are read-only in the whole system.
_PREVIEWABLE_READONLY_COMMANDS = frozenset({"project.preflight", "template.list"})


def _split_text_units(text: str, unit: str) -> list[str]:
    """把字幕文本按单元拆分（I05 逐字/逐词/逐行）。

    char: 逐字符，空白并入前一单元（保证累计拼接与原文一致）；
    word: 按空白切词；line: 按换行切行（丢空行）。
    """
    if unit == "char":
        units: list[str] = []
        for ch in text:
            if ch.isspace() and units:
                units[-1] += ch
            elif not ch.isspace():
                units.append(ch)
        return units
    if unit == "word":
        return text.split()
    return [ln for ln in text.split("\n") if ln.strip()]


class EditService:
    def __init__(self, store: Optional["ProjectStore"] = None,
                 registry: Optional[EffectRegistry] = None) -> None:
        # project_id -> Project
        self._projects: dict[str, Project] = {}
        # project_id -> {command_id: CommandResult}  幂等记录
        self._idempotency: dict[str, dict[str, CommandResult]] = {}
        # project_id -> [Event]  事件 outbox（M1 接 SSE 重发）
        self._events: dict[str, list[Event]] = {}
        # project_id -> [snapshot, ...]  撤销历史栈（存提交前快照，8.4）
        self._undo_stack: dict[str, list[Project]] = {}
        # project_id -> [snapshot, ...]  重做栈（undo 时弹出的快照）
        self._redo_stack: dict[str, list[Project]] = {}
        self._edit_leases: dict[str, dict] = {}
        # 可选持久化存储（T08）：传入则每次提交后持久化到 SQLite
        self._store = store
        # 效果注册表（T05/T17）：effect.add 按清单校验 ID 与参数
        self._effects: EffectRegistry = registry or default_registry()
        # 导出队列单例（V02）：懒创建，见 _get_export_queue / close。
        self._export_queue: Optional["ExportQueue"] = None
        self._export_queue_lock = threading.Lock()
        self._export_command_lock = threading.Lock()
        self._export_replays: dict[tuple[str, str], tuple[str, CommandResult]] = {}
        # 源素材时长是片段可用范围的硬边界。按文件状态缓存 ffprobe 结果，
        # 避免每次拖动裁剪都重复启动子进程；文件被替换后 mtime/size 会换键。
        self._source_duration_cache: dict[
            tuple[str, int, int], Optional[Rational]
        ] = {}
        # 命令处理器（绑定到实例）
        self._handlers: dict[str, Callable] = {
            "edit.batch": self._h_edit_batch,
            "track.add": self._h_track_add,
            "track.remove": self._h_track_remove,
            "track.update": self._h_track_update,
            "sequence.add": self._h_sequence_add,
            "sequence.switch": self._h_sequence_switch,
            "sequence.background": self._h_sequence_background,
            "clip.insert": self._h_clip_insert,
            "clip.attach": self._h_clip_attach,
            "clip.trim": self._h_clip_trim,
            "clip.move": self._h_clip_move,
            "clip.split": self._h_clip_split,
            "clip.speed": self._h_clip_speed,
            "clip.remove": self._h_clip_remove,
            "clip.duplicate": self._h_clip_duplicate,
            "clip.audio": self._h_clip_audio,
            "clip.detachAudio": self._h_clip_detach_audio,
            "clip.batch": self._h_clip_batch,
            "asset.swap": self._h_asset_swap,
            "clip.keyframe": self._h_clip_keyframe,
            "clip.group": self._h_clip_group,
            "clip.ungroup": self._h_clip_ungroup,
            "effect.add": self._h_effect_add,
            "effect.remove": self._h_effect_remove,
            "effect.copyVisual": self._h_effect_copy_visual,
            "effect.setAnimation": self._h_effect_set_animation,
            "effect.setTransition": self._h_effect_set_transition,
            "effect.bypass": self._h_effect_bypass,
            "effect.reorder": self._h_effect_reorder,
            "resource.favorite": self._h_resource_favorite,
            "resource.unfavorite": self._h_resource_unfavorite,
            "preset.save": self._h_preset_save,
            "preset.delete": self._h_preset_delete,
            "preset.apply": self._h_preset_apply,
            "builtinPreset.apply": self._h_builtin_preset_apply,
            "caption.add": self._h_caption_add,
            "caption.bulkAdd": self._h_caption_bulk_add,
            "caption.update": self._h_caption_update,
            "caption.patch": self._h_caption_patch,
            "caption.remove": self._h_caption_remove,
            "caption.batchStyle": self._h_caption_batch_style,
            # K01 字幕扩展：VTT 导入导出 + 批量时间偏移
            "caption.importVtt": self._h_caption_import_vtt,
            "caption.exportVtt": self._h_caption_export_vtt,
            "caption.shift": self._h_caption_shift,
            # I05 逐字/逐词/逐行动画
            "caption.splitByWords": self._h_caption_split_by_words,
            # H01 画布文字编辑写回（部分更新效果实例参数）
            "effect.update": self._h_effect_update,
            "marker.add": self._h_marker_add,
            "marker.remove": self._h_marker_remove,
            "history.undo": self._h_undo,
            "history.redo": self._h_redo,
            # J08 多机位（multicam）：一组时间对齐、不同机位的同源轨 → 选一路输出
            "multicam.create": self._h_multicam_create,
            "multicam.switch": self._h_multicam_switch,
            "multicam.remove": self._h_multicam_remove,
            # 非改工程的分析/导出命令（HTTP/CLI/MCP 三方一致；经 _execute_nonmutating）
            "export.cover": self._h_export_cover,
            "project.preflight": self._h_project_preflight,
            "audio.analyze": self._h_audio_analyze,
            # J09 智能字幕（基础版）：静音切分占位字幕 + 话语音频片段切分
            "caption.autoSegment": self._h_caption_auto_segment,
            "caption.transcribe": self._h_caption_transcribe,
            "audio.splitSentences": self._h_audio_split_sentences,
            # J05 声音创作：纯 ffmpeg 真实音频处理（去噪 / 人声增强 / 响度标准化）
            "audio.denoise": self._h_audio_denoise,
            "audio.vocalEnhance": self._h_audio_vocal_enhance,
            "audio.normalize": self._h_audio_normalize,
            # J06 合成/画质：纯 ffmpeg 真实视频处理（稳定 / 画质增强）
            "media.stabilize": self._h_media_stabilize,
            "media.enhance": self._h_media_enhance,
            "media.gif2video": self._h_media_gif2video,
            "media.video2gif": self._h_media_video2gif,
            "audio.record": self._h_audio_record,
            "audio.export": self._h_audio_export,
            "export.video": self._h_export_video,
            "export.still": self._h_export_still,
            "clip.freeze": self._h_clip_freeze,
            # 导出队列命令（V02 架构矫正）：做成 EditService 命令，
            # CLI/HTTP/Web/MCP 四接口自动齐。队列单例挂在 EditService 上。
            "export.enqueue": self._h_export_enqueue,
            "export.cancel": self._h_export_cancel,
            "export.jobs": self._h_export_jobs,
            # B02 波纹删除 / 关闭间隙：时间线同步编辑，走正常 execute（可撤销）。
            "clip.rippleDelete": self._h_clip_ripple_delete,
            "clip.closeGap": self._h_clip_close_gap,
            "clip.align": self._h_clip_align,
            "clip.distribute": self._h_clip_distribute,
            # J12 工程模板库（声明式）：加模板 = 加 JSON，不改代码。
            "template.list": self._h_template_list,
            "template.apply": self._h_template_apply,
        }

    def _probe_source_duration(self, source_path: str) -> Optional[Rational]:
        """返回视频/音频真实时长；静态图片或无法探测时返回 None。

        None 表示不施加有限时长约束（静态图片可任意延长）。这里故意在编辑
        服务层校验，保证 Web、CLI、MCP 和外部 Agent 都不能制造源外黑帧。
        """
        if not source_path:
            return None
        ext = os.path.splitext(source_path)[1].lower()
        if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg"}:
            return None
        try:
            stat = os.stat(source_path)
        except OSError:
            return None
        key = (os.path.abspath(source_path), stat.st_size, stat.st_mtime_ns)
        if key in self._source_duration_cache:
            return self._source_duration_cache[key]
        cmd = [
            os.environ.get("CUTVOKE_FFPROBE", "ffprobe"),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            source_path,
        ]
        duration: Optional[Rational] = None
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace")
            seconds = float((result.stdout or "").strip()) if result.returncode == 0 else 0.0
            if seconds > 0:
                duration = _rat_seconds(seconds)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            duration = None
        self._source_duration_cache[key] = duration
        return duration

    def _assert_clip_within_source(self, clip: Clip) -> None:
        """拒绝读取到素材末尾之外的片段，避免预览/导出产生无意义黑屏。"""
        if clip.nested is not None or clip.freeze_at is not None:
            return
        source_duration = self._probe_source_duration(clip.asset_ref.source_path)
        if source_duration is None:
            return
        if clip.source_start < Rational.of(0, 1):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "片段源内入点不能小于 0 秒")
        consumed = clip.consumed_source_duration
        source_end = clip.source_start + consumed
        tolerance = Rational.of(1, 1000)
        if source_end > source_duration + tolerance:
            available = max(
                0.0,
                float((source_duration - clip.source_start).to_fraction()),
            )
            speed = max(0.000001, float(_abs_rat(clip.speed).to_fraction()))
            max_timeline = available / speed
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                "片段不能超过源素材末尾："
                f"当前需要 {float(source_end.to_fraction()):.3f}s，"
                f"素材只有 {float(source_duration.to_fraction()):.3f}s；"
                f"当前速度下片段最长 {max_timeline:.3f}s",
            )

    @staticmethod
    def _crop_curve_clip(clip: Clip, start: Rational, end: Rational) -> None:
        """Crop a curved clip by timeline coordinates without losing its source map."""
        curve = clip.speed_curve
        if curve is None:
            raise ValueError("expected a curved clip")
        old_start, old_end = clip.timeline_start, clip.timeline_end
        if start < old_start or end > old_end or end <= start:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "曲线变速片段只能在当前源范围内裁短")
        left = (0.0 if start == old_start else curve.source_at_timeline(
            float((start - old_start).to_fraction())))
        right = (float(curve.source_duration.to_fraction()) if end == old_end
                 else curve.source_at_timeline(float((end - old_start).to_fraction())))
        if right - left < 0.001:
            raise EditError(ErrorCode.INVALID_ARGUMENT, "曲线变速片段裁切后过短")
        clip.source_start = clip.source_start + Rational.of(round(left * 1_000_000), 1_000_000)
        clip.speed_curve = curve.slice(left, right)
        clip.timeline_start, clip.timeline_end = start, end
        clip.speed = clip.speed_curve.source_duration / clip.duration
        EditService._rebase_effect_ranges(clip, old_start, old_end, start, end)

    @staticmethod
    def _rebase_effect_ranges(clip: Clip, old_start: Rational, old_end: Rational,
                              new_start: Rational, new_end: Rational) -> None:
        """Keep clip-local effect ranges aligned when a clip is trimmed or split."""
        offset = new_start - old_start
        duration = new_end - new_start
        retained: list[dict] = []
        for effect in clip.effects or []:
            effect_range = effect.get("range")
            if not isinstance(effect_range, dict):
                retained.append(effect)
                continue
            try:
                start = Rational.from_json(**effect_range["start"]) - offset
                end = Rational.from_json(**effect_range["end"]) - offset
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                # Existing malformed data is rejected when edited or rendered;
                # clip operations should not silently rewrite its meaning.
                retained.append(effect)
                continue
            start = max(Rational.of(0), start)
            end = min(duration, end)
            if start >= end:
                continue
            effect["range"] = {"start": start.to_json(), "end": end.to_json()}
            retained.append(effect)
        clip.effects = retained

    # 命令参数清单（声明式）：HTTP / CLI / MCP 共用，避免硬编码与 _handlers 漂移。
    # 不在本清单中的命令（但存在于 _handlers）仍会进入目录，描述回退为 "(未描述)"。
    COMMAND_PARAM_SPECS: dict[str, dict] = {
        "edit.batch": {
            "description": "将 1–32 个安全的工程编辑按顺序原子执行：可 dryRun 预演，一次提交与一次撤销；任何一步失败都不落盘。不可嵌套或包含导出、分析、历史命令。",
            "params": [
                {"name": "commands", "description": "按顺序执行的数组，每项 {type, payload}；支持轨道、片段、效果、字幕、标记等纯工程编辑", "required": True},
            ],
        },
        "track.add": {
            "description": "新增一条轨道（video、audio 或 text）。",
            "params": [
                {"name": "kind", "description": "轨道类型：video、audio 或 text（默认 video）", "required": False},
                {"name": "trackId", "description": "显式轨道 ID（缺省自动生成）", "required": False},
                {"name": "role", "description": "可选视觉轨用途：sticker；底层媒体类型仍为 video", "required": False},
            ],
        },
        "track.remove": {
            "description": "删除一条空轨道（非空轨需先删片段）。",
            "params": [
                {"name": "trackId", "description": "要删除的轨道 ID", "required": True},
            ],
        },
        "track.update": {
            "description": "更新轨道属性：锁定 / 静音 / 显隐。",
            "params": [
                {"name": "trackId", "description": "轨道 ID", "required": True},
                {"name": "locked", "description": "是否锁定（缺省不变）", "required": False},
                {"name": "muted", "description": "是否静音（缺省不变）", "required": False},
                {"name": "visible", "description": "是否可见（缺省不变）", "required": False},
            ],
        },
        "sequence.add": {
            "description": "新建一个序列（1.5-D 多时间线）。",
            "params": [
                {"name": "sequenceId", "description": "显式序列 ID（缺省自动生成）", "required": False},
                {"name": "width", "description": "画布宽（缺省沿用当前序列）", "required": False},
                {"name": "height", "description": "画布高（缺省沿用当前序列）", "required": False},
                {"name": "fps", "description": "帧率（缺省沿用当前序列）", "required": False},
                {"name": "switch", "description": "是否切到新序列（默认 true）", "required": False},
            ],
        },
        "sequence.switch": {
            "description": "切换到指定序列（1.5-D 多时间线）。",
            "params": [
                {"name": "sequenceId", "description": "目标序列 ID", "required": True},
            ],
        },
        "sequence.background": {
            "description": "设置活动序列画布的底色；可透过透明蒙版显示，并填充没有视频覆盖的区域。",
            "params": [
                {"name": "color", "description": "不透明 HEX 颜色，例如 #20242A", "required": True},
            ],
        },
        "clip.insert": {
            "description": "从素材向某轨插入一个片段；可选在同一命令中原子建轨（预演与撤销均以一次编辑计）。",
            "params": [
                {"name": "trackId", "description": "目标轨道 ID", "required": True},
                {"name": "createTrackKind", "description": "可选：video、audio 或 text；目标轨不存在时原子新建轨道并插入片段，若轨道已存在则拒绝", "required": False},
                {"name": "createTrackRole", "description": "可选：sticker；随 createTrackKind=video 原子创建独立贴纸叠加轨", "required": False},
                {"name": "role", "description": "可选：sticker；标记片段为透明贴纸并附默认可编辑变换", "required": False},
                {"name": "stickerScale", "description": "可选：贴纸初始缩放；用于铺满画布的背景纹理资源", "required": False},
                {"name": "stickerAnimation", "description": "可选：{effectId,params}；在同一撤销点给贴纸附加真实可渲染动画", "required": False},
                {"name": "text", "description": "文字轨片段必填：{content,fontSize,color,...}，由 cutvoke.text 校验并渲染；与字幕对象分离", "required": False},
                {"name": "textPresetId", "description": "可选：内置文字预设 ID；使用预设样式并以 text.content 覆盖示例文字", "required": False},
                {"name": "assetId", "description": "素材引用 ID（sourcePath 缺省时需要）", "required": False},
                {"name": "sourcePath", "description": "素材绝对路径（assetId 缺省时需要）", "required": False},
                {"name": "sourceStart", "description": "源内入点 {num,den}", "required": False},
                {"name": "timelineStart", "description": "时间线入点 {num,den}", "required": False},
                {"name": "timelineEnd", "description": "时间线出点 {num,den}", "required": False},
                {"name": "clipId", "description": "显式片段 ID（缺省自动生成）", "required": False},
                {"name": "attachedToClipId", "description": "可选：明确绑定到视频片段 ID；移动视频时同步移动此片段", "required": False},
                {"name": "mode", "description": "B02 编辑语义：overwrite=覆盖该区间（重叠片段裁短/删除，跨中间则劈成两段）；insert=在该点插入（起点>=该点的片段整体右移，跨点片段被切开）；缺省为纯追加", "required": False},
            ],
        },
        "clip.trim": {
            "description": "调整片段的入/出点边界（保持源素材范围守恒）。",
            "params": [
                {"name": "clipId", "description": "片段 ID", "required": True},
                {"name": "timelineStart", "description": "新时间线入点 {num,den}", "required": False},
                {"name": "timelineEnd", "description": "新时间线出点 {num,den}", "required": False},
                {"name": "sourceStart", "description": "新源内入点 {num,den}", "required": False},
            ],
        },
        "clip.move": {
            "description": "移动片段（改时间线起点，可选跨轨）；mode=reorder 时把完整片段插入指定片段前后。",
            "params": [
                {"name": "clipId", "description": "片段 ID", "required": True},
                {"name": "timelineStart", "description": "新时间线起点 {num,den}", "required": False},
                {"name": "trackId", "description": "目标轨道 ID（缺省同轨）", "required": False},
                {"name": "mode", "description": "可选；reorder=完整片段重排，缺省维持普通移动", "required": False},
                {"name": "anchorClipId", "description": "reorder 模式的目标片段 ID", "required": False},
                {"name": "anchorPosition", "description": "reorder 模式插在锚点 before 或 after", "required": False},
                {"name": "followAttachments", "description": "缺省 true；false 仅本次移动主片段，关联片段保持原位", "required": False},
            ],
        },
        "clip.attach": {
            "description": "将片段绑定到明确的视频片段 ID；传 null 解除绑定。绑定片段在主片段移动时同步平移。",
            "params": [
                {"name": "clipId", "description": "待绑定片段 ID", "required": True},
                {"name": "attachedToClipId", "description": "视频片段 ID，null 表示解除绑定", "required": True},
            ],
        },
        "clip.split": {
            "description": "在指定时间点把一个片段切成两个。",
            "params": [
                {"name": "clipId", "description": "片段 ID", "required": True},
                {"name": "at", "description": "分割时间点 {num,den}", "required": True},
            ],
        },
        "clip.speed": {
            "description": "设置片段恒定倍率或源位置速度曲线；写入曲线会保持源范围并重算时间线时长，也可单独设置慢动作补帧模式。",
            "params": [
                {"name": "clipId", "description": "片段 ID", "required": True},
                {"name": "speed", "description": "恒定倍率（有理数或数值 2 / -1 等），与 curve 二选一；不接受字符串倍率", "required": False},
                {"name": "curve", "description": "曲线 {points:[{at:0..1,speed:0.1..8},...]}；首末点 at 为 0/1", "required": False},
                {"name": "preservePitch", "description": "变速时保持原音高；默认 true，false 时音高随速度变化", "required": False},
                {"name": "frameInterpolation", "description": "慢动作补帧模式：none 关闭，motion 使用 FFmpeg 运动估算；仅适用于含低于 1x 区间的普通视频，可单独提交", "required": False},
            ],
        },
        "clip.freeze": {
            "description": "B06 定格帧：把片段在时间线期间固定渲染为某源时刻的单帧（省略 sourceTime 清除定格）。",
            "params": [
                {"name": "clipId", "description": "片段 ID", "required": True},
                {"name": "sourceTime", "description": "源时刻（相对源起点，Rational），省略=清除定格", "required": False},
            ],
        },
        "clip.remove": {
            "description": "删除一个片段。",
            "params": [
                {"name": "clipId", "description": "片段 ID", "required": True},
            ],
        },
        "clip.duplicate": {
            "description": "复制一个片段到同轨（时序紧随原片段之后）。",
            "params": [
                {"name": "clipId", "description": "源片段 ID", "required": True},
                {"name": "newClipId", "description": "新片段 ID（缺省自动生成）", "required": False},
                {"name": "timelineStart", "description": "新片段时间线入点（缺省紧跟原片段）", "required": False},
            ],
        },
        "clip.audio": {
            "description": "设置片段音频：音量 / 淡入淡出 / 音高（D06 / 1.5-C 变声）。",
            "params": [
                {"name": "clipId", "description": "片段 ID", "required": True},
                {"name": "volume", "description": "相对音量（1.0=原声，0=静音，可 >1 增益）", "required": False},
                {"name": "fadeIn", "description": "淡入时长秒", "required": False},
                {"name": "fadeOut", "description": "淡出时长秒", "required": False},
                {"name": "pitch", "description": "音高（1.0=原调，>1 升调，<1 降调）", "required": False},
            ],
        },
        "clip.detachAudio": {
            "description": "把视频片段的原声分离到相邻音轨；保留原素材、时间映射、音频属性和视频关联，一次撤销恢复。",
            "params": [
                {"name": "clipId", "description": "含原声的视频片段 ID", "required": True},
                {"name": "audioTrackId", "description": "新音轨 ID（缺省自动生成）", "required": False},
                {"name": "audioClipId", "description": "新音频片段 ID（缺省自动生成）", "required": False},
            ],
        },
        "clip.batch": {
            "description": "批量修改同一轨道上多个片段（显式 clipIds 或整轨），支持 speed/volume/fadeIn/fadeOut/pitch/opacity/animation/transition。",
            "params": [
                {"name": "trackId", "description": "目标轨道 ID", "required": True},
                {"name": "clipIds", "description": "显式片段 ID 列表（缺省=该轨全部）", "required": False},
                {"name": "params", "description": "参数字典：speed|volume|fadeIn|fadeOut|pitch|opacity", "required": True},
            ],
        },
        "asset.swap": {
            "description": "更换片段素材（换图套版 / 批量替换素材）：{clipIds?|trackId?, sourcePath|assetId}。只换素材引用，保留时长/动画/效果/关键帧等全部属性。",
            "params": [
                {"name": "clipIds", "description": "显式片段 ID 列表", "required": False},
                {"name": "trackId", "description": "目标轨道 ID（clipIds 缺省时整轨替换）", "required": False},
                {"name": "sourcePath", "description": "新素材绝对路径", "required": False},
                {"name": "assetId", "description": "新素材资产引用 ID", "required": False},
            ],
        },
        "clip.keyframe": {
            "description": "增/删片段参数关键帧（P3 动画，D07）。",
            "params": [
                {"name": "clipId", "description": "片段 ID", "required": True},
                {"name": "action", "description": "add（默认）、batch 或 remove", "required": False},
                {"name": "param", "description": "参数名 opacity/x/y/scale/rotation；add/remove 必填，batch 在各关键帧内提供", "required": False},
                {"name": "time", "description": "关键帧时刻（片段局部时间，{num,den} 或秒数）", "required": False},
                {"name": "value", "description": "关键帧值（add 时必填）", "required": False},
                {"name": "interpolation", "description": "linear/ease-in/ease-out（默认 linear）", "required": False},
                {"name": "keyframeId", "description": "要删除的关键帧 ID（remove 时必填）", "required": False},
            ],
        },
        "clip.group": {
            "description": "把同一轨道上、时间连续（允许间隙）的一组片段打包成一个复合片段：删原片段，在组区间插入一个复合 Clip（timeline_start=组内首个 start，timeline_end=组内末个 end），nested 内建一条同类型轨道保留各片段相对位置（子序列时间从 0 起）。跨轨或非连续选区 → INVALID_ARGUMENT。",
            "params": [
                {"name": "clipIds", "description": "要打包的片段 ID 列表（须同轨、连续选区）", "required": True},
                {"name": "groupId", "description": "显式复合片段 ID（缺省自动生成）", "required": False},
                {"name": "name", "description": "复合片段名称（可选，存于 nested 序列名）", "required": False},
            ],
        },
        "clip.ungroup": {
            "description": "展开复合片段回原组：删除该复合 Clip，把 nested 内各片段按原绝对时间（组首时间 + 子序列相对时间）插回同一条轨道。该 clip 必须带 nested，否则 INVALID_ARGUMENT。",
            "params": [
                {"name": "clipId", "description": "要展开的复合片段 ID", "required": True},
            ],
        },
        "effect.add": {
            "description": "给片段添加效果/转场实例（按清单校验）。",
            "params": [
                {"name": "clipId", "description": "目标片段 ID", "required": True},
                {"name": "effectId", "description": "效果 ID", "required": True},
                {"name": "params", "description": "效果参数对象（按清单校验）", "required": False},
                {"name": "version", "description": "效果版本（缺省取注册表默认）", "required": False},
            ],
        },
        "effect.remove": {
            "description": "移除片段上的某效果实例。",
            "params": [
                {"name": "clipId", "description": "片段 ID", "required": True},
                {"name": "effectId", "description": "效果 ID", "required": True},
            ],
        },
        "effect.copyVisual": {
            "description": "把一个片段的画面效果、滤镜和调色参数原子复制到多个片段；一次撤销。",
            "params": [
                {"name": "sourceClipId", "description": "来源片段 ID", "required": True},
                {"name": "targetClipIds", "description": "目标片段 ID 列表（1–100 个）", "required": True},
                {"name": "mode", "description": "replace 替换目标全部画面效果；merge 保留其它效果但替换同 ID 效果", "required": False},
            ],
        },
        "caption.add": {
            "description": "新增一条字幕（时间走 Rational）。",
            "params": [
                {"name": "text", "description": "字幕文本", "required": True},
                {"name": "start", "description": "入点 {num,den}", "required": True},
                {"name": "end", "description": "出点 {num,den}", "required": True},
                {"name": "captionId", "description": "显式字幕 ID（缺省自动生成）", "required": False},
                {"name": "fontFamily", "description": "字体 Noto Sans SC / Noto Serif SC（默认 Noto Sans SC）", "required": False},
                {"name": "fontSize", "description": "字号（默认 32）", "required": False},
                {"name": "color", "description": "文字颜色（默认 #ffffff）", "required": False},
                {"name": "strokeColor", "description": "描边颜色（默认 #000000）", "required": False},
                {"name": "strokeWidth", "description": "描边宽度（默认 2）", "required": False},
                {"name": "background", "description": "背景色（空=无背景）", "required": False},
                {"name": "align", "description": "对齐 left/center/right（默认 center）", "required": False},
                {"name": "bold", "description": "是否加粗（默认 false）", "required": False},
                {"name": "animIn", "description": "淡入时长（毫秒，0=无）", "required": False},
                {"name": "animOut", "description": "淡出时长（毫秒，0=无）", "required": False},
                {"name": "words", "description": "可选词级时间点 [{text,start,end}]，时间为工程时间轴 {num,den}", "required": False},
                {"name": "wordHighlightColor", "description": "词级高亮色 #RRGGBB；空字符串关闭", "required": False},
            ],
        },
        "caption.bulkAdd": {
            "description": "一次写入已审核的多条字幕；一次 revision 和一次撤销，支持识别来源校验。",
            "params": [
                {"name": "segments", "description": "1–1000 条 {text,start,end,words?} 字幕；words 可带逐词工程时间", "required": True},
                {"name": "style", "description": "应用到每条字幕的样式字段（含 fontFamily: Noto Sans SC / Noto Serif SC、wordHighlightColor）", "required": False},
                {"name": "sourceClipId", "description": "识别来源片段 ID", "required": False},
                {"name": "sourceSignature", "description": "识别时返回的来源签名，防止片段已变化", "required": False},
            ],
        },
        "caption.update": {
            "description": "修改字幕（缺省字段保持不变，含样式）。",
            "params": [
                {"name": "captionId", "description": "字幕 ID", "required": True},
                {"name": "text", "description": "新文本", "required": False},
                {"name": "start", "description": "新入点 {num,den}", "required": False},
                {"name": "end", "description": "新出点 {num,den}", "required": False},
                {"name": "words", "description": "替换词级时间点 [{text,start,end}]", "required": False},
                {"name": "fontFamily", "description": "字体 Noto Sans SC / Noto Serif SC", "required": False},
                {"name": "fontSize", "description": "字号", "required": False},
                {"name": "color", "description": "文字颜色", "required": False},
                {"name": "strokeColor", "description": "描边颜色", "required": False},
                {"name": "strokeWidth", "description": "描边宽度", "required": False},
                {"name": "background", "description": "背景色", "required": False},
                {"name": "align", "description": "对齐 left/center/right", "required": False},
                {"name": "bold", "description": "加粗", "required": False},
                {"name": "animIn", "description": "淡入毫秒（0=无）", "required": False},
                {"name": "animOut", "description": "淡出毫秒（0=无）", "required": False},
                {"name": "x", "description": "画布 x 归一化 0~1（默认 0.5 居中）", "required": False},
                {"name": "y", "description": "画布 y 归一化 0~1（默认 0.5 居中）", "required": False},
                {"name": "scale", "description": "缩放 0.1~5（默认 1）", "required": False},
                {"name": "rotation", "description": "旋转 -180~180 度（默认 0）", "required": False},
                {"name": "wordHighlightColor", "description": "词级高亮色 #RRGGBB；空字符串关闭", "required": False},
            ],
        },
        "caption.remove": {
            "description": "删除一条字幕。",
            "params": [
                {"name": "captionId", "description": "字幕 ID", "required": True},
            ],
        },
        "caption.patch": {
            "description": "原子修改多条字幕的文本、时间或样式；一次提交、一次撤销，适合 Agent 精修。",
            "params": [
                {"name": "updates", "description": "更新数组；每项为 captionId 加 text?/start?/end?/样式字段", "required": True},
            ],
        },
        "caption.batchStyle": {
            "description": "批量改字幕样式（指定 captionIds / 时间段 / 全部），复用 caption.update 样式字段。",
            "params": [
                {"name": "captionIds", "description": "显式字幕 ID 列表（与时间段二选一，缺省=全部）", "required": False},
                {"name": "fromTime", "description": "时间段起点 {num,den}", "required": False},
                {"name": "toTime", "description": "时间段终点 {num,den}", "required": False},
                {"name": "style", "description": "样式：fontFamily?(Noto Sans SC / Noto Serif SC)|fontSize?|color?|strokeColor?|strokeWidth?|background?|align?|bold?|animIn?|animOut?", "required": True},
            ],
        },
        "caption.importVtt": {
            "description": "导入 WebVTT：{text} 或 {path} 二选一，解析后追加为工程字幕（唯一 captionId，共存不覆盖）。",
            "params": [
                {"name": "text", "description": "VTT 文本（与 path 二选一）", "required": False},
                {"name": "path", "description": "VTT 文件路径（与 text 二选一）", "required": False},
            ],
        },
        "caption.exportVtt": {
            "description": "导出工程字幕为 WebVTT 文本（非改工程；支持 outPath 落盘）。",
            "params": [
                {"name": "outPath", "description": "导出文件路径（缺省仅返回 vtt 文本）", "required": False},
            ],
        },
        "caption.shift": {
            "description": "批量时间偏移：{offset 秒(可负)} + 可选 {ids}（缺省全部）。偏移后 start<0 或 end<=start 直接失败（候选副本零污染回滚）。",
            "params": [
                {"name": "offset", "description": "偏移量（秒，有理数/小数均可，可负）", "required": True},
                {"name": "ids", "description": "限定的字幕 ID 列表（缺省=全部）", "required": False},
            ],
        },
        "caption.splitByWords": {
            "description": "逐字/逐词/逐行动画：把一条字幕按单元拆成多条时间错开的揭示字幕，首尾对齐原区间（时间守恒），真实落库。",
            "params": [
                {"name": "captionId", "description": "要拆分的字幕 ID", "required": True},
                {"name": "unit", "description": "拆分单元：char | word | line（默认 char）", "required": False},
                {"name": "overlap", "description": "相邻单元淡出重叠秒数（渲染语义，不改变区间；默认 0）", "required": False},
            ],
        },
        "effect.update": {
            "description": "更新片段上效果实例参数（部分合并，按注册表校验，走撤销/持久化）。",
            "params": [
                {"name": "clipId", "description": "片段 ID", "required": True},
                {"name": "effectId", "description": "效果 ID", "required": True},
                {"name": "params", "description": "要合并的参数对象（非空；新键覆盖、旧键保留）", "required": True},
            ],
        },
        "effect.setAnimation": {
            "description": "原子设置一个动画槽位（入场/出场/循环/组合），其它槽位保留；effectId 为空且不传 slot 时清空全部动画。",
            "params": [
                {"name": "clipId", "description": "片段 ID", "required": True},
                {"name": "effectId", "description": "目标 cutvoke.anim.* 动画 ID；空字符串表示清除", "required": True},
                {"name": "params", "description": "目标动画参数；缺省使用注册表默认值", "required": False},
                {"name": "slot", "description": "清除时指定入场/出场/循环/组合；缺省清空全部动画", "required": False},
            ],
        },
        "effect.setTransition": {
            "description": "原子替换片段上的转场；effectId 为空则移除转场。一次提交、一次撤销。",
            "params": [
                {"name": "clipId", "description": "后一段片段 ID", "required": True},
                {"name": "effectId", "description": "目标 cutvoke.transition.* ID；空字符串表示清除", "required": True},
                {"name": "params", "description": "目标转场参数，例如 duration（秒）", "required": False},
            ],
        },
        "marker.add": {
            "description": "新增一个定位标记（可按名定位）。",
            "params": [
                {"name": "time", "description": "标记时间 {num,den}", "required": True},
                {"name": "name", "description": "标记名称", "required": False},
                {"name": "markerId", "description": "显式标记 ID（缺省自动生成）", "required": False},
            ],
        },
        "marker.remove": {
            "description": "删除一个定位标记。",
            "params": [
                {"name": "markerId", "description": "标记 ID", "required": True},
            ],
        },
        "history.undo": {
            "description": "撤销最近一次编辑（恢复上一快照）。",
            "params": [],
        },
        "history.redo": {
            "description": "重做最近一次被撤销的编辑。",
            "params": [],
        },
        "multicam.create": {
            "description": "把若干条时间对齐、同类型（video）的轨道编为一个多机位组：{groupId?, trackIds, activeTrackId?}。单序列仅允许 0 或 1 个机位组（覆盖已存在的组 → INVALID_ARGUMENT，需先 remove）。至少需要 2 条机位轨；跨 kind 或不存在的轨 → INVALID_ARGUMENT。",
            "params": [
                {"name": "trackIds", "description": "参与机位的轨道 ID 列表（至少 2 条，type 必须一致）", "required": True},
                {"name": "activeTrackId", "description": "初始输出轨（缺省取 trackIds[0]）", "required": False},
                {"name": "groupId", "description": "显式机位组 ID（缺省自动生成）", "required": False},
            ],
        },
        "multicam.switch": {
            "description": "在多机位组内切换当前输出轨（剪辑师在机位间切换）：{trackId}。trackId 必须是组内成员，否则 INVALID_ARGUMENT。",
            "params": [
                {"name": "trackId", "description": "要切到的组内轨道 ID（成为 activeTrackId）", "required": True},
            ],
        },
        "multicam.remove": {
            "description": "删除当前序列的多机位组（清掉 sequence.multicam 元数据；轨道本身不动）。",
            "params": [],
        },
        "effect.bypass": {
            "description": "旁路/恢复片段上的效果（保留在效果栈中，只是不参与渲染）。",
            "params": [
                {"name": "clipId", "description": "片段 ID", "required": True},
                {"name": "effectId", "description": "效果 ID", "required": True},
                {"name": "enabled", "description": "true=启用，false=旁路（默认 true）", "required": False},
            ],
        },
        "effect.reorder": {
            "description": "重排片段的效果栈顺序（列表顺序即渲染合成顺序，靠前先应用）。",
            "params": [
                {"name": "clipId", "description": "片段 ID", "required": True},
                {"name": "effectId", "description": "要移动的效果 ID（与 order 二选一）", "required": False},
                {"name": "toIndex", "description": "目标位置下标（与 effectId 搭配）", "required": False},
                {"name": "order", "description": "整体顺序（effectId 数组，优先于 effectId）", "required": False},
            ],
        },
        "resource.favorite": {
            "description": "收藏一个效果或内置贴纸（工程级，随工程保存/撤销）。",
            "params": [
                {"name": "effectId", "description": "效果 ID（与 stickerId 二选一）", "required": False},
                {"name": "stickerId", "description": "内置贴纸 ID（与 effectId 二选一）", "required": False},
            ],
        },
        "resource.unfavorite": {
            "description": "取消收藏一个效果或内置贴纸（未收藏时幂等成功）。",
            "params": [
                {"name": "effectId", "description": "效果 ID（与 stickerId 二选一）", "required": False},
                {"name": "stickerId", "description": "内置贴纸 ID（与 effectId 二选一）", "required": False},
            ],
        },
        "preset.save": {
            "description": "保存个人预设：把片段当前效果栈（或显式 effects）存为一组可复用组合。",
            "params": [
                {"name": "name", "description": "预设名称", "required": True},
                {"name": "clipId", "description": "以此片段当前效果栈为内容（与 effects 二选一）", "required": False},
                {"name": "effects", "description": "显式效果数组 [{effectId, params}]", "required": False},
                {"name": "presetId", "description": "显式预设 ID（缺省自动生成）", "required": False},
            ],
        },
        "preset.delete": {
            "description": "删除个人预设。",
            "params": [
                {"name": "presetId", "description": "预设 ID", "required": True},
            ],
        },
        "preset.apply": {
            "description": "把个人预设应用到片段；merge=追加，replace=先移除同类效果再追加。",
            "params": [
                {"name": "clipId", "description": "目标片段 ID", "required": True},
                {"name": "presetId", "description": "预设 ID", "required": True},
                {"name": "mode", "description": "merge（默认）或 replace", "required": False},
            ],
        },
        "builtinPreset.apply": {
            "description": "按稳定 ID 原子应用内置预设；文字预设替换标题样式并保留内容，一次撤销还原。",
            "params": [
                {"name": "clipId", "description": "目标片段 ID", "required": True},
                {"name": "presetId", "description": "内置预设 ID（取自 GET /api/v1/presets）", "required": True},
                {"name": "duration", "description": "转场时长覆盖值（仅转场预设）", "required": False},
            ],
        },
        "export.cover": {
            "description": "抽取工程在指定时刻的一帧作为封面（PNG/JPEG），可选按 width 缩放。",
            "params": [
                {"name": "outPath", "description": "输出封面路径（扩展名决定 PNG/JPEG）", "required": True},
                {"name": "t", "description": "封面时刻（秒，默认 0）", "required": False},
                {"name": "width", "description": "缩放目标宽度（px，保持比例；缺省原尺寸）", "required": False},
            ],
        },
        "project.preflight": {
            "description": "只读检查当前序列及复合片段的素材/LUT 文件与工程结构；缺失项带片段/轨道定位，不修改工程，不替代媒体解码。",
            "params": [
                {"name": "range", "description": "选区 {start,end}（秒）；缺省检查当前序列完整输出", "required": False},
            ],
        },
        "audio.analyze": {
            "description": "分析音频文件：时长 / 采样率 / 声道数 / 响度(I) / 节拍(BPM)。",
            "params": [
                {"name": "audioPath", "description": "音频文件绝对路径", "required": True},
            ],
        },
        "caption.autoSegment": {
            "description": "J09 智能字幕（基础版）：ffmpeg 静音检测把音频切成「话语句子」并生成占位字幕（无 ASR）。可选 applyToProject 批量落工程。",
            "params": [
                {"name": "audioPath", "description": "音频/视频文件路径（脚本自行抽音频）", "required": True},
                {"name": "minSilence", "description": "静音判定时长下限（秒，默认 0.35）", "required": False},
                {"name": "maxWords", "description": "每块词数上限提示（占位，无 ASR 暂不影响切分）", "required": False},
                {"name": "style", "description": "字幕样式 {fontFamily?(Noto Sans SC / Noto Serif SC)|fontSize?|color?|strokeColor?|strokeWidth?|background?|align?|bold?|animIn?|animOut?}", "required": False},
                {"name": "applyToProject", "description": "是否直接落工程（true 时按既有 caption.add 批量写入）", "required": False},
                {"name": "projectId", "description": "applyToProject=true 时的目标工程 ID", "required": False},
            ],
        },
        "caption.transcribe": {
            "description": "用本地 faster-whisper 识别选中有声片段，返回真实文字和工程时间码；不改工程。需安装 cutvoke[asr]，首次使用下载模型。",
            "params": [
                {"name": "clipId", "description": "视频或音频片段 ID", "required": True},
                {"name": "model", "description": "tiny/base/small/medium（默认 base）", "required": False},
                {"name": "language", "description": "auto/zh/en/ja/ko（默认 auto）", "required": False},
            ],
        },
        "audio.splitSentences": {
            "description": "J09 辅助：按静音点把音频切出多个有声片段（文本剪辑素材准备，剪掉停顿）。",
            "params": [
                {"name": "audioPath", "description": "音频/视频文件路径", "required": True},
                {"name": "minSilence", "description": "静音判定时长下限（秒，默认 0.35）", "required": False},
                {"name": "outDir", "description": "输出目录（缺省用临时目录）", "required": False},
            ],
        },
        "audio.denoise": {
            "description": "J05 去噪：ffmpeg anlmdn 非局部均值降噪（真实可渲染，不引入外部模型）。",
            "params": [
                {"name": "audioPath", "description": "音频文件绝对路径", "required": True},
                {"name": "outPath", "description": "输出路径（缺省同目录加 _denoised 后缀）", "required": False},
                {"name": "strength", "description": "降噪强度 0.01~0.5（默认 0.1）", "required": False},
            ],
        },
        "audio.vocalEnhance": {
            "description": "J05 人声增强/去伴奏：立体声中置(人声)或侧声道(去人声)近似抽取，按 amount 与原音混合。",
            "params": [
                {"name": "audioPath", "description": "音频文件绝对路径", "required": True},
                {"name": "outPath", "description": "输出路径（缺省同目录加 _vocal/_accompaniment 后缀）", "required": False},
                {"name": "amount", "description": "处理音混合比例 0~1（默认 0.5）", "required": False},
                {"name": "mode", "description": "vocal=人声增强 / accompaniment=去人声留伴奏（默认 vocal）", "required": False},
            ],
        },
        "audio.normalize": {
            "description": "J05 响度标准化：ffmpeg loudnorm 双遍（测量+带measured参数应用）输出到文件。",
            "params": [
                {"name": "audioPath", "description": "音频文件绝对路径", "required": True},
                {"name": "outPath", "description": "输出路径（缺省同目录加 _normalized 后缀）", "required": False},
                {"name": "I", "description": "目标集成响度 LUFS（默认 -16）", "required": False},
            ],
        },
        "media.stabilize": {
            "description": "J06 视频稳定（防抖）：vidstab 两遍（detect+transform）或 deshake 单遍，输出到文件。",
            "params": [
                {"name": "videoPath", "description": "视频文件绝对路径", "required": True},
                {"name": "outPath", "description": "输出路径（缺省同目录加 _stabilized 后缀）", "required": False},
                {"name": "method", "description": "vidstab（两遍，默认）/ deshake（单遍轻量）", "required": False},
            ],
        },
        "media.enhance": {
            "description": "J06 画质增强：unsharp 锐化 + hqdn3d 降噪组合，输出到文件。",
            "params": [
                {"name": "videoPath", "description": "视频文件绝对路径", "required": True},
                {"name": "outPath", "description": "输出路径（缺省同目录加 _enhanced 后缀）", "required": False},
                {"name": "sharpness", "description": "锐化强度 0~3（默认 1.0；0 关闭）", "required": False},
                {"name": "denoise", "description": "降噪强度 0~30（默认 0；0 关闭）", "required": False},
            ],
        },
        "media.video2gif": {
            "description": "V03 动图输出：视频 → gif（palettegen/paletteuse），文件级输出。",
            "params": [
                {"name": "videoPath", "description": "视频文件绝对路径", "required": True},
                {"name": "outPath", "description": "输出 gif 路径（缺省同名 .gif）", "required": False},
                {"name": "fps", "description": "gif 帧率（默认 10）", "required": False},
            ],
        },
        "media.gif2video": {
            "description": "A03 动图转可剪辑视频：gif → mp4（循环铺满），文件级输出。",
            "params": [
                {"name": "gifPath", "description": "gif 文件绝对路径", "required": True},
                {"name": "outPath", "description": "输出 mp4 路径（缺省同目录同名 .mp4）", "required": False},
            ],
        },
        "audio.record": {
            "description": "J05 录音：ffmpeg dshow 从麦克风采集到 wav（Windows）；非 Windows 用内置音源替代。",
            "params": [
                {"name": "outPath", "description": "输出 wav 绝对路径", "required": True},
                {"name": "seconds", "description": "录制时长秒（0.5~300，默认 5）", "required": False},
                {"name": "device", "description": "麦克风设备名（缺省系统默认）", "required": False},
            ],
        },
        "audio.export": {
            "description": "V04 独立声音输出：把工程混音后的音轨导出为 m4a/aac（不渲染画面）。",
            "params": [
                {"name": "outPath", "description": "输出音频绝对路径（.m4a）", "required": True},
                {"name": "overwrite", "description": "覆盖已存在文件（默认 False）", "required": False},
            ],
        },
        "export.video": {
            "description": "V01 视频导出（带预设/码率）：按 quality 画质档或目标码率导出 MP4。",
            "params": [
                {"name": "outPath", "description": "输出 MP4 绝对路径", "required": True},
                {"name": "quality", "description": "画质 high/medium/low（默认 high）", "required": False},
                {"name": "videoBitrateKbps", "description": "目标视频码率 kbps（缺省用 crf 画质档）", "required": False},
                {"name": "audioBitrateKbps", "description": "目标音频码率 kbps（默认 192）", "required": False},
                {"name": "overwrite", "description": "覆盖已存在文件（默认 False）", "required": False},
                {"name": "transparent", "description": "V05 透明通道：true 时导出带 alpha 的 ProRes 4444（outPath 必须是 .mov）", "required": False},
                {"name": "range", "description": "V01 区间导出：{start, end}（秒，十进制字符串或数字），从完整时间线成片中截取 [start,end)，保留字幕、转场、变速、混音等原时间线语义；缺省导出全片。越界/start>=end/负数 → 拒绝", "required": False},
                {"name": "colorSpace", "description": "O06 色彩管理：目标输出色彩空间 bt709/bt601/bt2020（缺省不转换）。输出流的 color_primaries 会被校验，转换未生效即报错", "required": False},
                {"name": "sourceColorSpace", "description": "O06：声明输入素材色彩空间 bt709/bt601/bt2020（默认 bt709）。素材未标定色彩属性时必须显式声明，否则 zimg 找不到转换路径", "required": False},
                {"name": "toneMap", "description": "O06 HDR→SDR 色调映射算法 none/linear/gamma/clip/reinhard/hable/mobius（默认 none）。启用时源按 BT.2020 + PQ 解释，输出锁定 BT.709", "required": False},
                {"name": "sourceTransfer", "description": "O06：HDR 源的传输函数 pq/HLG（默认 pq），仅 toneMap 生效时使用", "required": False},
            ],
        },
        "export.enqueue": {
            "description": "V02 异步导出入队（非阻塞）：提交一次后台导出任务，立即返回 jobId。串行消费、可取消、versioned 永不覆盖。不修改工程。",
            "params": [
                {"name": "outPath", "description": "输出 MP4 绝对路径（必填）", "required": True},
                {"name": "quality", "description": "画质 high/medium/low（默认 high）", "required": False},
                {"name": "versioned", "description": "多版本：true 时永不覆盖已有成片，依次 name.mp4→name_v2.mp4（默认 False）", "required": False},
                {"name": "transparent", "description": "透明通道：true 时导出带 alpha 的 ProRes 4444（outPath 须 .mov）", "required": False},
                {"name": "videoBitrateKbps", "description": "目标视频码率 kbps", "required": False},
                {"name": "audioBitrateKbps", "description": "目标音频码率 kbps", "required": False},
                {"name": "overwrite", "description": "覆盖已存在文件（默认 False）", "required": False},
                {"name": "range", "description": "区间导出：{start, end}（秒），从完整时间线成片截取 [start,end)，保留原字幕、转场、变速和混音语义；缺省导出全片", "required": False},
            ],
        },
        "export.cancel": {
            "description": "V02 取消导出任务：queued 直接出队、running 交给渲染看门狗终止。已完成任务如实回报 cancelled=false（含 already <status>，不假装成功）；任务不在本进程队列（如重启遗留）→ 抛 NOT_CANCELLABLE。不修改工程。",
            "params": [
                {"name": "jobId", "description": "要取消的任务 ID（必填）", "required": True},
            ],
        },
        "export.jobs": {
            "description": "V02 导出任务账本视图（非阻塞只读）：以账本为准列出导出任务。返回 {type:export_jobs, jobs}。不修改工程。",
            "params": [
                {"name": "projectId", "description": "按工程过滤（缺省全部）", "required": False},
                {"name": "status", "description": "按状态过滤 queued|running|succeeded|failed|cancelled|interrupted", "required": False},
                {"name": "limit", "description": "返回条数上限（默认 200）", "required": False},
            ],
        },
        "template.list": {
            "description": "J12 工程模板清单（非阻塞只读）：列出内置工程模板与各自的素材位；被跳过的坏模板一并回报。不修改工程。",
            "params": [
                {"name": "category", "description": "按分类过滤（如 相册/口播/展示/短视频/卡点/片头片尾）", "required": False},
            ],
        },
        "template.apply": {
            "description": "J12 套用工程模板：按模板建好轨道/画面/转场/字幕/背景音乐，未提供素材的槽位用内置背景占位，套完即可直接导出成片。",
            "params": [
                {"name": "templateId", "description": "模板 ID（取自 template.list）", "required": True},
                {"name": "sources", "description": "素材位映射 {slotKey: assetId 或 绝对路径}；未给的槽位用内置背景占位", "required": False},
                {"name": "clearExisting", "description": "true 先清空现有轨道与字幕再套用；false（默认）追加到现有内容之后", "required": False},
                {"name": "setCanvas", "description": "是否按模板画幅调整画布尺寸（默认 true；如 9:16 模板会切成 1080x1920）", "required": False},
            ],
        },
        "export.still": {
            "description": "V03 静帧/封面导出：把时间线某时刻导出为图片（png/jpg）；transparent=true 时导出带 alpha 的透明 PNG。",
            "params": [
                {"name": "outPath", "description": "输出图片绝对路径（如 .png）", "required": True},
                {"name": "timelineTime", "description": "时间线时刻（秒，默认 0）", "required": False},
                {"name": "transparent", "description": "导出带 alpha 的透明图片（默认 False）", "required": False},
            ],
        },
        "clip.rippleDelete": {
            "description": "B02 波纹删除：删除片段并把同轨上起点在其之后的片段整体左移（左移量=被删片段时长），消除删除造成的空洞。",
            "params": [
                {"name": "clipId", "description": "要删除的片段 ID", "required": True},
            ],
        },
        "clip.closeGap": {
            "description": "B02 关闭间隙：合拢全轨、指定片段之后的全部空隙，或 beforeClipId 前的单个空隙。",
            "params": [
                {"name": "trackId", "description": "目标轨道 ID", "required": True},
                {"name": "afterClipId", "description": "只关闭该片段之后的空隙（缺省关闭全轨）", "required": False},
                {"name": "beforeClipId", "description": "只关闭该片段前的一处空隙；后续片段同量左移，保留其它空隙。不能与 afterClipId 同用", "required": False},
            ],
        },
        "clip.align": {
            "description": "N01 画面对齐：把片段在画面上对齐到画布或另一个片段。只改 transform.position（显示区域对齐），不改时间线时间。",
            "params": [
                {"name": "clipIds", "description": "参与对齐的片段 ID 数组（>=1）", "required": True},
                {"name": "align", "description": "对齐方向：left|right|top|bottom|hcenter|vcenter", "required": True},
                {"name": "target", "description": "参照物：canvas（默认，对齐到工程画布）| first（对齐到 clipIds[0]）", "required": False},
            ],
        },
        "clip.distribute": {
            "description": "N01 画面分布：让片段在指定轴上等间距分布（首尾不动、中间按中心均分）。只改 transform.position。",
            "params": [
                {"name": "clipIds", "description": "参与分布的片段 ID 数组（>=2）", "required": True},
                {"name": "axis", "description": "分布轴：x（默认）| y", "required": False},
            ],
        },
    }

    def command_catalog(self, lang: str = "zh-CN") -> list[dict]:
        """命令目录：自动派生自 self._handlers（单一事实来源，杜绝硬编码漂移）。

        返回按命令名排序的列表，每项：
            {"type", "description", "params": [{"name","description","required"}]}。
        出现在 _handlers 但不在 COMMAND_PARAM_SPECS 的命令，描述回退为 "(未描述)"。
        """
        from .command_schema import payload_schema
        try:
            text_schema = self._effects.get("cutvoke.text").to_dict()["parameters"]
        except EffectNotFound:
            text_schema = {"type": "object", "properties": {"content": {"type": "string"}}}
        catalog: list[dict] = []
        for cmd in sorted(self._handlers.keys()):
            spec = self.COMMAND_PARAM_SPECS.get(cmd)
            if spec is None:
                catalog.append({"type": cmd, "description": "(未描述)", "params": []})
            else:
                entry = {
                    "type": cmd,
                    "description": spec.get("description", "(未描述)"),
                    "params": [dict(p) for p in spec.get("params", [])],
                    "previewSupported": (cmd not in _NONMUTATING_COMMANDS
                                         or cmd in _PREVIEWABLE_READONLY_COMMANDS),
                }
                if cmd == "edit.batch":
                    entry["allowedCommands"] = sorted(_BATCH_EDIT_COMMANDS)
                    entry["maxCommands"] = _BATCH_MAX_COMMANDS
                entry["inputSchema"] = payload_schema(cmd, entry["params"], {"cutvoke.text": text_schema})
                catalog.append(entry)
        return catalog

    def _persist(self, project: Project) -> None:
        """若配置了 store，则在每次成功提交后持久化工程快照。"""
        if self._store is not None:
            self._store.save(project)

    @property
    def store(self) -> Optional[ProjectStore]:
        """持久层访问器（只读）。导出任务账本等旁路数据由调用方经此读写，
        避免把 store 内部结构泄漏给上层。内存态服务返回 None。"""
        return self._store

    # ------------------------------------------------------------------
    # 导出队列单例（V02 架构矫正）
    #
    # 队列原本挂在 HttpApi 上（接口层），导致 MCP 等接口触达不到。现改为
    # 挂在 EditService：四接口共用同一份命令，自动齐。懒创建——首次入队/
    # 取消/查询时才起后台线程，避免大量短命实例/测试各拉一个线程。
    # 全进程只此一份实例：两份队列各一个工作线程会重复消费、并发渲染。
    # ------------------------------------------------------------------
    def _get_export_queue(self, render_factory: Optional[Callable] = None):
        """取（或惰性创建）本 EditService 唯一的导出队列。

        render_factory：仅首次创建时生效（已存在则忽略）。HTTP 接口传
        `lambda: self.render` 以复用其 RenderService（便于测试注入假渲染器）；
        命令路径不传则用默认 RenderService。
        """
        with self._export_queue_lock:
            if self._export_queue is None:
                from .export_queue import ExportQueue
                from .render import RenderService
                self._export_queue = ExportQueue(
                    store=self._store, service=self,
                    render_factory=render_factory or RenderService)
            return self._export_queue

    def close(self) -> None:
        """停掉导出队列的后台线程（测试收尾 / 服务退出时必须调用）。

        幂等：多次调用安全。
        """
        with self._export_queue_lock:
            q, self._export_queue = self._export_queue, None
        if q is not None:
            q.shutdown()

    # ---- 工程生命周期 ----
    def create_project(self, project_id: str, name_hint: str = "",
                       width: int = 1920, height: int = 1080,
                       fps: Rational = Rational.of(30, 1)) -> Project:
        if project_id in self._projects:
            raise EditError(ErrorCode.INVALID_ARGUMENT, f"project exists: {project_id}")
        seq = Sequence(id=f"{project_id}_seq", width=width, height=height, fps=fps)
        proj = Project(schema_version="1", project_id=project_id,
                       revision="0", sequence=seq, name=name_hint)
        # 先持久（事务），成功才进内存（A02：创建失败不伪成功）
        if self._store is not None:
            if self._store.exists(project_id):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"project exists in store: {project_id}")
            try:
                self._store.commit(proj)  # 原子创建
            except RevisionConflict as e:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"project already created elsewhere: {e}",
                                retryable=True) from e
        self._projects[project_id] = proj
        self._idempotency[project_id] = {}
        self._events[project_id] = []
        self._undo_stack[project_id] = []
        self._redo_stack[project_id] = []
        return proj

    def import_project(self, project: Project,
                       *, project_id: Optional[str] = None) -> Project:
        """导入一个已存在的工程对象（如从资源包解包出来的）到服务。

        与 create_project（从零建空工程）不同：这里直接接纳传入的 Project，
        替换其 project_id 为新的唯一 id（避免与现有工程冲突），然后注册到内存
        并（若有持久层）原子落盘。素材路径由调用方（projectpack.unpack_project）
        已重写为解包目录下的绝对路径，这里不再触碰。

        返回注册后的 Project（其 project_id 为服务分配的新值）。
        """
        new_id = project_id or f"p_{uuid.uuid4().hex[:8]}"
        if new_id in self._projects or (self._store is not None and self._store.exists(new_id)):
            raise EditError(ErrorCode.INVALID_ARGUMENT, f"project exists: {new_id}")
        project.project_id = new_id
        if self._store is not None:
            try:
                # No valid revision equals -1. A concurrent creator must never
                # be overwritten by an import/clone with the same requested ID.
                self._store.commit(project, expected_prev_revision="-1")
            except RevisionConflict as error:
                raise EditError(ErrorCode.INVALID_ARGUMENT, f"project exists: {new_id}", retryable=True) from error
        self._projects[new_id] = project
        self._idempotency.setdefault(new_id, {})
        self._events.setdefault(new_id, [])
        self._undo_stack.setdefault(new_id, [])
        self._redo_stack.setdefault(new_id, [])
        return project

    def load_project(self, project_id: str) -> Project:
        """从 store 恢复工程到内存（要求构造时传入了 store）。"""
        if self._store is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "EditService created without store; cannot load")
        loaded = self._store.load(project_id)
        if loaded is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT, f"project not found: {project_id}")
        self._projects[project_id] = loaded
        self._idempotency.setdefault(project_id, {})
        self._events.setdefault(project_id, [])
        self._undo_stack.setdefault(project_id, [])
        self._redo_stack.setdefault(project_id, [])
        return loaded

    def rename_project(self, project_id: str, name: str,
                       *, edit_lease_id: str = "") -> bool:
        """重命名工程：仅改 name，不动 revision / 事件 / 历史。

        内存与持久层同步更新；工程不存在返回 False。
        """
        proj = self._projects.get(project_id)
        if proj is None:
            if self._store is not None and self._store.exists(project_id):
                proj = self.load_project(project_id)
            else:
                return False
        self.assert_editable(project_id, edit_lease_id)
        proj.name = name
        if self._store is not None:
            self._store.rename_project(project_id, name)
        return True

    def get_project(self, project_id: str) -> Project:
        # SQLite 是跨 Web/CLI/MCP 进程的权威快照。不能只返回启动时加载的
        # _projects，否则外部 Agent 提交后长期运行的 Web 会一直看到旧工程。
        if self._store is not None:
            loaded = self._store.load(project_id)
            if loaded is None:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"project not found: {project_id}")
            self._projects[project_id] = loaded
            self._idempotency.setdefault(project_id, {})
            self._events.setdefault(project_id, [])
            self._undo_stack.setdefault(project_id, [])
            self._redo_stack.setdefault(project_id, [])
            return loaded
        if project_id not in self._projects:
            raise EditError(ErrorCode.INVALID_ARGUMENT, f"project not found: {project_id}")
        return self._projects[project_id]

    def project_summary(self, project_id: str) -> dict:
        """Return a stable, low-token timeline index for an external Agent.

        The summary deliberately omits caption text and effect parameters. They
        are fetched on demand from the full project only after an Agent has
        identified the clip or caption it wants to inspect or change.
        """
        proj = self.get_project(project_id)
        sequence = proj.sequence
        project_end = Rational.of(0, 1)
        tracks: list[dict] = []
        for track in sequence.tracks:
            clips: list[dict] = []
            for clip in sorted(track.clips, key=lambda item: item.timeline_start):
                project_end = max(project_end, clip.timeline_end)
                source = str(clip.asset_ref.source_path or "").replace("\\", "/")
                clips.append({
                    "clipId": clip.id,
                    "assetId": clip.asset_ref.asset_id,
                    "sourceName": source.rsplit("/", 1)[-1],
                    "timelineStart": clip.timeline_start.to_json(),
                    "timelineEnd": clip.timeline_end.to_json(),
                    "sourceStart": clip.source_start.to_json(),
                    "hidden": clip.hidden,
                    "effectIds": [
                        str(effect.get("effectId", ""))
                        for effect in clip.effects
                        if effect.get("effectId")
                    ],
                })
            tracks.append({
                "trackId": track.id,
                "kind": track.kind,
                "locked": track.locked,
                "muted": track.muted,
                "visible": track.visible,
                "clipCount": len(clips),
                "clips": clips,
            })
        return {
            "projectId": proj.project_id,
            "name": proj.name,
            "revision": proj.revision,
            "canvas": {
                "width": sequence.width,
                "height": sequence.height,
                "fps": sequence.fps.to_json(),
            },
            "duration": project_end.to_json(),
            "trackCount": len(tracks),
            "captionCount": len(sequence.captions),
            "editLease": self.get_edit_lease(project_id),
            "tracks": tracks,
        }

    def project_lookup(self, project_id: str, *, entity_type: str,
                       entity_id: str = "", text_contains: str = "",
                       at_seconds: Optional[float] = None,
                       from_seconds: Optional[float] = None,
                       to_seconds: Optional[float] = None,
                       fields: Optional[list[str]] = None,
                       limit: int = 10) -> dict:
        """Read only the captions or clips an Agent needs for a local edit."""
        if entity_type not in ("caption", "clip"):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "entityType must be 'caption' or 'clip'")
        if not isinstance(entity_id, str) or not isinstance(text_contains, str):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "entityId and textContains must be strings")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            raise EditError(ErrorCode.INVALID_ARGUMENT, "limit must be 1..50")
        defaults = ({"id", "text", "start", "end"} if entity_type == "caption"
                    else {"id", "trackId", "sourceName", "timelineStart", "timelineEnd"})
        available = ({"id", "text", "start", "end", "fontFamily", "fontSize", "color",
                      "strokeColor", "strokeWidth", "background", "align", "bold",
                      "animIn", "animOut", "x", "y", "scale", "rotation", "shadow"}
                     if entity_type == "caption" else
                     {"id", "trackId", "sourceName", "assetRef", "timelineStart",
                      "timelineEnd", "sourceStart", "speed", "speedCurve", "frameInterpolation", "preservePitch", "effects", "linked",
                      "attachedToClipId",
                      "hidden", "volume", "fadeIn", "fadeOut", "pitch", "freezeAt",
                      "nested", "keyframes"})
        if fields is not None and (not isinstance(fields, list) or
                                   any(not isinstance(f, str) or f not in available
                                       for f in fields)):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "fields contains an unknown field for this entityType")
        selected = set(fields) if fields is not None else defaults
        selected.add("id")
        if entity_type == "clip":
            selected.add("trackId")
        try:
            at = Rational.from_float(float(at_seconds)) if at_seconds is not None else None
            lower = Rational.from_float(float(from_seconds)) if from_seconds is not None else None
            upper = Rational.from_float(float(to_seconds)) if to_seconds is not None else None
        except (TypeError, ValueError, OverflowError):
            raise EditError(ErrorCode.INVALID_ARGUMENT, "time filters must be finite seconds") from None
        if lower is not None and upper is not None and lower >= upper:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "toSeconds must be greater than fromSeconds")

        project = self.get_project(project_id)
        found: list[dict] = []
        needle = text_contains.casefold()
        if entity_type == "caption":
            candidates = ((c, c.to_dict()) for c in project.sequence.captions)
        else:
            candidates = (
                (c, {**c.to_dict(), "trackId": track.id,
                     "sourceName": c.asset_ref.source_path.replace("\\", "/").rsplit("/", 1)[-1]})
                for track in project.sequence.tracks for c in track.clips
            )
        for entity, record in candidates:
            if entity_id and entity.id != entity_id:
                continue
            searchable = entity.text if entity_type == "caption" else record["sourceName"]
            if needle and needle not in searchable.casefold():
                continue
            start = entity.start if entity_type == "caption" else entity.timeline_start
            end = entity.end if entity_type == "caption" else entity.timeline_end
            if at is not None and not start <= at < end:
                continue
            if lower is not None and end <= lower:
                continue
            if upper is not None and start >= upper:
                continue
            found.append({key: record[key] for key in selected})
            if len(found) > limit:
                break
        return {"projectId": project.project_id, "revision": project.revision,
                "entityType": entity_type, "items": found[:limit],
                "hasMore": len(found) > limit}

    # ------------------------------------------------------------------
    # Agent 编辑租约：协调“可见但只读”，智能决策仍在外部 Agent。
    # ------------------------------------------------------------------

    @staticmethod
    def _lease_ttl(ttl_seconds: float) -> float:
        try:
            value = float(ttl_seconds)
        except (TypeError, ValueError):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "ttlSeconds must be a number")
        if value <= 0:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "ttlSeconds must be positive")
        return max(5.0, min(value, 300.0))

    def get_edit_lease(self, project_id: str) -> Optional[dict]:
        self.get_project(project_id)
        if self._store is not None:
            return self._store.get_edit_lease(project_id)
        lease = self._edit_leases.get(project_id)
        if lease is not None and float(lease["expiresAt"]) <= time.time():
            self._edit_leases.pop(project_id, None)
            return None
        return dict(lease) if lease is not None else None

    def acquire_edit_lease(self, project_id: str, *, owner: str = "agent",
                           lease_id: str = "", ttl_seconds: float = 30.0) -> dict:
        self.get_project(project_id)
        ttl = self._lease_ttl(ttl_seconds)
        lease_id = lease_id or f"lease_{uuid.uuid4().hex[:16]}"
        owner = str(owner or "agent")[:200]
        try:
            if self._store is not None:
                return self._store.acquire_edit_lease(
                    project_id=project_id, lease_id=lease_id,
                    owner=owner, ttl_seconds=ttl)
            current = self.get_edit_lease(project_id)
            if current is not None and current["leaseId"] != lease_id:
                raise EditLeaseConflict(
                    f"project {project_id} is locked by {current['owner']}")
            now = time.time()
            lease = {
                "projectId": project_id, "leaseId": lease_id, "owner": owner,
                "expiresAt": now + ttl,
                "acquiredAt": current["acquiredAt"] if current else now,
            }
            self._edit_leases[project_id] = lease
            return dict(lease)
        except EditLeaseConflict as e:
            raise EditError(ErrorCode.EDIT_LOCKED, str(e), retryable=True) from e

    def renew_edit_lease(self, project_id: str, lease_id: str,
                         ttl_seconds: float = 30.0) -> dict:
        self.get_project(project_id)
        ttl = self._lease_ttl(ttl_seconds)
        if self._store is not None:
            lease = self._store.renew_edit_lease(
                project_id=project_id, lease_id=lease_id, ttl_seconds=ttl)
        else:
            current = self.get_edit_lease(project_id)
            if current is None or current["leaseId"] != lease_id:
                lease = None
            else:
                current["expiresAt"] = time.time() + ttl
                self._edit_leases[project_id] = current
                lease = dict(current)
        if lease is None:
            raise EditError(ErrorCode.EDIT_LOCKED,
                            "edit lease is missing or expired", retryable=True)
        return lease

    def release_edit_lease(self, project_id: str, lease_id: str) -> bool:
        self.get_project(project_id)
        if self._store is not None:
            return self._store.release_edit_lease(
                project_id=project_id, lease_id=lease_id)
        current = self._edit_leases.get(project_id)
        if current is None or current["leaseId"] != lease_id:
            return False
        self._edit_leases.pop(project_id, None)
        return True

    def assert_editable(self, project_id: str, lease_id: str = "") -> None:
        lease = self.get_edit_lease(project_id)
        if lease is not None and lease["leaseId"] != lease_id:
            raise EditError(
                ErrorCode.EDIT_LOCKED,
                f"project is being edited by {lease['owner']}",
                retryable=True,
            )

    def get_project_revision(self, project_id: str) -> str:
        """返回工程当前 revision（纯读，不校验，供客户端比对版本漂移）。"""
        return self.get_project(project_id).revision

    def export_srt(self, project_id: str) -> str:
        """把工程字幕导出为 SRT 文本（F23，纯读，不改工程）。"""
        from .captions import export_srt
        proj = self.get_project(project_id)
        return export_srt(proj.sequence.captions)

    def import_srt(self, project_id: str, srt_text: str,
                   actor: Optional[Actor] = None,
                   *, edit_lease_id: str = "") -> int:
        """解析 SRT 文本并追加为工程字幕（F23），返回导入条数。

        走完整提交链（原子 + 版本 + 撤销 + 持久化），与单命令一致：
        - 候选副本上追加，校验通过才提交（失败零污染）
        - revision 提升一次（多条字幕 = 一次批量编辑，一次撤销可全回退）
        - 保留已有字幕，不覆盖；每条新字幕生成唯一 captionId
        """
        from .captions import parse_srt
        proj = self.get_project(project_id)
        pid = project_id
        self.assert_editable(pid, edit_lease_id)
        previous = proj.revision

        imported = parse_srt(srt_text)
        if not imported:
            return 0

        candidate = copy.deepcopy(proj)
        existing_ids = {c.id for c in candidate.sequence.captions}
        for cap in imported:
            while cap.id in existing_ids:
                cap.id = new_id("cap")
            existing_ids.add(cap.id)
            candidate.sequence.captions.append(cap)

        validation = validate_project(candidate)
        if not validation.structurally_valid:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "SRT import violates project invariant: "
                            + "; ".join(validation.errors))

        new_rev = bump_revision(previous)
        candidate.revision = new_rev
        event = Event(event_id=f"event_srt_{uuid.uuid4().hex[:8]}",
                      project_id=pid, previous_revision=previous,
                      revision=new_rev, command_id="", transaction_id=f"tx_srt_{new_rev}",
                      actor=actor or Actor("internal", "srt-import"),
                      changed_entities=[{"type": "caption", "change": "imported",
                                         "count": len(imported)}])

        history_push = {"depth": self._undo_depth(pid),
                        "snapshot": proj.to_dict(), "revision": previous}
        if self._store is not None:
            try:
                self._store.commit(candidate, expected_prev_revision=previous,
                                   event_payload=event.to_dict(),
                                   history_push=history_push,
                                   history_clear_redo=True)
            except RevisionConflict as e:
                raise EditError(ErrorCode.REVISION_CONFLICT, str(e),
                                retryable=True) from e
            self._projects[pid] = self._store.load(pid) or candidate
        else:
            self._undo_stack[pid].append(copy.deepcopy(proj))
            self._redo_stack[pid].clear()
            self._projects[pid] = candidate
            # 内存态没有 SQLite outbox；必须像 execute() 一样保留事件，
            # 否则 Web 活动面板与轮询无法观察 SRT 批量导入。
            self._events[pid].append(event)
        return len(imported)

    def get_project_at(self, project_id: str, revision: str) -> Project:
        """按请求的 revision 读取一致快照（任务书 12.1/12.2/AC11）。

        M0 内存态只保留当前版本，无历史快照：
          - 请求 revision == 当前 → 返回当前工程
          - 请求 revision != 当前 → 返回 REVISION_CONFLICT（旧请求不能静默拿最新）
        M1 T08 接 SQLite 后，可保留历史快照，届时旧 revision 若仍在保留窗口内
        可返回对应快照；超出窗口返回 REVISION_UNAVAILABLE。
        """
        proj = self.get_project(project_id)
        if revision == proj.revision:
            return proj
        raise EditError(
            ErrorCode.REVISION_CONFLICT,
            f"requested revision {revision}, current {proj.revision}",
            retryable=True,
        )

    @staticmethod
    def _assert_locked_track_clips_unchanged(before: Project, after: Project) -> None:
        """Prevent every command path from changing clips on a locked track.

        Track mute/visibility and explicit lock/unlock remain available; this guard
        protects the clips themselves and prevents removal of a locked track.
        """
        after_tracks = {track.id: track for track in after.sequence.tracks}
        for track in before.sequence.tracks:
            if not track.locked:
                continue
            updated = after_tracks.get(track.id)
            if updated is None:
                raise EditError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"track {track.id} is locked; unlock it before removing or editing its clips",
                )
            before_clips = {clip.id: clip.to_dict() for clip in track.clips}
            after_clips = {clip.id: clip.to_dict() for clip in updated.clips}
            if before_clips != after_clips:
                raise EditError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"track {track.id} is locked; unlock it before editing its clips",
                )

    def preview_command(self, command: Command) -> dict:
        """试算一个编辑命令（P4 AI 接续）：在候选副本上执行 handler 并校验，
        但**不提交、不持久化、不提 revision**，返回「会发生什么」。

        对片段和字幕影响附上时间线修改前后的区间，方便 Agent 在提交前
        检查编辑范围。返回 {"changedEntities": [...], "valid": true}；失败与
        execute 同语义抛 EditError（版本冲突 / 参数非法 / 不变量违反），
        且内存状态零变化。
        """
        proj = self.get_project(command.project_id)
        if command.expected_revision != proj.revision:
            raise EditError(
                ErrorCode.REVISION_CONFLICT,
                f"expected revision {command.expected_revision}, current {proj.revision}",
                retryable=True,
            )
        handler = self._handlers.get(command.type)
        if handler is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"unknown command type: {command.type}")
        if (command.type in _NONMUTATING_COMMANDS
                and command.type not in _PREVIEWABLE_READONLY_COMMANDS):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"{command.type} cannot be dry-run without external side effects; "
                            "use command_apply to run this operation explicitly")
        candidate = copy.deepcopy(proj)
        try:
            changed = handler(candidate, command.payload)
        except EditError:
            raise
        except Exception as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"{command.type} failed: {e}") from e
        self._assert_locked_track_clips_unchanged(proj, candidate)
        validation = validate_project(candidate)
        if not validation.structurally_valid:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                "project invariant violation: "
                + "; ".join(validation.errors))

        def timeline_range(project: Project, entity_type: str, entity_id: str) -> Optional[dict]:
            if entity_type == "clip":
                for track in project.sequence.tracks:
                    clip = next((item for item in track.clips if item.id == entity_id), None)
                    if clip is not None:
                        return {"start": clip.timeline_start.to_json(),
                                "end": clip.timeline_end.to_json()}
            elif entity_type == "caption":
                caption = next((item for item in project.sequence.captions
                                if item.id == entity_id), None)
                if caption is not None:
                    return {"start": caption.start.to_json(), "end": caption.end.to_json()}
            return None

        impact: list[dict] = []
        for entity in changed:
            enriched = dict(entity)
            if entity.get("type") in ("clip", "caption"):
                before = timeline_range(proj, entity["type"], entity["id"])
                after = timeline_range(candidate, entity["type"], entity["id"])
                if before is not None or after is not None:
                    enriched["timelineRange"] = {"before": before, "after": after}
            impact.append(enriched)
        return {"valid": True, "changedEntities": impact,
                "commandType": command.type}

    def machine_schema(self, lang: str = "zh-CN") -> dict:
        """机器可读 schema（P4 SDK）：命令目录 + 效果的 JSON Schema 形态。

        供类型化 SDK / 客户端生成代码使用。返回：
          {"schemaVersion", "commands": [...], "effects": [...]}
        commands 每项含 type + params（name/description/required）及 payload inputSchema。
        effects 每项含 effectId + parameters 的 JSON Schema。
        """
        commands = self.command_catalog(lang)
        effects = []
        for spec in self._effects.all():
            d = spec.to_dict(lang)
            effects.append({
                "effectId": d.get("effectId"),
                "category": d.get("category"),
                "name": d.get("name"),
                "parameters": d.get("parameters"),
                "defaults": d.get("defaults"),
            })
        return {"schemaVersion": 1, "commands": commands, "effects": effects}

    # ------------------------------------------------------------------
    # 非改工程命令：导出封面 / 音频分析（J09/J10 真实工具）
    # ------------------------------------------------------------------
    def export_cover(self, project_id: str, out_path: str, *,
                     t: float = 0.0, width: Optional[int] = None) -> dict:
        """抽取工程在时刻 t 的一帧作为封面（PNG/JPEG），可选按 width 等比缩放。

        复用既有 render.extract_frame 抽帧；若给定 width 再用 ffmpeg scale 缩放。
        返回 {outPath, width, height}。空时间线 / 该时刻无覆盖画面抛 INVALID_ARGUMENT。
        """
        from .render import RenderService

        proj = self.get_project(project_id)
        ts = 0.0 if t is None else float(t)
        out_abs = os.path.abspath(out_path)
        out_dir = os.path.dirname(out_abs) or "."
        os.makedirs(out_dir, exist_ok=True)

        render = RenderService()
        fd, tmp_png = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            frame = render.extract_frame(proj, ts, tmp_png, size=None, timeout=60)
            if frame is None:
                raise EditError(
                    ErrorCode.INVALID_ARGUMENT,
                    "export.cover: 该时刻无覆盖画面（空时间线或未覆盖）")
            cmd: list[str] = [render.ffmpeg, "-y", "-i", tmp_png]
            vf: list[str] = []
            if width is not None:
                w = int(width)
                if w <= 0:
                    raise EditError(ErrorCode.INVALID_ARGUMENT,
                                    "export.cover: width must be positive")
                vf.append(f"scale={w}:-2")
            if vf:
                cmd += ["-vf", ",".join(vf)]
            cmd.append(out_abs)
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired as e:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"export.cover timed out: {e}") from e
            if r.returncode != 0 or not os.path.isfile(out_abs):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"export.cover failed: {r.stderr[-400:]}")
            info = render.probe_media(out_abs)
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp_png)
        return {"outPath": out_abs, "width": info["width"], "height": info["height"]}

    def audio_analyze(self, audio_path: str) -> dict:
        """分析音频文件：时长 / 采样率 / 声道数 / 响度(I) / 节拍(BPM)。

        响度优先用 ffmpeg ebur128，失败回退到 RMS 近似；BPM 用纯 Python 能量包络
        峰检测（见 core/audio_analysis.py）。返回分析字典。
        """
        from . import audio_analysis

        if not os.path.isfile(audio_path):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"audio.analyze: file not found: {audio_path}")
        try:
            return audio_analysis.analyze_audio(audio_path)
        except EditError:
            raise
        except Exception as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"audio.analyze failed: {e}") from e

    def _execute_nonmutating(self, command: Command) -> CommandResult:
        """执行非改工程命令（export.cover / audio.analyze）。

        不查工程版本、不提 revision、不记 undo；仅运行 handler 并返回结果。
        audio.analyze 不依赖工程（handler 的 proj 参数传 None）。
        """
        if command.type == "export.enqueue":
            return self._execute_export_enqueue(command)
        handler = self._handlers.get(command.type)
        if handler is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"unknown command type: {command.type}")
        try:
            if command.type in ("audio.analyze", "caption.autoSegment",
                                "audio.splitSentences", "audio.denoise",
                                "audio.vocalEnhance", "audio.normalize",
                                # J12：模板清单只读目录，不需要工程
                                "template.list"):
                # 分析/切分/音频处理本身不依赖工程
                payload = handler(None, command.payload)
            else:  # export.cover 需要工程
                proj = self.get_project(command.project_id)
                payload = handler(proj, command.payload)
        except EditError:
            raise
        except Exception as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"{command.type} failed: {e}") from e
        return CommandResult(
            command_id=command.command_id,
            previous_revision="",
            revision="",
            transaction_id="",
            changed_entities=payload,
        )

    def _execute_export_enqueue(self, command: Command) -> CommandResult:
        """Freeze the requested revision and persist the job and retry receipt together."""
        def receipt(raw):
            result = CommandResult.from_dict(json.loads(raw))
            # Older installed versions persisted an empty revision for exports.
            # Upgrade their response from the job's frozen revision, never from
            # the live project which may have been edited since submission.
            if not result.revision and self._store is not None:
                job_id = next((item.get("jobId") for item in result.changed_entities if item.get("jobId")), None)
                job = self._store.get_export_job(job_id) if job_id else None
                if job is not None:
                    result.previous_revision = result.revision = job["revision"]
            return result

        with self._export_command_lock:
            key = (command.project_id, command.command_id)
            request_hash = self._request_hash(command)
            existing = (self._store.get_idempotent(*key)
                        if self._store is not None and command.command_id else None)
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise EditError(ErrorCode.IDEMPOTENCY_MISMATCH, "commandId already used with different payload")
                return receipt(existing["result_json"])
            if command.command_id and key in self._export_replays:
                old_hash, old_result = self._export_replays[key]
                if old_hash != request_hash:
                    raise EditError(ErrorCode.IDEMPOTENCY_MISMATCH, "commandId already used with different payload")
                return old_result
            project = copy.deepcopy(self.get_project(command.project_id))
            if command.expected_revision != project.revision:
                raise EditError(ErrorCode.REVISION_CONFLICT,
                                f"expected revision {command.expected_revision}, current {project.revision}", retryable=True)
            try:
                changed = self._h_export_enqueue(project, command.payload, command=command)
            except RevisionConflict as exc:
                raise EditError(ErrorCode.REVISION_CONFLICT, str(exc), retryable=True) from exc
            except ExportJobMismatch as exc:
                raise EditError(ErrorCode.IDEMPOTENCY_MISMATCH, str(exc)) from exc
            if self._store is not None and command.command_id:
                return receipt(self._store.get_idempotent(*key)["result_json"])
            result = CommandResult(command_id=command.command_id, previous_revision=project.revision, revision=project.revision,
                                   transaction_id="", changed_entities=changed)
            if command.command_id:
                self._export_replays[key] = (request_hash, result)
            return result

    # ---- 命令执行（统一入口） ----
    def execute(self, command: Command) -> CommandResult:
        """执行一个编辑命令，结果**持久化成功后才**可见（WP-01）。

        提交链（G0 可信基线）：
          1. 持久幂等优先：同 commandId 若在 store 已有记录——
             相同 request_hash → 复用原结果；不同 → IDEMPOTENCY_MISMATCH。
          2. 版本校验：expectedRevision 必须匹配**内存**当前版本。
          3. 在候选副本上执行 handler；任何异常/校验失败都抛给调用方，
             内存与存储均零变化（A03）。
          4. 一次性事务提交 store：工程快照 + 幂等记录 + outbox 事件 +
             撤销历史（A02/A10；跨进程冲突由 store 比对 DB revision 拒绝）。
          5. 提交成功后才更新内存权威状态并返回成功（A01：失败不伪成功）。
        """
        # 非改工程命令（分析/导出封面）走独立路径：不校验版本、不提 revision、不记 undo。
        if command.type in _NONMUTATING_COMMANDS:
            return self._execute_nonmutating(command)

        proj = self.get_project(command.project_id)
        pid = command.project_id

        # ---- 1) 持久幂等优先（跨进程/重启复用，A10）----
        if command.command_id:
            if self._store is not None:
                existing = self._store.get_idempotent(pid, command.command_id)
                if existing is not None:
                    req_hash = self._request_hash(command)
                    if existing["request_hash"] == req_hash:
                        # 复用原结果（即便重启后亦可）
                        return CommandResult.from_dict(
                            json.loads(existing["result_json"]))
                    raise EditError(
                        ErrorCode.IDEMPOTENCY_MISMATCH,
                        f"command {command.command_id} already committed with "
                        f"different payload")
            else:
                idem = self._idempotency[pid]
                if command.command_id in idem:
                    return idem[command.command_id]

        # Agent 持有租约时，页面仍可读取/预览，但所有不带该租约的写命令
        # 都必须被拒绝，避免人工操作与 Agent 编辑互相覆盖。
        self.assert_editable(pid, command.edit_lease_id)

        # ---- 2) 版本校验 ----
        if command.expected_revision != proj.revision:
            raise EditError(
                ErrorCode.REVISION_CONFLICT,
                f"expected revision {command.expected_revision}, current {proj.revision}",
                retryable=True,
            )

        previous = proj.revision
        handler = self._handlers.get(command.type)
        if handler is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"unknown command type: {command.type}")

        # ---- 3) 候选副本执行（A03：异常/校验失败零污染）----
        candidate = copy.deepcopy(proj)
        try:
            changed = handler(candidate, command.payload)
        except EditError:
            raise
        except Exception as e:  # handler 内部畸形输入（如分母 0）→ 不污染状态
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"{command.type} failed: {e}") from e

        self._assert_locked_track_clips_unchanged(proj, candidate)
        validation = validate_project(candidate)
        if not validation.structurally_valid:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                f"project invariant violation: {'; '.join(validation.errors)}",
            )

        new_rev = bump_revision(previous)
        candidate.revision = new_rev
        tx_id = f"tx_{command.command_id}"

        result = CommandResult(
            command_id=command.command_id,
            previous_revision=previous,
            revision=new_rev,
            transaction_id=tx_id,
            changed_entities=changed,
        )

        # 事件（同事务持久化）
        event = Event(
            event_id=f"event_{command.command_id}",
            project_id=pid,
            previous_revision=previous,
            revision=new_rev,
            command_id=command.command_id,
            transaction_id=tx_id,
            actor=command.actor,
            changed_entities=changed,
        )

        # 撤销历史：普通编辑 push 提交前快照；undo/redo 特殊（候选已是目标态）
        history_push = None
        clear_redo = False
        history_pop_depth = None
        if command.type == "history.undo" and self._store is not None:
            # _h_undo 只读取快照；真正删除放到 commit 同一事务中。
            history_pop_depth = self._store.history_depth(pid)
        if command.type == "history.redo" and self._store is not None:
            # Redo must put the state being left back on the persistent undo
            # stack, so a second undo returns to the same state instead of
            # consuming an older edit's snapshot.
            history_push = {
                "depth": self._undo_depth(pid),
                "snapshot": proj.to_dict(),
                "revision": previous,
            }
        elif command.type not in ("history.undo", "history.redo"):
            depth = self._undo_depth(pid)
            history_push = {
                "depth": depth,
                "snapshot": proj.to_dict(),   # 提交前状态
                "revision": previous,
            }
            clear_redo = True

        # ---- 4) 持久层原子提交（A02：失败则全部回滚，内存不变）----
        if self._store is not None:
            try:
                self._store.commit(
                    candidate,
                    expected_prev_revision=previous,
                    idem_entry=(command.command_id, self._request_hash(command),
                                json.dumps(result.to_dict(), ensure_ascii=False)),
                    event_payload=event.to_dict(),
                    history_push=history_push,
                    history_clear_redo=clear_redo,
                    history_pop_depth=history_pop_depth,
                )
            except RevisionConflict as e:
                # 另一进程已推进数据库：转协议错误码
                raise EditError(ErrorCode.REVISION_CONFLICT, str(e), retryable=True) from e
            # 持久成功后，刷新数据库权威到内存（含候选工程）
            self._projects[pid] = self._store.load(pid) or candidate
            # undo/redo 的内存栈也必须在持久提交成功后才改变；否则冲突
            # 会出现“工程未变，但历史栈已被消费”的半提交状态。
            if command.type == "history.undo":
                self._redo_stack[pid].append(copy.deepcopy(proj))
            elif command.type == "history.redo":
                if self._redo_stack[pid]:
                    self._redo_stack[pid].pop()
            elif clear_redo:
                self._redo_stack[pid].clear()
        else:
            # 内存态也只在候选工程通过校验后变更历史栈，避免失败命令
            # 消费 undo/redo 快照；redo 后重新补回刚才离开的 undo 状态。
            if command.type == "history.undo":
                self._undo_stack[pid].pop()
                self._redo_stack[pid].append(copy.deepcopy(proj))
            elif command.type == "history.redo":
                if self._redo_stack[pid]:
                    self._redo_stack[pid].pop()
                self._undo_stack[pid].append(copy.deepcopy(proj))
            else:
                self._undo_stack[pid].append(copy.deepcopy(proj))
                self._redo_stack[pid].clear()

        # ---- 5) 成功确认后，更新内存可见状态 ----
        if self._store is None:
            self._projects[pid] = candidate
        self._idempotency[pid][command.command_id] = result
        self._events[pid].append(event)

        return result

    def _request_hash(self, command: Command) -> str:
        """命令请求摘要：type + payload 确定（不含 revision，因重试沿用旧 rev）。"""
        return hashlib.sha256(
            json.dumps({"type": command.type, "payload": command.payload},
                       sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    def _undo_depth(self, project_id: str) -> int:
        """当前撤销栈深度（持久化历史用）。"""
        if self._store is not None:
            return self._store.history_depth(project_id) + 1
        return len(self._undo_stack[project_id])

    # ---- 命令实现（每类返回 changed_entities） ----

    def _h_edit_batch(self, proj: Project, p: dict) -> list[dict]:
        """在 execute/preview_command 的同一个候选副本上顺序应用编辑。"""
        operations = p.get("commands")
        if not isinstance(operations, list) or not 1 <= len(operations) <= _BATCH_MAX_COMMANDS:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"edit.batch requires 1 to {_BATCH_MAX_COMMANDS} commands")
        # 先验证整个列表，避免执行到一半才发现不支持的命令。
        for index, operation in enumerate(operations):
            if not isinstance(operation, dict):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"edit.batch command {index} must be an object")
            command_type = operation.get("type")
            if not isinstance(command_type, str) or command_type not in _BATCH_EDIT_COMMANDS \
                    or command_type not in self._handlers:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"edit.batch command {index} is not a safe project edit: {command_type!r}")
            if not isinstance(operation.get("payload"), dict):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"edit.batch command {index} requires a payload object")

        changed: list[dict] = []
        for index, operation in enumerate(operations):
            command_type = operation["type"]
            try:
                entries = self._handlers[command_type](proj, operation["payload"])
            except EditError as error:
                raise EditError(error.code,
                                f"edit.batch command {index} ({command_type}): {error.message}",
                                retryable=error.retryable) from error
            except Exception as error:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"edit.batch command {index} ({command_type}) failed: {error}") from error
            changed.extend({**entry, "batchIndex": index, "commandType": command_type}
                           for entry in entries)
        return changed

    def _h_track_add(self, proj: Project, p: dict) -> list[dict]:
        kind = p.get("kind", "video")
        role = p.get("role", "")
        if role not in ("", "sticker") or (role == "sticker" and kind != "video"):
            raise EditError(ErrorCode.INVALID_ARGUMENT, "sticker role requires a video track")
        track_id = p.get("trackId") or f"track_{len(proj.sequence.tracks)}"
        if any(t.id == track_id for t in proj.sequence.tracks):
            raise EditError(ErrorCode.INVALID_ARGUMENT, f"track exists: {track_id}")
        proj.sequence.tracks.append(Track(id=track_id, kind=kind, role=role))
        return [{"type": "track", "id": track_id, "change": "created"}]

    def _h_track_remove(self, proj: Project, p: dict) -> list[dict]:
        """删除轨道。

        安全语义：仅允许删除**空轨**（无任何片段），非空轨道删除会连带删除
        其上所有片段——这是有损操作，界面必须二次确认。这里保守实现：
        空轨直接删；非空轨抛 INVALID_ARGUMENT（提示先删片段）。
        """
        track_id = p["trackId"]
        for idx, track in enumerate(proj.sequence.tracks):
            if track.id == track_id:
                if track.clips:
                    raise EditError(
                        ErrorCode.INVALID_ARGUMENT,
                        f"track {track_id} is not empty "
                        f"({len(track.clips)} clips); remove clips first")
                proj.sequence.tracks.pop(idx)
                return [{"type": "track", "id": track_id, "change": "deleted"}]
        raise EditError(ErrorCode.INVALID_ARGUMENT, f"track not found: {track_id}")

    def _h_track_update(self, proj: Project, p: dict) -> list[dict]:
        """更新轨道属性：{trackId, locked?, muted?, visible?}，缺省字段不变。"""
        track_id = p["trackId"]
        track = next((t for t in proj.sequence.tracks if t.id == track_id), None)
        if track is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT, f"track not found: {track_id}")
        changes = []
        if "locked" in p:
            track.locked = bool(p["locked"])
            changes.append("locked")
        if "muted" in p:
            track.muted = bool(p["muted"])
            changes.append("muted")
        if "visible" in p:
            track.visible = bool(p["visible"])
            changes.append("visible")
        if not changes:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "track.update requires at least one of locked/muted/visible")
        return [{"type": "track", "id": track_id, "change": "updated",
                 "fields": changes}]

    def _h_sequence_add(self, proj: Project, p: dict) -> list[dict]:
        """新建序列（1.5-D 多时间线）。缺省沿用当前序列的画布/帧率。"""
        cur = proj.active_sequence
        seq_id = p.get("sequenceId") or new_id("seq")
        if any(s.id == seq_id for s in proj.sequences):
            raise EditError(ErrorCode.INVALID_ARGUMENT, f"sequence exists: {seq_id}")
        width = p.get("width", cur.width)
        height = p.get("height", cur.height)
        fps = (Rational.from_json(**p["fps"]) if isinstance(p.get("fps"), dict)
               else Rational.of(int(p.get("fps", 30)), 1)) if "fps" in p else cur.fps
        new_seq = Sequence(id=seq_id, width=width, height=height, fps=fps,
                           background_color=cur.background_color)
        proj.sequences.append(new_seq)
        switch = p.get("switch", True)
        if switch:
            proj.active_sequence_id = seq_id
            proj.sequence = new_seq
        return [{"type": "sequence", "id": seq_id, "change": "created"}]

    def _h_sequence_background(self, proj: Project, p: dict) -> list[dict]:
        """设置活动序列的画布底色，颜色只接受不透明 #RRGGBB。"""
        color = p.get("color")
        valid = (isinstance(color, str) and len(color) == 7 and color[0] == "#"
                 and all(char in "0123456789abcdefABCDEF" for char in color[1:]))
        if not valid:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "sequence.background color must be a #RRGGBB value")
        seq = proj.active_sequence
        seq.background_color = color.upper()
        return [{"type": "sequence", "id": seq.id, "change": "background_updated",
                 "fields": ["backgroundColor"]}]

    def _h_sequence_switch(self, proj: Project, p: dict) -> list[dict]:
        """切换活动序列（1.5-D 多时间线）。"""
        sid = p["sequenceId"]
        for s in proj.sequences:
            if s.id == sid:
                proj.active_sequence_id = sid
                proj.sequence = s
                return [{"type": "sequence", "id": sid, "change": "activated"}]
        raise EditError(ErrorCode.INVALID_ARGUMENT, f"sequence not found: {sid}")

    # ---- J08 多机位（multicam）：一组时间对齐、不同机位的同源轨 → 选一路输出 ----
    def _h_multicam_create(self, proj: Project, p: dict) -> list[dict]:
        """把若干条同类型（video）、时间对齐的轨道编为一个多机位组。

        约束：
          - 单序列仅允许 0 或 1 个机位组（已有组必须先 remove）。
          - trackIds 至少 2 条，否则「需要至少两条机位轨」。
          - 每条轨道必须存在；全部必须同 kind（多机位=同一时刻不同画面）。
          - 覆盖已有机位组 → INVALID_ARGUMENT（需先 remove）。
        activeTrackId 缺省取 trackIds[0]。返回组信息。
        """
        seq = proj.sequence
        if seq.multicam is not None:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                "multicam group already exists; remove it first (multicam.remove)")

        track_ids = p.get("trackIds") or []
        if not isinstance(track_ids, list) or len(track_ids) < 2:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                "需要至少两条机位轨（trackIds 至少 2 条）")

        tracks_by_id = {t.id: t for t in seq.tracks}
        for tid in track_ids:
            if tid not in tracks_by_id:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"track not found: {tid}")

        # 全部必须同 kind（多机位=同一时刻不同画面；这里不限定死 video，
        # 但一组内必须一致，跨 kind 拒绝）
        kinds = {tracks_by_id[tid].kind for tid in track_ids}
        if len(kinds) != 1:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                f"multicam tracks must share one kind, got {sorted(kinds)}")

        active = p.get("activeTrackId") or track_ids[0]
        if active not in tracks_by_id:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"activeTrackId not found: {active}")

        group_id = p.get("groupId") or new_id("mc")
        seq.multicam = {
            "groupId": group_id,
            "trackIds": list(track_ids),
            "activeTrackId": active,
            "sync": "time",
        }
        return [{"type": "multicam", "id": group_id, "change": "created",
                 "activeTrackId": active, "trackIds": list(track_ids)}]

    def _h_multicam_switch(self, proj: Project, p: dict) -> list[dict]:
        """在多机位组内切换当前输出轨：把 activeTrackId 设为 payload.trackId。

        trackId 必须是组内成员，否则 INVALID_ARGUMENT。
        """
        seq = proj.sequence
        if seq.multicam is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "no multicam group exists (multicam.create first)")
        track_id = p["trackId"]
        if track_id not in seq.multicam["trackIds"]:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                f"track {track_id} is not a member of the multicam group")
        # 在候选副本上以新 dict 更新，避免就地改到共享引用
        seq.multicam = dict(seq.multicam)
        seq.multicam["activeTrackId"] = track_id
        return [{"type": "multicam", "id": seq.multicam["groupId"],
                 "change": "switched", "activeTrackId": track_id}]

    def _h_multicam_remove(self, proj: Project, p: dict) -> list[dict]:
        """删除当前序列的多机位组（清掉 sequence.multicam 元数据；轨道本身不动）。"""
        seq = proj.sequence
        if seq.multicam is None:
            return [{"type": "multicam", "change": "noop"}]
        group_id = seq.multicam.get("groupId")
        seq.multicam = None
        return [{"type": "multicam", "id": group_id, "change": "removed"}]

    def _h_clip_insert(self, proj: Project, p: dict) -> list[dict]:
        """插入片段。

        payload 新增 `mode`（B02，2026-09-21）：
          - 缺省/None：纯追加（旧行为，不做覆盖也不做插入位移）
          - "overwrite"：新片段占据 [start,end)，与该区间**重叠**的既有片段被裁短
            或删除；被新片段**从中间劈开**的既有片段拆成左右两段（保留两侧内容，
            源内入点按时间线偏移同步）。
          - "insert"：在 start 处插入，该轨上**起点 >= start** 的片段整体右移新片段
            时长；若 start 落在某片段内部，该片段在 start 处被切开，后半段随之右移。

        约束：不允许产生负时间/负时长，也不允许同轨重叠（invariants 会拒绝）。
        """
        before_positions = {c.id: c.timeline_start
                            for lane in proj.sequence.tracks for c in lane.clips}
        track_id = p["trackId"]
        track = next((t for t in proj.sequence.tracks if t.id == track_id), None)
        create_kind = p.get("createTrackKind")
        create_role = p.get("createTrackRole", "")
        creating_track = create_kind is not None
        if creating_track:
            if create_kind not in ("video", "audio", "text"):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "createTrackKind must be 'video', 'audio' or 'text'")
            if create_role not in ("", "sticker") or (create_role == "sticker" and create_kind != "video"):
                raise EditError(ErrorCode.INVALID_ARGUMENT, "sticker role requires a video track")
            if track is not None:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"track exists: {track_id}")
            track = Track(id=track_id, kind=create_kind, role=create_role)
        elif track is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT, f"track not found: {track_id}")
        elif create_role:
            raise EditError(ErrorCode.INVALID_ARGUMENT, "createTrackRole requires createTrackKind")
        clip_id = p.get("clipId")
        if not clip_id:
            suffix = len(track.clips)
            used_ids = {clip.id for lane in proj.sequence.tracks for clip in lane.clips}
            while f"clip_{suffix}" in used_ids:
                suffix += 1
            clip_id = f"clip_{suffix}"
        asset = p.get("assetId", "")
        source_path = p.get("sourcePath", "")
        if asset and not source_path and track.kind != "text" and self._store is not None:
            if not isinstance(asset, str):
                raise EditError(ErrorCode.INVALID_ARGUMENT, "assetId must be a string")
            registered = self._store.get_asset(asset)
            if registered is None:
                raise EditError(ErrorCode.INVALID_ARGUMENT, f"asset not found: {asset}")
            source_path = registered["path"]
        clip_role = p.get("role", "") or track.role
        if clip_role not in ("", "sticker"):
            raise EditError(ErrorCode.INVALID_ARGUMENT, f"unsupported clip role: {clip_role}")
        if clip_role == "sticker":
            if track.kind != "video" or track.role != "sticker":
                raise EditError(ErrorCode.INVALID_ARGUMENT, "stickers require a sticker overlay track")
            if not str(source_path).lower().endswith((".png", ".webp", ".gif")):
                raise EditError(ErrorCode.INVALID_ARGUMENT, "sticker source must be an image")
        resource_ref = p.get("resourceRef")
        if resource_ref is not None:
            required = ("resourceId", "packId", "packVersion", "resourceVersion", "sha256")
            if (clip_role != "sticker" or not isinstance(resource_ref, dict) or
                    any(not isinstance(resource_ref.get(key), str) or not resource_ref[key]
                        for key in required) or
                    len(resource_ref.get("sha256", "")) != 64 or
                    any(char not in "0123456789abcdef" for char in resource_ref["sha256"].lower())):
                raise EditError(ErrorCode.INVALID_ARGUMENT, "invalid sticker resource reference")
            resource_ref = {key: resource_ref[key] for key in required}
        required_track = _source_track_kind(source_path)
        if required_track is not None and track.kind in ("video", "audio") \
                and required_track != track.kind:
            label = "audio" if required_track == "audio" else "visual"
            article = "an" if required_track == "audio" else "a"
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                f"{label} source cannot be inserted into {track.kind} track; "
                f"use {article} {required_track} track")
        source_start = Rational.from_json(**p["sourceStart"]) if "sourceStart" in p else Rational.of(0, 1)
        start = Rational.from_json(**p["timelineStart"]) if "timelineStart" in p else Rational.of(0, 1)
        end = Rational.from_json(**p["timelineEnd"]) if "timelineEnd" in p else start + Rational.of(1, 1)

        mode = p.get("mode")
        if mode is not None:
            mode = str(mode).strip().lower()
        if mode not in (None, "", "overwrite", "insert"):
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                f"mode must be 'overwrite' or 'insert', got {mode!r}")
        if mode == "":
            mode = None
        if end <= start:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "clip.insert requires timelineEnd > timelineStart")

        clip = Clip(id=clip_id, asset_ref=AssetReference(
                        asset, source_path=source_path,
                        fingerprint=resource_ref["sha256"] if resource_ref else "",
                        resource_ref=resource_ref),
                    timeline_start=start, timeline_end=end,
                    source_start=source_start, role=clip_role,
                    attached_to_clip_id=p.get("attachedToClipId"))
        if track.kind == "text":
            if asset or source_path or clip_role or source_start != Rational.of(0, 1):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "text clips use text parameters, not source media")
            text_input = p.get("text")
            if not isinstance(text_input, dict) or not isinstance(text_input.get("content"), str) \
                    or not text_input["content"].strip():
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "text clip requires non-empty text.content")
            text_preset = None
            if "textPresetId" in p:
                text_preset = self._require_builtin_preset(p["textPresetId"])
                if text_preset.family != "text":
                    raise EditError(ErrorCode.INVALID_ARGUMENT,
                                    "textPresetId requires a text preset")
                text_input = {**text_preset.params, **text_input}
            try:
                text_params = self._effects.validate_params("cutvoke.text", text_input)
            except Exception as error:
                raise EditError(ErrorCode.INVALID_ARGUMENT, f"invalid text style: {error}") from error
            text_effect = {"effectId": "cutvoke.text", "version": "1.0.0",
                           "params": text_params}
            if text_preset is not None:
                text_effect.update({"presetId": text_preset.id,
                                    "presetVersion": text_preset.version,
                                    "presetPartIndex": 0})
            clip.effects.append(text_effect)
        elif "text" in p or "textPresetId" in p:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "text parameters and textPresetId require a text track")
        if clip_role == "sticker":
            sticker_scale = p.get("stickerScale", 0.28)
            try:
                initial_transform = self._effects.validate_params(
                    "cutvoke.transform", {"position": {"x": 0, "y": 0},
                                          "scale": sticker_scale})
                size = round(min(proj.sequence.width, proj.sequence.height) *
                             initial_transform["scale"])
                position = {
                    "x": round((proj.sequence.width - size) / 2),
                    "y": round((proj.sequence.height - size) / 2),
                }
                transform_params = self._effects.validate_params(
                    "cutvoke.transform", {**initial_transform, "position": position})
            except Exception as error:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"invalid sticker scale: {error}") from error
            clip.effects.append({
                "effectId": "cutvoke.transform", "version": "1.0.0",
                "params": transform_params,
            })
        animation = p.get("stickerAnimation")
        if animation is not None:
            if clip_role != "sticker" or not isinstance(animation, dict):
                raise EditError(ErrorCode.INVALID_ARGUMENT, "stickerAnimation requires a sticker clip")
            effect_id = animation.get("effectId")
            spec = self._effects.find(effect_id) if isinstance(effect_id, str) else None
            if spec is None or spec.category != "animation" or "image" not in spec.applies_to:
                raise EditError(ErrorCode.INVALID_ARGUMENT, "stickerAnimation requires an image animation")
            try:
                params = self._effects.validate_params(effect_id, animation.get("params", {}))
            except Exception as error:
                raise EditError(ErrorCode.INVALID_ARGUMENT, f"invalid sticker animation: {error}") from error
            clip.effects.append({"effectId": effect_id, "version": spec.version,
                                 "params": params})
        self._assert_clip_within_source(clip)

        changed: list[dict] = []
        if creating_track:
            proj.sequence.tracks.append(track)
            changed.append({"type": "track", "id": track_id, "change": "created"})

        if mode == "insert":
            shifted = self._insert_shift(track, start, end - start)
            changed.extend(shifted)
            for item in shifted:
                if item.get("reason") != "insert_split":
                    continue
                for lane in proj.sequence.tracks:
                    for child in lane.clips:
                        if (child.attached_to_clip_id == item.get("splitFrom") and
                                child.timeline_start >= start):
                            old = child.timeline_start
                            child.attached_to_clip_id = item["id"]
                            child.timeline_start = old + (end - start)
                            child.timeline_end = child.timeline_end + (end - start)
                            changed.append({"type": "clip", "id": child.id,
                                            "change": "moved", "reason": "anchor_split",
                                            "from": old.to_json(),
                                            "to": child.timeline_start.to_json()})
        elif mode == "overwrite":
            overwritten = self._overwrite_span(track, start, end, clip_id)
            changed.extend(overwritten)
            deleted = {item["id"] for item in overwritten if item.get("change") == "deleted"}
            split_to = {item["splitFrom"]: item["id"] for item in overwritten
                        if item.get("reason") == "overwrite_split"}
            for lane in proj.sequence.tracks:
                for child in lane.clips:
                    parent_id = child.attached_to_clip_id
                    if parent_id in deleted:
                        child.attached_to_clip_id = None
                    elif parent_id in split_to and child.timeline_start >= end:
                        child.attached_to_clip_id = split_to[parent_id]
                    else:
                        continue
                    changed.append({"type": "clip", "id": child.id,
                                    "change": "updated", "reason": "anchor_overwritten"})

        track.clips.append(clip)
        track.clips.sort(key=lambda current: current.timeline_start)
        changed.extend(self._follow_attached_clips(proj, before_positions, True))
        changed.append({"type": "clip", "id": clip_id, "change": "created",
                        "mode": mode or "append"})
        return changed

    def _overwrite_span(self, track: Track, start: Rational, end: Rational,
                        new_clip_id: str) -> list[dict]:
        """把 [start,end) 从轨上腾出来：重叠片段裁短/删除/劈开。

        三种情形：
          - 既有片段被新片段**完全覆盖** → 删除；
          - 既有片段与新区间**部分相交**（在某一端）→ 从相交端裁短；
          - 既有片段**跨越**新区间（两头都在外面）→ 拆成左右两段（保留两侧内容，
            右侧段源内入点按时间线偏移同步，与 clip.split 的语义一致）。
        """
        changed: list[dict] = []
        kept: list[Clip] = []
        for c in sorted(track.clips, key=lambda x: x.timeline_start):
            if c.id == new_clip_id:
                kept.append(c)
                continue
            cs, ce = c.timeline_start, c.timeline_end
            if ce <= start or cs >= end:
                kept.append(c)  # 不相交
                continue
            fully_covered = cs >= start and ce <= end
            if fully_covered:
                changed.append({"type": "clip", "id": c.id, "change": "deleted",
                                "reason": "overwritten"})
                continue
            # 左端相交：裁掉尾部 → 保留 [cs, start)
            if cs < start and ce <= end:
                if c.speed_curve is not None:
                    self._crop_curve_clip(c, cs, start)
                else:
                    c.timeline_end = start
                kept.append(c)
                changed.append({"type": "clip", "id": c.id, "change": "trimmed",
                                "edge": "end", "to": start.to_json()})
                continue
            # 右端相交：裁掉头部 → 保留 [end, ce)
            if cs >= start and ce > end:
                # 头部被吃掉，源内入点随之前移（保持画面内容与时间线对齐）
                if c.speed_curve is not None:
                    self._crop_curve_clip(c, end, ce)
                else:
                    c.source_start = c.source_start + (end - c.timeline_start) * c.speed
                    c.timeline_start = end
                kept.append(c)
                changed.append({"type": "clip", "id": c.id, "change": "trimmed",
                                "edge": "start", "to": end.to_json()})
                continue
            # 跨越：拆成左右两段
            left = c
            if c.speed_curve is not None:
                right = copy.deepcopy(c)
                right.id = new_id("clip")
                self._crop_curve_clip(right, end, ce)
                self._crop_curve_clip(left, cs, start)
            else:
                source_at = c.source_start + (end - c.timeline_start) * c.speed
                right = Clip(id=new_id("clip"), asset_ref=c.asset_ref,
                             timeline_start=end, timeline_end=ce,
                             source_start=source_at, speed=c.speed,
                             preserve_pitch=c.preserve_pitch,
                             effects=list(c.effects), linked=c.linked,
                             attached_to_clip_id=c.attached_to_clip_id,
                             hidden=c.hidden,
                             keyframes={k: list(v) for k, v in c.keyframes.items()},
                             volume=c.volume, fade_in=c.fade_in, fade_out=c.fade_out,
                             pitch=c.pitch, freeze_at=c.freeze_at, nested=c.nested,
                             role=c.role)
                left.timeline_end = start
            kept.append(left)
            kept.append(right)
            changed.append({"type": "clip", "id": c.id, "change": "split",
                            "edge": "start", "to": start.to_json()})
            changed.append({"type": "clip", "id": right.id, "change": "created",
                            "reason": "overwrite_split",
                            "splitFrom": c.id,
                            "from": end.to_json()})
        track.clips = sorted(kept, key=lambda x: x.timeline_start)
        return changed

    def _insert_shift(self, track: Track, at: Rational,
                      dur: Rational) -> list[dict]:
        """在 at 处腾出 dur 时长：起点 >= at 的片段整体右移；跨 at 的片段被切开。

        与 `clip.closeGap` 的镜像操作（一个左移合拢、一个右移让位）。
        """
        changed: list[dict] = []
        for c in sorted(track.clips, key=lambda x: x.timeline_start):
            if c.timeline_start >= at:
                old = c.timeline_start
                c.timeline_start = c.timeline_start + dur
                c.timeline_end = c.timeline_end + dur
                changed.append({"type": "clip", "id": c.id, "change": "moved",
                                "from": old.to_json(),
                                "to": c.timeline_start.to_json()})
            elif c.timeline_end > at:
                # 插入点落在片段内部：切开，后半段右移 dur
                if c.speed_curve is not None:
                    old_start, old_end = c.timeline_start, c.timeline_end
                    right = copy.deepcopy(c)
                    right.id = new_id("clip")
                    self._crop_curve_clip(right, at, old_end)
                    right.timeline_start = right.timeline_start + dur
                    right.timeline_end = right.timeline_end + dur
                    self._crop_curve_clip(c, old_start, at)
                else:
                    source_at = c.source_start + (at - c.timeline_start) * c.speed
                    right = Clip(id=new_id("clip"), asset_ref=c.asset_ref,
                                 timeline_start=at + dur, timeline_end=c.timeline_end + dur,
                                 source_start=source_at, speed=c.speed,
                                 preserve_pitch=c.preserve_pitch,
                                 effects=list(c.effects), linked=c.linked,
                                 attached_to_clip_id=c.attached_to_clip_id,
                                 hidden=c.hidden,
                                 keyframes={k: list(v) for k, v in c.keyframes.items()},
                                 volume=c.volume, fade_in=c.fade_in, fade_out=c.fade_out,
                                 pitch=c.pitch, freeze_at=c.freeze_at, nested=c.nested,
                                 role=c.role)
                    c.timeline_end = at
                track.clips.append(right)
                changed.append({"type": "clip", "id": c.id, "change": "split",
                                "edge": "end", "to": at.to_json()})
                changed.append({"type": "clip", "id": right.id, "change": "created",
                                "reason": "insert_split",
                                "splitFrom": c.id,
                                "from": (at + dur).to_json()})
        track.clips = sorted(track.clips, key=lambda x: x.timeline_start)
        return changed

    def _h_clip_trim(self, proj: Project, p: dict) -> list[dict]:
        clip_id = p["clipId"]
        before = {c.id: c.timeline_start for lane in proj.sequence.tracks
                  for c in lane.clips}
        for track in proj.sequence.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    if clip.speed_curve is not None:
                        start = (Rational.from_json(**p["timelineStart"])
                                 if "timelineStart" in p else clip.timeline_start)
                        end = (Rational.from_json(**p["timelineEnd"])
                               if "timelineEnd" in p else clip.timeline_end)
                        self._crop_curve_clip(clip, start, end)
                        if clip.frame_interpolation == "motion" and not clip.has_slow_motion:
                            clip.frame_interpolation = "none"
                        if "sourceStart" in p:
                            requested = Rational.from_json(**p["sourceStart"])
                            difference = abs(float((requested - clip.source_start).to_fraction()))
                            if difference > 0.05:
                                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                                "曲线变速裁切的源入点必须按曲线时间映射计算")
                        self._assert_clip_within_source(clip)
                        changed = [{"type": "clip", "id": clip_id,
                                    "change": "updated"}]
                        changed.extend(self._follow_attached_clips(proj, before, True))
                        return changed
                    old_start, old_end = clip.timeline_start, clip.timeline_end
                    if "timelineStart" in p:
                        clip.timeline_start = Rational.from_json(**p["timelineStart"])
                    if "timelineEnd" in p:
                        clip.timeline_end = Rational.from_json(**p["timelineEnd"])
                    if "sourceStart" in p:
                        clip.source_start = Rational.from_json(**p["sourceStart"])
                    self._assert_clip_within_source(clip)
                    self._rebase_effect_ranges(
                        clip, old_start, old_end, clip.timeline_start, clip.timeline_end)
                    changed = [{"type": "clip", "id": clip_id,
                                "change": "updated"}]
                    changed.extend(self._follow_attached_clips(proj, before, True))
                    return changed
        raise EditError(ErrorCode.INVALID_ARGUMENT, f"clip not found: {clip_id}")

    def _h_clip_attach(self, proj: Project, p: dict) -> list[dict]:
        """Set an explicit parent clip ID. Project validation checks the target and track."""
        if "attachedToClipId" not in p:
            raise EditError(ErrorCode.INVALID_ARGUMENT, "clip.attach requires attachedToClipId")
        target = p["attachedToClipId"]
        if target is not None and (not isinstance(target, str) or not target):
            raise EditError(ErrorCode.INVALID_ARGUMENT, "attachedToClipId must be a clip ID or null")
        for track in proj.sequence.tracks:
            for clip in track.clips:
                if clip.id == p["clipId"]:
                    clip.attached_to_clip_id = target
                    return [{"type": "clip", "id": clip.id, "change": "updated",
                             "attachedToClipId": target}]
        raise EditError(ErrorCode.INVALID_ARGUMENT, f"clip not found: {p['clipId']}")

    @staticmethod
    def _follow_attached_clips(proj: Project, before: dict[str, Rational],
                               follow: bool) -> list[dict]:
        """Apply each moved parent's delta to its explicitly attached clips once."""
        if not follow:
            return []
        clips = {clip.id: clip for track in proj.sequence.tracks for clip in track.clips}
        changed: list[dict] = []
        # A parent is a normal video clip; attachment chains are intentionally
        # unsupported by the project invariant, so a single pass is sufficient.
        for clip in clips.values():
            parent_id = clip.attached_to_clip_id
            if not parent_id or parent_id not in before:
                continue
            parent = clips.get(parent_id)
            if parent is None:
                continue
            delta = parent.timeline_start - before[parent_id]
            if delta == Rational.of(0, 1):
                continue
            old = before[clip.id]
            duration = clip.duration
            clip.timeline_start = old + delta
            clip.timeline_end = clip.timeline_start + duration
            if clip.timeline_start < Rational.of(0, 1):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"attached clip {clip.id} would start before zero")
            changed.append({"type": "clip", "id": clip.id, "change": "moved",
                            "from": old.to_json(), "to": clip.timeline_start.to_json(),
                            "reason": "attached"})
        return changed

    def _h_clip_move(self, proj: Project, p: dict) -> list[dict]:
        """移动片段：改变时间线起点（偏移），可选跨轨移动。

        payload:
          clipId        要移动的片段
          timelineStart 新的时间线起点（可选；缺省保持当前起点）
          trackId       目标轨道（可选；缺省保持当前轨 = 同轨移动）
          mode           reorder 时把完整片段插入锚点片段前后
          anchorClipId   reorder 模式的锚点片段
          anchorPosition before 或 after
        """
        clip_id = p["clipId"]
        mode = str(p.get("mode") or "move").strip().lower()
        follow = p.get("followAttachments", True)
        if not isinstance(follow, bool):
            raise EditError(ErrorCode.INVALID_ARGUMENT, "followAttachments must be boolean")
        all_before = {c.id: c.timeline_start for lane in proj.sequence.tracks for c in lane.clips}
        if mode not in ("move", "reorder"):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"clip.move mode must be 'move' or 'reorder', got {mode!r}")
        new_start = None
        if "timelineStart" in p:
            new_start = Rational.from_json(**p["timelineStart"])

        # 定位源片段所在轨道
        src_track: Optional[Track] = None
        clip: Optional[Clip] = None
        for track in proj.sequence.tracks:
            for c in track.clips:
                if c.id == clip_id:
                    src_track, clip = track, c
                    break
            if src_track is not None:
                break
        if clip is None or src_track is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT, f"clip not found: {clip_id}")
        old_start = clip.timeline_start
        dur = clip.duration
        if new_start is None:
            new_start = old_start

        # 目标轨道：显式 trackId 跨轨；同轨移动不换列表
        dest_track = src_track
        if "trackId" in p and p["trackId"] != src_track.id:
            tid = p["trackId"]
            for track in proj.sequence.tracks:
                if track.id == tid:
                    dest_track = track
                    break
            if dest_track is None:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"target track not found: {tid}")
            if (src_track.kind == "audio" and clip.attached_to_clip_id is not None and
                    dest_track.kind != "audio"):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "attached audio clips must remain on an audio track")
            required_track = _source_track_kind(clip.asset_ref.source_path)
            # 正常片段只能在同类型轨道之间移动；历史版本曾允许纯音频落入
            # 视频轨，因此给这类脏数据一条可撤销的人工修复路径：只允许移动到
            # 素材真实需要的轨道，不能借此把正常素材跨类型乱放。
            if dest_track.kind != src_track.kind and required_track != dest_track.kind:
                raise EditError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"cannot move {clip_id} from {src_track.kind} track "
                    f"to {dest_track.kind} track")

        # 目标时间/轨道上的重叠校验（同轨非重叠是工程不变量，见 invariants.py）
        def _overlaps_on(track_: Track, start: Rational, end: Rational,
                         except_id: str) -> bool:
            for c in track_.clips:
                if c.id == except_id:
                    continue
                if c.timeline_start < end and start < c.timeline_end:
                    return True
            return False

        if mode == "reorder":
            anchor_id = str(p.get("anchorClipId") or "")
            anchor_position = str(p.get("anchorPosition") or "").lower()
            if not anchor_id or anchor_id == clip_id:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "clip.move reorder requires another anchorClipId")
            if anchor_position not in ("before", "after"):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "clip.move reorder anchorPosition must be 'before' or 'after'")
            anchor = next((c for c in dest_track.clips if c.id == anchor_id), None)
            if anchor is None:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"reorder anchor clip not found on target track: {anchor_id}")

            affected_tracks = [src_track] if src_track is dest_track else [src_track, dest_track]
            before = {
                c.id: c.timeline_start
                for track in affected_tracks
                for c in track.clips
            }

            # Remove the moving clip and close exactly the duration it occupied.
            # Existing gaps remain intact; no neighbouring clip is trimmed or split.
            old_end = clip.timeline_end
            src_track.clips.remove(clip)
            for current in src_track.clips:
                if current.timeline_start >= old_end:
                    current.timeline_start = current.timeline_start - dur
                    current.timeline_end = current.timeline_end - dur

            # The anchor may have moved while closing the source slot (same-track
            # reorder), so derive the insertion boundary only after that step.
            insert_at = (anchor.timeline_start if anchor_position == "before"
                         else anchor.timeline_end)
            for current in dest_track.clips:
                if current.timeline_start >= insert_at:
                    current.timeline_start = current.timeline_start + dur
                    current.timeline_end = current.timeline_end + dur

            clip.timeline_start = insert_at
            clip.timeline_end = insert_at + dur
            dest_track.clips.append(clip)
            src_track.clips.sort(key=lambda item: item.timeline_start)
            if dest_track is not src_track:
                dest_track.clips.sort(key=lambda item: item.timeline_start)

            changed: list[dict] = []
            for track in affected_tracks:
                for current in track.clips:
                    previous = before.get(current.id)
                    if previous is not None and previous != current.timeline_start:
                        changed.append({
                            "type": "clip", "id": current.id, "change": "moved",
                            "from": previous.to_json(),
                            "to": current.timeline_start.to_json(),
                            "reason": "reordered",
                        })
            changed.extend(self._follow_attached_clips(proj, all_before, follow))
            return changed

        new_end = new_start + dur
        if _overlaps_on(dest_track, new_start, new_end, clip_id):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"clip would overlap another clip on track {dest_track.id}")

        # 更新时间 + 跨轨时迁移到目标轨列表
        clip.timeline_start = new_start
        clip.timeline_end = new_end
        if dest_track is not src_track:
            src_track.clips.remove(clip)
            dest_track.clips.append(clip)
        changed = [{"type": "clip", "id": clip_id, "change": "updated"}]
        changed.extend(self._follow_attached_clips(proj, all_before, follow))
        return changed

    # ------------------------------------------------------------------
    # 片段写入基元（供单个命令与 clip.batch 批量命令复用，避免重复实现）
    # ------------------------------------------------------------------
    def _set_clip_speed(self, clip: Clip, speed_val) -> None:
        """设置片段恒定变速倍率（守恒源素材范围，仅用有理数精确运算）。"""
        new_speed = _parse_speed(speed_val)
        if new_speed == Rational.of(0, 1):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "clip.speed cannot be zero (undefined time mapping)")
        # 守恒源素材范围：新时间线时长 = 原时长 * |原speed| / |新speed|
        source_span = clip.consumed_source_duration
        new_tl = source_span / _abs_rat(new_speed)
        clip.timeline_end = clip.timeline_start + new_tl
        clip.speed = new_speed
        clip.speed_curve = None
        if clip.frame_interpolation == "motion" and not clip.has_slow_motion:
            clip.frame_interpolation = "none"

    def _set_clip_audio(self, clip: Clip, p: dict) -> None:
        """按 payload 设置片段音频（volume / fadeIn / fadeOut / pitch）。

        复用于 clip.audio 与 clip.batch，字段名与 clip.audio 完全对齐。
        """
        if "volume" in p:
            v = p["volume"]
            vol = (Rational.from_json(**v) if isinstance(v, dict)
                   else Rational.from_float(float(v)))
            if vol < Rational.of(0, 1):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "volume cannot be negative")
            clip.volume = vol
        if "fadeIn" in p:
            f = p["fadeIn"]
            clip.fade_in = (Rational.from_json(**f) if isinstance(f, dict)
                            else Rational.from_float(float(f)))
        if "fadeOut" in p:
            f = p["fadeOut"]
            clip.fade_out = (Rational.from_json(**f) if isinstance(f, dict)
                             else Rational.from_float(float(f)))
        if "pitch" in p:
            pt = p["pitch"]
            pitch = (Rational.from_json(**pt) if isinstance(pt, dict)
                     else Rational.from_float(float(pt)))
            if pitch < Rational.of(1, 10) or pitch > Rational.of(8, 1):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "clip.audio pitch must be between 0.1 and 8")
            clip.pitch = pitch

    def _set_clip_opacity(self, clip: Clip, opacity_val) -> None:
        """设置片段不透明度（复用 cutvoke.transform 效果，按注册表校验 opacity）。

        不存在 transform 效果则追加一个；已存在则就地更新 opacity，保留其它参数。
        """
        val = float(opacity_val)
        params = self._effects.validate_params(EFFECT_TRANSFORM, {"opacity": val})
        opacity = params["opacity"]
        for e in clip.effects:
            if e.get("effectId") == EFFECT_TRANSFORM:
                ep = dict(e.get("params") or {})
                ep["opacity"] = opacity
                e["params"] = ep
                return
        clip.effects.append({"effectId": EFFECT_TRANSFORM, "version": "1.0.0",
                             "params": {"opacity": opacity}})

    def _h_clip_speed(self, proj: Project, p: dict) -> list[dict]:
        """设置恒速或源位置速度曲线，保持原素材范围。

        语义（任务书 7.2 / F12）：
          - speed=2 表示 2 倍速：时间线时长减半
          - speed=-1 表示倒放：时间线时长不变，方向反转
          - 时间线时长 = 原时间线时长 * |原speed| / |新speed|（守恒源素材范围，
            且仅用有理数精确运算，不引入浮点漂移）

        preservePitch 控制恒速音频是否保调；曲线音频始终保调。
        """
        clip_id = p["clipId"]
        has_speed = "speed" in p
        has_curve = "curve" in p
        has_interpolation = "frameInterpolation" in p
        if (has_speed and has_curve) or not (has_speed or has_curve or has_interpolation):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "clip.speed requires speed, curve, or frameInterpolation")
        preserve_pitch = p.get("preservePitch")
        if preserve_pitch is not None and not isinstance(preserve_pitch, bool):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "clip.speed preservePitch must be boolean")
        for track in proj.sequence.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    if has_curve:
                        if (track.kind not in ("video", "audio") or clip.nested is not None
                                or clip.freeze_at is not None or
                                os.path.splitext(clip.asset_ref.source_path)[1].lower() in _IMAGE_SUFFIXES):
                            raise EditError(ErrorCode.INVALID_ARGUMENT,
                                            "速度曲线仅适用于普通视频或音频片段")
                        if preserve_pitch is False or (preserve_pitch is None and not clip.preserve_pitch):
                            raise EditError(ErrorCode.INVALID_ARGUMENT,
                                            "曲线变速目前需要保持音高")
                        raw = p["curve"]
                        if not isinstance(raw, dict):
                            raise EditError(ErrorCode.INVALID_ARGUMENT, "curve must be an object")
                        try:
                            curve = SpeedCurve.from_points(
                                clip.consumed_source_duration, raw.get("points"))
                        except ValueError as exc:
                            raise EditError(ErrorCode.INVALID_ARGUMENT, str(exc)) from exc
                        timeline_duration = curve.timeline_duration
                        clip.timeline_end = clip.timeline_start + timeline_duration
                        clip.speed = curve.source_duration / timeline_duration
                        clip.speed_curve = curve
                    elif has_speed:
                        self._set_clip_speed(clip, p["speed"])
                    if preserve_pitch is not None:
                        clip.preserve_pitch = preserve_pitch
                    if has_interpolation:
                        interpolation = p["frameInterpolation"]
                        if interpolation not in ("none", "motion"):
                            raise EditError(ErrorCode.INVALID_ARGUMENT,
                                            "frameInterpolation must be 'none' or 'motion'")
                        if interpolation == "motion" and (
                                track.kind != "video" or clip.nested is not None
                                or clip.freeze_at is not None
                                or os.path.splitext(clip.asset_ref.source_path)[1].lower() in _IMAGE_SUFFIXES
                                or not clip.has_slow_motion):
                            raise EditError(
                                ErrorCode.INVALID_ARGUMENT,
                                "运动估算补帧仅适用于包含低于 1x 区间的普通视频片段")
                        clip.frame_interpolation = interpolation
                    elif (has_speed or has_curve) and clip.frame_interpolation == "motion" \
                            and not clip.has_slow_motion:
                        clip.frame_interpolation = "none"
                    self._assert_clip_within_source(clip)
                    return [{"type": "clip", "id": clip_id, "change": "updated"}]
        raise EditError(ErrorCode.INVALID_ARGUMENT, f"clip not found: {clip_id}")

    def _h_clip_freeze(self, proj: Project, p: dict) -> list[dict]:
        """B06 定格帧：把片段在时间线期间固定渲染为某源时刻的单帧。

        {clipId, sourceTime?}：sourceTime 是相对源起点的时间点（Rational）。
        省略/None → 清除定格（恢复正常播放）。渲染层把源窗口恒定为该时刻。
        """
        clip_id = p["clipId"]
        clip = None
        for track in proj.sequence.tracks:
            clip = next((c for c in track.clips if c.id == clip_id), None)
            if clip is not None:
                break
        if clip is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT, f"clip not found: {clip_id}")
        if p.get("sourceTime") is None:
            clip.freeze_at = None
        else:
            st = _parse_speed(p["sourceTime"])  # 复用 Rational 解析（形式同 speed）
            src_dur = clip.duration * _abs_rat(clip.speed)
            if st < Rational.of(0, 1) or st > src_dur:
                raise EditError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"freeze sourceTime {st} out of source range 0~{src_dur}")
            clip.freeze_at = clip.source_start + st
            clip.frame_interpolation = "none"
        return [{"type": "clip", "id": clip_id, "change": "updated"}]

    def _h_clip_audio(self, proj: Project, p: dict) -> list[dict]:
        """设置片段音频（D06 F27/F28）：{clipId, volume?, fadeIn?, fadeOut?, pitch?}。

        volume 为相对音量（1.0=原声）；fadeIn/fadeOut 为淡入淡出时长（秒）。
        缺省字段保持不变。复用 _set_clip_audio 写入基元。
        """
        clip_id = p["clipId"]
        if not any(k in p for k in ("volume", "fadeIn", "fadeOut", "pitch")):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "clip.audio requires volume/fadeIn/fadeOut/pitch")
        for track in proj.sequence.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    self._set_clip_audio(clip, p)
                    return [{"type": "clip", "id": clip_id, "change": "updated"}]
        raise EditError(ErrorCode.INVALID_ARGUMENT, f"clip not found: {clip_id}")

    def _h_clip_detach_audio(self, proj: Project, p: dict) -> list[dict]:
        """Move a video's embedded sound to an editable, attached audio clip.

        The audio clip references the same source file and stream, so no lossy
        intermediate asset is created. Both changes belong to one undo point.
        """
        clip_id = p.get("clipId")
        source_track = next((track for track in proj.sequence.tracks
                             if track.kind == "video" and track.role != "sticker" and
                             any(clip.id == clip_id for clip in track.clips)), None)
        if source_track is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "clip.detachAudio requires a video clip")
        source = next(clip for clip in source_track.clips if clip.id == clip_id)
        path = source.asset_ref.source_path
        if not path or not os.path.isfile(path):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "video source is unavailable; relink it before detaching audio")
        if any(track.kind == "audio" and
               any(clip.attached_to_clip_id == clip_id and
                   clip.asset_ref.source_path == path for clip in track.clips)
               for track in proj.sequence.tracks):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "this video already has an attached audio clip")
        from .render import RenderError, RenderService
        try:
            info = RenderService(ffprobe_path=os.environ.get("CUTVOKE_FFPROBE", "ffprobe")).probe_media(path)
        except (RenderError, OSError) as error:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"cannot inspect video audio: {error}") from error
        if not info["has_video"] or not info["has_audio"]:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "video source has no embedded audio stream")

        used_ids = {item.id for track in proj.sequence.tracks
                    for item in [track, *track.clips]}
        def free_id(requested: object, base: str) -> str:
            if requested is not None:
                if not isinstance(requested, str) or not requested or requested in used_ids:
                    raise EditError(ErrorCode.INVALID_ARGUMENT,
                                    f"invalid or occupied audio ID: {requested!r}")
                used_ids.add(requested)
                return requested
            candidate = base
            index = 2
            while candidate in used_ids:
                candidate = f"{base}_{index}"
                index += 1
            used_ids.add(candidate)
            return candidate

        track_id = free_id(p.get("audioTrackId"), f"audio_{clip_id}")
        audio_id = free_id(p.get("audioClipId"), f"{clip_id}_audio")
        audio_effects = []
        visual_effects = []
        for effect in source.effects:
            spec = self._effects.find(effect.get("effectId", ""))
            (audio_effects if spec is not None and "audio" in spec.applies_to
             else visual_effects).append(effect)
        detached = Clip(
            id=audio_id,
            asset_ref=copy.deepcopy(source.asset_ref),
            timeline_start=source.timeline_start,
            timeline_end=source.timeline_end,
            source_start=source.source_start,
            speed=source.speed,
            speed_curve=copy.deepcopy(source.speed_curve),
            preserve_pitch=source.preserve_pitch,
            effects=copy.deepcopy(audio_effects),
            attached_to_clip_id=clip_id,
            volume=source.volume,
            fade_in=source.fade_in,
            fade_out=source.fade_out,
            pitch=source.pitch,
        )
        source.effects = visual_effects
        source.volume = Rational.of(0, 1)
        source.fade_in = Rational.of(0, 1)
        source.fade_out = Rational.of(0, 1)
        source.pitch = Rational.of(1, 1)
        source.linked = False
        new_track = Track(id=track_id, kind="audio", clips=[detached])
        proj.sequence.tracks.insert(proj.sequence.tracks.index(source_track) + 1, new_track)
        return [
            {"type": "clip", "id": clip_id, "change": "updated", "reason": "audio_detached"},
            {"type": "track", "id": track_id, "change": "created"},
            {"type": "clip", "id": audio_id, "change": "created", "attachedToClipId": clip_id},
        ]

    def _h_clip_batch(self, proj: Project, p: dict) -> list[dict]:
        """批量修改同一轨道上多个片段：{trackId, clipIds?, params:{speed?|volume?|
        fadeIn?|fadeOut?|pitch?|opacity?}}。

        复用 clip.speed / clip.audio / cutvoke.transform(opacity) 的既有写入基元，
        不重复实现。不支持的参数名报 INVALID_ARGUMENT。返回每条片段一项。
        """
        track_id = p.get("trackId")
        if not track_id:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "clip.batch requires 'trackId'")
        params = p.get("params")
        if not isinstance(params, dict) or not params:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "clip.batch requires a non-empty 'params' object")
        allowed = {"speed", "volume", "fadeIn", "fadeOut", "pitch", "opacity",
                   "animation", "transition"}
        for k in params:
            if k not in allowed:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"clip.batch: unsupported parameter '{k}'")
        track = next((t for t in proj.sequence.tracks if t.id == track_id), None)
        if track is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"track not found: {track_id}")
        has_audio = any(k in params for k in ("volume", "fadeIn", "fadeOut", "pitch"))
        if "clipIds" in p and p["clipIds"]:
            idset = set(p["clipIds"])
            targets = [c for c in track.clips if c.id in idset]
            missing = idset - {c.id for c in targets}
            if missing:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"clip.batch: clipIds not on track: {sorted(missing)}")
        else:
            targets = list(track.clips)
        if not targets:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "clip.batch: no target clips on the track")
        changed = []
        for clip in targets:
            if "speed" in params:
                self._set_clip_speed(clip, params["speed"])
                self._assert_clip_within_source(clip)
            if has_audio:
                self._set_clip_audio(clip, params)
            if "opacity" in params:
                self._set_clip_opacity(clip, params["opacity"])
            if "animation" in params:
                # D06 批量动画：{effectId?, params?} → 先移除既有动画再添加
                # 指定的动画（与 AnimationSection 的「单一动画」语义一致）。
                anim_cfg = params["animation"]
                eid = (anim_cfg.get("effectId") if isinstance(anim_cfg, dict)
                       else anim_cfg)
                eid = str(eid or "")
                if not eid.startswith("cutvoke.anim."):
                    raise EditError(
                        ErrorCode.INVALID_ARGUMENT,
                        f"clip.batch animation must be cutvoke.anim.*: {eid}")
                self._require_effect(eid)
                anim_params = (anim_cfg.get("params") if isinstance(anim_cfg, dict)
                               else None) or {}
                clip.effects = [
                    e for e in clip.effects
                    if not str(e.get("effectId", "")).startswith("cutvoke.anim.")]
                anim_params = self._effects.validate_params(eid, anim_params)
                clip.effects.append({"effectId": eid, "version": "1.0.0",
                                     "params": anim_params})
            if "transition" in params:
                # G05 批量转场：{effectId?, params?} → 移除既有转场后添加指定转场
                # （挂在「被切入」片段，与 TransitionPanel 语义一致）。
                tr_cfg = params["transition"]
                teid = (tr_cfg.get("effectId") if isinstance(tr_cfg, dict)
                        else tr_cfg)
                teid = str(teid or "")
                self._require_effect(teid)
                tr_params = (tr_cfg.get("params") if isinstance(tr_cfg, dict)
                             else None) or {}
                clip.effects = [
                    e for e in clip.effects
                    if not str(e.get("effectId", "")).startswith("cutvoke.transition.")]
                tr_params = self._effects.validate_params(teid, tr_params)
                clip.effects.append({"effectId": teid, "version": "1.0.0",
                                     "params": tr_params})
            changed.append({"type": "clip", "id": clip.id, "change": "updated"})
        return changed

    def _h_asset_swap(self, proj: Project, p: dict) -> list[dict]:
        """更换片段素材（J02 换图套版 / J09 批量替换素材）：{trackId?|clipIds?,
        sourcePath 或 assetId}。

        只替换片段引用的素材（asset_ref），**保留全部其他属性**（timeline
        时长、effects 动画、speed、keyframes、volume、fade 等）——这正是
        「模板槽位」的语义：模板 = 一组固定位置/动画/效果的片段，换图 = 换
        素材不换布局。支持 clipIds 显式列表或 trackId 整轨。
        """
        source_path = p.get("sourcePath")
        asset_id = p.get("assetId", "")
        if not source_path and not asset_id:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                "asset.swap requires 'sourcePath' or 'assetId'")
        if not source_path:
            asset = self._store.get_asset(asset_id) if self._store is not None else None
            if asset is not None:
                source_path = asset.get("path")
            else:
                source_path = next((clip.asset_ref.source_path
                                    for track in proj.sequence.tracks for clip in track.clips
                                    if clip.asset_ref.asset_id == asset_id
                                    and clip.asset_ref.source_path), None)
            if not source_path:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"asset.swap: unknown or unavailable assetId {asset_id!r}; "
                                "provide a sourcePath or import the asset first")
        if not isinstance(source_path, str) or not source_path.strip():
            raise EditError(ErrorCode.INVALID_ARGUMENT, "asset.swap sourcePath must be a nonempty string")
        # 选目标片段：clipIds 显式 / trackId 整轨。保留轨道引用，先完整校验
        # 再修改，确保批量替换不会出现前半成功、后半失败的中间状态。
        if p.get("clipIds"):
            idset = set(p["clipIds"])
            target_pairs = [(tr, c) for tr in proj.sequence.tracks for c in tr.clips
                            if c.id in idset]
            missing = idset - {c.id for _, c in target_pairs}
            if missing:
                raise EditError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"asset.swap: unknown clipIds: {sorted(missing)}")
        elif p.get("trackId"):
            track = next((t for t in proj.sequence.tracks
                          if t.id == p["trackId"]), None)
            if track is None:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"track not found: {p['trackId']}")
            target_pairs = [(track, clip) for clip in track.clips]
        else:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                "asset.swap requires 'clipIds' or 'trackId'")
        if not target_pairs:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "asset.swap: no target clips")
        required_track = _source_track_kind(source_path or "")
        if required_track is not None:
            incompatible = [
                (track.id, clip.id, track.kind)
                for track, clip in target_pairs
                if track.kind in ("video", "audio") and track.kind != required_track
            ]
            if incompatible:
                track_id, clip_id, actual_kind = incompatible[0]
                article = "an" if required_track == "audio" else "a"
                raise EditError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"asset.swap source requires {article} {required_track} track; "
                    f"clip {clip_id} is on {actual_kind} track {track_id}")
        for _, clip in target_pairs:
            clip.asset_ref = AssetReference(asset_id, source_path=source_path)
            if source_path and os.path.splitext(source_path)[1].lower() in _IMAGE_SUFFIXES:
                clip.frame_interpolation = "none"
            self._assert_clip_within_source(clip)
        return [{"type": "clip", "id": c.id, "change": "asset.swapped"}
                for _, c in target_pairs]

    def _h_caption_batch_style(self, proj: Project, p: dict) -> list[dict]:
        """批量改字幕样式：{captionIds?, fromTime?, toTime?, style:{...}}。

        style 字段复用 caption.update 的 _apply_caption_style（fontSize/color/
        strokeColor/strokeWidth/background/align/bold/animIn/animOut）。返回每条字幕一项。
        """
        style = p.get("style")
        if not isinstance(style, dict) or not style:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption.batchStyle requires a non-empty 'style' object")
        caps = proj.sequence.captions
        if "captionIds" in p and p["captionIds"]:
            idset = set(p["captionIds"])
            targets = [c for c in caps if c.id in idset]
            missing = idset - {c.id for c in targets}
            if missing:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"caption.batchStyle: unknown captionIds: {sorted(missing)}")
        elif "fromTime" in p or "toTime" in p:
            ft = (Rational.from_json(**p["fromTime"]) if "fromTime" in p
                  else Rational.of(0, 1))
            tt = (Rational.from_json(**p["toTime"]) if "toTime" in p else None)
            targets = [c for c in caps
                       if c.start >= ft and (tt is None or c.end <= tt)]
        else:
            targets = list(caps)
        if not targets:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption.batchStyle: no target captions")
        for c in targets:
            _apply_caption_style(c, style)
        return [{"type": "caption", "id": c.id, "change": "updated"} for c in targets]

    def _h_export_cover(self, proj: Project, p: dict) -> list[dict]:
        """导出封面（经 execute 调用时的薄封装；HTTP 端点直接调 service.export_cover）。

        返回 [{"type":"cover","change":"exported","outPath","width","height"}]。
        """
        out_path = p.get("outPath")
        if not out_path:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "export.cover requires 'outPath'")
        t = 0.0 if p.get("t") is None else float(p["t"])
        width = (int(p["width"]) if p.get("width") is not None else None)
        result = self.export_cover(proj.project_id, out_path, t=t, width=width)
        return [{"type": "cover", "change": "exported",
                 "outPath": result["outPath"],
                 "width": result["width"], "height": result["height"]}]

    def _h_audio_analyze(self, proj: Project, p: dict) -> list[dict]:
        """分析音频（经 execute 调用时的薄封装；HTTP 端点直接调 service.audio_analyze）。

        返回 [{"type":"audio_analysis","change":"analyzed","result":{...}}]。
        """
        audio_path = p.get("audioPath")
        if not audio_path:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "audio.analyze requires 'audioPath'")
        result = self.audio_analyze(audio_path)
        return [{"type": "audio_analysis", "change": "analyzed", "result": result}]

    def _h_caption_auto_segment(self, proj: Optional[Project], p: dict) -> list[dict]:
        """J09 智能字幕（基础版）：静音切分生成占位字幕。

        payload: {audioPath, minSilence?, maxWords?, style?, applyToProject?, projectId?}
        - 经 core.auto_caption.segment_audio 用 ffmpeg silencedetect 切出有声段。
        - 返回 proposed 字幕列表（segments，含 Rational 序列化的 start/end）。
        - applyToProject=true 时，对 projectId 工程**复用既有 caption.add 逻辑**
          逐块落字幕（每块走一次完整 execute：revision/undo/持久一致），不重复实现。
        """
        from . import auto_caption

        audio_path = p.get("audioPath")
        if not audio_path:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption.autoSegment requires 'audioPath'")
        min_silence = float(p.get("minSilence", 0.35))
        max_words = p.get("maxWords")
        style = p.get("style") or {}
        apply = bool(p.get("applyToProject", False))
        project_id = p.get("projectId")

        try:
            result = auto_caption.segment_audio(
                audio_path, min_silence=min_silence, max_words=max_words)
        except FileNotFoundError as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT, str(e)) from e
        except Exception as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"caption.autoSegment failed: {e}") from e

        applied: list[dict] = []
        if apply:
            if not project_id:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "caption.autoSegment applyToProject requires 'projectId'")
            # 复用 caption.add：每块一次 execute，保证 revision/undo/持久一致
            for cap in result["captions"]:
                block = {
                    "text": cap["text"],
                    "start": cap["start"].to_json(),
                    "end": cap["end"].to_json(),
                }
                block.update(style)  # 样式字段透传给 caption.add
                res = self.execute(Command(
                    type="caption.add", payload=block, project_id=project_id,
                    actor=Actor("autoSegment", "worker"),
                    expected_revision=self.get_project_revision(project_id)))
                applied.extend(res.changed_entities)

        return [{
            "type": "autoSegment", "change": "analyzed",
            "duration": result["duration"],
            "silenceCount": result["silenceCount"],
            "segmentCount": result["segmentCount"],
            "segments": result["segments"],
            "appliedCount": len(applied),
            "applied": applied,
        }]

    def _h_caption_transcribe(self, proj: Project, p: dict) -> list[dict]:
        """Real local ASR proposal; caption.bulkAdd applies reviewed text later."""
        from .speech_recognition import SpeechRecognitionError, transcribe_clip

        clip_id = p.get("clipId")
        if not isinstance(clip_id, str) or not clip_id:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption.transcribe requires 'clipId'")
        try:
            result = transcribe_clip(proj, clip_id,
                                     model_size=p.get("model", "base"),
                                     language=p.get("language", "auto"))
        except SpeechRecognitionError as exc:
            raise EditError(ErrorCode.INVALID_ARGUMENT, str(exc)) from exc
        return [{"type": "transcript", "change": "recognized", **result}]

    def _h_audio_split_sentences(self, proj: Optional[Project], p: dict) -> list[dict]:
        """J09 辅助：按静音点把音频切成多个有声片段（文本剪辑素材准备）。

        payload: {audioPath, minSilence?, outDir?}
        - 复用 core.auto_caption.segment_audio 的切分结果（同一 silencedetect 参数）。
        - 用 ffmpeg -ss/-to 重编码（pcm_s16le）精确切出每段到 outDir/segment_NNN.wav。
        - 纯写文件、不改工程；非改工程命令。
        """
        from . import auto_caption
        from .render import DEFAULT_FFMPEG

        audio_path = p.get("audioPath")
        if not audio_path:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "audio.splitSentences requires 'audioPath'")
        min_silence = float(p.get("minSilence", 0.35))
        out_dir = p.get("outDir") or tempfile.mkdtemp(prefix="cutvoke_seg_")
        os.makedirs(out_dir, exist_ok=True)

        try:
            result = auto_caption.segment_audio(
                audio_path, min_silence=min_silence)
        except FileNotFoundError as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT, str(e)) from e
        except Exception as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"audio.splitSentences failed: {e}") from e

        segments_out = []
        for cap in result["captions"]:
            s = float(cap["start"].to_fraction())
            e = float(cap["end"].to_fraction())
            out_path = os.path.join(out_dir, f"segment_{cap['index']:03d}.wav")
            cmd = [DEFAULT_FFMPEG, "-y", "-i", audio_path,
                   "-ss", f"{s:.6f}", "-to", f"{e:.6f}",
                   "-acodec", "pcm_s16le", "-ar", "44100", out_path]
            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            except subprocess.TimeoutExpired as ex:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"audio.splitSentences ffmpeg timeout: {ex}") from ex
            segments_out.append({
                "index": cap["index"],
                "path": out_path,
                "start": cap["start"].to_json(),
                "end": cap["end"].to_json(),
            })

        return [{
            "type": "splitSentences", "change": "split",
            "outDir": out_dir,
            "count": len(segments_out),
            "segments": segments_out,
        }]

    # ------------------------------------------------------------------
    # J05 声音创作：纯 ffmpeg 真实音频处理（去噪 / 人声增强 / 响度标准化）
    # 文件级处理，不改工程；非改工程命令（经 _execute_nonmutating，proj 传 None）。
    # ------------------------------------------------------------------
    def _h_audio_denoise(self, proj: Optional[Project], p: dict) -> list[dict]:
        """去噪（anlmdn）。返回 [{"type":"audio_denoise","change":"denoised",...}]。"""
        from . import audio_tools

        audio_path = p.get("audioPath")
        if not audio_path:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "audio.denoise requires 'audioPath'")
        out_path = p.get("outPath")
        strength = float(p.get("strength", 0.1))
        try:
            result = audio_tools.denoise_audio(
                audio_path, out_path=out_path, strength=strength)
        except Exception as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"audio.denoise failed: {e}") from e
        return [{"type": "audio_denoise", "change": "denoised",
                 "outPath": result["outPath"], "duration": result["duration"],
                 "strength": strength}]

    def _h_audio_vocal_enhance(self, proj: Optional[Project], p: dict) -> list[dict]:
        """人声增强 / 去伴奏（中置 / 侧声道近似）。"""
        from . import audio_tools

        audio_path = p.get("audioPath")
        if not audio_path:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "audio.vocalEnhance requires 'audioPath'")
        out_path = p.get("outPath")
        amount = float(p.get("amount", 0.5))
        mode = p.get("mode", "vocal")
        try:
            result = audio_tools.vocal_enhance_audio(
                audio_path, out_path=out_path, amount=amount, mode=mode)
        except Exception as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"audio.vocalEnhance failed: {e}") from e
        return [{"type": "audio_vocalEnhance", "change": "enhanced",
                 "outPath": result["outPath"], "duration": result["duration"],
                 "mode": result["mode"], "amount": amount}]

    def _h_audio_normalize(self, proj: Optional[Project], p: dict) -> list[dict]:
        """响度标准化（loudnorm 双遍）。"""
        from . import audio_tools

        audio_path = p.get("audioPath")
        if not audio_path:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "audio.normalize requires 'audioPath'")
        out_path = p.get("outPath")
        I = float(p.get("I", -16.0))
        try:
            result = audio_tools.normalize_audio(
                audio_path, out_path=out_path, I=I)
        except Exception as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"audio.normalize failed: {e}") from e
        return [{"type": "audio_normalize", "change": "normalized",
                 "outPath": result["outPath"], "duration": result["duration"],
                 "targetI": result["targetI"], "measuredI": result["measuredI"]}]

    def _h_media_stabilize(self, proj: Optional[Project], p: dict) -> list[dict]:
        """视频稳定（J06）：vidstab 两遍或 deshake 单遍，文件级输出。"""
        from . import media_tools

        video_path = p.get("videoPath")
        if not video_path:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "media.stabilize requires 'videoPath'")
        method = str(p.get("method", "vidstab"))
        try:
            result = media_tools.stabilize_video(
                video_path, out_path=p.get("outPath"), method=method)
        except Exception as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"media.stabilize failed: {e}") from e
        return [{"type": "media_stabilize", "change": "stabilized",
                 "outPath": result["outPath"],
                 "duration": result["duration"],
                 "width": result["width"], "height": result["height"]}]

    def _h_media_enhance(self, proj: Optional[Project], p: dict) -> list[dict]:
        """画质增强（J06）：unsharp 锐化 + hqdn3d 降噪，文件级输出。"""
        from . import media_tools

        video_path = p.get("videoPath")
        if not video_path:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "media.enhance requires 'videoPath'")
        try:
            result = media_tools.enhance_video(
                video_path, out_path=p.get("outPath"),
                sharpness=float(p.get("sharpness", 1.0)),
                denoise=float(p.get("denoise", 0.0)))
        except Exception as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"media.enhance failed: {e}") from e
        return [{"type": "media_enhance", "change": "enhanced",
                 "outPath": result["outPath"],
                 "duration": result["duration"],
                 "width": result["width"], "height": result["height"]}]

    def _h_audio_record(self, proj: Optional[Project], p: dict) -> list[dict]:
        """录音（J05）：麦克风采集到 wav；非 Windows 用 lavfi 音源替代。"""
        from . import audio_tools

        out_path = p.get("outPath")
        if not out_path:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "audio.record requires 'outPath'")
        try:
            result = audio_tools.record_audio(
                out_path, seconds=float(p.get("seconds", 5.0)),
                device=p.get("device"))
        except Exception as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"audio.record failed: {e}") from e
        return [{"type": "audio_record", "change": "recorded",
                 "outPath": result["outPath"],
                 "duration": result["duration"]}]

    def _h_audio_export(self, proj: Project, p: dict) -> list[dict]:
        """V04 独立声音输出：把工程混音后的音轨导出为 m4a/aac。

        {outPath, overwrite?}；工程无音轨抛 INVALID_ARGUMENT。
        复用 RenderService.render_audio_only（filter graph 只 map [outa]）。
        """
        out_path = p.get("outPath")
        if not out_path:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "audio.export requires 'outPath'")
        from .render import RenderService

        render = RenderService()
        try:
            result = render.render_audio_only(
                proj, os.path.abspath(out_path),
                overwrite=bool(p.get("overwrite", False)))
        except EditError:
            raise
        except Exception as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"audio.export failed: {e}") from e
        return [{"type": "audio_export", "change": "exported",
                 "outPath": result["output_path"],
                 "duration": result["duration"],
                 "hasAudio": result["has_audio"]}]

    def _h_project_preflight(self, proj: Project, p: dict) -> list[dict]:
        from .project_health import project_preflight
        output_range = (self._validated_export_range(proj, p["range"])
                        if p.get("range") is not None else None)
        return [{"type": "project_preflight",
                 "report": project_preflight(proj, output_range=output_range)}]

    def _h_export_video(self, proj: Project, p: dict) -> list[dict]:
        """V01 视频导出（带预设/码率 / V05 透明通道 / V01 区间导出）：
        {outPath, quality?('high'|'medium'|'low'), videoBitrateKbps?,
         audioBitrateKbps?, overwrite?, transparent?, range?}。

        range={start,end}（秒，十进制字符串或数字）：只导出时间线 [start,end)
        区间，输出时长 = end - start。区间全部落在工程时间线 [0, total] 内；
        越界 / start >= end / 负数 → INVALID_ARGUMENT，绝不静默裁剪成一个
        看似正常的成片。缺省导出全片。

        transparent=true 时导出**带 alpha 的 ProRes 4444(.mov)**：
        底为全透明画布，抠像/留白区域真的透明（V05）。非 .mov 后缀会
        明确报错，绝不产出"看起来透明其实不透明"的文件。
        """
        out_path = p.get("outPath")
        if not out_path:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "export.video requires 'outPath'")
        quality = str(p.get("quality", "high"))
        if quality not in ("high", "medium", "low"):
            quality = "high"
        transparent = bool(p.get("transparent", False))
        # O06 色彩管理（导出级）：未知枚举直接拒绝，不静默忽略。
        # render 是惰性导入（避免模块环），这里一并取常量表做校验。
        from .render import (COLOR_SPACE_TRIPLE, HDR_TRANSFER as _HDR,
                             TONEMAP_ALGORITHMS as _TM_ALGOS)
        color_space = p.get("colorSpace")
        source_color_space = p.get("sourceColorSpace")
        tone_map = p.get("toneMap")
        source_transfer = p.get("sourceTransfer")
        if color_space is not None and color_space not in COLOR_SPACE_TRIPLE:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                f"colorSpace must be one of {sorted(COLOR_SPACE_TRIPLE)}, "
                f"got {color_space!r}")
        if source_color_space is not None and \
                source_color_space not in COLOR_SPACE_TRIPLE:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                f"sourceColorSpace must be one of {sorted(COLOR_SPACE_TRIPLE)}, "
                f"got {source_color_space!r}")
        if tone_map is not None and \
                str(tone_map).strip().lower() not in _TM_ALGOS:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                f"toneMap must be one of {sorted(_TM_ALGOS)}, "
                f"got {tone_map!r}")
        if source_transfer is not None and \
                str(source_transfer).strip().lower() not in _HDR:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                f"sourceTransfer must be one of {sorted(_HDR)}, "
                f"got {source_transfer!r}")

        rng = p.get("range")
        output_range = (self._validated_export_range(proj, rng)
                        if rng is not None else None)

        from .render import RenderService

        render = RenderService()
        try:
            render_options = dict(
                quality=quality,
                overwrite=bool(p.get("overwrite", False)),
                video_bitrate_kbps=(int(p["videoBitrateKbps"])
                                    if p.get("videoBitrateKbps") else None),
                audio_bitrate_kbps=(int(p["audioBitrateKbps"])
                                    if p.get("audioBitrateKbps") else None),
                alpha=transparent,
                color_space=color_space,
                source_color_space=source_color_space,
                tone_map=tone_map,
                source_transfer=source_transfer)
            if output_range is not None:
                start, end = output_range
                render_options.update(output_start=start,
                                      output_duration=end - start)
            result = render.render(proj, os.path.abspath(out_path),
                                   **render_options)
        except EditError:
            raise
        except Exception as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"export.video failed: {e}") from e
        return [{"type": "video_export", "change": "exported",
                 "outPath": result["output_path"],
                 "duration": result["duration"],
                 "width": result["width"], "height": result["height"],
                 "hasAudio": result["has_audio"],
                 "hasAlpha": result.get("has_alpha", False),
                 "pixFmt": result.get("pix_fmt")}]

    @staticmethod
    def _parse_export_range(rng: dict) -> tuple["Rational", "Rational"]:
        """解析并校验 V01 区间导出的 {start,end}（秒，十进制字符串或数字）。

        返回 (start, end) 两个 Rational。越界 / start >= end / 负数 → 拒绝。
        """
        if not isinstance(rng, dict) or "start" not in rng or "end" not in rng:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                "export.video range requires {start, end} (seconds)")
        try:
            if isinstance(rng["start"], bool) or isinstance(rng["end"], bool):
                raise ValueError("boolean is not a time")
            start = Rational.from_float(float(rng["start"]))
            end = Rational.from_float(float(rng["end"]))
            if not math.isfinite(float(rng["start"])) or \
                    not math.isfinite(float(rng["end"])):
                raise ValueError("time must be finite")
        except (TypeError, ValueError, OverflowError):
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                f"export.video range must be numeric seconds, got {rng!r}")
        zero = Rational.of(0, 1)
        if start < zero or end <= zero:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                f"export.video range must be >= 0 (got start={start}, end={end})")
        if end <= start:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                f"export.video range end must be > start (got start={start}, end={end})")
        return start, end

    @staticmethod
    def _validated_export_range(
            proj: Project, rng: dict) -> tuple[float, float]:
        """Validate and normalize a timeline range for final-output trimming."""
        start, end = EditService._parse_export_range(rng)
        total = max(
            (clip.timeline_end
             for track in proj.sequence.tracks
             for clip in track.clips),
            default=Rational.of(0, 1),
        )
        if start >= total:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                f"export.video range start ({start}) is beyond the "
                f"timeline (total={total}); out of bounds")
        if end > total:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                f"export.video range end ({end}) is beyond the "
                f"timeline (total={total}); out of bounds")
        return float(start.to_fraction()), float(end.to_fraction())

    def _h_export_still(self, proj: Project, p: dict) -> list[dict]:
        """V03 静帧/封面导出（可透明）：{outPath, timelineTime?, transparent?}。

        与 export.cover 的区别：恒定走完整编译图（效果/转场/字幕齐全），
        transparent=true 时导出**真带 alpha 的 PNG**（V05 的透明画布语义），
        抠像/留白区域是透明像素而不是黑块。
        """
        out_path = p.get("outPath")
        if not out_path:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "export.still requires 'outPath'")
        t = 0.0 if p.get("timelineTime") is None else float(p["timelineTime"])
        if t < 0:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "export.still timelineTime must be >= 0")
        transparent = bool(p.get("transparent", False))
        from .render import RenderService

        render = RenderService()
        try:
            result = render.extract_still(
                proj, t, os.path.abspath(out_path), alpha=transparent)
        except EditError:
            raise
        except Exception as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"export.still failed: {e}") from e
        return [{"type": "still_export", "change": "exported",
                 "outPath": result["output_path"],
                 "timelineTime": t,
                 "width": result["width"], "height": result["height"],
                 "hasAlpha": result["has_alpha"],
                 "pixFmt": result["pix_fmt"]}]

    def _h_media_video2gif(self, proj, p: dict) -> list[dict]:
        """视频转动图（V03）：mp4 → gif，文件级输出。"""
        from . import media_tools

        video_path = p.get("videoPath")
        if not video_path:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "media.video2gif requires 'videoPath'")
        try:
            result = media_tools.video2gif(
                video_path, out_path=p.get("outPath"),
                fps=int(p.get("fps", 10)))
        except Exception as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"media.video2gif failed: {e}") from e
        return [{"type": "media_video2gif", "change": "converted",
                 "outPath": result["outPath"]}]

    def _h_media_gif2video(self, proj, p: dict) -> list[dict]:
        """动图转视频（A03）：gif → mp4，文件级输出。"""
        from . import media_tools

        gif_path = p.get("gifPath")
        if not gif_path:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "media.gif2video requires 'gifPath'")
        try:
            result = media_tools.gif2video(gif_path, out_path=p.get("outPath"))
        except Exception as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"media.gif2video failed: {e}") from e
        return [{"type": "media_gif2video", "change": "converted",
                 "outPath": result["outPath"], "duration": result["duration"]}]

    def _h_clip_keyframe(self, proj: Project, p: dict) -> list[dict]:
        """增/删片段参数关键帧（P3 动画 D07）。

        add: {clipId, action='add', param, time, value, interpolation?}
        batch: {clipId, action='batch', keyframes:[{param,time,value,interpolation?}]}
        remove: {clipId, action='remove', param, keyframeId}
        time 为片段局部呈现时间（秒数或 {num,den}）。
        """
        clip_id = p["clipId"]
        param = p.get("param", "")
        action = p.get("action", "add")
        supported_params = {"opacity", "x", "y", "scale", "rotation"}

        def parse_add(item: dict) -> tuple[str, Rational, float, str]:
            key_param = item.get("param", "")
            if not key_param:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "clip.keyframe requires 'param'")
            if key_param not in supported_params:
                raise EditError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"unsupported keyframe parameter: {key_param}; "
                    f"available: {', '.join(sorted(supported_params))}")
            if "time" not in item or "value" not in item:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "keyframe add requires 'time' and 'value'")
            t = (Rational.from_json(**item["time"]) if isinstance(item["time"], dict)
                 else Rational.from_float(float(item["time"])))
            interp = item.get("interpolation", "linear")
            if interp not in Keyframe.VALID_INTERP:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"invalid interpolation: {interp}")
            try:
                value = float(item["value"])
            except (TypeError, ValueError) as error:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "keyframe value must be a finite number") from error
            if not math.isfinite(value):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "keyframe value must be a finite number")
            if key_param == "opacity" and not 0.0 <= value <= 1.0:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "opacity keyframe value must be in [0, 1]")
            if key_param == "scale" and not 0.05 <= value <= 5.0:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "scale keyframe value must be in [0.05, 5]")
            if key_param == "rotation" and not -180.0 <= value <= 180.0:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "rotation keyframe value must be in [-180, 180]")
            return key_param, t, value, interp

        for track in proj.sequence.tracks:
            for clip in track.clips:
                if clip.id != clip_id:
                    continue
                if action == "remove":
                    if not param:
                        raise EditError(ErrorCode.INVALID_ARGUMENT,
                                        "clip.keyframe requires 'param'")
                    kfs = clip.keyframes.setdefault(param, [])
                    kid = p.get("keyframeId")
                    if not kid:
                        raise EditError(ErrorCode.INVALID_ARGUMENT,
                                        "keyframe remove requires 'keyframeId'")
                    before = len(kfs)
                    clip.keyframes[param] = [k for k in kfs if k.id != kid]
                    if len(clip.keyframes[param]) == before:
                        raise EditError(ErrorCode.INVALID_ARGUMENT,
                                        f"keyframe not found: {kid}")
                    return [{"type": "clip", "id": clip_id, "change": "updated"}]
                if action == "batch":
                    raw_updates = p.get("keyframes")
                    if not isinstance(raw_updates, list) or not raw_updates:
                        raise EditError(ErrorCode.INVALID_ARGUMENT,
                                        "keyframe batch requires a non-empty 'keyframes' list")
                    # Validate every item before changing the project so one bad frame
                    # cannot leave a partially applied pointer gesture.
                    updates = [parse_add(item) for item in raw_updates
                               if isinstance(item, dict)]
                    if len(updates) != len(raw_updates):
                        raise EditError(ErrorCode.INVALID_ARGUMENT,
                                        "keyframe batch items must be objects")
                else:
                    updates = [parse_add(p)]
                for key_param, t, value, interp in updates:
                    kfs = clip.keyframes.setdefault(key_param, [])
                    existing = next((keyframe for keyframe in kfs
                                     if keyframe.time.to_fraction() == t.to_fraction()), None)
                    if existing is not None:
                        existing.value = value
                        existing.interpolation = interp
                    else:
                        kfs.append(Keyframe(id=new_id("kf"), time=t, value=value,
                                            interpolation=interp))
                return [{"type": "clip", "id": clip_id, "change": "updated"}]
        raise EditError(ErrorCode.INVALID_ARGUMENT, f"clip not found: {clip_id}")

    def _h_clip_split(self, proj: Project, p: dict) -> list[dict]:
        """在指定时间点分割片段为两个。"""
        clip_id = p["clipId"]
        at = Rational.from_json(**p["at"])
        for track in proj.sequence.tracks:
            for idx, clip in enumerate(track.clips):
                if clip.id == clip_id:
                    if at <= clip.timeline_start or at >= clip.timeline_end:
                        raise EditError(ErrorCode.INVALID_ARGUMENT,
                                        f"split point {at} outside clip [{clip.timeline_start},{clip.timeline_end})")
                    left = clip
                    if clip.speed_curve is not None:
                        old_start, old_end = clip.timeline_start, clip.timeline_end
                        right = copy.deepcopy(clip)
                        right.id = f"{clip_id}_r"
                        self._crop_curve_clip(right, at, old_end)
                        self._crop_curve_clip(left, old_start, at)
                        if left.frame_interpolation == "motion" and not left.has_slow_motion:
                            left.frame_interpolation = "none"
                        if right.frame_interpolation == "motion" and not right.has_slow_motion:
                            right.frame_interpolation = "none"
                    else:
                        old_start, old_end = clip.timeline_start, clip.timeline_end
                        # 源时间偏移 = 分割点对应素材位置
                        source_at = clip.source_start + (at - clip.timeline_start) * clip.speed
                        right = Clip(
                            id=f"{clip_id}_r",
                            asset_ref=clip.asset_ref,
                            timeline_start=at,
                            timeline_end=clip.timeline_end,
                            source_start=source_at,
                            speed=clip.speed,
                            preserve_pitch=clip.preserve_pitch,
                            effects=copy.deepcopy(clip.effects),
                            linked=clip.linked,
                            attached_to_clip_id=clip.attached_to_clip_id,
                            hidden=clip.hidden,
                            keyframes={name: list(kfs)
                                      for name, kfs in clip.keyframes.items()},
                            volume=clip.volume,
                            fade_in=clip.fade_in,
                            fade_out=clip.fade_out,
                            pitch=clip.pitch,
                            freeze_at=clip.freeze_at,
                            nested=clip.nested,
                            role=clip.role,
                            frame_interpolation=clip.frame_interpolation,
                        )
                        left.timeline_end = at
                        self._rebase_effect_ranges(right, old_start, old_end, at, old_end)
                        self._rebase_effect_ranges(left, old_start, old_end, old_start, at)
                    track.clips.insert(idx + 1, right)
                    changed = [{"type": "clip", "id": clip_id, "change": "updated"},
                               {"type": "clip", "id": right.id, "change": "created"}]
                    if track.kind == "video" and track.role != "sticker":
                        for lane in proj.sequence.tracks:
                            for child in lane.clips:
                                if (child.attached_to_clip_id == clip_id and
                                        child.timeline_start >= at):
                                    child.attached_to_clip_id = right.id
                                    changed.append({"type": "clip", "id": child.id,
                                                    "change": "updated", "reason": "anchor_split"})
                    return changed
        raise EditError(ErrorCode.INVALID_ARGUMENT, f"clip not found: {clip_id}")

    def _h_clip_remove(self, proj: Project, p: dict) -> list[dict]:
        clip_id = p["clipId"]
        for track in proj.sequence.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    track.clips.remove(clip)
                    changed = [{"type": "clip", "id": clip_id, "change": "deleted"}]
                    for lane in proj.sequence.tracks:
                        for child in lane.clips:
                            if child.attached_to_clip_id == clip_id:
                                child.attached_to_clip_id = None
                                changed.append({"type": "clip", "id": child.id,
                                                "change": "updated", "reason": "anchor_removed"})
                    return changed
        raise EditError(ErrorCode.INVALID_ARGUMENT, f"clip not found: {clip_id}")

    # ------------------------------------------------------------------
    # V02 导出队列命令（队列单例挂在 EditService，见 _get_export_queue）
    # ------------------------------------------------------------------
    def _h_export_enqueue(self, proj: Project, p: dict, *, command: Optional[Command] = None) -> list[dict]:
        """export.enqueue：非阻塞入队，返回 enqueued 实体（含 jobId/status）。

        不修改工程（只入队 + 落账本），故走 _execute_nonmutating。
        """
        out_path = p.get("outPath")
        if not out_path:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "export.enqueue requires 'outPath'")
        queue_payload = dict(p)
        if p.get("range") is not None:
            start, end = self._validated_export_range(proj, p["range"])
            queue_payload["range"] = {"start": start, "end": end}
        queue = self._get_export_queue()
        job = queue.submit(proj, queue_payload,
                           command_id=command.command_id if command else None,
                           command_hash=self._request_hash(command) if command else None,
                           expected_revision=command.expected_revision if command else None)
        return [{"type": "export_enqueued", "jobId": job["jobId"],
                 "status": job["status"], "outPath": job["outPath"],
                 "versioned": bool(job.get("versioned", False))}]

    def _h_export_cancel(self, proj: Project, p: dict) -> list[dict]:
        """export.cancel：取消任务。

        - 取消成功 → {type:export_cancelled, jobId, cancelled:true, status}
        - 已完成/已取消 → cancelled:false 且 message 含 "already <status>"
          （绝不假装取消成功）
        - 任务不在本进程队列（如重启遗留，账本里有但队列内存里无）→ 抛
          NOT_CANCELLABLE（HTTP 层转 409）
        - 完全未知 job（账本也无）→ 抛 NOT_FOUND（HTTP 层转 404）
        """
        job_id = p.get("jobId")
        if not job_id:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "export.cancel requires 'jobId'")
        queue = self._get_export_queue()
        try:
            r = queue.cancel(job_id)
        except KeyError as e:
            msg = str(e)
            if "unknown job" in msg:
                raise EditError("NOT_FOUND", msg)
            raise EditError("NOT_CANCELLABLE", msg)
        return [{"type": "export_cancelled", "jobId": job_id,
                 "cancelled": bool(r.get("cancelled", False)),
                 "status": r.get("status"),
                 "message": r.get("message", "")}]

    def _h_export_jobs(self, proj: Project, p: dict) -> list[dict]:
        """export.jobs：以账本为准的非阻塞只读查询（队列每次状态迁移都落账）。

        参数 projectId/status/limit 过滤；返回 {type:export_jobs, jobs}。
        """
        queue = self._get_export_queue()
        project_id = p.get("projectId") or None
        status = p.get("status") or None
        limit = p.get("limit")
        jobs = queue.list(project_id, status)
        if limit is not None:
            try:
                jobs = jobs[:int(limit)]
            except (TypeError, ValueError):
                pass
        return [{"type": "export_jobs", "jobs": jobs}]

    # ------------------------------------------------------------------
    # J12 工程模板库（声明式：模板是 JSON 数据，不是代码）
    # ------------------------------------------------------------------
    def _h_template_list(self, proj: Project, p: dict) -> list[dict]:
        """template.list：列出内置模板（非阻塞只读，不需要工程）。

        坏模板不隐藏：skipped 一并回报，便于发现写坏的 JSON。
        """
        specs, skipped = load_templates()
        category = p.get("category") or None
        items = [s.to_dict() for s in specs]
        if category:
            items = [i for i in items if i["category"] == category]
        return [{"type": "template_list", "templates": items,
                 "count": len(items), "skipped": skipped}]

    def _resolve_template_source(self, value: str) -> dict:
        """把 sources 里的一项解析成 {assetId, sourcePath}。

        绝对路径优先（存在即用）；否则当作 assetId 查素材账本。
        两者都不成立 → INVALID_ARGUMENT（绝不静默换成占位素材冒充成功）。
        """
        val = value.strip()
        looks_like_path = os.path.isabs(val) or ("/" in val) or ("\\" in val)
        if looks_like_path:
            if not os.path.isfile(val):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"模板素材文件不存在: {val}")
            return {"assetId": "", "sourcePath": os.path.normpath(val)}
        if self._store is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"无持久层，无法按 assetId 解析模板素材: {val}")
        asset = self._store.get_asset(val)
        if asset is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"未知素材 assetId: {val}")
        return {"assetId": asset.get("id") or asset.get("assetId") or val,
                "sourcePath": asset.get("path") or ""}

    def _timeline_end_seconds(self, proj: Project) -> float:
        """现有内容的时间线末尾（秒）。用于「追加」模式避免与既有片段重叠。"""
        end = Rational.of(0, 1)
        for track in proj.sequence.tracks:
            for clip in track.clips:
                if clip.timeline_end > end:
                    end = clip.timeline_end
        frac = end.to_fraction()
        return frac.numerator / frac.denominator

    def _template_effect(self, fx: dict) -> dict:
        """按效果清单校验模板里的效果参数（未知效果/越界参数 → 明确报错）。"""
        effect_id = fx["effectId"]
        try:
            spec = self._effects.get(effect_id)
            params = self._effects.validate_params(effect_id, fx.get("params") or {})
        except EffectNotFound as e:
            raise EditError(ErrorCode.EFFECT_UNAVAILABLE, str(e)) from e
        except EffectParamInvalid as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT, str(e)) from e
        return {"effectId": effect_id, "version": spec.version, "params": params}

    def _h_template_apply(self, proj: Project, p: dict) -> list[dict]:
        """template.apply：把模板落成真实的轨道 / 片段 / 字幕 / 背景音乐。

        - 走正常 execute 路径 → 一次 history.undo 可整体回退。
        - clearExisting=false（默认）时追加到现有内容之后（origin=现有末尾），
          避免新片段与既有片段时间线重叠（同轨不重叠是工程不变量）。
        - 未提供素材的槽位用内置背景图占位，并在 changed_entities 里
          如实标 placeholder=true —— 不把占位说成用户素材。
        """
        template_id = p.get("templateId")
        if not template_id:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "template.apply requires 'templateId'")
        spec = get_template(str(template_id))
        if spec is None:
            raise EditError("NOT_FOUND", f"unknown template: {template_id}")

        raw_sources = p.get("sources") or {}
        if not isinstance(raw_sources, dict):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "template.apply 'sources' must be an object")
        resolved: dict[str, dict] = {}
        for key, val in raw_sources.items():
            if not isinstance(val, str) or not val.strip():
                raise EditError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"sources[{key}] 必须是非空字符串（assetId 或绝对路径）")
            resolved[str(key)] = self._resolve_template_source(val)

        clear = bool(p.get("clearExisting", False))
        if clear:
            proj.sequence.tracks = []
            proj.sequence.captions = []

        if p.get("setCanvas", True):
            try:
                width, height = canvas_for_aspect(spec.aspect)
            except ValueError as e:
                raise EditError(ErrorCode.INVALID_ARGUMENT, str(e)) from e
            proj.sequence.width, proj.sequence.height = width, height

        origin = 0.0 if clear else self._timeline_end_seconds(proj)
        try:
            plan = build_plan(spec, resolved, origin=origin)
        except ValueError as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT, str(e)) from e

        # ---- 帧网格吸附（必须做，否则模板可能导出失败）----
        # 模板里的时长是「人写的秒」（音乐卡点 0.75s）。30fps 下那是 22.5 帧，
        # 编码器按帧取整 → 每段 23 帧 × 8 段 = 184 帧 = 6.133s，而时间线声明
        # 6.000s，渲染期时长校验（容差 0.1s）直接判失败。吸附到帧网格后
        # 「时间线声明的时长」与「真实编码帧数」恒等。
        fps_frac = proj.sequence.fps.to_fraction()
        fps_value = fps_frac.numerator / fps_frac.denominator

        def frame_rat(frames: int) -> Rational:
            """帧序 → Rational 秒（frames × den / num）。"""
            return Rational.of(int(frames) * fps_frac.denominator,
                               fps_frac.numerator)

        def clip_frames(c: dict) -> int:
            """片段时长（秒）→ 帧数，至少 1 帧，避免零长片段。"""
            return max(1, int(round((c["end"] - c["start"]) * fps_value)))

        origin_frames = int(round(origin * fps_value))
        # 先算画面总帧数：音频轨必须跟着画面走，各自取整会让音画错位。
        body_frames = 0
        for plan_track in plan["tracks"]:
            if plan_track["kind"] == "video":
                body_frames += sum(clip_frames(c) for c in plan_track["clips"])
        end_frames = origin_frames + body_frames

        changed: list[dict] = []
        for plan_track in plan["tracks"]:
            kind = plan_track["kind"]
            track = Track(id=new_id("trk"), kind=kind)
            cursor = origin_frames
            for c in plan_track["clips"]:
                if kind == "video":
                    start_f = cursor
                    end_f = cursor + clip_frames(c)
                    cursor = end_f
                else:
                    # 音频（垫乐）铺满画面全长，起点与画面一致
                    start_f = origin_frames
                    end_f = max(origin_frames + 1, end_frames)
                clip = Clip(
                    id=new_id("clip"),
                    asset_ref=AssetReference(c["assetId"],
                                             source_path=c["sourcePath"]),
                    timeline_start=frame_rat(start_f),
                    timeline_end=frame_rat(end_f),
                    source_start=_rat_seconds(c["sourceStart"]))
                volume = float(c.get("volume", 1.0))
                if abs(volume - 1.0) > 1e-9:
                    clip.volume = _rat_seconds(volume)
                for fx in c.get("effects") or []:
                    clip.effects.append(self._template_effect(fx))
                track.clips.append(clip)
                changed.append({"type": "clip", "id": clip.id,
                                "change": "created", "trackId": track.id,
                                "slotKey": c["slotKey"],
                                "placeholder": bool(c["placeholder"]),
                                "sourcePath": c["sourcePath"]})
            proj.sequence.tracks.append(track)
            changed.append({"type": "track", "id": track.id,
                            "change": "created", "kind": track.kind})

        for c in plan["captions"]:
            caption_id = new_id("cap")
            caption = Caption(id=caption_id, text=c["text"],
                              start=frame_rat(int(round(c["start"] * fps_value))),
                              end=frame_rat(max(
                                  int(round(c["start"] * fps_value)) + 1,
                                  int(round(c["end"] * fps_value)))))
            _apply_caption_style(caption, c["style"])
            proj.sequence.captions.append(caption)
            changed.append({"type": "caption", "id": caption_id,
                            "change": "created"})

        changed.append({
            "type": "template_applied",
            "templateId": spec.id,
            "name": spec.name,
            "aspect": spec.aspect,
            "canvas": [proj.sequence.width, proj.sequence.height],
            "origin": origin,
            "durationSec": plan["durationSec"],
            "placeholderCount": plan["placeholderCount"],
            "clearedExisting": clear,
        })
        return changed

    # ------------------------------------------------------------------
    # B02 波纹删除 / 关闭间隙（时间线同步，走正常 execute → 可撤销）
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # N01 对齐与分布（空间对齐；只改 transform.position，不动时间线）
    # ------------------------------------------------------------------

    @staticmethod
    def _clip_display_box(clip: Clip, width: int, height: int) -> dict:
        """片段的**显示区域**（画布坐标）。

        语义（与 render.py 的 `_scale_to_canvas` 一致，2026-09-21 定）：
          - `transform.scale` 是**画布占比**（1.0 = 铺满画布）；
          - `transform.position` 是相对画布中心的**像素偏移**；
          - 因此显示区域尺寸 = (W*scale, H*scale)，中心 = (W/2 + pos.x, H/2 + pos.y)。
        对齐/分布操作的就是这个矩形（而不是素材原生尺寸）——这样"居中/贴边"
        对缩放过和没缩放的片段含义一致。
        """
        tr = clip.transform()
        sc = float(tr["scale"])
        w = max(1.0, width * sc)
        h = max(1.0, height * sc)
        cx = width / 2.0 + float(tr["position"]["x"])
        cy = height / 2.0 + float(tr["position"]["y"])
        return {"x": cx - w / 2.0, "y": cy - h / 2.0,
                "w": w, "h": h, "cx": cx, "cy": cy,
                "scale": sc, "rotation": float(tr["rotation"])}

    def _set_clip_position(self, clip: Clip, x: float, y: float) -> None:
        """把片段的 transform.position 设为 (x,y)，无 transform 效果则先加一个。"""
        fx = next((e for e in clip.effects
                   if e.get("effectId") == EFFECT_TRANSFORM), None)
        if fx is None:
            params = self._effects.validate_params(EFFECT_TRANSFORM, {})
            clip.effects.append({"effectId": EFFECT_TRANSFORM,
                                 "version": self._effects.get(EFFECT_TRANSFORM).version,
                                 "params": params})
            fx = clip.effects[-1]
        params = fx.setdefault("params", {})
        params["position"] = {"x": int(round(x)), "y": int(round(y))}

    _ALIGN_VALUES = ("left", "right", "top", "bottom", "hcenter", "vcenter")

    def _h_clip_align(self, proj: Project, p: dict) -> list[dict]:
        """clip.align：把片段在画面上对齐（N01）。

        payload:
          clipIds  必填，要参与对齐的片段 id 数组（>=1）
          align    必填，left|right|top|bottom|hcenter|vcenter
          target   可选，canvas（默认，对齐到工程画布）| first（对齐到 clipIds[0]）

        只改 `transform.position`，**不改时间线时间**。对齐的是片段的**显示区域**
        （见 `_clip_display_box`），对缩放过与未缩放的片段语义一致。
        """
        clip_ids = p.get("clipIds")
        if not isinstance(clip_ids, list) or not clip_ids:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "clip.align requires non-empty 'clipIds'")
        align = str(p.get("align", "")).strip().lower()
        if align not in self._ALIGN_VALUES:
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                f"align must be one of {list(self._ALIGN_VALUES)}, got {align!r}")
        target = str(p.get("target", "canvas")).strip().lower()
        if target not in ("canvas", "first"):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"target must be 'canvas' or 'first', got {target!r}")

        W, H = proj.sequence.width, proj.sequence.height
        clips = [self._clip_by_id(proj, cid) for cid in clip_ids]
        boxes = [self._clip_display_box(c, W, H) for c in clips]

        if target == "canvas":
            ref = {"x": 0.0, "y": 0.0, "w": float(W), "h": float(H)}
        else:
            ref = boxes[0]

        changed: list[dict] = []
        for clip, box in zip(clips, boxes):
            # 目标左上角（按对齐方向解出 position 偏移）
            if align == "left":
                nx = ref["x"]
                ny = box["y"]
            elif align == "right":
                nx = ref["x"] + ref["w"] - box["w"]
                ny = box["y"]
            elif align == "hcenter":
                nx = ref["x"] + (ref["w"] - box["w"]) / 2.0
                ny = box["y"]
            elif align == "top":
                nx = box["x"]
                ny = ref["y"]
            elif align == "bottom":
                nx = box["x"]
                ny = ref["y"] + ref["h"] - box["h"]
            else:  # vcenter
                nx = box["x"]
                ny = ref["y"] + (ref["h"] - box["h"]) / 2.0
            # 左上角 → position 偏移（position 是相对画布中心的偏移）
            px = nx + box["w"] / 2.0 - W / 2.0
            py = ny + box["h"] / 2.0 - H / 2.0
            self._set_clip_position(clip, px, py)
            changed.append({"type": "clip", "id": clip.id, "change": "aligned",
                            "align": align, "target": target,
                            "position": {"x": int(round(px)), "y": int(round(py))}})
        return changed

    def _h_clip_distribute(self, proj: Project, p: dict) -> list[dict]:
        """clip.distribute：让片段在指定轴上**等间距**分布（N01）。

        payload:
          clipIds  必填，>=2
          axis     可选，x（默认）| y

        首尾两个片段的显示区域**保持不动**，中间的按中心等距重排。
        同样只改 `transform.position`。
        """
        clip_ids = p.get("clipIds")
        if not isinstance(clip_ids, list) or len(clip_ids) < 2:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "clip.distribute requires 'clipIds' with >= 2 items")
        axis = str(p.get("axis", "x")).strip().lower()
        if axis not in ("x", "y"):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"axis must be 'x' or 'y', got {axis!r}")

        W, H = proj.sequence.width, proj.sequence.height
        clips = [self._clip_by_id(proj, cid) for cid in clip_ids]
        boxes = [self._clip_display_box(c, W, H) for c in clips]

        # 按该轴上的位置排序，首尾不动，中间等距（间距按中心点均分）
        order = sorted(range(len(clips)), key=lambda i: boxes[i]["cx" if axis == "x" else "cy"])
        first_i, last_i = order[0], order[-1]
        span = (boxes[last_i]["cx" if axis == "x" else "cy"]
                - boxes[first_i]["cx" if axis == "x" else "cy"])
        step = span / (len(order) - 1)

        changed: list[dict] = []
        for rank, i in enumerate(order):
            if rank in (0, len(order) - 1):
                continue  # 首尾保持不动
            target_center = (boxes[first_i]["cx" if axis == "x" else "cy"]
                             + step * rank)
            box = boxes[i]
            if axis == "x":
                px = target_center - W / 2.0
                py = box["cy"] - H / 2.0
            else:
                px = box["cx"] - W / 2.0
                py = target_center - H / 2.0
            self._set_clip_position(clips[i], px, py)
            changed.append({"type": "clip", "id": clips[i].id,
                            "change": "distributed", "axis": axis,
                            "position": {"x": int(round(px)),
                                         "y": int(round(py))}})
        return changed

    def _h_clip_ripple_delete(self, proj: Project, p: dict) -> list[dict]:
        """clip.rippleDelete：删除片段，并把同一轨上起点在其之后的片段整体左移
        左移量 = 被删片段时长，消除删除造成的空洞。

        id 不存在 → NOT_FOUND；缺 clipId → INVALID_ARGUMENT。
        左移后若某片段时间线起点 < 0（理论不会发生，因被删片段起点>=0）→ 拒绝。
        """
        clip_id = p.get("clipId")
        before = {c.id: c.timeline_start for lane in proj.sequence.tracks for c in lane.clips}
        if not clip_id:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "clip.rippleDelete requires 'clipId'")
        for track in proj.sequence.tracks:
            for idx, clip in enumerate(track.clips):
                if clip.id != clip_id:
                    continue
                del_dur = clip.duration
                deleted_end = clip.timeline_end
                track.clips.pop(idx)
                changed = [{"type": "clip", "id": clip_id, "change": "deleted"}]
                # 按时间位置找后续片段，不依赖 clips 列表原有顺序；导入工程或
                # 先插入后删除都可能让旧数据的列表顺序与时间线顺序不同。
                for c in track.clips:
                    if c.timeline_start < deleted_end:
                        continue
                    new_start = c.timeline_start - del_dur
                    if new_start < Rational.of(0, 1):
                        raise EditError(
                            ErrorCode.INVALID_ARGUMENT,
                            "clip.rippleDelete would push a clip to negative "
                            "timeline")
                    old_start = c.timeline_start
                    c.timeline_end = c.timeline_end - del_dur
                    c.timeline_start = new_start
                    changed.append({"type": "clip", "id": c.id,
                                    "change": "moved",
                                    "from": old_start.to_json(),
                                    "to": c.timeline_start.to_json()})
                track.clips.sort(key=lambda current: current.timeline_start)
                for lane in proj.sequence.tracks:
                    for child in lane.clips:
                        if child.attached_to_clip_id == clip_id:
                            child.attached_to_clip_id = None
                            changed.append({"type": "clip", "id": child.id,
                                            "change": "updated", "reason": "anchor_removed"})
                changed.extend(self._follow_attached_clips(proj, before, True))
                return changed
        raise EditError("NOT_FOUND", f"clip not found: {clip_id}")

    def _h_clip_close_gap(self, proj: Project, p: dict) -> list[dict]:
        """clip.closeGap：关闭该轨上片段之间的空隙，使片段紧邻排列（保持各片段
        自身时长与顺序不变）。

        {trackId(必填), afterClipId?, beforeClipId?}：afterClipId 给定时关闭
        其之后的所有空隙。beforeClipId 给定时只关闭该片段前的一处空隙，
        将该片段及后续片段同量左移，保留其余空隙。

        id 不存在 → NOT_FOUND；缺 trackId → INVALID_ARGUMENT。
        左移后若某片段起点 < 0（理论不会发生）→ 拒绝。
        """
        track_id = p.get("trackId")
        before = {c.id: c.timeline_start for lane in proj.sequence.tracks for c in lane.clips}
        if not track_id:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "clip.closeGap requires 'trackId'")
        track = next((t for t in proj.sequence.tracks if t.id == track_id), None)
        if track is None:
            raise EditError("NOT_FOUND", f"track not found: {track_id}")
        after_id = p.get("afterClipId")
        before_id = p.get("beforeClipId")
        if after_id is not None and before_id is not None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "clip.closeGap cannot combine afterClipId and beforeClipId")
        ordered = sorted(track.clips, key=lambda c: c.timeline_start)
        if not ordered:
            return [{"type": "track", "id": track_id, "change": "no_clips"}]
        if before_id is not None:
            target_idx = next((i for i, c in enumerate(ordered)
                               if c.id == before_id), None)
            if target_idx is None:
                raise EditError("NOT_FOUND", f"clip not found: {before_id}")
            boundary = (ordered[target_idx - 1].timeline_end if target_idx
                        else Rational.of(0, 1))
            gap = ordered[target_idx].timeline_start - boundary
            if gap <= Rational.of(0, 1):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "clip.closeGap: no gap before target clip")
            changed: list[dict] = []
            for current in ordered[target_idx:]:
                old_start = current.timeline_start
                current.timeline_start = current.timeline_start - gap
                current.timeline_end = current.timeline_end - gap
                changed.append({"type": "clip", "id": current.id,
                                "change": "moved", "from": old_start.to_json(),
                                "to": current.timeline_start.to_json(),
                                "reason": "closed_gap"})
            changed.extend(self._follow_attached_clips(proj, before, True))
            return changed
        if after_id is not None:
            ai = next((i for i, c in enumerate(ordered) if c.id == after_id), None)
            if ai is None:
                raise EditError("NOT_FOUND", f"clip not found: {after_id}")
            cursor = ordered[ai].timeline_end
            start_idx = ai + 1
        else:
            cursor = ordered[0].timeline_start
            start_idx = 0
        changed: list[dict] = []
        for i, c in enumerate(ordered):
            if i < start_idx:
                continue
            dur = c.duration
            new_start = cursor
            if new_start < Rational.of(0, 1):
                raise EditError(
                    ErrorCode.INVALID_ARGUMENT,
                    "clip.closeGap would push a clip to negative timeline")
            if new_start != c.timeline_start:
                c.timeline_end = new_start + dur
                c.timeline_start = new_start
                cursor = c.timeline_end
                changed.append({"type": "clip", "id": c.id, "change": "moved",
                                "to": new_start.to_json()})
            else:
                cursor = c.timeline_end
        changed.extend(self._follow_attached_clips(proj, before, True))
        return changed

    def _h_clip_duplicate(self, proj: Project, p: dict) -> list[dict]:
        """复制片段：{clipId, newClipId?, timelineStart?}。

        深拷贝源片段（含效果栈/关键帧/变速/联动/显隐），插入同一轨道，
        时间线入点默认紧跟源片段之后（源片段时间线终点），保留源时长。
        """
        clip_id = p["clipId"]
        for track in proj.sequence.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    new_id_ = p.get("newClipId") or new_id("clip")
                    if any(c.id == new_id_ for t in proj.sequence.tracks
                           for c in t.clips):
                        raise EditError(ErrorCode.INVALID_ARGUMENT,
                                        f"clip exists: {new_id_}")
                    duration = clip.duration
                    start = (Rational.from_json(**p["timelineStart"])
                             if "timelineStart" in p else clip.timeline_end)
                    dup = Clip(
                        id=new_id_,
                        asset_ref=AssetReference(
                            clip.asset_ref.asset_id,
                            source_path=clip.asset_ref.source_path,
                            fingerprint=clip.asset_ref.fingerprint),
                        timeline_start=start,
                        timeline_end=start + duration,
                        source_start=clip.source_start,
                        speed=clip.speed,
                        speed_curve=clip.speed_curve,
                        frame_interpolation=clip.frame_interpolation,
                        preserve_pitch=clip.preserve_pitch,
                        effects=[dict(e) for e in clip.effects],
                        linked=clip.linked,
                        attached_to_clip_id=clip.attached_to_clip_id,
                        hidden=clip.hidden,
                        keyframes={k: list(v) for k, v in clip.keyframes.items()},
                        volume=clip.volume,
                        fade_in=clip.fade_in,
                        fade_out=clip.fade_out,
                        pitch=clip.pitch,
                        freeze_at=clip.freeze_at,
                        nested=clip.nested,
                        role=clip.role,
                    )
                    track.clips.append(dup)
                    return [{"type": "clip", "id": new_id_, "change": "created"}]
        raise EditError(ErrorCode.INVALID_ARGUMENT, f"clip not found: {clip_id}")

    def _h_clip_group(self, proj: Project, p: dict) -> list[dict]:
        """把同轨、连续选区的一组片段打包成一个复合片段（J08 高级时间线）。

        payload: {clipIds: [...], groupId?, name?}
          - 选中片段必须全部位于同一条轨道（否则 INVALID_ARGUMENT 跨轨拒绝）
          - 选中片段必须在该轨道上连续（选区跨度内不得夹带未选片段，
            允许片段之间存在时间间隙；否则 INVALID_ARGUMENT 不连续拒绝）
          - 复合 Clip 的 timeline_start = 组内首个片段 start，
            timeline_end = 组内末个片段 end；nested 内建一条同类型轨道，
            各片段相对位置完整保留（子序列时间从 0 起，平移减去组首时间）。
          - 复合 Clip 的 asset_ref 可空（内容在 nested 内）。
        返回新复合片段实体信息。
        """
        clip_ids = p.get("clipIds")
        if not isinstance(clip_ids, list) or not clip_ids:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "clip.group: clipIds must be a non-empty list")
        selected_set = set(clip_ids)

        seq = proj.active_sequence
        # 1) 定位所有选中片段所在轨道（必须同轨）
        found: list[Clip] = []
        src_track: Optional[Track] = None
        for track in seq.tracks:
            for clip in track.clips:
                if clip.id in selected_set:
                    if src_track is not None and src_track is not track:
                        raise EditError(
                            ErrorCode.INVALID_ARGUMENT,
                            "clip.group: clips span multiple tracks "
                            "(cross-track grouping not supported)")
                    src_track = track
                    found.append(clip)
        if src_track is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "clip.group: no selected clips found on any track")
        if len(found) != len(selected_set):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "clip.group: some clipIds not found")

        # 2) 连续选区校验：选区跨度 [span_start, span_end) 内不得夹带未选片段
        span_start = min(c.timeline_start for c in found)
        span_end = max(c.timeline_end for c in found)
        for clip in src_track.clips:
            if clip.id in selected_set:
                continue
            # 未选片段若与选区相交 → 选区不连续
            if clip.timeline_start < span_end and span_start < clip.timeline_end:
                raise EditError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"clip.group: non-contiguous selection "
                    f"(clip {clip.id} lies within the span but is not selected)")

        # 3) 构造复合片段
        group_id = p.get("groupId") or new_id("clip")
        if any(c.id == group_id for t in seq.tracks for c in t.clips):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"clip.group: groupId {group_id} already exists")
        name = p.get("name", "")

        # nested 子序列：沿用活动序列画布/帧率，内部建一条同类型轨道
        nested_seq = Sequence(
            id=f"{group_id}_seq", width=seq.width, height=seq.height, fps=seq.fps,
            name=name,
        )
        nested_track = Track(id=f"{group_id}_track", kind=src_track.kind)
        # 选中片段按 start 升序，平移进子序列（相对位置保留，含间隙）
        for clip in sorted(found, key=lambda c: c.timeline_start):
            inner = Clip(
                id=clip.id,
                asset_ref=AssetReference(
                    clip.asset_ref.asset_id,
                    source_path=clip.asset_ref.source_path,
                    fingerprint=clip.asset_ref.fingerprint),
                timeline_start=clip.timeline_start - span_start,
                timeline_end=clip.timeline_end - span_start,
                source_start=clip.source_start,
                speed=clip.speed,
                speed_curve=clip.speed_curve,
                frame_interpolation=clip.frame_interpolation,
                preserve_pitch=clip.preserve_pitch,
                effects=[dict(e) for e in clip.effects],
                linked=clip.linked,
                hidden=clip.hidden,
                keyframes={k: list(v) for k, v in clip.keyframes.items()},
                volume=clip.volume,
                fade_in=clip.fade_in,
                fade_out=clip.fade_out,
                pitch=clip.pitch,
                nested=clip.nested,
            )
            nested_track.clips.append(inner)
        nested_seq.tracks = [nested_track]

        compound = Clip(
            id=group_id,
            asset_ref=AssetReference(""),  # 复合片段内容在 nested 内，asset_ref 可空
            timeline_start=span_start,
            timeline_end=span_end,
            source_start=Rational.of(0, 1),
            nested=nested_seq,
        )

        # 4) 用复合片段替换原连续块（保持其在轨道中的位置）
        indices = [i for i, c in enumerate(src_track.clips) if c.id in selected_set]
        insert_at = min(indices)
        for c in found:
            src_track.clips.remove(c)
        src_track.clips.insert(insert_at, compound)

        return [{"type": "clip", "id": group_id, "change": "grouped",
                 "trackId": src_track.id, "count": len(found),
                 "nestedTrackId": nested_track.id,
                 "nestedClipIds": [c.id for c in nested_track.clips]}]

    def _h_clip_ungroup(self, proj: Project, p: dict) -> list[dict]:
        """展开复合片段回原组（J08 高级时间线）。

        payload: {clipId}
          - 该 clip 必须带 nested（否则 INVALID_ARGUMENT 非复合拒绝）
          - 删除复合 Clip，把 nested 内各片段按原绝对时间
            （组首时间 + 子序列相对时间）插回同一条轨道。
        返回展开后的片段信息。
        """
        clip_id = p["clipId"]
        seq = proj.active_sequence
        src_track: Optional[Track] = None
        compound: Optional[Clip] = None
        for track in seq.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    src_track, compound = track, clip
                    break
            if src_track is not None:
                break
        if compound is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"clip.ungroup: clip not found: {clip_id}")
        if compound.nested is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"clip.ungroup: clip {clip_id} is not a compound clip")

        group_start = compound.timeline_start
        # 展开 nested 内全部轨道的全部片段，平移回绝对时间，插回原轨
        restored: list[Clip] = []
        for track in compound.nested.tracks:
            for clip in track.clips:
                restored.append(Clip(
                    id=clip.id,
                    asset_ref=AssetReference(
                        clip.asset_ref.asset_id,
                        source_path=clip.asset_ref.source_path,
                        fingerprint=clip.asset_ref.fingerprint),
                    timeline_start=clip.timeline_start + group_start,
                    timeline_end=clip.timeline_end + group_start,
                    source_start=clip.source_start,
                    speed=clip.speed,
                    speed_curve=clip.speed_curve,
                    frame_interpolation=clip.frame_interpolation,
                    preserve_pitch=clip.preserve_pitch,
                    effects=[dict(e) for e in clip.effects],
                    linked=clip.linked,
                    hidden=clip.hidden,
                    keyframes={k: list(v) for k, v in clip.keyframes.items()},
                    volume=clip.volume,
                    fade_in=clip.fade_in,
                    fade_out=clip.fade_out,
                    pitch=clip.pitch,
                    nested=clip.nested,
                ))
        # 按绝对时间排序，保持原顺序插回
        restored.sort(key=lambda c: c.timeline_start)

        remove_idx = next(i for i, c in enumerate(src_track.clips) if c.id == clip_id)
        src_track.clips.remove(compound)
        for clip in restored:
            src_track.clips.insert(remove_idx, clip)
            remove_idx += 1

        return [{"type": "clip", "id": clip_id, "change": "ungrouped",
                 "trackId": src_track.id,
                 "restoredClipIds": [c.id for c in restored]}]

    def _h_effect_add(self, proj: Project, p: dict) -> list[dict]:
        """给片段加效果实例（effectId + version + params）。

        提交阶段就按效果清单校验（AC18/AC19）：
          - 未注册的效果 → EFFECT_UNAVAILABLE，不进工程
          - 参数不符合清单声明的 schema → INVALID_ARGUMENT，不进工程
        参数会按清单 default 补齐后存储，保证"可查询、可组合、可复现"。
        """
        clip_id = p["clipId"]
        effect_id = p["effectId"]
        try:
            spec = self._effects.get(effect_id)
            params = self._effects.validate_params(effect_id, p.get("params", {}))
        except EffectNotFound as e:
            raise EditError(ErrorCode.EFFECT_UNAVAILABLE, str(e)) from e
        except EffectParamInvalid as e:
            raise EditError(ErrorCode.INVALID_ARGUMENT, str(e)) from e
        version = p.get("version") or spec.version
        clip = self._clip_by_id(proj, clip_id)
        target_types = self._effect_target_types(proj, clip)
        if not target_types.intersection(spec.applies_to):
            target_label = self._effect_target_label(target_types)
            allowed = "、".join(spec.applies_to) or "无"
            raise EditError(
                ErrorCode.INVALID_ARGUMENT,
                f"效果 {effect_id} 不适用于{target_label}片段（适用对象：{allowed}）",
            )
        if spec.category == "transition":
            self._require_adjacent_transition_target(proj, clip)
        if effect_id == "cutvoke.text" and any(
                item.get("effectId") == "cutvoke.text" for item in clip.effects):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "文字片段已有文字效果；请编辑属性或应用文字预设替换样式")
        slot = animation_slot(effect_id)
        if slot and any(animation_slot(str(item.get("effectId", ""))) == slot
                        for item in clip.effects):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"{slot}动画已存在；请用 effect.setAnimation 替换该动画槽位")
        clip.effects.append({"effectId": effect_id, "version": version,
                             "params": params})
        # J01 最近使用：把该效果提到最近列表最前（去重，上限 24）
        rec = [e for e in proj.recent_effects if e != effect_id]
        proj.recent_effects = [effect_id] + rec
        del proj.recent_effects[24:]
        return [{"type": "clip", "id": clip_id, "change": "updated",
                 "effectId": effect_id}]

    def _h_effect_remove(self, proj: Project, p: dict) -> list[dict]:
        """移除片段上的效果（按 effectId 删该片段全部实例）。

        payload: {clipId, effectId}
        移除转场 = 该片段回到与前一相邻片段直接拼接（无转场）。
        """
        clip_id = p["clipId"]
        effect_id = p["effectId"]
        for track in proj.sequence.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    before = len(clip.effects)
                    clip.effects = [e for e in clip.effects
                                    if e.get("effectId") != effect_id]
                    if len(clip.effects) == before:
                        raise EditError(
                            ErrorCode.INVALID_ARGUMENT,
                            f"effect {effect_id} not found on clip {clip_id}")
                    return [{"type": "clip", "id": clip_id, "change": "updated"}]
        raise EditError(ErrorCode.INVALID_ARGUMENT, f"clip not found: {clip_id}")

    def _h_effect_copy_visual(self, proj: Project, p: dict) -> list[dict]:
        """Copy a source clip's visual effect stack to many targets atomically.

        The source is read from the candidate project, so the same command works
        inside edit.batch. Audio, transitions, animation, text and transform
        effects stay on their original clips. Preserve filter preset provenance,
        bypass state and effect order along with the edited parameters.
        """
        source_id = p.get("sourceClipId")
        target_ids = p.get("targetClipIds")
        mode = p.get("mode", "replace")
        if not isinstance(source_id, str) or not source_id:
            raise EditError(ErrorCode.INVALID_ARGUMENT, "sourceClipId 必须是非空片段 ID")
        if not isinstance(target_ids, list) or not 1 <= len(target_ids) <= 100 \
                or any(not isinstance(item, str) or not item for item in target_ids) \
                or len(set(target_ids)) != len(target_ids) or source_id in target_ids:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "targetClipIds 必须是 1–100 个不同的目标片段 ID，且不能包含来源")
        if mode not in ("replace", "merge"):
            raise EditError(ErrorCode.INVALID_ARGUMENT, "mode 必须是 replace 或 merge")

        source = self._clip_by_id(proj, source_id)
        if not self._effect_target_types(proj, source).intersection({"video", "image"}):
            raise EditError(ErrorCode.INVALID_ARGUMENT, "来源必须是视频或图片片段")
        source_effects = []
        for effect in source.effects:
            effect_id = self._require_effect(str(effect.get("effectId", "")))
            spec = self._effects.get(effect_id)
            if spec.to_dict()["browseCategory"] not in ("fx", "filter", "color"):
                continue
            item = copy.deepcopy(effect)
            item["params"] = self._effects.validate_params(effect_id, item.get("params"))
            source_effects.append(item)
        if not source_effects:
            raise EditError(ErrorCode.INVALID_ARGUMENT, "来源片段没有可复制的画面效果或调色")

        targets = [self._clip_by_id(proj, item) for item in target_ids]
        for target in targets:
            target_types = self._effect_target_types(proj, target)
            if not target_types.intersection({"video", "image"}):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"目标 {target.id} 必须是视频或图片片段")
            for effect in source_effects:
                spec = self._effects.get(effect["effectId"])
                if not target_types.intersection(spec.applies_to):
                    raise EditError(ErrorCode.INVALID_ARGUMENT,
                                    f"效果 {spec.id} 不适用于目标片段 {target.id}")

        copied_ids = {item["effectId"] for item in source_effects}
        changed = []
        for target in targets:
            if mode == "replace":
                target.effects = [effect for effect in target.effects
                                  if not self._is_visual_effect(str(effect.get("effectId", "")))]
            else:
                target.effects = [effect for effect in target.effects
                                  if effect.get("effectId") not in copied_ids]
            target.effects.extend(copy.deepcopy(source_effects))
            changed.append({"type": "clip", "id": target.id,
                            "change": "effect.visualCopied", "sourceClipId": source_id,
                            "mode": mode, "effectIds": [item["effectId"] for item in source_effects]})
        return changed

    def effect_capabilities(self, lang: str = "zh-CN") -> dict:
        """当前可用的效果清单（UI / CLI / HTTP / MCP 共用的能力查询）。"""
        return self._effects.capabilities(lang)

    # ------------------------------------------------------------------
    # J01 效果栈与资源共同基础（计划 5.1 / 6「效果合成顺序」）
    #   effect.bypass   旁路/恢复（保留在栈里但不参与渲染）
    #   effect.reorder  重排效果栈（决定渲染合成顺序）
    #   resource.*      收藏 / 取消收藏（全部效果可见，收藏用于快速取用）
    #   preset.*        个人预设：保存 / 删除 / 应用
    # ------------------------------------------------------------------
    @staticmethod
    def _clip_by_id(proj: Project, clip_id: str) -> Clip:
        for track in proj.sequence.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return clip
        raise EditError(ErrorCode.INVALID_ARGUMENT, f"clip not found: {clip_id}")

    @staticmethod
    def _effect_target_types(proj: Project, clip: Clip) -> set[str]:
        """Return renderable target kinds before adding a clip effect.

        A video clip may also carry audio, so audio filters remain available there.
        Still images expose only the image path; this turns a silent no-op (for
        example loudness normalisation on a PNG) into an actionable error.
        """
        track = next(
            (item for item in proj.sequence.tracks if any(c.id == clip.id for c in item.clips)),
            None,
        )
        if track is not None:
            if track.kind == "audio":
                return {"audio"}
            if track.kind == "text":
                return {"text"}
        source = clip.asset_ref.source_path.split("?", 1)[0].lower()
        if os.path.splitext(source)[1] in _IMAGE_SUFFIXES:
            return {"image"}
        return {"video", "audio"}

    @staticmethod
    def _effect_target_label(target_types: set[str]) -> str:
        if target_types == {"image"}:
            return "图片"
        if target_types == {"audio"}:
            return "音频"
        if target_types == {"text"}:
            return "文字"
        return "视频"

    @staticmethod
    def _require_adjacent_transition_target(proj: Project, clip: Clip) -> None:
        track = next(t for t in proj.sequence.tracks
                     if any(c.id == clip.id for c in t.clips))
        ordered = sorted(track.clips, key=lambda c: c.timeline_start)
        index = next(i for i, c in enumerate(ordered) if c.id == clip.id)
        if track.kind != "video" or index == 0:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "转场只能应用在视频轨中有前一段的片段上")
        if ordered[index - 1].timeline_end != clip.timeline_start:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "两段之间有空隙，先关闭间隙再应用转场")

    def _require_effect(self, effect_id: str) -> str:
        """校验效果已注册（未注册直接拒绝，不进工程）。"""
        try:
            self._effects.get(effect_id)
        except EffectNotFound as e:
            raise EditError(ErrorCode.EFFECT_UNAVAILABLE, str(e)) from e
        return effect_id

    def _effect_category(self, effect_id: str) -> Optional[str]:
        spec = self._effects.find(effect_id)
        return None if spec is None else spec.category

    def _is_visual_effect(self, effect_id: str) -> bool:
        spec = self._effects.find(effect_id)
        return bool(spec and spec.to_dict()["browseCategory"] in ("fx", "filter", "color"))

    def _h_effect_bypass(self, proj: Project, p: dict) -> list[dict]:
        """旁路/恢复片段上的效果：{clipId, effectId, enabled}。

        旁路的效果仍保留在效果栈里（可查询、可恢复），但不参与渲染——
        满足计划 6「用户能重排、旁路、复用，不会出现暗中覆盖」。
        """
        clip = self._clip_by_id(proj, p["clipId"])
        eid = p["effectId"]
        enabled = bool(p.get("enabled", True))
        hit = 0
        for e in clip.effects:
            if e.get("effectId") == eid:
                e["enabled"] = enabled
                hit += 1
        if not hit:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"effect {eid} not found on clip {clip.id}")
        return [{"type": "clip", "id": clip.id, "change": "effect.bypass",
                 "effectId": eid, "enabled": enabled, "count": hit}]

    def _h_effect_reorder(self, proj: Project, p: dict) -> list[dict]:
        """重排效果栈：{clipId, effectId, toIndex} 或 {clipId, order:[effectId,...]}。

        列表顺序即渲染合成顺序（靠前先应用）。
        """
        clip = self._clip_by_id(proj, p["clipId"])
        order = p.get("order")
        if isinstance(order, list) and order:
            head: list[dict] = []
            for eid in order:
                for e in clip.effects:
                    if e.get("effectId") == eid and not any(h is e for h in head):
                        head.append(e)
            rest = [e for e in clip.effects if not any(h is e for h in head)]
            clip.effects = head + rest
        else:
            eid = p.get("effectId")
            if not eid:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "effect.reorder requires effectId or order")
            idx = next((i for i, e in enumerate(clip.effects)
                        if e.get("effectId") == eid), None)
            if idx is None:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"effect {eid} not found on clip {clip.id}")
            item = clip.effects.pop(idx)
            to_index = max(0, min(int(p.get("toIndex", 0)), len(clip.effects)))
            clip.effects.insert(to_index, item)
        return [{"type": "clip", "id": clip.id, "change": "effect.reorder",
                 "order": [e.get("effectId") for e in clip.effects]}]

    def _h_resource_favorite(self, proj: Project, p: dict) -> list[dict]:
        """收藏一个效果或内置贴纸；工程级，随保存与撤销。"""
        if ("effectId" in p) == ("stickerId" in p):
            raise EditError(ErrorCode.INVALID_ARGUMENT, "provide exactly one of effectId or stickerId")
        if "stickerId" in p:
            eid = str(p["stickerId"])
            if eid not in {item["stickerId"] for item in load_builtin_stickers()}:
                raise EditError(ErrorCode.INVALID_ARGUMENT, f"unknown sticker: {eid}")
        else:
            eid = self._require_effect(p["effectId"])
        if eid not in proj.favorites:
            proj.favorites.append(eid)
        return [{"type": "resource", "id": eid, "change": "favorited",
                 "favorites": list(proj.favorites)}]

    def _h_resource_unfavorite(self, proj: Project, p: dict) -> list[dict]:
        """取消收藏：{effectId|stickerId}（未收藏时幂等成功）。"""
        if ("effectId" in p) == ("stickerId" in p):
            raise EditError(ErrorCode.INVALID_ARGUMENT, "provide exactly one of effectId or stickerId")
        eid = p.get("effectId", p.get("stickerId"))
        if eid in proj.favorites:
            proj.favorites.remove(eid)
        return [{"type": "resource", "id": eid, "change": "unfavorited",
                 "favorites": list(proj.favorites)}]

    def _h_preset_save(self, proj: Project, p: dict) -> list[dict]:
        """保存个人预设：{name, clipId}（采用该片段当前效果栈）或 {name, effects:[...]}。

        预设是「一组效果 + 参数」的可复用组合，AI 与 Web 共用。
        """
        name = str(p.get("name", "")).strip()
        if not name:
            raise EditError(ErrorCode.INVALID_ARGUMENT, "preset.save requires name")
        effects = p.get("effects")
        if not effects:
            if "clipId" not in p:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "preset.save requires effects or clipId")
            clip = self._clip_by_id(proj, p["clipId"])
            effects = [{"effectId": e.get("effectId"),
                        "params": dict(e.get("params", {}) or {})}
                       for e in clip.effects if e.get("effectId")]
        if not effects:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "preset.save: 没有可保存的效果")
        normalized: list[dict] = []
        for e in effects:
            if not isinstance(e, dict) or not e.get("effectId"):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "preset.save: effects 元素需含 effectId")
            eid = self._require_effect(str(e["effectId"]))
            normalized.append({
                "effectId": eid,
                "params": self._effects.validate_params(eid, e.get("params", {})),
            })
        pid = p.get("presetId") or new_id("preset")
        entry = {"id": pid, "name": name, "effects": normalized}
        proj.presets = [x for x in proj.presets if x.get("id") != pid] + [entry]
        return [{"type": "preset", "id": pid, "change": "saved",
                 "name": name, "count": len(normalized)}]

    def _h_preset_delete(self, proj: Project, p: dict) -> list[dict]:
        """删除个人预设：{presetId}。"""
        pid = p["presetId"]
        before = len(proj.presets)
        proj.presets = [x for x in proj.presets if x.get("id") != pid]
        if len(proj.presets) == before:
            raise EditError(ErrorCode.INVALID_ARGUMENT, f"preset not found: {pid}")
        return [{"type": "preset", "id": pid, "change": "deleted"}]

    def _h_preset_apply(self, proj: Project, p: dict) -> list[dict]:
        """应用个人预设到片段：{clipId, presetId, mode?}。

        mode=merge（默认）：追加预设效果，保留片段原有其它效果。
        mode=replace：先移除片段上与预设**同类**（category）的效果，再追加，
        避免同类效果叠加造成「暗中覆盖」。
        """
        clip = self._clip_by_id(proj, p["clipId"])
        pid = p["presetId"]
        preset = next((x for x in proj.presets if x.get("id") == pid), None)
        if preset is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT, f"preset not found: {pid}")
        mode = p.get("mode", "merge")
        if mode not in ("merge", "replace"):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "preset.apply mode 必须是 merge 或 replace")
        effects = list(preset.get("effects", []) or [])
        cats = {self._effect_category(e.get("effectId")) for e in effects}
        cats.discard(None)
        if mode == "replace" and cats:
            clip.effects = [
                e for e in clip.effects
                if self._effect_category(e.get("effectId")) not in cats]
        for e in effects:
            eid = self._require_effect(str(e.get("effectId")))
            spec = self._effects.get(eid)
            clip.effects.append({
                "effectId": eid,
                "version": spec.version,
                "params": self._effects.validate_params(eid, e.get("params", {})),
            })
        return [{"type": "clip", "id": clip.id, "change": "preset.applied",
                 "presetId": pid, "mode": mode, "count": len(effects)}]

    def _require_builtin_preset(self, preset_id: object):
        if not isinstance(preset_id, str) or not preset_id:
            raise EditError(ErrorCode.INVALID_ARGUMENT, "内置预设 ID 不能为空")
        try:
            catalog = PresetCatalog.builtin(self._effects)
        except PresetInvalid as error:
            raise EditError(ErrorCode.EFFECT_UNAVAILABLE,
                            f"内置预设目录不可用：{error}") from error
        preset = next((item for item in catalog.all() if item.id == preset_id), None)
        if preset is None or preset.status == "retired":
            raise EditError(ErrorCode.INVALID_ARGUMENT, f"内置预设不存在或已停用：{preset_id}")
        if preset.download_state != "bundled" or not (
            preset._exists(preset.cover) and preset._exists(preset.motion_preview)
        ):
            raise EditError(ErrorCode.EFFECT_UNAVAILABLE, f"内置预设资源不可用：{preset_id}")
        return preset

    def _h_builtin_preset_apply(self, proj: Project, p: dict) -> list[dict]:
        """Apply a curated stack in one revision and keep its version provenance."""
        clip_id = p.get("clipId")
        if not isinstance(clip_id, str) or not clip_id:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "builtinPreset.apply requires clipId and presetId")
        preset = self._require_builtin_preset(p.get("presetId"))
        if "duration" in p and preset.family != "transition":
            raise EditError(ErrorCode.INVALID_ARGUMENT, "duration 只适用于转场预设")

        clip = self._clip_by_id(proj, clip_id)
        if preset.family == "personFx":
            try:
                from .render import RenderService, _pix_fmt_has_alpha
                media = RenderService().probe_media(clip.asset_ref.source_path)
            except Exception as exc:  # noqa: BLE001 — translate probe errors into an actionable edit error
                raise EditError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"人物特效需要人物抠像后的透明视频；无法读取所选素材格式：{exc}") from exc
            if not _pix_fmt_has_alpha(media.get("pix_fmt")):
                raise EditError(
                    ErrorCode.INVALID_ARGUMENT,
                    "人物特效只适用于保留 Alpha 通道的人物抠像视频；"
                    "请先完成人物抠像并选中生成的人物层")
        if preset.family == "text":
            if "text" not in self._effect_target_types(proj, clip):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "文字预设只能应用到文字轨片段")
            current = next((effect for effect in clip.effects
                            if effect.get("effectId") == "cutvoke.text"), None)
            if current is None:
                raise EditError(ErrorCode.INVALID_ARGUMENT, "文字片段缺少文字内容")
            content = current.get("params", {}).get("content")
            if not isinstance(content, str) or not content.strip():
                raise EditError(ErrorCode.INVALID_ARGUMENT, "文字内容不能为空")
            params = self._effects.validate_params(
                "cutvoke.text", {**preset.params, "content": content})
            current.update({"effectId": "cutvoke.text", "version": preset.effects[0]["version"],
                            "params": params, "presetId": preset.id,
                            "presetVersion": preset.version, "presetPartIndex": 0})
            return [{"type": "clip", "id": clip_id, "change": "builtinPreset.applied",
                     "presetId": preset.id, "presetVersion": preset.version,
                     "effectIds": ["cutvoke.text"]}]
        for index, item in enumerate(preset.effects):
            params = dict(item["params"])
            if index == 0 and "duration" in p:
                duration = p["duration"]
                if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                        or not math.isfinite(duration)):
                    raise EditError(ErrorCode.INVALID_ARGUMENT, "转场时长必须是有限数值")
                params["duration"] = duration
            if index == 0 and preset.family == "transition":
                self._h_effect_set_transition(proj, {
                    "clipId": clip_id, "effectId": item["effectId"], "params": params,
                })
                applied = next(effect for effect in clip.effects
                               if effect["effectId"] == item["effectId"])
            elif animation_slot(item["effectId"]):
                self._h_effect_set_animation(proj, {
                    "clipId": clip_id, "effectId": item["effectId"], "params": params,
                })
                applied = next(effect for effect in clip.effects
                               if effect["effectId"] == item["effectId"])
            else:
                self._h_effect_add(proj, {
                    "clipId": clip_id, "effectId": item["effectId"],
                    "version": item["version"], "params": params,
                })
                applied = clip.effects[-1]
            applied["presetId"] = preset.id
            applied["presetVersion"] = preset.version
            applied["presetPartIndex"] = index
        return [{"type": "clip", "id": clip_id, "change": "builtinPreset.applied",
                 "presetId": preset.id, "presetVersion": preset.version,
                 "effectIds": [item["effectId"] for item in preset.effects]}]

    def _h_caption_add(self, proj: Project, p: dict) -> list[dict]:
        """新增字幕：{captionId?, text, start:{num,den}, end:{num,den}}。

        时间走 Rational，由 protocol 的 {num,den} 构造，不碰浮点。
        """
        if "text" not in p:
            raise EditError(ErrorCode.INVALID_ARGUMENT, "caption.add requires 'text'")
        if "start" not in p or "end" not in p:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption.add requires 'start' and 'end'")
        if not isinstance(p["text"], str) or not p["text"].strip():
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption text must be a non-empty string")
        caption_id = p.get("captionId") or new_id("cap")
        if any(c.id == caption_id for c in proj.sequence.captions):
            raise EditError(ErrorCode.INVALID_ARGUMENT, f"caption exists: {caption_id}")
        start = Rational.from_json(**p["start"])
        end = Rational.from_json(**p["end"])
        words = _parse_caption_words(p.get("words", []), start, end)
        cap = Caption(id=caption_id, text=p["text"], start=start, end=end,
                      words=words)
        _apply_caption_style(cap, p)
        proj.sequence.captions.append(cap)
        return [{"type": "caption", "id": caption_id, "change": "created"}]

    def _h_caption_bulk_add(self, proj: Project, p: dict) -> list[dict]:
        """Commit reviewed ASR captions atomically through the normal edit path."""
        from .speech_recognition import (SpeechRecognitionError, find_source_clip,
                                         source_signature)

        segments = p.get("segments")
        if not isinstance(segments, list) or not 1 <= len(segments) <= 1000:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption.bulkAdd requires 1..1000 segments")
        style = p.get("style", {})
        if not isinstance(style, dict):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption.bulkAdd style must be an object")
        source_clip_id = p.get("sourceClipId")
        if source_clip_id is not None:
            if not isinstance(source_clip_id, str) or not p.get("sourceSignature"):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "caption.bulkAdd requires sourceSignature with sourceClipId")
            try:
                current_signature = source_signature(find_source_clip(proj, source_clip_id))
            except SpeechRecognitionError as exc:
                raise EditError(ErrorCode.INVALID_ARGUMENT, str(exc)) from exc
            if current_signature != p["sourceSignature"]:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "识别来源片段已变化，请重新识别后再应用字幕")
        changed: list[dict] = []
        for segment in segments:
            if not isinstance(segment, dict):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "caption.bulkAdd segments must contain objects")
            block = {**style, "text": segment.get("text"),
                     "start": segment.get("start"), "end": segment.get("end"),
                     "words": segment.get("words", [])}
            changed.extend(self._h_caption_add(proj, block))
        return changed

    def _h_caption_update(self, proj: Project, p: dict) -> list[dict]:
        """修改字幕：{captionId, text?, start?, end?}，缺省字段保持不变。"""
        if "captionId" not in p:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption.update requires 'captionId'")
        cap = next((c for c in proj.sequence.captions if c.id == p["captionId"]), None)
        if cap is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"caption not found: {p['captionId']}")
        text_changed = False
        if "text" in p:
            if not isinstance(p["text"], str) or not p["text"].strip():
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "caption text must be a non-empty string")
            text_changed = p["text"] != cap.text
            cap.text = p["text"]
        old_start, old_end = cap.start, cap.end
        new_start = Rational.from_json(**p["start"]) if "start" in p else old_start
        new_end = Rational.from_json(**p["end"]) if "end" in p else old_end
        if text_changed:
            cap.words = []
        elif new_start != old_start or new_end != old_end:
            cap.words = _rebase_caption_words(
                cap.words, old_start, old_end, new_start, new_end)
        cap.start, cap.end = new_start, new_end
        if "words" in p:
            cap.words = _parse_caption_words(p["words"], cap.start, cap.end)
        _apply_caption_style(cap, p)
        return [{"type": "caption", "id": cap.id, "change": "updated"}]

    def _h_caption_patch(self, proj: Project, p: dict) -> list[dict]:
        """原子应用多条字幕补丁：{updates:[{captionId, text?, start?, end?, ...}]}。

        面向外部 Agent 的字幕精修。所有更新都在 execute() 的候选工程上运行，
        因此任一条字幕不存在或时间非法时，整批不会写入、不会推进 revision。
        成功时只生成一个 revision 和一个撤销点，减少逐条命令的往返成本。
        """
        updates = p.get("updates")
        if not isinstance(updates, list) or not updates:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption.patch requires a non-empty 'updates' list")

        seen: set[str] = set()
        changed: list[dict] = []
        for update in updates:
            if not isinstance(update, dict):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "caption.patch updates must contain objects")
            caption_id = update.get("captionId")
            if not isinstance(caption_id, str) or not caption_id:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "caption.patch update requires a non-empty captionId")
            if caption_id in seen:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"caption.patch repeats captionId: {caption_id}")
            seen.add(caption_id)
            changed.extend(self._h_caption_update(proj, update))
        return changed

    def _h_caption_remove(self, proj: Project, p: dict) -> list[dict]:
        """删除字幕：{captionId}。"""
        if "captionId" not in p:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption.remove requires 'captionId'")
        captions = proj.sequence.captions
        for cap in captions:
            if cap.id == p["captionId"]:
                captions.remove(cap)
                return [{"type": "caption", "id": cap.id, "change": "deleted"}]
        raise EditError(ErrorCode.INVALID_ARGUMENT,
                        f"caption not found: {p['captionId']}")

    def _h_caption_import_vtt(self, proj: Project, p: dict) -> list[dict]:
        """导入 WebVTT（K01）：{text} 或 {path} 二选一，解析后追加为工程字幕。

        与既有字幕共存、不唯一覆盖；每条新字幕生成唯一 captionId。
        无 cue / 解析为空则报错；最终由 execute 的 validate_project 把关不变量。
        """
        from .captions import parse_vtt
        text = p.get("text")
        if not text and p.get("path"):
            try:
                with open(p["path"], "r", encoding="utf-8") as f:
                    text = f.read()
            except OSError as e:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"caption.importVtt: read path failed: {e}") from e
        if not text:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption.importVtt requires 'text' or 'path'")
        imported = parse_vtt(text)
        if not imported:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption.importVtt: no cues parsed")
        existing = {c.id for c in proj.sequence.captions}
        for cap in imported:
            while cap.id in existing:
                cap.id = new_id("cap")
            existing.add(cap.id)
            proj.sequence.captions.append(cap)
        return [{"type": "caption", "change": "imported", "count": len(imported)}]

    def _h_caption_export_vtt(self, proj: Project, p: dict) -> list[dict]:
        """导出 WebVTT（K01，非改工程）：把工程字幕序列化为 WebVTT 文本返回。

        支持 {outPath} 落盘；changed_entities 携带 vtt 文本与 count，供 HTTP/MCP 直接回显。
        """
        from .captions import to_vtt
        if not proj.sequence.captions:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption.exportVtt: project has no captions")
        vtt = to_vtt(proj.sequence.captions)
        out = p.get("outPath")
        if out:
            import os
            parent = os.path.dirname(os.path.abspath(out))
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                f.write(vtt)
        return [{"type": "caption", "change": "exportedVtt",
                 "vtt": vtt, "count": len(proj.sequence.captions)}]

    def _h_caption_shift(self, proj: Project, p: dict) -> list[dict]:
        """批量时间偏移（K01）：{offset 秒(可负)} + 可选 {ids}（缺省全部）。

        用 Decimal(str(offset)) 构造 Rational 避免浮点；偏移后任一 caption
        start<0 或 end<=start 直接抛 EditError——候选副本机制保证内存/存储零污染回滚。
        """
        from decimal import Decimal
        from fractions import Fraction
        if "offset" not in p:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption.shift requires 'offset'")
        dec = Decimal(str(p["offset"]))
        frac = Fraction(dec)
        offset = Rational(frac.numerator, frac.denominator)

        ids = p.get("ids")
        caps = proj.sequence.captions
        if ids:
            idset = set(ids)
            missing = idset - {c.id for c in caps}
            if missing:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"caption.shift: unknown ids: {sorted(missing)}")
            targets = [c for c in caps if c.id in idset]
        else:
            targets = list(caps)

        if not targets:
            return []
        for c in targets:
            c.start = c.start + offset
            c.end = c.end + offset
            for word in c.words:
                word.start = word.start + offset
                word.end = word.end + offset
        # 不变量校验（越界回滚）
        zero = Rational.of(0, 1)
        for c in targets:
            if c.start < zero:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"caption.shift would make start<0: {c.id}")
            if c.end <= c.start:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"caption.shift would make end<=start: {c.id}")
        return [{"type": "caption", "id": c.id, "change": "updated"} for c in targets]

    def _h_caption_split_by_words(self, proj: Project, p: dict) -> list[dict]:
        """逐字/逐词/逐行动画（I05）：把一条字幕按单元拆成多条时间错开字幕。

        {captionId, unit:"char"|"word"|"line", overlap 秒}
        - 累计揭示：第 i 段文本 = 单元[0..i] 累计（卡拉OK式）
        - 时间守恒：把 [start,end] 平分为 n 段，首尾对齐原区间（末段结束恰为 end）
        - overlap 作为相邻段淡出秒数（渲染语义，不改区间）
        - 删除原 caption、真实插入拆分后的多条（可导出 SRT/VTT 验证）
        """
        from decimal import Decimal
        from fractions import Fraction
        if "captionId" not in p:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption.splitByWords requires 'captionId'")
        unit = p.get("unit", "char")
        if unit not in ("char", "word", "line"):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"caption.splitByWords: invalid unit {unit}")
        overlap = Rational.of(0, 1)
        if "overlap" in p and p["overlap"] is not None:
            dec = Decimal(str(p["overlap"]))
            f = Fraction(dec)
            overlap = Rational(f.numerator, f.denominator)

        caps = proj.sequence.captions
        idx = next((i for i, c in enumerate(caps) if c.id == p["captionId"]), None)
        if idx is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"caption not found: {p['captionId']}")
        src = caps[idx]
        start, end = src.start, src.end
        total = end - start
        if total <= Rational.of(0, 1):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "caption.splitByWords: zero-length caption")

        units = _split_text_units(src.text, unit)
        n = len(units)
        if n <= 1:
            return [{"type": "caption", "id": src.id, "change": "unchanged"}]

        joiner = {"char": "", "word": " ", "line": "\n"}[unit]
        base = Rational.of(total.num, total.den * n)  # 每段时长 = 总长 / n（精确）
        overlap_ms = int((overlap.to_fraction() * 1000)) if overlap != Rational.of(0, 1) else 0

        new_caps: list = []
        for i in range(n):
            seg_text = joiner.join(units[:i + 1])
            seg_start = start + base * Rational.of(i, 1)
            seg_end = start + base * Rational.of(i + 1, 1)
            if i == n - 1:
                seg_end = end  # 末段精确对齐原 end，消除整数约分边界
            new_caps.append(Caption(
                id=new_id("cap"), text=seg_text, start=seg_start, end=seg_end,
                animOut=overlap_ms))

        caps[idx:idx + 1] = new_caps
        return [{"type": "caption", "id": c.id, "change": "created"} for c in new_caps]

    def _h_effect_update(self, proj: Project, p: dict) -> list[dict]:
        """更新片段效果参数或其剪辑区间：{clipId,effectId,params?,range?,effectIndex?}。

        params 部分更新：与既有实例 params 浅层合并（新键覆盖旧键，旧键保留），
        合并结果必须过注册表 validate_params——任何非法参数整个命令失败，
        候选副本机制保证零污染回滚。效果区间是相对片段起点的有理数秒，
        只支持 cutvoke.fx 效果；crop 分支会先恢复画布尺寸再做区间混合。
        """
        if "clipId" not in p or "effectId" not in p:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "effect.update requires 'clipId' and 'effectId'")
        new_params = p.get("params")
        if "params" in p and (not isinstance(new_params, dict) or not new_params):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "effect.update 'params' must be a non-empty object")
        if not new_params and "range" not in p:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "effect.update requires 'params' or 'range'")
        clip = None
        for t in proj.sequence.tracks:
            found = next((c for c in t.clips if c.id == p["clipId"]), None)
            if found is not None:
                clip = found
                break
        if clip is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"clip not found: {p['clipId']}")
        effects = clip.effects or []
        requested_index = p.get("effectIndex")
        if requested_index is not None:
            if isinstance(requested_index, bool) or not isinstance(requested_index, int) \
                    or requested_index < 0 or requested_index >= len(effects):
                raise EditError(ErrorCode.INVALID_ARGUMENT, "effectIndex is out of range")
            inst = effects[requested_index]
            if inst.get("effectId") != p["effectId"]:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "effectIndex does not match effectId")
            effect_index = requested_index
        else:
            effect_index = next((i for i, e in enumerate(effects)
                                 if e.get("effectId") == p["effectId"]), None)
            inst = effects[effect_index] if effect_index is not None else None
        if inst is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"effect not found on clip {p['clipId']}: {p['effectId']}")
        eid = self._require_effect(str(p["effectId"]))
        if new_params:
            merged = dict(inst.get("params") or {})
            merged.update(new_params)
            inst["params"] = self._effects.validate_params(eid, merged)
        if "range" in p:
            if not eid.startswith("cutvoke.fx."):
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "该效果暂不支持独立时间区间")
            raw_range = p["range"]
            if not isinstance(raw_range, dict) or set(raw_range) != {"start", "end"}:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "effect range requires start and end")
            try:
                start = Rational.from_json(**raw_range["start"])
                end = Rational.from_json(**raw_range["end"])
            except (TypeError, ValueError, ZeroDivisionError) as e:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                "effect range must use valid rational seconds") from e
            if start < Rational.of(0) or end > clip.duration or start >= end:
                raise EditError(ErrorCode.INVALID_ARGUMENT,
                                f"effect range must satisfy 0 <= start < end <= {clip.duration}")
            inst["range"] = {"start": start.to_json(), "end": end.to_json()}
        return [{"type": "clip", "id": clip.id, "change": "effect.updated",
                 "effectId": inst["effectId"], "effectIndex": effect_index}]

    def _h_effect_set_animation(self, proj: Project, p: dict) -> list[dict]:
        clip_id = p.get("clipId")
        effect_id = p.get("effectId")
        if not isinstance(clip_id, str) or not clip_id or not isinstance(effect_id, str):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "effect.setAnimation requires clipId and effectId (empty clears)")
        slot = animation_slot(effect_id) if effect_id else p.get("slot")
        if effect_id and slot is None:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "effect.setAnimation requires a cutvoke.anim.* effect")
        if slot is not None and slot not in ("入场", "出场", "循环", "组合"):
            raise EditError(ErrorCode.INVALID_ARGUMENT, f"invalid animation slot: {slot}")
        keyframe_policy = p.get("keyframePolicy", "combine")
        if keyframe_policy not in ("combine", "replace"):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "keyframePolicy must be 'combine' or 'replace'")
        clip = self._clip_by_id(proj, clip_id)
        old_indices = [index for index, item in enumerate(clip.effects)
                       if animation_slot(str(item.get("effectId", ""))) == slot
                       or (slot is None and animation_slot(str(item.get("effectId", ""))) is not None)]
        insert_at = min(old_indices) if old_indices else len(clip.effects)
        clip.effects = [item for item in clip.effects
                        if not (animation_slot(str(item.get("effectId", ""))) == slot
                                or (slot is None and animation_slot(str(item.get("effectId", ""))) is not None))]
        if effect_id:
            self._h_effect_add(proj, {"clipId": clip_id, "effectId": effect_id,
                                      "params": p.get("params", {})})
            added = clip.effects.pop()
            clip.effects.insert(min(insert_at, len(clip.effects)), added)
            if keyframe_policy == "replace":
                clip.keyframes.pop("opacity", None)
        return [{"type": "clip", "id": clip_id, "change": "animation.set",
                 "effectId": effect_id, "slot": slot,
                 "keyframePolicy": keyframe_policy if effect_id else None}]

    def _h_effect_set_transition(self, proj: Project, p: dict) -> list[dict]:
        return self._h_effect_set_family(
            proj, p, command_type="effect.setTransition",
            prefix="cutvoke.transition.", change="transition.set")

    def _h_effect_set_family(self, proj: Project, p: dict, *, command_type: str,
                             prefix: str, change: str) -> list[dict]:
        """Replace one effect family without exposing a remove/add intermediate state."""
        clip_id = p.get("clipId")
        effect_id = p.get("effectId")
        if not isinstance(clip_id, str) or not clip_id:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"{command_type} requires clipId")
        if not isinstance(effect_id, str):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"{command_type} requires effectId (empty string clears)")
        if effect_id and not effect_id.startswith(prefix):
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            f"{command_type} requires a {prefix}* effect")

        clip = self._clip_by_id(proj, clip_id)
        if effect_id and command_type == "effect.setTransition":
            self._require_adjacent_transition_target(proj, clip)
        old_indices = [i for i, effect in enumerate(clip.effects)
                       if str(effect.get("effectId", "")).startswith(prefix)]
        insert_at = min(old_indices) if old_indices else len(clip.effects)
        clip.effects = [effect for effect in clip.effects
                        if not str(effect.get("effectId", "")).startswith(prefix)]
        if effect_id:
            self._h_effect_add(proj, {"clipId": clip_id, "effectId": effect_id,
                                      "params": p.get("params", {})})
            added = clip.effects.pop()
            clip.effects.insert(min(insert_at, len(clip.effects)), added)
        return [{"type": "clip", "id": clip_id, "change": change,
                 "effectId": effect_id}]

    def _h_marker_add(self, proj: Project, p: dict) -> list[dict]:
        """新增标记：{markerId?, name?, time:{num,den}}。标记用于定位（F11）。"""
        if "time" not in p:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "marker.add requires 'time'")
        marker_id = p.get("markerId") or new_id("mk")
        if any(m.id == marker_id for m in proj.sequence.markers):
            raise EditError(ErrorCode.INVALID_ARGUMENT, f"marker exists: {marker_id}")
        marker = Marker(id=marker_id, name=p.get("name", ""),
                        time=Rational.from_json(**p["time"]))
        proj.sequence.markers.append(marker)
        return [{"type": "marker", "id": marker_id, "change": "created"}]

    def _h_marker_remove(self, proj: Project, p: dict) -> list[dict]:
        """删除标记：{markerId}。"""
        if "markerId" not in p:
            raise EditError(ErrorCode.INVALID_ARGUMENT,
                            "marker.remove requires 'markerId'")
        markers = proj.sequence.markers
        for m in markers:
            if m.id == p["markerId"]:
                markers.remove(m)
                return [{"type": "marker", "id": m.id, "change": "deleted"}]
        raise EditError(ErrorCode.INVALID_ARGUMENT,
                        f"marker not found: {p['markerId']}")

    def _h_undo(self, proj: Project, p: dict) -> list[dict]:
        """撤销：把 proj（候选）恢复为最近一次编辑前的快照（8.4）。

        候选副本语义：本 handler 只修改传入的 proj，由 execute 的统一
        提交链持久化——失败不会污染内存/存储。
        """
        pid = proj.project_id
        if self._store is not None:
            depth = self._store.history_depth(pid)
            if depth < 0:
                raise EditError(ErrorCode.UNDO_CONFLICT, "nothing to undo")
            # 只读快照；由 execute -> ProjectStore.commit 在同一事务内消费。
            # 这样 revision 冲突时，工程和撤销历史都会保持不变。
            snap_json = self._store.peek_history(pid, depth)
            if snap_json is None:
                raise EditError(ErrorCode.UNDO_CONFLICT, "nothing to undo")
            prev = Project.from_dict(json.loads(snap_json))
        else:
            stack = self._undo_stack[pid]
            if not stack:
                raise EditError(ErrorCode.UNDO_CONFLICT, "nothing to undo")
            prev = stack[-1]
        # 撤销栈的 pop 与重做栈的 push 都由 execute 在工程通过校验并
        # 提交成功后完成，保证失败命令不会消耗历史。
        # 把 prev 的状态应用到候选 proj 上（revision 由 execute 统一提升）
        self._apply_snapshot(proj, prev)
        return [{"type": "project", "id": pid, "change": "undone"}]

    def _h_redo(self, proj: Project, p: dict) -> list[dict]:
        """重做：应用被撤销操作前的下一个状态（8.4）。候选副本语义同上。"""
        pid = proj.project_id
        stack = self._redo_stack[pid]
        if not stack:
            raise EditError(ErrorCode.UNDO_CONFLICT, "nothing to redo")
        # redo 项的 pop 与当前状态重新入 undo 栈，都由 execute 在成功
        # 提交后完成；候选校验或 revision 冲突不会消耗 redo 历史。
        next_state = stack[-1]
        self._apply_snapshot(proj, next_state)
        return [{"type": "project", "id": pid, "change": "redone"}]

    def _apply_snapshot(self, target: Project, source: Project) -> None:
        """把 source 的工程状态应用到 target（revision 归属由 execute 处理）。

        必须**整体**同步，不能只赋 sequence。原因（真实缺陷，2026-09-21 修）：
        `Project.sequence` 与 `sequences[active_sequence_id]` 在模型里指向同一个
        对象，而 `to_dict()/from_dict()` 以 `sequences` + `activeSequenceId` 为准
        （`__post_init__` 会用 sequences[active] 覆盖 sequence）。旧实现只赋
        `target.sequence`，`target.sequences` 仍是旧值 → 提交的 JSON 里
        `sequences` 是旧状态 → 重新 load 后撤销被"还原"回去。
        结果：history.undo 返回成功、revision 递增，但工程状态实际没变（撤销静默失效）。

        这里重建 `sequences` 并让 `sequence` 重新指向活动序列，维持模型不变量。
        """
        target.schema_version = source.schema_version
        target.sequences = copy.deepcopy(source.sequences)
        target.active_sequence_id = source.active_sequence_id
        target.sequence = copy.deepcopy(source.sequence)
        # 维持不变量：sequence 必须就是 sequences 里的活动序列（同一个对象）。
        for s in target.sequences:
            if s.id == target.active_sequence_id:
                target.sequence = s
                break
        else:
            target.sequences.append(target.sequence)
        # J01 工程级资源状态也随快照回退（否则撤销后收藏/最近/预设错位）
        target.favorites = list(source.favorites)
        target.recent_effects = list(source.recent_effects)
        target.presets = [dict(p) for p in source.presets]

    def events_since(self, project_id: str, revision: str) -> list[Event]:
        """返回 revision 之后的所有事件（10.2 重连补取）。

        持久化服务从 SQLite outbox 读取，保证 Web 能发现 MCP/CLI 进程的提交；
        内存态服务继续使用本地事件列表。
        """
        if self._store is not None:
            raw_events = self._store.events_since(project_id)
            out: list[Event] = []
            for raw in raw_events:
                try:
                    if int(raw.get("revision", "0")) > int(revision):
                        out.append(Event.from_dict(raw))
                except (TypeError, ValueError, KeyError):
                    # 损坏/旧格式事件不阻断正常补取；诊断仍可通过数据库查看。
                    continue
            return out
        events = self._events.get(project_id, [])
        return [e for e in events if int(e.revision) > int(revision)]
