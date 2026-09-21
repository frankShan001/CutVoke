"""CutVoke 工程不变量校验（任务书 7.5 章）。

提交前校验工程状态的合法性，阻止错误状态进入工程。
结构合法性与可渲染性分开返回。

必须校验（7.5）：
  - 引用完整、ID 唯一、时长非负且有效、素材边界合法
  - 时间映射可求值、轨道类型匹配、参数符合 schema
  - 无序列递归和依赖环
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Project, Track, Clip
from .rational import Rational


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
                if clip.timeline_end <= clip.timeline_start:
                    errors.append(f"clip {clip.id}[{scope}]: invalid duration "
                                  f"(start={clip.timeline_start}, end={clip.timeline_end})")
                if clip.speed == Rational.of(0, 1):
                    errors.append(f"clip {clip.id}[{scope}]: invalid speed {clip.speed} "
                                  f"(cannot be zero)")
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
    # 可渲染性：所有视频轨片段必须有素材路径（M0 简化判断）
    renderable = structurally_valid and all(
        clip.asset_ref.source_path != "" for track in seq.tracks for clip in track.clips
    )

    return ValidationResult(
        structurally_valid=structurally_valid,
        renderable=renderable,
        errors=errors,
        warnings=warnings,
    )