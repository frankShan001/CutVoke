"""Original, bounded Vlog geometry and content-driven transitions.

The two-point lock is manually authored, never an automatic tracker. All
sampling uses the existing FFmpeg compositor; no model, asset or service is
required. Coordinates are normalized in the source frame before canvas fit.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _labels() -> dict:
    path = Path(__file__).resolve().parent.parent / "assets/effects/vlog-labels.json"
    return json.loads(path.read_text(encoding="utf-8"))


def builtin_vlog_effects():
    from cutvoke.rendering.effects import EffectSpec

    labels = _labels()
    from cutvoke.rendering.luma_transition import controls as luma_controls

    def number(key, default, low, high, *, unit=None, step=.01):
        result = {"type": "number", "default": default, "minimum": low,
                  "maximum": high, "description": labels[key],
                  "x-cutvoke-label": labels[key], "x-cutvoke-step": step}
        if unit:
            result["unit"] = unit
        return result

    def choice(key, values, default):
        return {"type": "string", "enum": values, "default": default,
                "description": labels[key], "x-cutvoke-label": labels[key],
                "x-cutvoke-enum-labels": {v: labels[v] for v in values}}

    time = lambda key, default: number(key, default, .05, 3600, unit="s", step=.05)
    point = lambda key, default: number(key, default, .2, .8)
    specifications = [
        ("fx.subjectLock", "构图", {"engine": "internal", "filter": "vlogSubjectLock"}, {
            "fromX": point("fromX", .4), "fromY": point("fromY", .5),
            "toX": point("toX", .6), "toY": point("toY", .5),
            "targetX": point("targetX", .5), "targetY": point("targetY", .5),
            "zoom": number("minimumZoom", 1.4, 1, 4, unit="×"),
            "start": number("start", 0, 0, 3600, unit="s", step=.05),
            "duration": time("pathDuration", 2),
            "path": choice("path", ["linear", "smooth"], "linear"),
        }, ("vlog", "subject lock", "manual anchor", "主体锁定", "手工锚定")),
        ("fx.punchIn", "动感", {"engine": "internal", "filter": "vlogPunchIn"}, {
            "anchorX": number("anchorX", .5, 0, 1),
            "anchorY": number("anchorY", .5, 0, 1),
            "zoom": number("zoom", 1.32, 1, 2.5, unit="×"),
            "start": number("start", .4, 0, 3600, unit="s", step=.05),
            "enter": number("enter", .18, .05, 3, unit="s"),
            "hold": number("hold", .65, 0, 10, unit="s", step=.05),
            "leave": number("leave", .28, .05, 3, unit="s"),
            "overshoot": number("overshoot", .6, 0, 2),
        }, ("vlog", "punch in", "comedy", "emphasis", "强调", "包袱", "推近")),
        ("transition.matchAlign", "运镜", {
            "engine": "ffmpeg-xfade", "filter": "xfade", "transition": "custom",
            "vlogPattern": "matchAlign"}, {
            "duration": number("transitionDuration", .5, .05, 2, unit="s"),
            "aX": number("aX", .35, .1, .9), "aY": number("aY", .5, .1, .9),
            "bX": number("bX", .65, .1, .9), "bY": number("bY", .5, .1, .9),
            "aScale": number("aScale", 1.25, .5, 3, unit="×"),
            "bScale": number("bScale", 1.25, .5, 3, unit="×"),
            "aAngle": number("aAngle", 0, -45, 45, unit="°", step=1),
            "bAngle": number("bAngle", 0, -45, 45, unit="°", step=1),
            "mixWidth": number("mixWidth", .2, .05, 1),
        }, ("vlog", "match cut", "anchor", "匹配剪辑", "对齐", "锚点")),
        ("transition.lumaReveal", "叠化", {
            "engine": "ffmpeg-xfade", "filter": "xfade", "transition": "custom",
            "vlogPattern": "lumaReveal"}, {
            **luma_controls(),
            "duration": number("transitionDuration", .6, .05, 2, unit="s"),
            "softness": number("softness", .08, .005, .35, step=.005),
            "reverse": {"type": "boolean", "default": False,
                        "description": labels["reverse"], "x-cutvoke-label": labels["reverse"]},
            "keySource": choice("keySource", ["outgoing", "incoming"], "outgoing"),
            "curve": choice("curve", ["linear", "smooth"], "smooth"),
        }, ("vlog", "luma", "luminance", "亮度", "明暗", "揭示")),
    ]
    return [EffectSpec(
        id="cutvoke." + leaf, version="1.0.0", name=labels[leaf + ".name"],
        description=labels[leaf + ".description"], category=leaf.split(".")[0],
        license="MIT", inputs=("video:a", "video:b") if leaf.startswith("transition.") else ("video",),
        output="video", source="builtin", applies_to=("image", "video"),
        subcategory=group, keywords=words, implementation=implementation,
        parameters={"type": "object", "properties": properties, "additionalProperties": False},
    ) for leaf, group, implementation, properties, words in specifications]


def _smooth(value: str) -> str:
    return f"(({value})*({value})*(3-2*({value})))"


def subject_lock_zoom(params: dict) -> float:
    """Minimum safe crop for every point of the bounded two-point path."""
    needed = [float(params.get("zoom", 1.4))]
    for axis, default in (("X", .4), ("Y", .5)):
        target = float(params.get("target" + axis, .5))
        for key, fallback in (("from", default), ("to", .6 if axis == "X" else .5)):
            position = float(params.get(key + axis, fallback))
            needed.extend((target / position, (1 - target) / (1 - position)))
    return max(needed)


def geometry_filter(key: str, params: dict) -> str:
    """Inverse-map pixels with bilinear sampling and the same alpha transform.

    geq's per-plane W/H make the normalized map valid for both subsampled video
    and full-resolution alpha. The clip-local timestamp T survives windowing.
    """
    if key == "vlogSubjectLock":
        start = float(params.get("start", 0))
        duration = float(params.get("duration", 2))
        phase = f"clip((T-{start:.9f})/{duration:.9f},0,1)"
        if params.get("path", "linear") == "smooth":
            phase = _smooth(phase)
        zoom = subject_lock_zoom(params)
        positions = []
        for axis, size, pixel, a, b in (("X", "W", "X", .4, .6), ("Y", "H", "Y", .5, .5)):
            source = float(params.get("from" + axis, a))
            delta = float(params.get("to" + axis, b)) - source
            target = float(params.get("target" + axis, .5))
            positions.append(f"({pixel}-{target:.9f}*({size}-1))/{zoom:.9f}+"
                             f"({source:.9f}+{delta:.9f}*({phase}))*({size}-1)")
        x, y = positions
    elif key == "vlogPunchIn":
        start = float(params.get("start", .4))
        enter = float(params.get("enter", .18))
        hold = float(params.get("hold", .65))
        leave = float(params.get("leave", .28))
        overshoot = float(params.get("overshoot", .6))
        u = f"clip((T-{start:.9f})/{enter:.9f},0,1)"
        # Ease-out-back reaches exactly one, then holds; return is C1 smooth.
        attack = f"(1+{1+overshoot:.9f}*pow(({u})-1,3)+{overshoot:.9f}*pow(({u})-1,2))"
        v = f"clip((T-{start+enter+hold:.9f})/{leave:.9f},0,1)"
        zoom = f"(1+{float(params.get('zoom', 1.32))-1:.9f}*({attack})*(1-{_smooth(v)}))"
        ax = float(params.get("anchorX", .5)); ay = float(params.get("anchorY", .5))
        x = f"(X-{ax:.9f}*(W-1))/{zoom}+{ax:.9f}*(W-1)"
        y = f"(Y-{ay:.9f}*(H-1))/{zoom}+{ay:.9f}*(H-1)"
    else:
        raise ValueError(f"unsupported Vlog geometry: {key}")
    expressions = ":".join(f"{option}='{sample}({x},{y})'" for option, sample in
                           (("lum", "lum"), ("cb", "cb"), ("cr", "cr"), ("a", "alpha")))
    return f"geq={expressions}:interpolation=bilinear"


def transition_expr(pattern: str, params: dict, *, has_alpha=False) -> str:
    """xfade P runs from one (outgoing) to zero (incoming)."""
    if pattern == "lumaReveal":
        phase = "(1-P)"
        if params.get("curve", "smooth") == "smooth":
            phase = _smooth(phase)
        source = "a" if params.get("keySource", "outgoing") == "outgoing" else "b"
        luma = f"clip(({source}0(X,Y)-16)/219,0,1)"
        if params.get("reverse", False):
            luma = f"(1-({luma}))"
        soft = float(params.get("softness", .08))
        threshold = f"(1+{soft:.9f}-(1+{2*soft:.9f})*{phase})"
        mask = f"clip((({luma})-{threshold}+{soft:.9f})/{2*soft:.9f},0,1)"
        # xfade shares one AVExpr across slice workers. st/ld registers would
        # race between pixels and produce colored noise; keep the mask pure.
        return f"A*(1-({mask}))+B*({mask})"
    if pattern != "matchAlign":
        raise ValueError(f"unsupported Vlog transition: {pattern}")

    def sample(source, weight, default_x):
        ax = float(params.get(source + "X", default_x))
        ay = float(params.get(source + "Y", .5))
        scale = float(params.get(source + "Scale", 1.25))
        angle = float(params.get(source + "Angle", 0)) * 3.141592653589793 / 180
        dx = f"(X-({ax:.9f}+{.5-ax:.9f}*{weight})*(W-1))"
        dy = f"(Y-({ay:.9f}+{.5-ay:.9f}*{weight})*(H-1))"
        z = f"(1+{scale-1:.9f}*{weight})"
        theta = f"({angle:.9f}*{weight})"
        x = f"{ax:.9f}*(W-1)+({dx}*cos({theta})+{dy}*sin({theta}))/{z}"
        y = f"{ay:.9f}*(H-1)+(-{dx}*sin({theta})+{dy}*cos({theta}))/{z}"
        # Reflection supplies deliberate, bounded edge extension during affine
        # motion. Both endpoints are the untouched original frame.
        x = f"(W-1)-abs(mod(abs({x}),2*(W-1))-(W-1))"
        y = f"(H-1)-abs(mod(abs({y}),2*(H-1))-(H-1))"
        planes = [f"{source}{plane}({x},{y})" for plane in range(4 if has_alpha else 3)]
        result = planes[-1]
        for plane in range(len(planes)-2, -1, -1):
            result = f"if(eq(PLANE,{plane}),{planes[plane]},{result})"
        return result

    a = sample("a", "ld(0)", .35)
    b = sample("b", "ld(1)", .65)
    width = float(params.get("mixWidth", .2))
    mix = _smooth(f"clip((1-P-{.5-width/2:.9f})/{width:.9f},0,1)")
    return (f"st(0,{_smooth('clip(2*(1-P),0,1)')});"
            f"st(1,{_smooth('clip(2*P,0,1)')});st(2,{mix});"
            f"({a})*(1-ld(2))+({b})*ld(2)")


def preview_time(spec, duration: float, overrides: dict | None = None,
                 *, fps: float = 30) -> float | None:
    """Show the authored action, even when the chosen clip is much longer."""
    key = spec.implementation.get("filter")
    params = {**spec.default_params(), **(overrides or {})}
    if key == "vlogPunchIn":
        at = params["start"] + params["enter"] + params["hold"] / 2
    elif key == "vlogSubjectLock":
        at = params["start"] + params["duration"] / 2
    else:
        return None
    # The action can occur after the clip midpoint. Clamp only to a covered
    # frame at the end; seeking to the end-exclusive boundary yields no image.
    return min(at, max(0, duration - 1 / fps))
