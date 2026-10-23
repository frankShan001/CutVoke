"""Track → optional subgroup → master. Shared by preview and final compilation."""
from __future__ import annotations


def envelope_expression(points, clock="t"):
    if not points:
        return "0"
    def segments(start, end):
        if end - start == 1:
            a, b = points[start], points[end]
            slope = (b["gainDb"] - a["gainDb"]) / (b["time"] - a["time"])
            return f"({a['gainDb']:.9f}+({clock}-{a['time']:.9f})*{slope:.12f})"
        middle = (start + end) // 2
        return f"if(lt({clock},{points[middle]['time']:.9f}),{segments(start,middle)},{segments(middle,end)})"
    expression = (segments(0, len(points)-1) if len(points) > 1 else f"{points[0]['gainDb']:.9f}")
    expression = f"if(gte({clock},{points[-1]['time']:.9f}),{points[-1]['gainDb']:.9f},{expression})"
    return f"if(lt({clock},{points[0]['time']:.9f}),{points[0]['gainDb']:.9f},{expression})"


def mix_filters(mix, origin, effect_filter):
    filters = []
    gain = mix.get("gainDb", 0)
    if mix.get("muted"):
        filters.append("volume=0")
    elif mix.get("envelope"):
        db = envelope_expression(mix["envelope"], f"(t+{origin:.9f})")
        filters.append(f"volume='pow(10,({gain:.9f}+{db})/20)':eval=frame")
    elif gain:
        filters.append(f"volume={10 ** (gain / 20):.12f}")
    pan = mix.get("pan", 0)
    if pan:
        filters.append(f"pan=stereo|c0={min(1, 1-pan):.9f}*c0|c1={min(1, 1+pan):.9f}*c1")
    for effect in mix.get("effects", []):
        if effect.get("enabled") is not False:
            filters.append(effect_filter(effect))
    return ",".join(filters) or "anull"


def audible_track_ids(seq):
    candidates = [t for t in seq.tracks if t.kind in ("audio", "video")]
    solo_tracks = {t.id for t in candidates if t.audio_mix.get("solo")}
    solo_buses = {b["id"] for b in seq.audio_buses if b.get("mix", {}).get("solo")}
    muted_buses = {b["id"] for b in seq.audio_buses if b.get("mix", {}).get("muted")}
    return {t.id for t in candidates if not t.audio_mix.get("muted") and
            t.audio_mix.get("busId", "") not in muted_buses and
            (not solo_tracks or t.id in solo_tracks) and
            (not solo_buses or t.audio_mix.get("busId") in solo_buses)}


def finish_mix(seq, track_ids, labels, parts, origin, effect_filter):
    tracks = {t.id: t for t in seq.tracks}
    buses = {bus["id"]: bus for bus in seq.audio_buses}
    routed = {}
    direct = []

    def combine(inputs, output):
        if len(inputs) == 1:
            parts.append(f"[{inputs[0]}]anull[{output}]")
        else:
            parts.append("".join(f"[{label}]" for label in inputs) +
                         f"amix=inputs={len(inputs)}:duration=longest:normalize=0[{output}]")

    for index, (track_id, label) in enumerate(zip(track_ids, labels)):
        mix = tracks[track_id].audio_mix
        output = f"mixtrack{index}"
        parts.append(f"[{label}]{mix_filters(mix, origin, effect_filter)}[{output}]")
        bus_id = mix.get("busId", "")
        if bus_id and bus_id not in buses:
            raise ValueError(f"audio track {track_id} routes to missing bus {bus_id}")
        if bus_id:
            routed.setdefault(bus_id, []).append(output)
        else:
            direct.append(output)
    for index, (bus_id, inputs) in enumerate(routed.items()):
        source, output = f"businput{index}", f"busoutput{index}"
        combine(inputs, source)
        parts.append(f"[{source}]{mix_filters(buses[bus_id].get('mix', {}), origin, effect_filter)}[{output}]")
        direct.append(output)
    combine(direct, "masterinput")
    parts.append(f"[masterinput]{mix_filters(seq.master_audio, origin, effect_filter)}[outa]")
