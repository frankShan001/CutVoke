"""有理数时间模型（任务书 ADR03，accepted 硬性要求）。

权威时间用归一化有理数，分母大于零，约分后保存。JSON 中用十进制字符串
(num, den) 表示，避免 JavaScript 大整数精度损失。禁止用浮点秒累加。

帧率可以是 30000/1001，不能持久化成 29.97 再参与精确运算。
"""

from __future__ import annotations

from fractions import Fraction
from typing import NamedTuple


class Rational(NamedTuple):
    """归一化有理数。num/den，den > 0，已约分。"""

    num: int
    den: int

    @classmethod
    def of(cls, n: int, d: int = 1) -> "Rational":
        if d == 0:
            raise ValueError("rational denominator must be non-zero")
        f = Fraction(n, d)
        return cls(f.numerator, f.denominator)

    @classmethod
    def from_float(cls, x: float) -> "Rational":
        """从浮点数显式转换（仅用于外部输入，不参与权威运算）。"""
        f = Fraction(str(x))
        return cls(f.numerator, f.denominator)

    @classmethod
    def from_json(cls, num: str, den: str) -> "Rational":
        return cls.of(int(num), int(den))

    def to_json(self) -> dict[str, str]:
        return {"num": str(self.num), "den": str(self.den)}

    def to_fraction(self) -> Fraction:
        return Fraction(self.num, self.den)

    # ---- arithmetic (returns normalized Rational) ----
    def __add__(self, other: "Rational") -> "Rational":
        f = self.to_fraction() + other.to_fraction()
        return Rational(f.numerator, f.denominator)

    def __sub__(self, other: "Rational") -> "Rational":
        f = self.to_fraction() - other.to_fraction()
        return Rational(f.numerator, f.denominator)

    def __mul__(self, other: "Rational") -> "Rational":
        f = self.to_fraction() * other.to_fraction()
        return Rational(f.numerator, f.denominator)

    def __truediv__(self, other: "Rational") -> "Rational":
        f = self.to_fraction() / other.to_fraction()
        return Rational(f.numerator, f.denominator)

    # ---- comparison ----
    def __lt__(self, other: "Rational") -> bool:
        return self.to_fraction() < other.to_fraction()

    def __le__(self, other: "Rational") -> bool:
        return self.to_fraction() <= other.to_fraction()

    def __gt__(self, other: "Rational") -> bool:
        return self.to_fraction() > other.to_fraction()

    def __ge__(self, other: "Rational") -> bool:
        return self.to_fraction() >= other.to_fraction()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Rational):
            return NotImplemented
        return self.num == other.num and self.den == other.den

    def __hash__(self) -> int:
        return hash((self.num, self.den))

    def __repr__(self) -> str:
        return f"Rational({self.num}/{self.den})"


def snap_to_frame(time: Rational, frame_rate: Rational,
                  policy: str = "reject") -> tuple[Rational, bool]:
    """将时间对齐到序列帧边界。

    policy: reject | nearest | floor | ceil
    - reject: 不对齐则返回原值与 False
    - nearest: 最近帧（中点向外）
    - floor: 向前（不大于）
    - ceil: 向后（不小于）

    返回 (对齐后的时间, 是否对齐)。
    """
    # 帧时长 = 1 / frame_rate
    frame_dur = Rational(1, 1) / frame_rate
    # time / frame_dur = 帧位置（可能非整数）
    exact = time.to_fraction() / frame_dur.to_fraction()
    whole = exact.numerator // exact.denominator
    rem = exact - whole

    if rem == 0:
        return time, True

    if policy == "reject":
        return time, False

    if policy == "floor":
        return Rational(whole, 1) * frame_dur, False
    if policy == "ceil":
        return Rational(whole + 1, 1) * frame_dur, False
    if policy == "nearest":
        if rem * 2 >= 1:
            return Rational(whole + 1, 1) * frame_dur, False
        return Rational(whole, 1) * frame_dur, False

    raise ValueError(f"unknown snapPolicy: {policy}")


def frame_at(time: Rational, frame_rate: Rational, policy: str = "floor") -> int:
    """返回时间在给定帧率下对应的帧序号（整数）。"""
    frame_dur = Rational(1, 1) / frame_rate
    exact = time.to_fraction() / frame_dur.to_fraction()
    q = exact.numerator // exact.denominator
    if policy == "floor":
        return q
    if policy == "ceil":
        return q + (0 if exact.numerator % exact.denominator == 0 else 1)
    if policy == "nearest":
        rem = exact - q
        return q + (1 if rem * 2 >= 1 else 0)
    raise ValueError(f"unknown policy: {policy}")