"""Canvas versions are editable sequences, never display-only aspect ratios."""
from __future__ import annotations
import copy
from cutvoke.domain.model import Sequence, Project, new_id
from cutvoke.domain.rational import Rational


def configure(sequence, payload):
    width, height = payload.get("width", sequence.width), payload.get("height", sequence.height)
    if any(type(v) is not int or not 2 <= v <= 8192 or v % 2 for v in (width, height)):
        raise ValueError("Canvas dimensions must be even integers from 2 to 8192")
    raw = payload.get("fps", sequence.fps.to_json())
    fps = Rational.from_json(**raw) if isinstance(raw, dict) else Rational.from_float(float(raw))
    if not 1 <= float(fps.to_fraction()) <= 240:
        raise ValueError("Frame rate must be from 1 to 240")
    mode = payload.get("reframe", sequence.reframe)
    if mode not in ("stretch", "contain", "cover"):
        raise ValueError("reframe must be stretch, contain or cover")
    name = payload.get("name", sequence.name)
    if not isinstance(name, str) or len(name) > 200:
        raise ValueError("Sequence name must be a string of at most 200 characters")
    rx, ry = width / sequence.width, height / sequence.height
    for track in sequence.tracks:
        for clip in track.clips:
            if clip.motion_path:
                for point in clip.motion_path['points']:
                    for key,ratio in (('x',rx),('inX',rx),('outX',rx),('y',ry),('inY',ry),('outY',ry)):
                        if key in point:point[key]*=ratio
            for param,link in clip.property_links.items():
                target_ratio=rx if param=='x' else ry if param=='y' else 1.
                source=link['source']
                source_ratio=(rx if source.get('param')=='x' else ry if source.get('param')=='y' else 1.) if source.get('type','property')=='property' else 1.
                link['scale']=link.get('scale',1)*target_ratio/source_ratio
                link['offset']=link.get('offset',0)*target_ratio
            for effect in clip.effects:
                if effect.get("effectId") == "cutvoke.text":
                    params = effect.get("params", {})
                    for key, maximum, minimum in (("fontSize", 200, 8), ("strokeWidth", 8, 0),
                            ("outerStrokeWidth", 16, 0), ("animGlow", 24, 0)):
                        if key in params: params[key] = max(minimum, min(maximum, params[key] * min(rx, ry)))
                    if "shadow" in params: params["shadow"] = max(0, min(12, round(params["shadow"] * min(rx, ry))))
                    for key, ratio in (("blockShadowX", rx), ("blockShadowY", ry)):
                        if key in params: params[key] = max(-24, min(24, params[key] * ratio))
                if effect.get("effectId") == "cutvoke.transform":
                    for key, ratio in (("x", rx), ("y", ry)):
                        position = effect.get("params", {}).get("position", {})
                        if key in position: position[key] = round(position[key] * ratio)
            for key, ratio in (("x", rx), ("y", ry)):
                for frame in clip.keyframes.get(key, []):
                    frame.value *= ratio
    for caption in sequence.captions:
        if caption.fontSize > 0:
            caption.fontSize = max(1, min(500, round(caption.fontSize * min(rx, ry))))
        caption.strokeWidth = max(0, min(20, round(caption.strokeWidth * min(rx, ry))))
        caption.shadow = max(0, min(12, round(caption.shadow * min(rx, ry))))
    sequence.width, sequence.height, sequence.fps = width, height, fps
    sequence.reframe, sequence.name = mode, name


def duplicate(project, payload):
    from cutvoke.editing.compound import fresh_nested
    raw = fresh_nested(project.sequence).to_dict()
    raw["id"] = payload.get("sequenceId") or new_id("seq")
    if any(seq.id == raw["id"] for seq in project.sequences): raise ValueError("Sequence ID already exists")
    sequence = Sequence.from_dict(raw)
    configure(sequence, dict(payload, name=payload.get("name") or project.sequence.name or "Version"))
    project.sequences.append(sequence)
    if payload.get("switch", True): project.active_sequence_id, project.sequence = sequence.id, sequence
    return sequence


def select(project, sequence_id):
    if sequence_id is None: return project
    snapshot = Project.from_dict(project.to_dict())
    sequence = next((seq for seq in snapshot.sequences if seq.id == sequence_id), None)
    if sequence is None: raise ValueError("Sequence not found: " + str(sequence_id))
    snapshot.sequence, snapshot.active_sequence_id = sequence, sequence.id
    return snapshot
