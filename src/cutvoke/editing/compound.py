"""Cross-track compounds retain lane layout and all structured child data."""

from copy import deepcopy

from cutvoke.domain.model import AssetReference, Clip, Sequence, Track, new_id
from cutvoke.domain.rational import Rational


def _shift_mix_clock(mix, start, end):
    points = mix.get("envelope", [])
    if not points:
        return
    def gain(at):
        if at <= points[0]["time"]:
            return points[0]["gainDb"]
        for a,b in zip(points,points[1:]):
            if at <= b["time"]:
                f = (at-a["time"]) / (b["time"]-a["time"])
                return a["gainDb"]+f*(b["gainDb"]-a["gainDb"])
        return points[-1]["gainDb"]
    first, last = float(start.to_fraction()),float(end.to_fraction())
    mix["envelope"] = ([{"time":0., "gainDb":gain(first)}]
        + [{"time":p["time"]-first,"gainDb":p["gainDb"]} for p in points if first < p["time"] < last]
        + [{"time":last-first,"gainDb":gain(last)}])


def group(seq, payload):
    ids = payload.get("clipIds")
    if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
        raise ValueError("复合片段需要不重复的片段 ID 列表")
    wanted = set(ids)
    selected = [(t, c) for t in seq.tracks for c in t.clips if c.id in wanted]
    if len(selected) != len(wanted):
        raise ValueError("部分选中片段不存在")
    if any(t.locked for t, _ in selected):
        raise ValueError("请先解锁选中片段所在轨道")
    if (any(c.attached_to_clip_id and c.attached_to_clip_id not in wanted for _,c in selected)
            or any(c.id not in wanted and c.attached_to_clip_id in wanted
                   for t in seq.tracks for c in t.clips)):
        raise ValueError("请一并选中绑定的源片段及其跟随图层，避免丢失绑定关系")
    start = min(c.timeline_start for _, c in selected)
    end = max(c.timeline_end for _, c in selected)
    lanes = [t for t in seq.tracks if any(lane is t for lane, _ in selected)]
    if (any(t.audio_mix.get("solo") for t in seq.tracks)
            or any(b.get("mix",{}).get("solo") for b in seq.audio_buses)):
        raise ValueError("请先关闭轨道或总线独奏，再创建复合片段以保持混音结果")
    buses = {b["id"]:b for b in seq.audio_buses}
    for lane in lanes:
        bus = buses.get(lane.audio_mix.get("busId"),{})
        if any(e.get("enabled") is not False for mix in (lane.audio_mix,bus.get("mix",{}))
               for e in mix.get("effects",[])):
            raise ValueError("轨道或总线效果会跨片段处理，请先旁路这些效果再创建复合片段")
    for lane in lanes:
        if any(c.id not in wanted and c.timeline_start < end and c.timeline_end > start
               for c in lane.clips):
            raise ValueError("选区跨度内还有未选片段，请一并选中后创建复合片段")
    paint_tracks = sorted([t for t in seq.tracks if t.kind == "video"],key=lambda t:t.role=="sticker")
    paint_tracks += [t for t in seq.tracks if t.kind == "text"]
    positions = [i for i,t in enumerate(paint_tracks) if t in lanes]
    if positions:
        for lane in paint_tracks[min(positions):max(positions)+1]:
            if lane not in lanes and lane.visible and lane.kind in {"video", "text"} and any(
                    c.timeline_start < end and c.timeline_end > start for c in lane.clips):
                raise ValueError("选中图层之间还有可见图层，请一并选中以保持合成顺序")
    if any(t.kind == "text" for t in lanes) and any(t.kind == "text" and t not in lanes
            and t.visible and any(c.timeline_start < end and c.timeline_end > start for c in t.clips)
            for t in seq.tracks):
        raise ValueError("请一并选中同一时间内的文字图层，以保持文字合成顺序")
    gid = payload.get("groupId") or new_id("clip")
    if any(c.id == gid for t in seq.tracks for c in t.clips):
        raise ValueError("复合片段 ID 已存在")
    nested = Sequence(id=f"{gid}_seq", width=seq.width, height=seq.height, fps=seq.fps,
                      audio_sample_rate=seq.audio_sample_rate, reframe=seq.reframe,
                      name=str(payload.get("name", "")))
    nested.audio_buses = deepcopy(seq.audio_buses)
    for bus in nested.audio_buses:
        _shift_mix_clock(bus.get("mix",{}),start,end)
    for lane in lanes:
        inner = deepcopy(lane)
        inner.id = f"{gid}_lane_{len(nested.tracks)}"
        inner.locked = False
        inner.clips = []
        _shift_mix_clock(inner.audio_mix,start,end)
        nested.compound_layout.append({"nestedTrackId": inner.id,
                                      "trackId": lane.id,
                                      "index": seq.tracks.index(lane),
                                      "kind": lane.kind, "role": lane.role})
        for c in lane.clips:
            if c.id not in wanted:
                continue
            child = deepcopy(c)
            child.timeline_start = child.timeline_start - start
            child.timeline_end = child.timeline_end - start
            if child.attached_to_clip_id not in wanted:
                child.attached_to_clip_id = None
            inner.clips.append(child)
        nested.tracks.append(inner)
    only_audio = all(t.kind == "audio" for t in lanes)
    destination = next((t for t in reversed(lanes) if t.kind == "video"), None)
    if only_audio:
        destination = lanes[0]
    if destination is None or len(lanes) > 1 or getattr(destination, "audio_mix", {}):
        destination = Track(id=new_id("track"), kind="audio" if only_audio else "video")
        if any(t.kind == "text" for t in lanes):
            destination.role = "sticker"
            index = max((i for i,t in enumerate(seq.tracks) if t.kind == "video"),default=-1)+1
        else:
            visual = [t for t in lanes if t.kind == "video"]
            if visual and any(t.role == "sticker" for t in visual):
                destination.role = "sticker"
            index = max(seq.tracks.index(t) for t in (visual or lanes))+1
        seq.tracks.insert(index, destination)
    compound = Clip(id=gid, asset_ref=AssetReference(""), timeline_start=start,
                    timeline_end=end, source_start=Rational.of(0), nested=nested)
    compound.hidden = all(c.hidden or not t.visible or (t.kind == "audio" and t.muted)
                          for t,c in selected)
    for lane in lanes:
        lane.clips = [c for c in lane.clips if c.id not in wanted]
    destination.clips.append(compound)
    destination.clips.sort(key=lambda c:c.timeline_start)
    return [{"type":"clip", "id":gid, "change":"grouped", "trackId":destination.id,
             "count":len(selected), "nestedClipIds":ids,
             "nestedTrackId":nested.tracks[0].id}]


def ungroup(service, seq, payload):
    pair = next(((t,c) for t in seq.tracks for c in t.clips
                 if c.id == payload["clipId"]), None)
    if pair is None or pair[1].nested is None:
        raise ValueError("请选择复合片段")
    lane, compound = pair
    if lane.locked:
        raise ValueError("请先解锁复合片段所在轨道")
    if (compound.effects or compound.keyframes or compound.speed_curve
            or compound.speed != Rational.of(1) or compound.volume != Rational.of(1)
            or compound.fade_in != Rational.of(0) or compound.fade_out != Rational.of(0)
            or compound.pitch != Rational.of(1) or compound.freeze_at is not None):
        raise ValueError("复合片段已有整体效果或变速，请先还原后展开，避免丢失合成结果")
    layout = {entry["nestedTrackId"]:entry for entry in compound.nested.compound_layout}
    restored, staged = [], []
    from cutvoke.editing.timeline_ops import crop_clip
    for inner in compound.nested.tracks:
        info = layout.get(inner.id, {"trackId":lane.id,"index":seq.tracks.index(lane)})
        destination = next((t for t in seq.tracks if t.id == info["trackId"]), None)
        if destination is None:
            destination = deepcopy(inner)
            destination.id = info["trackId"]
            destination.clips = []
            seq.tracks.insert(min(info.get("index",len(seq.tracks)),len(seq.tracks)),destination)
        if destination.locked:
            raise ValueError(f"原轨道 {destination.id} 已锁定，请解锁后展开")
        for original in inner.clips:
            start = max(original.timeline_start, compound.source_start)
            end = min(original.timeline_end, compound.source_start + compound.duration)
            if end <= start:
                continue
            c = crop_clip(service, original, start, end)
            c.timeline_start = c.timeline_start - compound.source_start + compound.timeline_start
            c.timeline_end = c.timeline_end - compound.source_start + compound.timeline_start
            if any(existing is not compound and c.timeline_start < existing.timeline_end
                   and c.timeline_end > existing.timeline_start for existing in destination.clips):
                raise ValueError(f"原轨道 {destination.id} 已有重叠片段，无法展开")
            staged.append((destination,c))
            restored.append(c.id)
    lane.clips.remove(compound)
    for destination,c in staged:
        destination.clips.append(c)
        destination.clips.sort(key=lambda item:item.timeline_start)
    anchors = {c.id for t in seq.tracks for c in t.clips}
    for t in seq.tracks:
        for c in t.clips:
            if c.attached_to_clip_id not in anchors:
                c.attached_to_clip_id = None
    return [{"type":"clip","id":compound.id,"change":"ungrouped",
             "trackId":lane.id,"restoredClipIds":restored}]


def fresh_nested(sequence):
    """A second instance needs fresh child identities, without changing user text."""
    from cutvoke.editing.identity_clone import fresh_data
    return Sequence.from_dict(fresh_data(sequence.to_dict()))
