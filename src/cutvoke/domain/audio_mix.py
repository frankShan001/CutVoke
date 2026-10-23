"""Validated, portable track/bus/master mixer state. Envelope time is sequence time."""
from __future__ import annotations

import copy
import math

AUDIO_EFFECTS = frozenset({"cutvoke.fx.equalizer", "cutvoke.fx.compressor", "cutvoke.fx.loudnorm", "cutvoke.fx.pan"})


def number(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a finite number in [{low}, {high}]")
    return float(value)


def validate_mix(value, *, allow_route=True):
    if not isinstance(value, dict):
        raise ValueError("audio mix must be an object")
    allowed = {"gainDb", "pan", "solo", "muted", "envelope", "effects"} | ({"busId"} if allow_route else set())
    if set(value) - allowed:
        raise ValueError(f"unknown audio mix fields: {sorted(set(value) - allowed)}")
    result = copy.deepcopy(value)
    for field, low, high in (("gainDb", -96, 24), ("pan", -1, 1)):
        if field in result:
            result[field] = number(result[field], low, high, field)
    for field in ("solo", "muted"):
        if field in result and not isinstance(result[field], bool):
            raise ValueError(f"{field} must be boolean")
    if "busId" in result and (not isinstance(result["busId"], str) or len(result["busId"]) > 128):
        raise ValueError("busId must be a string of at most 128 characters")
    if "envelope" in result:
        envelope = result["envelope"]
        if not isinstance(envelope, list) or len(envelope) > 1000:
            raise ValueError("envelope must have at most 1000 points")
        previous = -1
        for point in envelope:
            if not isinstance(point, dict) or set(point) != {"time", "gainDb"}:
                raise ValueError("envelope points require time and gainDb")
            time = number(point["time"], 0, 604800, "envelope time")
            number(point["gainDb"], -96, 24, "envelope gainDb")
            if time <= previous:
                raise ValueError("envelope times must be strictly increasing")
            previous = time
    if "effects" in result:
        if not isinstance(result["effects"], list) or len(result["effects"]) > 16:
            raise ValueError("mixer effects must have at most 16 entries")
        for effect in result["effects"]:
            if not isinstance(effect, dict) or set(effect) - {"effectId", "params", "enabled"}:
                raise ValueError("invalid mixer effect")
            if effect.get("effectId") not in AUDIO_EFFECTS or not isinstance(effect.get("params", {}), dict):
                raise ValueError("unsupported mixer effect")
            if "enabled" in effect and not isinstance(effect["enabled"], bool):
                raise ValueError("mixer effect enabled must be boolean")
    return result


def validate_buses(value):
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("audioBuses must have at most 32 entries")
    ids = set()
    for bus in value:
        if not isinstance(bus, dict) or set(bus) - {"id", "name", "mix"}:
            raise ValueError("invalid audio bus")
        if not isinstance(bus.get("id"), str) or not bus["id"] or bus["id"] in ids:
            raise ValueError("audio bus IDs must be unique nonempty strings")
        ids.add(bus["id"])
        if not isinstance(bus.get("name", ""), str):
            raise ValueError("audio bus name must be a string")
        validate_mix(bus.get("mix", {}), allow_route=False)
    return copy.deepcopy(value)
