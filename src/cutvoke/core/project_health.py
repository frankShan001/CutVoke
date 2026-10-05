"""Cheap, read-only dependency checks before preview/export.

File availability is not media decoding. Keep this check proportional to unique
paths, so repeated clips do not spawn probes or reread large source files.
"""

from __future__ import annotations

import os
import stat

from .invariants import validate_project
from .model import Project, Sequence
from .rational import Rational


def project_preflight(project: Project, *, output_range: tuple[float, float] | None = None) -> dict:
    sequence = project.sequence
    validation = validate_project(project)
    errors = [{"code": "INVALID_PROJECT", "message": message}
              for message in validation.errors]
    warnings = [{"code": "PROJECT_WARNING", "message": message}
                for message in validation.warnings]
    files: dict[str, dict] = {}
    empty_paths: list[dict] = []
    clip_count = 0
    active_clip_count = 0

    def dependency(path: str, resource_kind: str, reference: dict) -> None:
        if not isinstance(path, str) or not path.strip():
            empty_paths.append({"code": "MISSING_SOURCE_PATH",
                                "message": "片段没有素材路径，请重新链接素材。",
                                "resourceKind": resource_kind, **reference})
            return
        absolute = os.path.abspath(path)
        key = os.path.normcase(absolute)
        item = files.setdefault(key, {"path": absolute, "available": False,
                                      "required": False, "resourceKind": resource_kind,
                                      "references": []})
        item["required"] = item["required"] or reference["required"]
        item["references"].append(reference)

    def walk(seq: Sequence, parent_required: bool = True,
             window: tuple[float, float] | None = None) -> None:
        nonlocal clip_count, active_clip_count
        camera = (seq.multicam or {}).get("activeTrackId")
        for track in seq.tracks:
            picture_active = (track.kind == "video" and track.visible
                              and (camera is None or track.id == camera))
            audio_active = (track.kind in ("video", "audio") and track.visible
                            and not track.muted)
            transition_neighbors: set[str] = set()
            if window is not None and picture_active:
                ordered = sorted((clip for clip in track.clips if not clip.hidden),
                                 key=lambda clip: clip.timeline_start)
                for previous, incoming in zip(ordered, ordered[1:]):
                    transition = next((effect for effect in incoming.effects
                        if str(effect.get("effectId", "")).startswith("cutvoke.transition.")
                        and effect.get("enabled", True) is not False), None)
                    if transition is not None:
                        value = (transition.get("params") or {}).get("duration", 0.0)
                        try:
                            duration = (float(Rational.from_json(**value).to_fraction())
                                        if isinstance(value, dict) else float(value))
                            duration = max(0.0, min(duration,
                                float(previous.duration.to_fraction()) / 2,
                                float(incoming.duration.to_fraction()) / 2))
                        except (TypeError, ValueError, ZeroDivisionError):
                            errors.append({"code": "INVALID_EFFECT_PARAMETER",
                                "message": "转场时长无效，请重新设置转场。",
                                "clipIds": [incoming.id], "trackIds": [track.id]})
                            continue
                        start = float(incoming.timeline_start.to_fraction())
                        if (abs(float(previous.timeline_end.to_fraction()) - start) < 0.001
                                and duration > 0 and start < window[1]
                                and start + duration > window[0]):
                            transition_neighbors.add(previous.id)
            for clip in track.clips:
                clip_count += 1
                start = float(clip.timeline_start.to_fraction())
                end = float(clip.timeline_end.to_fraction())
                in_window = (window is None or (end > window[0] and start < window[1])
                             or clip.id in transition_neighbors)
                required = (parent_required and not clip.hidden and in_window
                            and (picture_active or audio_active))
                reference = {"clipId": clip.id, "trackId": track.id,
                             "assetId": clip.asset_ref.asset_id, "required": required}
                for effect in clip.effects:
                    if (effect.get("effectId") == "cutvoke.fx.lut"
                            and effect.get("enabled", True) is not False):
                        path = (effect.get("params") or {}).get("file")
                        if path:
                            dependency(path, "lut", {**reference,
                                "required": required and picture_active})
                if clip.nested is not None:
                    # The renderer resolves child offsets/speed. Conservatively
                    # check every child dependency when this parent is used.
                    walk(clip.nested, required)
                    continue
                if track.kind in ("video", "audio"):
                    if required:
                        active_clip_count += 1
                    dependency(clip.asset_ref.source_path, "media", reference)

    walk(sequence, window=output_range)
    for issue in empty_paths:
        required = issue.pop("required")
        for singular, plural in (("clipId", "clipIds"), ("trackId", "trackIds"),
                                 ("assetId", "assetIds")):
            issue[plural] = [issue.pop(singular)]
        (errors if required else warnings).append(issue)
    for item in files.values():
        try:
            info = os.stat(item["path"])
            item["available"] = stat.S_ISREG(info.st_mode) and info.st_size > 0
            failure = ("NOT_A_FILE" if not stat.S_ISREG(info.st_mode) else "EMPTY_FILE")
        except OSError:
            failure = "MISSING_FILE"
        if item["available"]:
            continue
        references = item["references"]
        issue = {"code": failure, "path": item["path"],
                 "message": ("自定义 LUT 文件不可用，请重新导入 LUT。" if
                             item["resourceKind"] == "lut" else
                             "素材文件不可用，请在素材库中重新链接。"),
                 "resourceKind": item["resourceKind"],
                 "clipIds": list(dict.fromkeys(ref["clipId"] for ref in references)),
                 "trackIds": list(dict.fromkeys(ref["trackId"] for ref in references)),
                 "assetIds": list(dict.fromkeys(ref["assetId"] for ref in references))}
        (errors if item["required"] else warnings).append(issue)
    if active_clip_count == 0:
        errors.append({"code": "EMPTY_TIMELINE",
                       "message": "没有可输出的视频或音频片段，请先将素材放入时间线。"})
    return {"projectId": project.project_id, "revision": project.revision,
            "sequenceId": sequence.id, "readyToRender": not errors,
            "checkedFileCount": len(files), "clipCount": clip_count,
            "errors": errors, "warnings": warnings, "files": list(files.values())}
