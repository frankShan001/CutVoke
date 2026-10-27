"""Atomic multi-clip edits and snapshot-based timeline clipboard operations."""
from __future__ import annotations

from copy import deepcopy

from cutvoke.domain.model import Clip, Track, new_id
from cutvoke.domain.rational import Rational
from cutvoke.editing.timeline_ops import seconds, insert_space

MAX_SELECTED_CLIPS = 5000


def _selection(seq, ids, include_attached=True):
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_SELECTED_CLIPS
            or any(not isinstance(cid, str) for cid in ids) or len(set(ids)) != len(ids)):
        raise ValueError("请选择 1 至 5000 个不同的片段")
    indexed = {clip.id: (track, clip) for track in seq.tracks for clip in track.clips}
    selected = set(ids)
    if not selected <= indexed.keys():
        raise ValueError("部分片段已不存在，请重新选择")
    if not isinstance(include_attached, bool):
        raise ValueError("includeAttached 必须是布尔值")
    if include_attached:
        while True:
            children = {cid for cid, (_, clip) in indexed.items()
                        if clip.attached_to_clip_id in selected}
            if children <= selected:
                break
            selected.update(children)
    if len(selected) > MAX_SELECTED_CLIPS:
        raise ValueError("选中片段及绑定图层不能超过 5000 个")
    result = [pair for cid, pair in indexed.items() if cid in selected]
    if any(track.locked for track, _ in result):
        raise ValueError("选中片段或绑定图层所在轨道已锁定，请先解锁")
    return result


def _no_overlap(seq):
    for track in seq.tracks:
        if track.kind != "video":
            continue
        ordered = sorted(track.clips, key=lambda clip: clip.timeline_start)
        for a, b in zip(ordered, ordered[1:]):
            if a.timeline_end > b.timeline_start:
                raise ValueError("目标位置已有片段，请移到空白区域或使用插入粘贴")


def edit(service, proj, payload):
    action = payload.get("action")
    selected = _selection(proj.sequence, payload.get("clipIds"),
                          payload.get("includeAttached", action in {"move", "remove"}))
    ids = {clip.id for _, clip in selected}
    changed = []
    if action == "move":
        offset = seconds(payload.get("offset", 0))
        track_offset = payload.get("trackOffset", 0)
        if isinstance(track_offset, bool) or not isinstance(track_offset, int):
            raise ValueError("轨道位移必须是整数")
        tracks = proj.sequence.tracks
        destinations = []
        for track, clip in selected:
            index = tracks.index(track) + track_offset
            if index < 0 or index >= len(tracks) or tracks[index].kind != track.kind:
                raise ValueError("整体移动需要为每个片段保留同类型的目标轨道")
            if tracks[index].locked:
                raise ValueError("目标轨道已锁定，请先解锁")
            if clip.timeline_start + offset < Rational.of(0):
                raise ValueError("整体移动不能超过时间线起点")
            destinations.append((track, tracks[index], clip))
        for source, target, clip in destinations:
            source.clips.remove(clip)
            clip.timeline_start += offset
            clip.timeline_end += offset
            target.clips.append(clip)
            changed.append({"type": "clip", "id": clip.id, "trackId": target.id, "change": "moved"})
        _no_overlap(proj.sequence)
    elif action == "remove":
        for track, clip in selected:
            track.clips.remove(clip)
            changed.append({"type": "clip", "id": clip.id, "change": "deleted"})
        for track in proj.sequence.tracks:
            for clip in track.clips:
                if clip.attached_to_clip_id in ids:
                    clip.attached_to_clip_id = None
    elif action == "audio":
        values = {key: payload[key] for key in ("volume", "fadeIn", "fadeOut") if key in payload}
        if not values:
            raise ValueError("请填写需要批量调整的音量或淡入淡出")
        for _, clip in selected:
            changed.extend(service._h_clip_audio(proj, {"clipId": clip.id, **values}))
    else:
        raise ValueError("不支持的批量片段操作")
    return changed


def _clone_items(items):
    """Fresh identities and controlled references; user text remains verbatim."""
    from cutvoke.editing.identity_clone import fresh_data
    data_items = fresh_data([item["clip"] for item in items])
    clones = []
    source_ids = {item["clip"].get("id") for item in items}
    for item, data in zip(items, data_items):
        # A copied follower cannot retain a binding to an uncopied original.
        if item["clip"].get("attachedToClipId") not in source_ids:
            data.pop("attachedToClipId", None)
        clones.append((item, Clip.from_dict(data)))
    return clones


def paste(service, proj, payload):
    items = payload.get("items")
    if (not isinstance(items, list) or not 1 <= len(items) <= MAX_SELECTED_CLIPS
            or any(not isinstance(item, dict) or not isinstance(item.get("clip"), dict)
                   or item.get("trackKind") not in {"video", "audio", "text"}
                   or isinstance(item.get("trackIndex"), bool)
                   or not isinstance(item.get("trackIndex"), int) for item in items)):
        raise ValueError("剪贴板中没有有效的时间线片段")
    source_ids = [item["clip"].get("id") for item in items]
    if any(not isinstance(cid, str) or not cid for cid in source_ids) or len(set(source_ids)) != len(source_ids):
        raise ValueError("剪贴板片段标识无效")
    at = seconds(payload["timelineStart"])
    if at < Rational.of(0):
        raise ValueError("粘贴位置不能早于时间线起点")
    clones = _clone_items(items)
    first = min(clip.timeline_start for _, clip in clones)
    span = max(clip.timeline_end for _, clip in clones) - first
    if span <= Rational.of(0) or first < Rational.of(0):
        raise ValueError("剪贴板片段时间范围无效")
    tracks = proj.sequence.tracks
    target_id = payload.get("targetTrackId")
    source_first = min(item["trackIndex"] for item in items)
    target_index = next((i for i, track in enumerate(tracks) if track.id == target_id), None)
    if target_id and target_index is None:
        raise ValueError("粘贴目标轨道已不存在")
    destinations = {}
    created_tracks = []
    for item in items:
        key = item["trackIndex"]
        if key in destinations:
            if destinations[key].kind != item["trackKind"]:
                raise ValueError("剪贴板轨道类型不一致")
            continue
        if target_index is not None:
            index = target_index + key - source_first
            target = tracks[index] if 0 <= index < len(tracks) else None
            if target is None or target.kind != item["trackKind"]:
                raise ValueError("目标区域缺少同类型轨道，请先选择原轨道或创建对应轨道")
        else:
            target = next((track for track in tracks if track.id == item.get("trackId")
                           and track.kind == item["trackKind"]), None)
            if target is None:
                target = Track(id=new_id("track"), kind=item["trackKind"],
                               role=item.get("trackRole") or ("sticker" if item["clip"].get("role") == "sticker" else ""))
                created_tracks.append(target)
        if target.locked:
            raise ValueError("粘贴目标轨道已锁定，请先解锁")
        destinations[key] = target
    changed = insert_space(service, proj.sequence, at, span)
    tracks.extend(created_tracks)
    changed.extend({"type": "track", "id": track.id, "change": "created"} for track in created_tracks)
    for item, clip in clones:
        clip.timeline_start += at - first
        clip.timeline_end += at - first
        target = destinations[item["trackIndex"]]
        target.clips.append(clip)
        changed.append({"type": "clip", "id": clip.id, "trackId": target.id,
                        "sourceClipId": item["clip"]["id"], "change": "created"})
    _no_overlap(proj.sequence)
    return changed


def duplicate(service, proj, payload):
    selected = _selection(proj.sequence, payload.get("clipIds"), payload.get("includeAttached", True))
    tracks = proj.sequence.tracks
    items = [{"trackId": track.id, "trackIndex": tracks.index(track), "trackRole": track.role,
              "trackKind": track.kind, "clip": clip.to_dict()} for track, clip in selected]
    at = payload.get("timelineStart", max(clip.timeline_end for _, clip in selected).to_json())
    return paste(service, proj, {"items": items, "timelineStart": at})


def duplicate_one(service, proj, payload):
    original = next((clip for track in proj.sequence.tracks for clip in track.clips
                     if clip.id == payload["clipId"]), None)
    if original is None:
        raise ValueError("片段已不存在，请重新选择")
    requested = payload.get("newClipId")
    if requested and any(clip.id == requested for track in proj.sequence.tracks for clip in track.clips):
        raise ValueError("新片段标识已存在")
    args = {"clipIds": [original.id], "includeAttached": False}
    if "timelineStart" in payload:
        args["timelineStart"] = payload["timelineStart"]
    changed = duplicate(service, proj, args)
    event = next(item for item in changed if item.get("sourceClipId") == original.id)
    copied = next(clip for track in proj.sequence.tracks for clip in track.clips if clip.id == event["id"])
    copied.attached_to_clip_id = original.attached_to_clip_id
    if requested:
        copied.id = requested
        event["id"] = requested
    return changed
