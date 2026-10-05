"""Prepare a complete playback file with bounded renders, outside HTTP threads."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from .render import RenderCancelled, RenderError


def preview_identity(project, renderer_version: int) -> str:
    """Captions/metadata don't invalidate video; nested content and files do."""
    sequences = [sequence.to_dict() for sequence in project.sequences]
    for sequence in sequences:
        sequence["captions"] = []
    paths: set[str] = set()

    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str) and "path" in key.lower() and item:
                    paths.add(item)
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(sequences)
    stats = []
    for path in sorted(paths):
        try:
            stat = os.stat(path)
            stats.append((path, stat.st_size, stat.st_mtime_ns))
        except OSError:
            stats.append((path, None, None))
    return hashlib.sha256(json.dumps({
        "assemblyVersion": 3, "rendererVersion": renderer_version,
        "activeSequenceId": project.active_sequence_id,
        "sequences": sequences, "sourceStats": stats,
    }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:32]


class PreviewPreparation:
    """One worker limits memory. Each request owns its cancellation/snapshot."""

    def __init__(self, cache_dir: str, render, window: Callable,
                 window_lock, *, renderer_version: int, window_seconds: int = 8, disk_cache=None):
        self.cache_dir = Path(cache_dir)
        self.render = render
        self.window = window
        self.window_lock = window_lock
        self.renderer_version = renderer_version
        self.window_seconds = window_seconds
        self.disk_cache = disk_cache
        self._lock = threading.RLock()
        self._jobs: dict[str, dict] = {}
        self._events: dict[str, threading.Event] = {}
        self._queue = queue.Queue()
        self._worker = None
        self._closed = False

    def submit(self, project) -> dict:
        snapshot = copy.deepcopy(project)
        clips = [clip for track in snapshot.sequence.tracks
                 if track.visible and (track.kind == "video" or
                                       (track.kind == "audio" and not track.muted))
                 for clip in track.clips if not clip.hidden]
        if not clips:
            raise RenderError("project has no visible video or audible audio")
        duration = max(float(clip.timeline_end.to_fraction()) for clip in clips)
        job_id = uuid.uuid4().hex
        with self._lock:
            if self._closed:
                raise RenderError("preview preparation is shutting down")
            # Bound process-local history, retaining live requests.
            for old_id, old in list(self._jobs.items()):
                if len(self._jobs) < 100:
                    break
                if old["state"] in ("completed", "failed", "cancelled") and time.time() - old.get("_accessed", 0) > 60:
                    self._jobs.pop(old_id)
                    self._events.pop(old_id, None)
            self._events[job_id] = threading.Event()
            self._jobs[job_id] = {
                "jobId": job_id, "projectId": snapshot.project_id,
                "revision": snapshot.revision, "state": "queued", "phase": "queued",
                "completedWindows": 0, "totalWindows": math.ceil(duration / self.window_seconds),
                "progress": 0, "duration": duration, "cached": False,
                "_snapshot": snapshot,
            }
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, name="preview-preparation", daemon=True)
                self._worker.start()
            self._queue.put(job_id)
            return self.get(job_id)

    def get(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return {key: value for key, value in job.items() if not key.startswith("_")}

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job["state"] not in ("completed", "failed", "cancelled"):
                self._events[job_id].set()
                job.update(state="cancelled", phase="cancelled")
            return self.get(job_id)

    def media(self, job_id: str) -> tuple[str, str, float]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job["state"] != "completed":
                raise RenderError("preview is not ready")
            job["_accessed"] = time.time()
            if self.disk_cache and Path(job["_path"]).parent == self.disk_cache.directory:
                path = self.disk_cache.get(Path(job["_path"]).name,
                    job.get("_metadata"), validate=lambda path: True)
                if not path:
                    raise RenderError("预览缓存已回收，请重新打开工程")
            elif not Path(job["_path"]).is_file():
                raise RenderError("预览缓存已回收，请重新打开工程")
            return job["_path"], job["identity"], job["duration"]

    def close(self):
        with self._lock:
            self._closed = True
            for event in self._events.values():
                event.set()
            self._queue.put(None)
        if self._worker:
            self._worker.join(timeout=10)

    def busy(self):
        with self._lock:
            return any(job["state"] in ("queued", "running") for job in self._jobs.values())

    def _update(self, job_id, **values):
        with self._lock:
            job = self._jobs[job_id]
            if self._events[job_id].is_set():
                raise RenderCancelled("preview preparation cancelled")
            job.update(values)

    def _run(self):
        while (job_id := self._queue.get()) is not None:
            with self._lock:
                event = self._events.get(job_id)
                if event is None or event.is_set():
                    continue
                job = self._jobs[job_id]
                snapshot = job.pop("_snapshot")
            try:
                self._prepare(job_id, snapshot, event)
            except Exception as error:  # noqa: BLE001 — keep worker alive after a bad project
                with self._lock:
                    job.update(state="cancelled" if event.is_set() else "failed",
                               phase="cancelled" if event.is_set() else "failed",
                               error=str(error))

    def _command(self, args: list[str], event: threading.Event, work: Path):
        # Own only this subprocess; cancellation never terminates the Web service.
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=log,
                                       cwd=work, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                while process.poll() is None:
                    if event.wait(0.05):
                        raise RenderCancelled("preview preparation cancelled")
                if event.is_set():
                    raise RenderCancelled("preview preparation cancelled")
                if process.returncode:
                    log.seek(0)
                    raise RenderError(log.read().decode("utf-8", errors="replace")[-1600:])
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

    def _prepare(self, job_id, project, event):
        identity = preview_identity(project, self.renderer_version)
        if self.disk_cache:
            self.cache_dir = self.disk_cache.directory
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        output = self.cache_dir / f"prepared_{identity}.mp4"
        job = self.get(job_id)
        duration, total = job["duration"], job["totalWindows"]
        seq = project.sequence
        scale = min(1.0, 960 / max(seq.width, seq.height))
        width = max(2, int(seq.width * scale) // 2 * 2)
        height = max(2, int(seq.height * scale) // 2 * 2)
        self._update(job_id, state="running", phase="checking")
        metadata = {"width": width, "height": height, "duration": duration}
        if output.is_file():
            try:
                def valid(path):
                    try:
                        self.render._verify(path, duration, width, height)
                        return True
                    except Exception:
                        return False
                if self.disk_cache:
                    if self.disk_cache.get(output.name, metadata, valid) is None:
                        raise RenderError("invalid prepared cache")
                else:
                    self.render._verify(str(output), duration, width, height)
            except Exception:
                output.unlink(missing_ok=True)
            else:
                if preview_identity(project, self.renderer_version) != identity:
                    raise RenderError("素材在加载过程中发生变化，请重试")
                self._update(job_id, state="completed", phase="ready", progress=100,
                             completedWindows=total, cached=True, identity=identity, _path=str(output), _metadata=metadata)
                return
        with tempfile.TemporaryDirectory(prefix="prepare-", dir=self.cache_dir) as directory:
            work = Path(directory)
            manifest = []
            for index in range(total):
                self._update(job_id, phase="rendering")
                original = work / f"source_{index}.mp4"
                # Background renders yield to foreground seeks between windows.
                if self.disk_cache:
                    path, _etag, window_duration = self.window(project, index, event, priority=10)
                    protection = self.disk_cache.pin(path)
                else:
                    protection = self.window_lock
                with protection:
                    if not self.disk_cache:
                        path, _etag, window_duration = self.window(project, index, event)
                    try:
                        os.link(path, original)
                    except OSError:
                        shutil.copyfile(path, original)
                normalized = work / f"part_{index}.mp4"
                info = self.render.probe_media(str(original))
                args = [self.render.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(original)]
                if not info.get("has_audio"):
                    args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                args += ["-map", "0:v:0", "-map", "0:a:0" if info.get("has_audio") else "1:a:0",
                         "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "2",
                         "-af", "aresample=async=1:first_pts=0,apad",
                         "-t", str(window_duration), str(normalized)]
                self._command(args, event, work)
                manifest += [f"file 'part_{index}.mp4'", f"duration {window_duration:.9f}"]
                self._update(job_id, completedWindows=index + 1,
                             progress=math.floor(90 * (index + 1) / total))
            self._update(job_id, phase="assembling", progress=90)
            (work / "parts.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
            result = work / "complete.mp4"
            # The editor normally displays a smaller canvas. Bound decode cost too;
            # export retains the original resolution and rendering graph.
            video_options = ["-c:v", "copy"] if (width, height) == (seq.width, seq.height) else [
                "-vf", f"scale={width}:{height}", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "25", "-pix_fmt", "yuv420p"]
            self._command([
                self.render.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-copyts", "-f", "concat", "-safe", "1", "-i", "parts.txt",
                "-map", "0:v:0", "-map", "0:a:0", *video_options, "-c:a", "aac",
                "-af", "aresample=async=1:first_pts=0", "-t", str(duration),
                "-movflags", "+faststart", str(result),
            ], event, work)
            self._update(job_id, phase="verifying", progress=95)
            self.render._verify(str(result), duration, width, height)
            if preview_identity(project, self.renderer_version) != identity:
                raise RenderError("素材在加载过程中发生变化，请重试")
            if event.is_set():
                raise RenderCancelled("preview preparation cancelled")
            if self.disk_cache:
                self.disk_cache.publish(output.name, result, metadata)
            else:
                os.replace(result, output)
            self._update(job_id, state="completed", phase="ready", progress=100,
                         identity=identity, _path=str(output), _metadata=metadata)
