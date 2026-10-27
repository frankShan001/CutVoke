"""Declarative scalar links and oscillators, without executing user scripts."""
from __future__ import annotations

import copy
import math

from cutvoke.domain.compositing import number
from cutvoke.domain.keyframes import Keyframe, evaluate, expression, parameter_spec
from cutvoke.domain.rational import Rational


class ExpressionLane(list):
    """Render-only adapter; editable authority stays in propertyLinks."""
    def __init__(self, param, formula, evaluator, duration):
        super().__init__([Keyframe("binding_" + param, Rational.of(0), evaluator(0.)),
                          Keyframe("binding_end_" + param, duration, evaluator(float(duration.to_fraction())))])
        self.formula, self.evaluator = formula, evaluator


def _static(clip, param):
    if param == "volume": return float(clip.volume.to_fraction())
    if param.startswith("effect:"): return parameter_spec(clip, param)[2]
    transform = clip.transform()
    return float(transform["position"].get(param, 0) if param in {"x", "y"} else transform[param])


def validate(sequence):
    clips = {clip.id: clip for track in sequence.tracks for clip in track.clips}
    edges = {}
    for clip in clips.values():
        if not isinstance(clip.property_links, dict) or len(clip.property_links) > 32:
            raise ValueError("propertyLinks accepts at most 32 scalar links per clip")
        for param, link in clip.property_links.items():
            if param == "volume":
                raise ValueError("visual controllers cannot target audio volume")
            parameter_spec(clip, param)
            if not isinstance(link, dict) or set(link) - {"source", "scale", "offset", "timeOffset"}:
                raise ValueError("binding accepts source/scale/offset/timeOffset")
            for key, low, high in (("scale", -10000, 10000), ("offset", -100000, 100000), ("timeOffset", -86400, 86400)):
                if key in link: number(link[key], key, low, high)
            source = link.get("source")
            if not isinstance(source, dict): raise ValueError("binding source is required")
            kind = source.get("type", "property")
            if kind == "property":
                if set(source) - {"type", "clipId", "param"}: raise ValueError("property source accepts type/clipId/param")
                other = clips.get(source.get("clipId"))
                if other is None: raise ValueError("binding source clip is missing")
                parameter_spec(other, source.get("param"))
                edges[(clip.id, param)] = (other.id, source["param"])
            elif kind in {"sine", "time", "constant"}:
                if set(source) - {"type", "amplitude", "frequency", "phase", "value"}: raise ValueError("invalid controller fields")
                for key, low, high in (("amplitude", -100000, 100000), ("frequency", -100, 100), ("phase", -36000, 36000), ("value", -100000, 100000)):
                    if key in source: number(source[key], key, low, high)
            else: raise ValueError("source type must be property/sine/time/constant")
    for node in edges:
        path = set()
        while node in edges:
            if node in path: raise ValueError("property binding dependency cycle")
            if len(path) > 32: raise ValueError("property binding chain exceeds 32 steps")
            path.add(node); node = edges[node]


def resolve(project):
    if not any(c.property_links for t in project.sequence.tracks for c in t.clips): return project
    snapshot = copy.deepcopy(project)
    seq = snapshot.sequence
    validate(seq)
    clips = {c.id: c for t in seq.tracks for c in t.clips}
    formulas = {}
    evaluators = {}
    def build(clip, param):
        key = (clip.id, param)
        if key in formulas: return formulas[key], evaluators[key]
        link = clip.property_links.get(param)
        if not link:
            lane, default = clip.keyframes.get(param), _static(clip, param)
            formula = expression(lane, "__TIME__", default) if lane else f"{default:.12g}"
            evaluator = (lambda t: evaluate(lane, Rational.from_float(t))) if lane else (lambda t: default)
        else:
            source = link["source"]
            kind = source.get("type", "property")
            shift = float(link.get("timeOffset", 0))
            if kind == "property":
                other = clips[source["clipId"]]
                formula, evaluator = build(other, source["param"])
                shift += float((clip.timeline_start - other.timeline_start).to_fraction())
            elif kind == "sine":
                amplitude, frequency, phase, center = source.get("amplitude", 1), source.get("frequency", 1), math.radians(source.get("phase", 0)), source.get("value", 0)
                formula = f"({center:.12g}+{amplitude:.12g}*sin(2*PI*{frequency:.12g}*__TIME__+{phase:.12g}))"
                evaluator = lambda t: center + amplitude * math.sin(math.tau * frequency * t + phase)
            elif kind == "time":
                formula, evaluator = "__TIME__", lambda t: t
            else:
                constant = source.get("value", 0)
                formula, evaluator = f"{constant:.12g}", lambda t: constant
            formula = formula.replace("__TIME__", f"(__TIME__+{shift:.12g})")
            previous = evaluator
            factor, offset = link.get("scale", 1), link.get("offset", 0)
            low, high, _ = parameter_spec(clip, param)
            formula = f"clip(({formula})*{factor:.12g}+{offset:.12g},{low:.12g},{high:.12g})"
            evaluator = lambda t, op=previous, shift=shift, factor=factor, offset=offset, low=low, high=high: max(low, min(high, op(t + shift) * factor + offset))
        formulas[key], evaluators[key] = formula, evaluator
        return formula, evaluator
    for clip in clips.values():
        for param in clip.property_links:
            formula, evaluator = build(clip, param)
            clip.keyframes[param] = ExpressionLane(param, formula, evaluator, clip.duration)
    return snapshot
