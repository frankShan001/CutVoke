"""CutVoke 工程不变量校验（任务书 7.5 章）。

提交前校验工程状态的合法性，阻止错误状态进入工程。
结构合法性与可渲染性分开返回。

必须校验（7.5）：
  - 引用完整、ID 唯一、时长非负且有效、素材边界合法
  - 时间映射可求值、轨道类型匹配、参数符合 schema
  - 无序列递归和依赖环
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .model import Project, Track, Clip
from .rational import Rational

_IMAGE_SUFFIXES = frozenset({
    ".avif", ".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp",
})


@dataclass
class ValidationResult:
    """校验结果：结构合法 vs 可渲染性分开（7.5）。"""

    structurally_valid: bool
    renderable: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return self.structurally_valid

    def to_dict(self) -> dict:
        return {
            "structurallyValid": self.structurally_valid,
            "renderable": self.renderable,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def validate_project(project: Project) -> ValidationResult:
    """校验工程不变量。"""
    errors: list[str] = []
    warnings: list[str] = []

    seq = project.sequence

    background_color = getattr(seq, "background_color", "#000000")
    if (not isinstance(background_color, str) or len(background_color) != 7
            or background_color[0] != "#"
            or any(char not in "0123456789abcdefABCDEF" for char in background_color[1:])):
        errors.append("sequence backgroundColor must be an opaque #RRGGBB value")

    # 1. ID 唯一性（所有对象在同一命名空间，含嵌套复合片段内的子对象）
    seen_ids: dict[str, str] = {}
    def _check_id(obj_id: str, kind: str) -> None:
        if obj_id in seen_ids:
            errors.append(f"duplicate id {obj_id} ({kind} vs {seen_ids[obj_id]})")
        else:
            seen_ids[obj_id] = kind

    # 递归校验一组轨道（外层序列或复合片段内的子序列）：ID/时长/重叠/类型。
    # 复合片段（clip.nested 非 None）内部要满足同规则（J08）。
    def _validate_tracks(tracks: list, scope: str) -> None:
        clips_by_id = {clip.id: (track, clip) for track in tracks for clip in track.clips}
        for track in tracks:
            _check_id(track.id, f"track[{scope}]")
            # 同轨视频片段不重叠（7.3）
            if track.kind == "video":
                clips_sorted = sorted(track.clips,
                                     key=lambda c: c.timeline_start.to_fraction())
                for i in range(len(clips_sorted) - 1):
                    a = clips_sorted[i]
                    b = clips_sorted[i + 1]
                    if b.timeline_start < a.timeline_end:
                        errors.append(
                            f"track {track.id}[{scope}]: overlapping clips "
                            f"{a.id} and {b.id} "
                            f"([{a.timeline_start},{a.timeline_end}) vs "
                            f"[{b.timeline_start},{b.timeline_end}))"
                        )
            # 类型匹配
            if track.kind not in ("video", "audio", "text", "caption"):
                warnings.append(f"track {track.id}[{scope}]: unusual kind '{track.kind}'")
            # 片段级校验 + 递归复合片段
            for clip in track.clips:
                _check_id(clip.id, f"clip[{scope}]")
                if clip.attached_to_clip_id is not None:
                    anchor = clips_by_id.get(clip.attached_to_clip_id)
                    if (anchor is None or anchor[0].kind != "video" or
                            anchor[0].role == "sticker" or anchor[1].role == "sticker" or
                            anchor[1].id == clip.id or anchor[0].id == track.id or
                            (track.kind == "video" and track.role != "sticker")):
                        errors.append(f"clip {clip.id}[{scope}]: invalid attachedToClipId "
                                      f"{clip.attached_to_clip_id}")
                if clip.timeline_end <= clip.timeline_start:
                    errors.append(f"clip {clip.id}[{scope}]: invalid duration "
                                  f"(start={clip.timeline_start}, end={clip.timeline_end})")
                if clip.speed == Rational.of(0, 1):
                    errors.append(f"clip {clip.id}[{scope}]: invalid speed {clip.speed} "
                                  f"(cannot be zero)")
                if clip.speed_curve is not None:
                    if clip.speed < Rational.of(0) or not clip.preserve_pitch:
                        errors.append(f"clip {clip.id}[{scope}]: curve speed requires "
                                      "forward playback and preservePitch")
                    difference = abs(float((clip.duration -
                                            clip.speed_curve.timeline_duration).to_fraction()))
                    if difference > 0.002:
                        errors.append(f"clip {clip.id}[{scope}]: curve timeline duration "
                                      f"differs by {difference:.6f}s")
                if clip.frame_interpolation not in ("none", "motion"):
                    errors.append(f"clip {clip.id}[{scope}]: unknown frame interpolation "
                                  f"{clip.frame_interpolation!r}")
                elif clip.frame_interpolation == "motion":
                    if (track.kind != "video" or clip.nested is not None
                            or clip.freeze_at is not None or not clip.has_slow_motion
                            or os.path.splitext(clip.asset_ref.source_path)[1].lower()
                            in _IMAGE_SUFFIXES):
                        errors.append(f"clip {clip.id}[{scope}]: motion interpolation requires "
                                      "a non-frozen video clip with a speed interval below 1x")
                if clip.source_start < Rational.of(0, 1):
                    errors.append(f"clip {clip.id}[{scope}]: negative source_start "
                                  f"{clip.source_start}")
                # 复合片段：递归校验其内部子序列（子序列时间从 0 起）
                if clip.nested is not None:
                    _validate_tracks(clip.nested.tracks, scope=f"{scope}/{clip.id}")

    _validate_tracks(seq.tracks, scope="seq")

    # 5. 字幕时间范围合法
    for cap in seq.captions:
        if cap.end <= cap.start:
            errors.append(f"caption {cap.id}: invalid range ({cap.start}, {cap.end})")

    structurally_valid = len(errors) == 0
    # 文字图层由结构化参数渲染，没有源文件；视频与音频片段才需要路径。
    renderable = structurally_valid and all(
        clip.asset_ref.source_path != ""
        for track in seq.tracks if track.kind in ("video", "audio") for clip in track.clips
    )

    return ValidationResult(
        structurally_valid=structurally_valid,
        renderable=renderable,
        errors=errors,
        warnings=warnings,
    )
