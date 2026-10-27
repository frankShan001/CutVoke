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
    if profile.get("target") is not None and identity != profile["target"]:
        return source, None
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
    text = stderr.decode("utf-8", "replace")
    samples = re.findall(r"\[bench@(cvp_[0-9a-f]+)[^\]]*\]\s+t:([0-9.]+)", text)
    # FFmpeg 6/7 log the private BenchContext as `[bench @ address]`, losing
    # the filter instance name. One targeted filter per pass is the only safe
    # attribution; pointer/order guesses break on parallel filter branches.
    if not samples and len(profile["registry"]) == 1:
        tag = next(iter(profile["registry"]))
        samples = [(tag, value) for value in re.findall(r"\[bench @ [^\]]*\]\s+t:([0-9.]+)", text)]
    for tag, value in samples:
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
    targets = [{"clipId": clip.id, "instanceId": effect.get("instanceId", ""), "effectId": effect.get("effectId", "")}
               for track in sample.sequence.tracks if track.visible for clip in track.clips if not clip.hidden
               for effect in renderer._enabled_fx(clip)]
    runs = []
    with tempfile.TemporaryDirectory(prefix="cutvoke-profile-") as directory:
        for index, name in enumerate(("first", "repeat")):
            effects, passes = [], []
            for pass_index, target in enumerate(targets or [None]):
                profile = {"registry": {}, "effects": {}, "target": target}
                renderer._render_ctx.profile = profile
                result = renderer.render_preview_window(sample, str(Path(directory) / f"sample{index}-{pass_index}.mp4"), start, duration,
                                                         preview_max_dimension=maximum)
                effects.extend(profile["effects"].values())
                passes.append({"target": target, "timings": result.get("timings", {})})
            runs.append({"run": name, "timings": {"totalRenderSeconds": sum(p["timings"].get("totalRenderSeconds", 0) for p in passes)},
                         "processor": result.get("effectProcessor"), "profilePasses": passes,
                         "effects": effects, "procedural": result.get("effectMetrics", [])})
    return {"projectId": project.project_id, "revision": project.revision, "start": start, "duration": duration,
            "width": sample.sequence.width, "height": sample.sequence.height, "fps": float(sample.sequence.fps.to_fraction()),
            "coverage": "Measured FFmpeg filter intervals and procedural generation. Each effect uses a separate targeted render pass for reliable attribution across FFmpeg versions. Timings sum those passes. Intervals include scheduling, are not exclusive CPU time or additive layer totals. Decode/transform/compositing/encoding remain in wall time. First run can already hit disk cache; cache status is measured, never assumed cold.",
            "runs": runs}
