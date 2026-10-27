"""Closed, portable compositing metadata shared by commands and renderers."""
from __future__ import annotations

import copy
import math

BLEND_MODES = ("normal", "multiply", "screen", "add", "difference")


def matte_source_ids(sequence, window=None):
    """Live dependency closure, including hidden source layers in their used time."""
    clips = {clip.id: clip for track in sequence.tracks for clip in track.clips}
    needed = set()
    def visit(clip, span, path=()):
        if clip.id in path: return
        setting = clip.compositing.get('matte')
        if not setting or setting.get('clipId') not in clips: return
        source = clips[setting['clipId']]
        start = max(span[0], float(source.timeline_start.to_fraction()))
        end = min(span[1], float(source.timeline_end.to_fraction()))
        if start >= end: return
        needed.add(source.id)
        visit(source, (start,end), (*path,clip.id))
    for track in sequence.tracks:
        if not track.visible or track.kind not in {'video','text'}: continue
        for clip in track.clips:
            if clip.hidden or clip.compositing.get('matteOnly'): continue
            start,end = float(clip.timeline_start.to_fraction()),float(clip.timeline_end.to_fraction())
            if window: start,end = max(start,window[0]),min(end,window[1])
            if start < end: visit(clip,(start,end))
    return needed


def number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a finite number in {low}..{high}")
    return float(value)


def effect_compositing(value):
    if not isinstance(value, dict) or set(value) - {"opacity", "blendMode", "region"}:
        raise ValueError("effect compositing accepts opacity, blendMode and region")
    result = copy.deepcopy(value)
    if "opacity" in value:
        result["opacity"] = number(value["opacity"], "opacity", 0, 1)
    if value.get("blendMode", "normal") not in BLEND_MODES:
        raise ValueError(f"blendMode must be one of {BLEND_MODES}")
    region = value.get("region")
    if region is not None:
        if not isinstance(region, dict) or set(region) - {"x", "y", "width", "height", "feather", "invert"}:
            raise ValueError("region accepts normalized x/y/width/height/feather and invert")
        for key in ("x", "y", "width", "height", "feather"):
            if key in region:
                number(region[key], key, 0, 1)
        if region.get("width", 1) <= 0 or region.get("height", 1) <= 0:
            raise ValueError("region width and height must be positive")
        if "invert" in region and not isinstance(region["invert"], bool):
            raise ValueError("region invert must be boolean")
    return result


def clip_compositing(value):
    if not isinstance(value, dict) or set(value) - {"matte", "matteOnly"}:
        raise ValueError("clip compositing accepts matte and matteOnly")
    if "matteOnly" in value and not isinstance(value["matteOnly"], bool):
        raise ValueError("matteOnly must be boolean")
    matte = value.get("matte")
    if matte is not None:
        if not isinstance(matte, dict) or set(matte) - {"clipId", "channel", "invert"}:
            raise ValueError("matte accepts clipId, channel and invert")
        if not isinstance(matte.get("clipId"), str) or not matte["clipId"]:
            raise ValueError("matte requires a stable clipId")
        if matte.get("channel", "alpha") not in {"alpha", "luma"}:
            raise ValueError("matte channel must be alpha or luma")
        if "invert" in matte and not isinstance(matte["invert"], bool):
            raise ValueError("matte invert must be boolean")
    return copy.deepcopy(value)


def validate_sequence(sequence):
    from cutvoke.domain.controllers import validate as validate_controllers
    validate_controllers(sequence)
    clips = {clip.id: (track, clip) for track in sequence.tracks for clip in track.clips}
    edges = {}
    for track, clip in clips.values():
        from cutvoke.domain.spatial_path import validate as validate_motion_path
        validate_motion_path(clip)
        if (track.role == "adjustment") != (clip.role == "adjustment"):
            raise ValueError("adjustment tracks can contain only adjustment clips")
        if clip.role == "adjustment":
            if track.kind != "video" or clip.nested is not None or clip.sequence_ref or clip.asset_ref.source_path:
                raise ValueError("adjustment clips require a source-free adjustment video track")
            transform = clip.transform()
            if transform["scale"] != 1 or transform["rotation"] != 0 or any(transform["position"].values()):
                raise ValueError("adjustment layers support opacity; use effect regions for spatial limits")
            if clip.motion_path or any(param in clip.keyframes or param in clip.property_links for param in ("x", "y", "scale", "rotation")):
                raise ValueError("adjustment geometry is controlled by effect regions")
            if any(effect.get('effectId', '').startswith(('cutvoke.anim.', 'cutvoke.transition.')) or effect.get('effectId') == 'cutvoke.text' for effect in clip.effects):
                raise ValueError("adjustment clips do not accept titles or clip entrance/transition animation")
        value = clip_compositing(clip.compositing)
        if value and track.kind not in {"video", "text"}:
            raise ValueError("compositing requires a visual clip")
        matte = value.get("matte")
        if matte:
            source = clips.get(matte["clipId"])
            if source is None or source[0].kind not in {"video", "text"} or source[1].role == "adjustment":
                raise ValueError(f"matte source {matte['clipId']} is missing or not a picture layer")
            edges[clip.id] = matte["clipId"]
        for effect in clip.effects:
            if "compositing" in effect:
                effect_compositing(effect["compositing"])
    for start in edges:
        seen = set()
        node = start
        while node in edges:
            if node in seen:
                raise ValueError(f"track matte dependency cycle at {node}")
            seen.add(node)
            node = edges[node]


def region_expression(region):
    if not region:
        return "1"
    x, y = region.get("x", 0), region.get("y", 0)
    right, bottom = x + region.get("width", 1), y + region.get("height", 1)
    feather = region.get("feather", 0)
    distance = f"min(min(X/W-{x:.9f},{right:.9f}-X/W),min(Y/H-{y:.9f},{bottom:.9f}-Y/H))"
    mask = f"clip(({distance})/{feather:.9f},0,1)" if feather else f"gte({distance},0)"
    return f"(1-({mask}))" if region.get("invert") else mask
