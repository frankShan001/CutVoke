"""Opt-in real filter interval measurements; never apportion an encode total."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path


def begin(renderer, clip, effect, source, parts, counter):
    profile = getattr(renderer._render_ctx, "profile", None)
    if profile is None:
        return source, None
    identity = {"clipId": clip.id, "instanceId": effect.get("instanceId", ""), "effectId": effect.get("effectId", "")}
    tag = "cvp_" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    profile["registry"][tag] = identity
    label = f"profilein{counter[0]}"; counter[0] += 1
    parts.append(f"[{source}]bench=start[{label}]")
    return label, tag


def end(tag, label, parts, counter):
    if tag is None:
        return label
    output = f"profileout{counter[0]}"; counter[0] += 1
    parts.append(f"[{label}]bench@{tag}=stop[{output}]")
    return output


def collect(renderer, stderr):
    profile = getattr(renderer._render_ctx, "profile", None)
    if profile is None:
        return
    for tag, value in re.findall(r"\[bench@(cvp_[0-9a-f]+)[^\]]*\]\s+t:([0-9.]+)", stderr.decode("utf-8", "replace")):
        record = profile["effects"].setdefault(tag, {**profile["registry"].get(tag, {}), "samples": 0, "filterIntervalSeconds": 0., "minSeconds": None, "maxSeconds": 0.})
        elapsed = float(value)
        record["samples"] += 1
        record["filterIntervalSeconds"] += elapsed
        record["minSeconds"] = min(record["minSeconds"] if record["minSeconds"] is not None else elapsed, elapsed)
        record["maxSeconds"] = max(record["maxSeconds"], elapsed)


def measure(project, *, start=0., duration=2., maximum=640):
    from cutvoke.rendering.render import RenderService
    from cutvoke.rendering.preview_quality import snapshot
    from cutvoke.domain.compositing import number
    start = number(start, "start", 0, 86400 * 7)
    duration = number(duration, "duration", .1, 5)
    if isinstance(maximum, bool) or maximum not in (320, 640, 960, 1280):
        raise ValueError("maximum must be 320/640/960/1280")
    renderer = RenderService()
    sample, _ = snapshot(project, maximum)
    runs = []
    with tempfile.TemporaryDirectory(prefix="cutvoke-profile-") as directory:
        for index, name in enumerate(("first", "repeat")):
            profile = {"registry": {}, "effects": {}}
            renderer._render_ctx.profile = profile
            result = renderer.render_preview_window(sample, str(Path(directory) / f"sample{index}.mp4"), start, duration,
                                                     preview_max_dimension=maximum)
            runs.append({"run": name, "timings": result.get("timings", {}), "processor": result.get("effectProcessor"),
                         "effects": list(profile["effects"].values()), "procedural": result.get("effectMetrics", [])})
    return {"projectId": project.project_id, "revision": project.revision, "start": start, "duration": duration,
            "width": sample.sequence.width, "height": sample.sequence.height, "fps": float(sample.sequence.fps.to_fraction()),
            "coverage": "Measured FFmpeg filter intervals and procedural generation. Intervals include filter scheduling and can overlap; they are not exclusive CPU time or additive layer totals. Decode/transform/compositing/encoding remain in wall time. First run can already hit disk cache; cache status is measured, never assumed cold.",
            "runs": runs}
