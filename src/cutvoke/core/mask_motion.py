"""Seek-safe motion of a matte, independent of the underlying picture.

Positions are canvas fractions; rotation and scale pivot at canvas centre.
Minimum jerk has zero velocity and acceleration at both ends. Inertia uses
constant acceleration, a cruise, and equal braking distance (continuous velocity).
No simulation state, random values, or frame history is involved.
"""
from __future__ import annotations

import re


def progress(time: float, start: float, duration: float, curve: str,
             ramp: float = .25) -> float:
    u = min(1., max(0., (time - start) / duration))
    if curve == "minimum-jerk":
        return u ** 3 * (10 - 15 * u + 6 * u * u)
    if curve == "inertia":
        if u < ramp:
            return u * u / (2 * ramp * (1 - ramp))
        if u > 1 - ramp:
            return 1 - (1 - u) ** 2 / (2 * ramp * (1 - ramp))
        return (u - ramp / 2) / (1 - ramp)
    return u


def coordinates(params: dict, clock: str = "T") -> tuple[str, str, str]:
    """Return geq initialization and inverse-mapped matte coordinates.

    geq registers are private to each expression. Compute the time curve and
    matrix once per pixel rather than expand polynomials at every path vertex.
    RGB and original-alpha sampling keep their untransformed coordinates.
    """
    curve = params.get("motion", "none")
    if curve == "none":
        return "", "X", "Y"
    start = float(params.get("motionStart", 0))
    duration = max(.001, float(params.get("motionDuration", 1)))
    ramp = float(params.get("motionRamp", .25))
    u = "ld(0)"
    if curve == "minimum-jerk":
        eased = f"{u}*{u}*{u}*(10-15*{u}+6*{u}*{u})"
    elif curve == "inertia":
        denominator = 2 * ramp * (1 - ramp)
        eased = (f"if(lt({u},{ramp:.9f}),{u}*{u}/{denominator:.9f},"
                 f"if(gt({u},{1-ramp:.9f}),1-(1-{u})*(1-{u})/{denominator:.9f},"
                 f"({u}-{ramp/2:.9f})/{1-ramp:.9f}))")
    else:
        eased = u

    def tween(name: str, default: float = 0) -> str:
        a = float(params.get("motionFrom" + name, default))
        b = float(params.get("motionTo" + name, default))
        return f"({a:.9f}+({b-a:.9f})*ld(1))"

    dx, dy = f"{tween('X')}*W", f"{tween('Y')}*H"
    angle = f"{tween('Rotation')}*PI/180"
    initial = (f"st(0,clip(({clock}-{start:.9f})/{duration:.9f},0,1));"
               f"st(1,{eased});st(4,cos({angle}));st(5,sin({angle}));"
               f"st(6,{tween('Scale', 1)});"
               f"st(2,((X-W/2-{dx})*ld(4)+(Y-H/2-{dy})*ld(5))/ld(6)+W/2);"
               f"st(3,(-(X-W/2-{dx})*ld(5)+(Y-H/2-{dy})*ld(4))/ld(6)+H/2);")
    return initial.replace(",", "\\,"), "ld(2)", "ld(3)"


def animate_expression(mask: str, params: dict) -> str:
    initial, x, y = coordinates(params)
    if not initial:
        return mask
    transformed = re.sub(r"\b[XY]\b", lambda m: x if m[0] == "X" else y, mask)
    return initial + transformed


def schema() -> dict:
    def number(default, minimum, maximum, zh, en, unit=None):
        result = dict(type="number", default=default, minimum=minimum, maximum=maximum,
                      description={"zh-CN": zh, "en": en})
        if unit:
            result["unit"] = unit
        return result
    props = {
        "motion": dict(type="string", enum=["none", "minimum-jerk", "inertia", "linear"],
                       default="none", description={"zh-CN": "蒙版运动曲线", "en": "Matte motion curve"},
                       **{"x-cutvoke-enum-labels": {
                           "none": {"zh-CN": "静态", "en": "Static"},
                           "minimum-jerk": {"zh-CN": "柔和加速与刹停", "en": "Minimum jerk"},
                           "inertia": {"zh-CN": "加速 · 巡航 · 制动", "en": "Accelerate · cruise · brake"},
                           "linear": {"zh-CN": "匀速", "en": "Linear"}}}),
        "motionStart": number(0, 0, 3600, "运动起始时间", "Motion start", "s"),
        "motionDuration": number(1, .01, 3600, "运动持续时间", "Motion duration", "s"),
        "motionRamp": number(.25, .05, .45, "加速减速时间比例", "Acceleration/braking fraction"),
    }
    for suffix, default, low, high, zh, en in [
        ("X", 0, -4, 4, "横向位移", "horizontal offset"),
        ("Y", 0, -4, 4, "纵向位移", "vertical offset"),
        ("Scale", 1, .01, 12, "缩放", "scale"),
        ("Rotation", 0, -360, 360, "旋转", "rotation"),
    ]:
        props["motionFrom" + suffix] = number(default, low, high, "起始" + zh, "Initial " + en)
        props["motionTo" + suffix] = number(default, low, high, "结束" + zh, "Final " + en)
    return props
