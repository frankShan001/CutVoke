"""参数关键帧数据模型（任务书 T22 / 第 7.2 章）。

M2 完整版之前的最小实现：先落地数据结构 + 线性插值求值，渲染生效留 M2。

- Keyframe: 片段局部呈现时间(time: Rational) + value(float) + interpolation
- 一个片段按参数名分组持有多组关键帧（先支持 opacity 这一种参数作为最小落地）
- evaluate: 对一组关键帧在片段局部时间 time 处求值，线性插值
  （首末关键帧之外保持端点值，符合 7.2「参数曲线在首末关键帧之外保持端点值」）

不引入第三方依赖；有理数时间权威不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .rational import Rational


@dataclass
class Keyframe:
    """单个参数关键帧：片段局部呈现时间 + 值 + 插值方式。

    time 是「片段局部呈现时间」(presentation time，不是工程时间)，与 7.2 一致。
    """

    id: str
    time: Rational
    value: float
    interpolation: str = "linear"  # linear | ease-in | ease-out

    VALID_INTERP = ("linear", "ease-in", "ease-out")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "time": self.time.to_json(),
            "value": self.value,
            "interpolation": self.interpolation,
        }

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
        )


def evaluate(keyframes: List[Keyframe], time: Rational) -> float:
    """对一组关键帧在片段局部时间 time 处求值（线性插值）。

    - 少于一个关键帧：报错（无定义）
    - time 在首关键帧之前：取首关键帧值（端点保持）
    - time 在末关键帧之后：取末关键帧值（端点保持）
    - 否则：相邻关键帧间线性插值

    每段使用起始关键帧声明的插值方式；ease-in/ease-out 在段内生效。
    """
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
            if a.interpolation == "ease-in":
                frac *= frac
            elif a.interpolation == "ease-out":
                frac = 1.0 - (1.0 - frac) * (1.0 - frac)
            return a.value + frac * (b.value - a.value)
    return kfs[-1].value


def expression(keyframes: List[Keyframe], time: str, default: float) -> str:
    """Compile the same endpoint holds and easing to an FFmpeg expression.

    Keep presentation time and values fractional: rounding positions or holding
    two output frames at a sampled midpoint makes slow camera moves judder.
    """
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
        value = f"({a.value:.12g}+({b.value - a.value:.12g})*({progress}))"
        result = f"if(lt({time},{end:.12g}),{value},{result})"
    first = float(kfs[0].time.to_fraction())
    return f"if(lte({time},{first:.12g}),{kfs[0].value:.12g},{result})"
