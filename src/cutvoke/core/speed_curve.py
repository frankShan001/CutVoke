"""Source-relative speed curves shared by edit, preview, export and ASR.

Control points are fractions of a fixed source span. Speeds interpolate
linearly in source time; the timeline mapping is the integral of 1/speed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .rational import Rational


def _rational(value: object) -> Rational:
    if isinstance(value, Rational):
        return value
    if isinstance(value, dict):
        return Rational.from_json(**value)
    if isinstance(value, bool):
        raise ValueError("curve value must be numeric")
    return Rational.from_float(float(value))


def _seconds(value: Rational) -> float:
    return float(value.to_fraction())


def _quantized(value: float) -> Rational:
    return Rational.of(round(value * 1_000_000), 1_000_000)


@dataclass(frozen=True)
class SpeedPoint:
    at: Rational
    speed: Rational

    def to_dict(self) -> dict:
        return {"at": self.at.to_json(), "speed": self.speed.to_json()}


@dataclass(frozen=True)
class SpeedCurve:
    source_duration: Rational
    points: tuple[SpeedPoint, ...]

    def __post_init__(self) -> None:
        if self.source_duration <= Rational.of(0):
            raise ValueError("curve source duration must be positive")
        if not 2 <= len(self.points) <= 12:
            raise ValueError("curve requires 2 to 12 control points")
        if self.points[0].at != Rational.of(0) or self.points[-1].at != Rational.of(1):
            raise ValueError("curve must start at 0 and end at 1")
        previous = Rational.of(-1)
        for point in self.points:
            if point.at <= previous or point.at > Rational.of(1):
                raise ValueError("curve positions must increase within 0..1")
            if point.speed < Rational.of(1, 10) or point.speed > Rational.of(8):
                raise ValueError("curve speeds must be within 0.1..8")
            previous = point.at

    @classmethod
    def from_points(cls, source_duration: Rational, points: list[dict]) -> "SpeedCurve":
        if not isinstance(points, list):
            raise ValueError("curve points must be a list")
        try:
            parsed = tuple(SpeedPoint(_rational(p["at"]), _rational(p["speed"]))
                           for p in points)
        except (KeyError, TypeError, OverflowError, ValueError) as exc:
            raise ValueError("curve point requires numeric at and speed") from exc
        return cls(source_duration, parsed)

    @classmethod
    def from_dict(cls, data: dict) -> "SpeedCurve":
        return cls.from_points(_rational(data["sourceDuration"]), data["points"])

    def to_dict(self) -> dict:
        return {"sourceDuration": self.source_duration.to_json(),
                "points": [point.to_dict() for point in self.points]}

    def _segments(self):
        duration = _seconds(self.source_duration)
        for left, right in zip(self.points, self.points[1:]):
            start = _seconds(left.at) * duration
            end = _seconds(right.at) * duration
            yield start, end, _seconds(left.speed), _seconds(right.speed)

    @staticmethod
    def _integral(offset: float, length: float, first: float, last: float) -> float:
        slope = (last - first) / length
        if abs(slope) < 1e-10:
            return offset / first
        return math.log1p(slope * offset / first) / slope

    def timeline_at_source(self, source_offset: float) -> float:
        target = min(max(source_offset, 0.0), _seconds(self.source_duration))
        elapsed = 0.0
        for start, end, first, last in self._segments():
            elapsed += self._integral(min(max(target - start, 0.0), end - start),
                                      end - start, first, last)
        return elapsed

    @property
    def timeline_duration(self) -> Rational:
        return _quantized(self.timeline_at_source(_seconds(self.source_duration)))

    def source_at_timeline(self, elapsed: float) -> float:
        low, high = 0.0, _seconds(self.source_duration)
        goal = min(max(elapsed, 0.0), self.timeline_at_source(high))
        for _ in range(50):
            mid = (low + high) / 2
            if self.timeline_at_source(mid) < goal:
                low = mid
            else:
                high = mid
        return (low + high) / 2

    def speed_at_source(self, source_offset: float) -> float:
        value = min(max(source_offset, 0.0), _seconds(self.source_duration))
        for start, end, first, last in self._segments():
            if value <= end:
                return first + (last - first) * (value - start) / (end - start)
        return _seconds(self.points[-1].speed)

    def slice(self, start_offset: float, end_offset: float) -> "SpeedCurve":
        total = _seconds(self.source_duration)
        if not 0 <= start_offset < end_offset <= total + 1e-6:
            raise ValueError("curve slice outside source range")
        end_offset = min(end_offset, total)
        span = end_offset - start_offset
        boundaries = [start_offset]
        boundaries.extend(_seconds(point.at) * total for point in self.points[1:-1]
                          if start_offset < _seconds(point.at) * total < end_offset)
        boundaries.append(end_offset)
        points = tuple(SpeedPoint(_quantized((at - start_offset) / span),
                                  _quantized(self.speed_at_source(at)))
                       for at in boundaries)
        # Quantization can collapse points when a trim is extremely close to a knot.
        unique = [points[0]]
        unique.extend(point for point in points[1:-1] if point.at > unique[-1].at)
        unique.append(SpeedPoint(Rational.of(1), points[-1].speed))
        return SpeedCurve(_quantized(span), tuple(unique))

    def video_setpts(self) -> str:
        """FFmpeg expression mapping each source PTS to its exact timeline PTS."""
        terms = []
        for start, end, first, last in self._segments():
            length = end - start
            used = f"max(min(PTS*TB-{start:.9f}\\,{length:.9f})\\,0)"
            slope = (last - first) / length
            if abs(slope) < 1e-10:
                terms.append(f"({used}/{first:.9f})")
            else:
                terms.append(f"(log(1+{slope:.12f}*{used}/{first:.9f})/{slope:.12f})")
        return f"setpts='({' + '.join(terms)})/TB'"

    def audio_commands(self, name: str) -> tuple[float, str]:
        """Source-time tempo commands for FFmpeg's rubberband filter.

        A single time-stretch stage receives each command at the source PTS.
        Driving several chained atempo stages with those same timestamps makes
        their internal clocks diverge and can truncate the end of the audio.
        """
        duration = _seconds(self.source_duration)
        count = max(1, min(8000, math.ceil(duration / 0.1)))
        step = duration / count
        initial = self.speed_at_source(0)
        lines = []
        for index in range(1, count):
            when = index * step
            factor = self.speed_at_source(when)
            lines.append(f"{when:.6f} rubberband@{name} tempo {factor:.9f};")
        return initial, "\n".join(lines) + "\n"
