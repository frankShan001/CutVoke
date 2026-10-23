"""Atomic sequence edits shared by timeline and transcript editing."""

from __future__ import annotations

from copy import deepcopy

from cutvoke.domain.model import CaptionWord, Clip, Sequence, new_id
from cutvoke.domain.rational import Rational


def seconds(value) -> Rational:
    if isinstance(value, dict):
        return Rational.from_json(**value)
    return Rational.of(round(float(value) * 1_000_000), 1_000_000)


def insert_space(service, seq: Sequence, at: Rational, duration: Rational) -> list[dict]:
    """Insert a gap across lanes so pasted shots preserve downstream sync."""
    if at < Rational.of(0) or duration <= Rational.of(0):
        raise ValueError("插入位置和时长无效")
    if any(track.locked and any(c.timeline_end > at for c in track.clips) for track in seq.tracks):
        raise ValueError("后续内容包含锁定轨道，请先解锁再插入粘贴")
    changed = []
    replacements = {}
    for track in seq.tracks:
        retained = []
        for original in track.clips:
            if original.timeline_end <= at:
                retained.append(original)
                replacements[original.id] = [original]
                continue
            if original.timeline_start >= at:
                original.timeline_start += duration
                original.timeline_end += duration
                pieces = [original]
            else:
                left = crop_clip(service, original, original.timeline_start, at)
                right = crop_clip(service, original, at, original.timeline_end)
                right.id = new_id("clip")
                if right.nested is not None:
                    from cutvoke.editing.compound import fresh_nested
                    right.nested = fresh_nested(right.nested)
                right.timeline_start += duration
                right.timeline_end += duration
                pieces = [left, right]
                changed.append({"type": "clip", "id": right.id, "trackId": track.id, "change": "created"})
            replacements[original.id] = pieces
            retained.extend(pieces)
            changed.append({"type": "clip", "id": original.id, "trackId": track.id, "change": "moved"})
        track.clips = retained
    for track in seq.tracks:
        for clip in track.clips:
            if clip.attached_to_clip_id in replacements:
                anchors = replacements[clip.attached_to_clip_id]
                clip.attached_to_clip_id = max(anchors, key=lambda a: min(a.timeline_end, clip.timeline_end)
                    - max(a.timeline_start, clip.timeline_start)).id
    captions = []
    for cap in seq.captions:
        if cap.end <= at:
            captions.append(cap)
        elif cap.start >= at:
            cap.start += duration
            cap.end += duration
            for word in cap.words:
                word.start += duration
                word.end += duration
            captions.append(cap)
        else:
            left, right = deepcopy(cap), deepcopy(cap)
            left.end, right.start, right.end = at, at + duration, cap.end + duration
            right.id = new_id("caption")
            left.words = [CaptionWord(w.text, w.start, min(w.end, at)) for w in cap.words if w.start < at]
            right.words = [CaptionWord(w.text, max(w.start, at) + duration, w.end + duration)
                           for w in cap.words if w.end > at]
            if cap.words:
                left.text = "".join(w.text for w in left.words)
                right.text = "".join(w.text for w in right.words)
            captions.extend(c for c in (left, right) if c.text.strip())
    seq.captions = captions
    for marker in seq.markers:
        if marker.time >= at:
            marker.time += duration
    if seq.multicam:
        for cut in seq.multicam.get("cuts", []):
            if seconds(cut.get("time", 0)) >= at:
                cut["time"] = (seconds(cut["time"]) + duration).to_json()
    for mix in [*(track.audio_mix for track in seq.tracks), getattr(seq, "master_audio", None),
                *(bus.get("mix", {}) for bus in getattr(seq, "audio_buses", []))]:
        if mix and mix.get("envelope"):
            points = mix["envelope"]
            s, delta = float(at.to_fraction()), float(duration.to_fraction())
            ordered = sorted(points, key=lambda p: p["time"])
            gain = ordered[0]["gainDb"]
            for a, b in zip(ordered, ordered[1:]):
                if s >= b["time"]:
                    gain = b["gainDb"]
                elif a["time"] <= s:
                    gain = a["gainDb"] + (b["gainDb"] - a["gainDb"]) * (s-a["time"])/(b["time"]-a["time"])
                    break
            mapped = [{**p, "time": p["time"] + (delta if p["time"] >= s else 0)} for p in points]
            mapped.extend([{"time": s, "gainDb": gain}, {"time": s+delta, "gainDb": gain}])
            mix["envelope"] = list({p["time"]: p for p in sorted(mapped, key=lambda p: p["time"])}.values())
    changed.append({"type": "sequence", "id": seq.id, "change": "space_inserted"})
    return changed


def crop_clip(service, original: Clip, start: Rational, end: Rational) -> Clip:
    """Keep an original timeline interval, including reverse/curved source maps."""
    clip = deepcopy(original)
    if original.speed_curve is not None:
        service._crop_curve_clip(clip, start, end)
    else:
        # Reverse clips store the lower source boundary; retain that convention.
        offset = ((start - original.timeline_start) if original.speed > Rational.of(0)
                  else (original.timeline_end - end))
        if original.freeze_at is None:
            clip.source_start = original.source_start + offset * Rational.of(
                abs(original.speed.num), original.speed.den)
        clip.timeline_start, clip.timeline_end = start, end
        service._rebase_effect_ranges(clip, original.timeline_start,
                                     original.timeline_end, start, end)
    from cutvoke.domain.keyframes import crop_keyframes
    clip.keyframes = {name: crop_keyframes(points, start - original.timeline_start,
                                         end - original.timeline_start)
                      for name, points in original.keyframes.items()}
    if clip.frame_interpolation == "motion" and not clip.has_slow_motion:
        clip.frame_interpolation = "none"
    if ((start != original.timeline_start or end != original.timeline_end)
            and (original.fade_in > Rational.of(0) or original.fade_out > Rational.of(0))):
        clip.audio_fade_duration = original.audio_fade_duration or original.duration
        clip.audio_fade_offset = original.audio_fade_offset + start - original.timeline_start
    return clip


def delete_range(service, seq: Sequence, start: Rational, end: Rational) -> list[dict]:
    """Remove [start,end) across every lane, captions, markers and camera cuts.

    Locks are a hard boundary: refuse the entire operation instead of silently
    desynchronizing protected media. The service commits a candidate snapshot.
    """
    zero = Rational.of(0)
    if start < zero or end <= start:
        raise ValueError("删除区间必须满足 0 ≤ 开始 < 结束")
    extent = max([zero] + [c.timeline_end for t in seq.tracks for c in t.clips]
                 + [c.end for c in seq.captions])
    if end > extent:
        raise ValueError("删除区间超出时间线末尾")
    for track in seq.tracks:
        if track.locked and any(c.timeline_end > start for c in track.clips):
            raise ValueError(f"轨道 {track.id} 已锁定，请解锁后执行联动删除")
    duration = end - start
    def mapped(at):
        return at if at < start else max(start, at - duration)
    changed: list[dict] = []
    replacements: dict[str, list[Clip]] = {}
    for track in seq.tracks:
        retained: list[Clip] = []
        for original in track.clips:
            if original.timeline_end <= start:
                retained.append(original)
                replacements[original.id] = [original]
                continue
            pieces = []
            if original.timeline_start < start:
                pieces.append(crop_clip(service, original, original.timeline_start, start))
            if original.timeline_end > end:
                right = crop_clip(service, original, max(end, original.timeline_start),
                                  original.timeline_end)
                right.timeline_start = right.timeline_start - duration
                right.timeline_end = right.timeline_end - duration
                if pieces:
                    right.id = new_id("clip")
                    if right.nested is not None:
                        from cutvoke.editing.compound import fresh_nested
                        right.nested = fresh_nested(right.nested)
                pieces.append(right)
            replacements[original.id] = pieces
            retained.extend(pieces)
            changed.append({"type": "clip", "id": original.id,
                            "change": "updated" if pieces else "deleted"})
            for piece in pieces[1:]:
                changed.append({"type": "clip", "id": piece.id,
                                "trackId": track.id, "change": "created"})
        track.clips = sorted(retained, key=lambda c: c.timeline_start)
        mix = getattr(track, "audio_mix", None)
        if mix:
            _map_envelope(mix, start, end)
    for track in seq.tracks:
        for clip in track.clips:
            if clip.attached_to_clip_id in replacements:
                anchors = replacements[clip.attached_to_clip_id]
                anchor = max(anchors, key=lambda a: float((min(a.timeline_end, clip.timeline_end)
                              - max(a.timeline_start, clip.timeline_start)).to_fraction()),
                             default=None)
                clip.attached_to_clip_id = anchor.id if anchor else None
    captions = []
    for original in seq.captions:
        if original.end <= start:
            captions.append(original)
            continue
        if original.start >= start and original.end <= end:
            changed.append({"type": "caption", "id": original.id, "change": "deleted"})
            continue
        cap = deepcopy(original)
        cap.start, cap.end = mapped(original.start), mapped(original.end)
        words = []
        for word in original.words:
            if word.start >= start and word.end <= end:
                continue
            ws, we = mapped(word.start), mapped(word.end)
            if we > ws:
                words.append(CaptionWord(word.text, ws, we))
        cap.words = words
        if original.words:
            cap.text = "".join(w.text for w in words)
        if cap.end > cap.start and cap.text.strip():
            captions.append(cap)
        changed.append({"type": "caption", "id": cap.id, "change": "updated"})
    seq.captions = captions
    for marker in seq.markers:
        marker.time = mapped(marker.time)
    if seq.multicam:
        cuts = seq.multicam.get("cuts", [])
        # Keep the camera active at the retained right edge.
        active = seq.multicam.get("activeTrackId")
        mapped_cuts = {}
        for cut in sorted(cuts, key=lambda c: float(seconds(c.get("time", 0)).to_fraction())):
            at = seconds(cut.get("time", 0))
            if at <= end:
                active = cut.get("trackId", active)
            if at < start or at >= end:
                new_cut = deepcopy(cut)
                new_cut["time"] = mapped(at).to_json()
                mapped_cuts[float(mapped(at).to_fraction())] = new_cut
        if active:
            mapped_cuts[float(start.to_fraction())] = {
                "time": start.to_json(), "trackId": active}
        seq.multicam["cuts"] = [mapped_cuts[t] for t in sorted(mapped_cuts)]
    for mix in [getattr(seq, "master_audio", None),
                *(bus.get("mix", {}) for bus in getattr(seq, "audio_buses", []))]:
        if mix:
            _map_envelope(mix, start, end)
    changed.append({"type": "sequence", "id": seq.id, "change": "range_deleted",
                    "start": start.to_json(), "end": end.to_json()})
    return changed


def _map_envelope(mix, start, end):
    """Remove time from sequence-clock gain automation without duplicate nodes."""
    points = mix.get("envelope", [])
    if not points:
        return
    s, e = float(start.to_fraction()), float(end.to_fraction())
    duration = e - s
    def gain(at):
        ordered = sorted(points, key=lambda p: p["time"])
        if at <= ordered[0]["time"]:
            return ordered[0]["gainDb"]
        for a, b in zip(ordered, ordered[1:]):
            if at <= b["time"]:
                ratio = (at-a["time"]) / max(1e-12, b["time"]-a["time"])
                return a["gainDb"] + ratio * (b["gainDb"]-a["gainDb"])
        return ordered[-1]["gainDb"]
    mapped = {p["time"] if p["time"] < s else p["time"]-duration: deepcopy(p)
              for p in points if p["time"] < s or p["time"] >= e}
    mapped[s] = {"time": s, "gainDb": gain(e)}
    # The removed interval may join different gain levels. Preserve the left
    # limit until the last audio sample instead of stretching a new ramp over
    # the entire retained region before the cut.
    if s > 0:
        left = max(0., s - 1 / 48000)
        mapped[left] = {"time": left, "gainDb": gain(left)}
    for at, point in mapped.items():
        point["time"] = at
    mix["envelope"] = [mapped[t] for t in sorted(mapped)]
