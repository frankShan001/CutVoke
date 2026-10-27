"""Materialize editable text through the authoritative alpha renderer for graphs.

Ordinary projects keep their existing title path. Compositing projects need
titles at their actual layer position so adjustments and selectable mattes can
consume exactly the same text pixels as final output.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import uuid

from cutvoke.domain.model import AssetReference, Project
from cutvoke.domain.rational import Rational

_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def prepare(renderer, project, cancel_event=None):
    seq = project.sequence
    if not any(c.compositing or c.role == "adjustment" for t in seq.tracks for c in t.clips):
        return project
    if not any(t.kind == "text" for t in seq.tracks):
        return project
    from cutvoke.rendering.render import RenderCancelled, RenderService
    snapshot = copy.deepcopy(project)
    root = (Path(project._directory_root) / "cache" / "layers" if getattr(project, "_directory_root", None)
            else Path(tempfile.gettempdir()) / "cutvoke-text-layers-v1")
    root.mkdir(parents=True, exist_ok=True)
    version = [(name, Path(__file__).with_name(name).stat().st_mtime_ns)
               for name in ("text_layers.py", "render.py", "caption_render.py", "title_motion.py")]
    for track in snapshot.sequence.tracks:
        if track.kind != "text":
            continue
        track.kind = "video"
        for clip in track.clips:
            child_sequence = copy.deepcopy(snapshot.sequence)
            child = copy.deepcopy(clip)
            child.compositing = {}
            child.timeline_start, child.timeline_end = Rational.of(0), clip.duration
            child_sequence.tracks = [copy.deepcopy(track)]
            child_sequence.tracks[0].kind = "text"
            child_sequence.tracks[0].visible = True
            child.hidden = False
            child_sequence.tracks[0].clips = [child]
            child_sequence.captions = []
            child_sequence.multicam = None
            identity = {"sequence": child_sequence.to_dict(), "version": version, "ffmpeg": renderer.ffmpeg}
            key = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            output = root / (key + ".mov")
            with _guard:
                lock = _locks.setdefault(key, threading.Lock())
            while not lock.acquire(timeout=.1):
                if cancel_event is not None and cancel_event.is_set():
                    raise RenderCancelled("text layer preparation cancelled")
            try:
                if cancel_event is not None and cancel_event.is_set():
                    raise RenderCancelled("text layer preparation cancelled")
                if not output.is_file() or not output.stat().st_size:
                    staging = root / (key + "_" + uuid.uuid4().hex + ".mov")
                    child_renderer = RenderService(renderer.ffmpeg, renderer.ffprobe, renderer.effects, gpu_effects=renderer.gpu_effects)
                    try:
                        child_renderer.render(Project(project.schema_version, project.project_id + "_text", project.revision, child_sequence),
                                              str(staging), quality="high", alpha=True, cancel_event=cancel_event)
                        os.replace(staging, output)
                    finally:
                        staging.unlink(missing_ok=True)
            finally:
                lock.release()
            clip.asset_ref = AssetReference("text_" + key, str(output))
            clip.source_start, clip.speed = Rational.of(0), Rational.of(1)
            clip.freeze_at, clip.speed_curve = None, None
            clip.effects, clip.keyframes = [], {}
    return snapshot
