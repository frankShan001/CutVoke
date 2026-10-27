"""Spatial cubic paths and roving timing, separate from temporal interpolation."""
from __future__ import annotations

import copy
import math

from cutvoke.domain.compositing import number
from cutvoke.domain.controllers import ExpressionLane
from cutvoke.domain.keyframes import Keyframe, ease, expression, validate_bezier
from cutvoke.domain.rational import Rational


def position(a, b, t, axis):
    key = axis.upper()
    p0, p3 = a[axis], b[axis]
    p1, p2 = p0 + a.get("out" + key, 0), p3 + b.get("in" + key, 0)
    return (1-t)**3*p0 + 3*(1-t)**2*t*p1 + 3*(1-t)*t*t*p2 + t**3*p3


def resolved_points(path):
    if not isinstance(path, dict) or set(path) - {"points", "sourceOrigin", "speedOrigin"}:
        raise ValueError("motionPath accepts points and engine-owned sourceOrigin/speedOrigin clocks")
    points = copy.deepcopy(path.get("points"))
    if not isinstance(points, list) or not 2 <= len(points) <= 32:
        raise ValueError("motion path requires 2..32 spatial points")
    for index, point in enumerate(points):
        if not isinstance(point, dict) or set(point) - {"at", "x", "y", "inX", "inY", "outX", "outY", "roving", "timeEase"}:
            raise ValueError("invalid spatial point fields")
        for axis in ("x", "y"):
            number(point.get(axis), axis, -100000, 100000)
        for key in ("inX", "inY", "outX", "outY"):
            if key in point: number(point[key], key, -100000, 100000)
        if "roving" in point and not isinstance(point["roving"], bool):
            raise ValueError("roving must be boolean")
        if point.get("roving") and index in {0, len(points)-1}:
            raise ValueError("path endpoints cannot rove")
        if not point.get("roving"):
            number(point.get("at"), "at", 0, 86400)
        if "timeEase" in point: validate_bezier(point["timeEase"])
    fixed = [index for index, point in enumerate(points) if not point.get("roving")]
    for left, right in zip(fixed, fixed[1:]):
        start, end = points[left]["at"], points[right]["at"]
        if end <= start: raise ValueError("fixed path point times must strictly increase")
        distances = []
        for a, b in zip(points[left:right], points[left+1:right+1]):
            previous, length = (a["x"], a["y"]), 0.
            for i in range(1, 65):
                point = position(a,b,i/64,"x"), position(a,b,i/64,"y")
                length += math.dist(previous, point); previous = point
            distances.append(length)
        total = sum(distances)
        if total <= 1e-9: distances, total = [1.] * len(distances), len(distances)
        elapsed = 0.
        for index in range(left+1, right):
            elapsed += distances[index-left-1]
            points[index]["at"] = start + (end-start) * elapsed / total
    return points


def validate(clip):
    if clip.motion_path is None: return
    resolved_points(clip.motion_path)
    if clip.speed_curve is not None or clip.freeze_at is not None:
        raise ValueError("motion paths require constant-speed non-frozen clips; remove the path before curve retiming")
    Rational.from_json(**clip.motion_path["sourceOrigin"])
    if Rational.from_json(**clip.motion_path["speedOrigin"]) == Rational.of(0):
        raise ValueError("motion path speedOrigin cannot be zero")


def resolve(project):
    if not any(c.motion_path for t in project.sequence.tracks for c in t.clips): return project
    result = copy.deepcopy(project)
    for track in result.sequence.tracks:
        for clip in track.clips:
            if clip.motion_path is None: continue
            validate(clip)
            path = clip.motion_path
            points = resolved_points(path)
            origin, speed = Rational.from_json(**path["sourceOrigin"]), Rational.from_json(**path["speedOrigin"])
            offset = float(((clip.source_start-origin)/speed).to_fraction())
            factor = float((clip.speed/speed).to_fraction())
            clock = f"(__TIME__*{factor:.12g}+{offset:.12g})"
            for axis in ("x", "y"):
                if axis in getattr(clip, '_essential_overrides', set()):continue
                formula = f"{points[-1][axis]:.12g}"
                for a,b in reversed(list(zip(points,points[1:]))):
                    controls = a.get("timeEase")
                    keys = [Keyframe("a",Rational.from_float(a["at"]),0,"cubic-bezier" if controls else "linear",controls),
                            Keyframe("b",Rational.from_float(b["at"]),1)]
                    fraction = expression(keys, clock, 0)
                    p0,p3 = a[axis],b[axis]
                    p1,p2 = p0+a.get("out"+axis.upper(),0),p3+b.get("in"+axis.upper(),0)
                    segment = f"st(9,{fraction});{p0:.12g}*pow(1-ld(9),3)+3*{p1:.12g}*pow(1-ld(9),2)*ld(9)+3*{p2:.12g}*(1-ld(9))*pow(ld(9),2)+{p3:.12g}*pow(ld(9),3)"
                    formula = f"if(lt({clock},{b['at']:.12g}),({segment}),({formula}))"
                formula = f"if(lt({clock},{points[0]['at']:.12g}),{points[0][axis]:.12g},({formula}))"
                def evaluate(t, axis=axis, points=points, offset=offset, factor=factor):
                    at=t*factor+offset
                    if at<=points[0]["at"]:return points[0][axis]
                    if at>=points[-1]["at"]:return points[-1][axis]
                    a,b=next((a,b) for a,b in zip(points,points[1:]) if a["at"]<=at<b["at"])
                    frac=(at-a["at"])/(b["at"]-a["at"])
                    if a.get("timeEase"): frac=ease(frac,"cubic-bezier",a["timeEase"])
                    return position(a,b,frac,axis)
                clip.keyframes[axis] = ExpressionLane("spatial_"+axis,formula,evaluate,clip.duration)
    return result
