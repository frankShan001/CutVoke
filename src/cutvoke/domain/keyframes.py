"""Clip-local numeric curves shared by editing, exact cropping and FFmpeg.

Support linear, ease-in/out, cubic Bezier and hold interpolation; values remain
at their endpoints outside the curve. Time stays rational throughout edits.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import List

from cutvoke.domain.rational import Rational


@dataclass
class Keyframe:
    """单个参数关键帧：片段局部呈现时间 + 值 + 插值方式。

    time 是「片段局部呈现时间」(presentation time，不是工程时间)，与 7.2 一致。
    """

    id: str
    time: Rational
    value: float
    interpolation: str = "linear"  # linear | ease-in | ease-out | cubic-bezier | hold
    bezier: tuple[float, float, float, float] | None = None

    VALID_INTERP = ("linear", "ease-in", "ease-out", "cubic-bezier", "hold")

    def __post_init__(self):
        if self.interpolation == "cubic-bezier":
            self.bezier = validate_bezier(self.bezier)

    def to_dict(self) -> dict:
        result = {
            "id": self.id,
            "time": self.time.to_json(),
            "value": self.value,
            "interpolation": self.interpolation,
        }
        if self.interpolation == "cubic-bezier":
            result["bezier"] = list(self.bezier)
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "Keyframe":
        interp = d.get("interpolation", "linear")
        if interp not in cls.VALID_INTERP:
            # 未知插值方式回退线性，保证可解析（M2 完整版扩展）
            interp = "linear"
        return cls(
            id=d["id"],
            time=Rational.from_json(**d["time"]),
            value=float(d["value"]),
            interpolation=interp,
            bezier=d.get("bezier"),
        )


def evaluate(keyframes: List[Keyframe], time: Rational) -> float:
    """对一组关键帧在片段局部时间 time 处求值（线性插值）。

    - 少于一个关键帧：报错（无定义）
    - time 在首关键帧之前：取首关键帧值（端点保持）
    - time 在末关键帧之后：取末关键帧值（端点保持）
    - 否则：相邻关键帧间线性插值

    每段使用起始关键帧声明的插值方式；ease-in/ease-out 在段内生效。
    """
    if hasattr(keyframes, "evaluator"):
        return keyframes.evaluator(float(time.to_fraction()))
    if not keyframes:
        raise ValueError("evaluate requires at least one keyframe")
    kfs = sorted(keyframes, key=lambda k: k.time.to_fraction())
    t = time.to_fraction()
    if t <= kfs[0].time.to_fraction():
        return kfs[0].value
    if t >= kfs[-1].time.to_fraction():
        return kfs[-1].value
    for i in range(len(kfs) - 1):
        a, b = kfs[i], kfs[i + 1]
        ta = a.time.to_fraction()
        tb = b.time.to_fraction()
        if ta <= t <= tb:
            if tb == ta:
                return b.value
            frac = float((t - ta) / (tb - ta))
            frac = ease(frac, a.interpolation, a.bezier)
            return a.value + frac * (b.value - a.value)
    return kfs[-1].value


def expression(keyframes: List[Keyframe], time: str, default: float) -> str:
    """Compile the same endpoint holds and easing to an FFmpeg expression.

    Keep presentation time and values fractional: rounding positions or holding
    two output frames at a sampled midpoint makes slow camera moves judder.
    """
    if hasattr(keyframes, "formula"):
        return keyframes.formula.replace("__TIME__", f"({time})")
    if not keyframes:
        return f"{default:.12g}"
    kfs = sorted(keyframes, key=lambda keyframe: keyframe.time.to_fraction())
    result = f"{kfs[-1].value:.12g}"
    for a, b in reversed(list(zip(kfs, kfs[1:]))):
        start = float(a.time.to_fraction())
        end = float(b.time.to_fraction())
        if end <= start:
            continue
        progress = f"(({time})-{start:.12g})/{end - start:.12g}"
        if a.interpolation == "ease-in":
            progress = f"pow({progress},2)"
        elif a.interpolation == "ease-out":
            progress = f"(1-pow(1-({progress}),2))"
        elif a.interpolation == "hold":
            progress = "0"
        elif a.interpolation == "cubic-bezier":
            x1, y1, x2, y2 = a.bezier
            bx = f"(3*(1-ld(3)))*(1-ld(3))*ld(3)*{x1:.12g}+3*(1-ld(3))*ld(3)*ld(3)*{x2:.12g}+pow(ld(3),3)"
            steps = [f"st(0,clip({progress},0,1))", "st(1,0)", "st(2,1)", "st(5,0)",
                "while(lt(ld(5),22),st(3,(ld(1)+ld(2))/2);"
                f"st(4,lt({bx},ld(0)));"
                "st(1,if(ld(4),ld(3),ld(1)));st(2,if(ld(4),ld(2),ld(3)));st(5,ld(5)+1))"]
            steps.append(f"(3*(1-ld(3))*(1-ld(3))*ld(3)*{y1:.12g}+3*(1-ld(3))*ld(3)*ld(3)*{y2:.12g}+pow(ld(3),3))")
            progress = "(" + ";".join(steps) + ")"
        value = f"({a.value:.12g}+({b.value - a.value:.12g})*({progress}))"
        result = f"if(lt({time},{end:.12g}),{value},{result})"
    first = float(kfs[0].time.to_fraction())
    return f"if(lte({time},{first:.12g}),{kfs[0].value:.12g},{result})"


def validate_bezier(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) or not 0 <= v <= 1 for v in value)):
        raise ValueError("bezier requires four finite control coordinates in [0, 1]")
    return tuple(float(v) for v in value)


def _coordinate(u, a, b):
    return 3*(1-u)**2*u*a + 3*(1-u)*u*u*b + u**3


def _inverse_x(x, controls):
    low, high = 0.0, 1.0
    for _ in range(40):
        u = (low+high)/2
        if _coordinate(u, controls[0], controls[2]) < x:
            low = u
        else:
            high = u
    return (low+high)/2


def ease(progress, interpolation="linear", bezier=None):
    progress = min(1.0, max(0.0, progress))
    if interpolation == 'hold':
        return 1.0 if progress >= 1 else 0.0
    if interpolation == "ease-in":
        return progress**2
    if interpolation == "ease-out":
        return 1-(1-progress)**2
    if interpolation == "cubic-bezier":
        controls = validate_bezier(bezier)
        return _coordinate(_inverse_x(progress, controls), controls[1], controls[3])
    return progress


def crop_keyframes(keyframes, start: Rational, end: Rational):
    """Exact easing subcurves for a retained clip-local interval, rebased to zero."""
    if not keyframes:
        return []
    if end <= start:
        raise ValueError("keyframe crop requires end > start")
    keys = sorted(keyframes, key=lambda k: k.time.to_fraction())
    times = sorted({start.to_fraction(), end.to_fraction(),
                    *(k.time.to_fraction() for k in keys if start < k.time < end)})
    def split(points, u):
        rows = [points]
        while len(rows[-1]) > 1:
            rows.append([tuple((1-u)*a[i]+u*b[i] for i in (0, 1))
                         for a, b in zip(rows[-1], rows[-1][1:])])
        return [row[0] for row in rows], [row[-1] for row in reversed(rows)]
    result = []
    for index, time in enumerate(times):
        stamp = Rational.of(time.numerator, time.denominator)
        old = next((k for k in keys if k.time == stamp), None)
        frame = Keyframe(old.id if old else f"{keys[0].id}_crop_{index}",
                         stamp-start, evaluate(keys, stamp))
        if index < len(times)-1:
            nxt = times[index+1]
            pair = next(((a,b) for a,b in zip(keys,keys[1:])
                         if a.time.to_fraction() <= time < b.time.to_fraction()), None)
            if pair and pair[0].value != pair[1].value:
                a,b = pair
                if a.interpolation == 'hold':
                    frame.interpolation = 'hold'
                    result.append(frame)
                    continue
                controls = (a.bezier if a.interpolation == "cubic-bezier" else
                    (1/3,0,2/3,1/3) if a.interpolation == "ease-in" else
                    (1/3,2/3,2/3,1) if a.interpolation == "ease-out" else (1/3,1/3,2/3,2/3))
                ta,tb = a.time.to_fraction(),b.time.to_fraction()
                u0 = _inverse_x(float((time-ta)/(tb-ta)), controls)
                u1 = _inverse_x(float((nxt-ta)/(tb-ta)), controls)
                points = [(0.,0.), controls[:2], controls[2:], (1.,1.)]
                left,_ = split(points,u1)
                _,sub = split(left,u0/u1 if u1 else 0)
                dx,dy = sub[-1][0]-sub[0][0],sub[-1][1]-sub[0][1]
                if dx > 1e-12 and abs(dy) > 1e-12:
                    normalized = [(min(1.,max(0.,(p[0]-sub[0][0])/dx)),
                                   min(1.,max(0.,(p[1]-sub[0][1])/dy))) for p in sub[1:3]]
                    frame.interpolation = "cubic-bezier"
                    frame.bezier = (*normalized[0],*normalized[1])
        result.append(frame)
    return result


# Stable effect-instance lanes. Unsupported effects remain explicit errors.
ANIMATABLE_EFFECTS = {
    "cutvoke.color": {"brightness": (-1., 1., 0.), "contrast": (0., 2., 1.), "saturation": (0., 3., 1.)},
    "cutvoke.fx.blur": {"sigma": (1., 20., 5.)},
}


def effect_instance_id(effect, index):
    return effect.get("instanceId") or f"legacy_{index}"


def parameter_spec(clip, param):
    base = {"opacity": (0.,1.,1.), "x": (-100000.,100000.,0.), "y": (-100000.,100000.,0.),
            "scale": (.05,5.,1.), "rotation": (-180.,180.,0.), "volume": (0.,8.,1.)}
    if param in base:
        return base[param]
    fields = param.split(":")
    if len(fields) == 3 and fields[0] == "effect":
        for index,effect in enumerate(clip.effects):
            if effect_instance_id(effect,index) == fields[1]:
                spec = ANIMATABLE_EFFECTS.get(effect.get("effectId"),{}).get(fields[2])
                if spec:
                    return spec[:2] + (float(effect.get("params",{}).get(fields[2],spec[2])),)
    raise ValueError(f"unsupported keyframe parameter or effect instance: {param}")
