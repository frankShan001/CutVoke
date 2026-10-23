"""Materialize nested compositions using the authoritative renderer.

The intermediate carries alpha and PCM audio, so parent transforms, trimming,
speed and effects apply to the complete visual/audio composition together.
"""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import uuid

from cutvoke.domain.model import AssetReference, Project

_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def prepare(renderer, project, cancel_event=None):
    if not any(c.nested for t in project.sequence.tracks if t.visible
               for c in t.clips if not c.hidden):
        return project
    from cutvoke.rendering.render import RenderCancelled, RenderError, RenderService
    snapshot = deepcopy(project)
    # Code identity makes development/runtime updates invalidate intermediates.
    version = hashlib.sha256()
    for filename in ("render.py", "compound_sources.py", "audio_mixer.py"):
        path = Path(__file__).with_name(filename)
        if path.is_file():
            version.update(path.read_bytes())
    def files(seq, depth=0):
        if depth > 4:
            raise RenderError("复合片段嵌套超过 4 层")
        result = []
        for lane in seq.tracks:
            if not lane.visible or (lane.kind == "audio" and lane.muted):
                continue
            for clip in lane.clips:
                if clip.hidden:
                    continue
                if clip.nested:
                    result.extend(files(clip.nested, depth+1))
                elif clip.asset_ref.source_path:
                    path = Path(clip.asset_ref.source_path)
                    try:
                        stat = path.stat()
                        result.append((str(path.resolve()), stat.st_size, stat.st_mtime_ns))
                    except OSError:
                        raise RenderError(f"复合片段素材缺失：{path}") from None
                for effect in clip.effects:
                    for key, value in effect.get("params", {}).items():
                        if isinstance(value, str) and key.lower().endswith(("path", "file")):
                            path = Path(value)
                            if path.is_file():
                                stat = path.stat()
                                result.append((str(path.resolve()), stat.st_size, stat.st_mtime_ns))
        return result
    for lane in snapshot.sequence.tracks:
        if not lane.visible or (lane.kind == "audio" and lane.muted):
            continue
        for clip in lane.clips:
            if clip.nested is None or clip.hidden:
                continue
            nested = clip.nested
            identity = {"sequence":nested.to_dict(), "files":files(nested),
                        "renderer":version.hexdigest(), "ffmpeg":renderer.ffmpeg}
            key = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            root = Path(tempfile.gettempdir()) / "cutvoke-compounds-v1"
            root.mkdir(parents=True, exist_ok=True)
            output = root / (key + ".mov")
            with _locks_guard:
                lock = _locks.setdefault(key, threading.Lock())
            while not lock.acquire(timeout=0.1):
                if cancel_event is not None and cancel_event.is_set():
                    raise RenderCancelled("compound preparation cancelled")
            try:
                if cancel_event is not None and cancel_event.is_set():
                    raise RenderCancelled("compound preparation cancelled")
                if not output.is_file() or output.stat().st_size == 0:
                    child = RenderService(ffmpeg_path=renderer.ffmpeg, ffprobe_path=renderer.ffprobe,
                                          registry=renderer.effects, gpu_effects=renderer.gpu_effects)
                    staging = root / (key + "_" + uuid.uuid4().hex + ".mov")
                    try:
                        child.render(Project(schema_version=project.schema_version,
                                             project_id=project.project_id+"_"+clip.id,
                                             revision=project.revision, sequence=nested),
                                     str(staging), quality="high", alpha=True,
                                     cancel_event=cancel_event)
                        os.replace(staging, output)
                    finally:
                        staging.unlink(missing_ok=True)
            finally:
                lock.release()
            clip.asset_ref = AssetReference("compound_"+key, source_path=str(output))
            clip.nested = None
    return snapshot
