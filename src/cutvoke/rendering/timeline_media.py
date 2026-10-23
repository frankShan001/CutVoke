"""Small, cached timeline strips. Never render the project or retain decoded audio."""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import uuid

import numpy as np
from PIL import Image, ImageDraw

from cutvoke.domain.rational import Rational
from cutvoke.rendering.render import RenderError

_WORKERS = threading.BoundedSemaphore(2)
_PRUNE_LOCK = threading.Lock()
_FLIGHTS_LOCK = threading.Lock()
_FLIGHTS: dict[str, list] = {}
_CACHE_BYTES = 128 * 1024 * 1024
_CACHE_FILES = 2048


@contextlib.contextmanager
def _single_flight(key: str):
    with _FLIGHTS_LOCK:
        entry = _FLIGHTS.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    entry[0].acquire()
    try:
        yield
    finally:
        entry[0].release()
        with _FLIGHTS_LOCK:
            entry[1] -= 1
            if entry[1] == 0:
                _FLIGHTS.pop(key, None)


def _source_at(clip, elapsed: float) -> float:
    if clip.freeze_at is not None:
        return float(clip.freeze_at.to_fraction())
    offset = clip.source_offset_at_timeline(Rational.of(round(elapsed * 1_000_000), 1_000_000))
    beginning = clip.source_start
    if clip.speed < Rational.of(0) and clip.speed_curve is None:
        beginning += clip.consumed_source_duration
    return float((beginning + offset).to_fraction())


def _prune(directory: Path) -> None:
    with _PRUNE_LOCK:
        entries = []
        for path in directory.glob("*.png"):
            if ".tmp." in path.name or ".frame." in path.name:
                continue
            with contextlib.suppress(OSError):
                stat = path.stat()
                entries.append((stat.st_mtime, stat.st_size, path))
        entries.sort()
        total, count = sum(item[1] for item in entries), len(entries)
        for _, size, entry in entries:
            if total <= _CACHE_BYTES and count <= _CACHE_FILES:
                break
            with contextlib.suppress(OSError):
                entry.unlink()
                total -= size
                count -= 1


def _waveform(render, source: str, clip, start: float, end: float, width: int, target: Path) -> None:
    positions = [_source_at(clip, start + (end - start) * i / width) for i in range(width + 1)]
    lo, hi = min(positions), max(positions)
    if hi - lo < 1e-6:
        Image.new("RGBA", (width, 36)).save(target)
        return
    rate, bins = 4000, min(8192, width * 4)
    expected = max(1, math.ceil((hi - lo) * rate))
    peaks = np.zeros(bins, dtype=np.float32)
    command = [render.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{max(0, lo):.6f}",
               "-i", source, "-t", f"{hi - lo:.6f}", "-map", "0:a:0", "-ac", "1", "-ar", str(rate),
               "-f", "s16le", "pipe:1"]
    # stderr goes to a bounded temporary file rather than filling a pipe while PCM is read.
    import tempfile
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors)
        timed_out = threading.Event()
        def stop():
            timed_out.set()
            with contextlib.suppress(OSError):
                process.kill()
        timer = threading.Timer(60, stop)
        timer.daemon = True
        timer.start()
        try:
            consumed = 0
            while True:
                block = process.stdout.read(64 * 1024)
                if not block:
                    break
                samples = np.frombuffer(block, dtype="<i2").astype(np.float32)
                indices = np.minimum(bins - 1, ((np.arange(len(samples)) + consumed) * bins // expected).astype(np.int32))
                np.maximum.at(peaks, indices, np.abs(samples) / 32768)
                consumed += len(samples)
            code = process.wait()
            if code or timed_out.is_set():
                errors.seek(max(0, errors.tell() - 500))
                raise RenderError("Timeline waveform timed out" if timed_out.is_set()
                                  else "Timeline waveform failed: " + errors.read(500).decode("utf-8", "replace"))
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
    image = Image.new("RGBA", (width, 36))
    draw = ImageDraw.Draw(image)
    for x in range(width):
        left, right = sorted((positions[x], positions[x + 1]))
        first = max(0, min(bins - 1, int((left - lo) / (hi - lo) * bins)))
        last = max(first + 1, min(bins, math.ceil((right - lo) / (hi - lo) * bins)))
        amplitude = max(0.3, min(17, float(np.max(peaks[first:last])) * 17))
        draw.line((x, 18 - amplitude, x, 18 + amplitude), fill=(99, 230, 207, 225))
    image.save(target)


def timeline_media(*, render, clip, cache_dir: Path, kind: str, width: int,
                   start: float = 0, end: float | None = None) -> Path | None:
    """Return source-mapped PNG for a visible portion of one ordinary clip.

    Audio is read in 64 KiB PCM blocks; at most two strip decoders run concurrently.
    The cache key includes source identity and time mapping, so trims and relinks invalidate it.
    """
    if kind not in ("thumbnail", "waveform"):
        raise ValueError("Unknown timeline media kind")
    duration = float(clip.duration.to_fraction())
    end = duration if end is None else end
    if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end <= start or end > duration + 1e-5:
        raise ValueError("Invalid timeline media range")
    width = max(64, min(960, int(width)))
    source = Path(clip.asset_ref.source_path)
    if clip.nested or not str(clip.asset_ref.source_path) or not source.is_file():
        return None
    stat = source.stat()
    mapping = {"start": clip.source_start.to_json(), "speed": clip.speed.to_json(),
               "consumed": clip.consumed_source_duration.to_json(),
               "curve": clip.speed_curve.to_dict() if clip.speed_curve else None,
               "freeze": clip.freeze_at.to_json() if clip.freeze_at else None}
    digest = hashlib.sha256(json.dumps([4, str(source.resolve()), stat.st_size, stat.st_mtime_ns,
                           kind, width, round(start, 6), round(end, 6), mapping], sort_keys=True).encode()).hexdigest()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    output = cache_dir / f"{digest}.png"
    if output.is_file():
        try:
            os.utime(output, None)
            return output
        except FileNotFoundError:
            pass
    with _single_flight(str(output.resolve())):
        return _build_strip(render, clip, source, cache_dir, output, kind, width, start, end)


def _build_strip(render, clip, source, cache_dir, output, kind, width, start, end):
    if output.is_file():
        return output
    if not _WORKERS.acquire(timeout=30):
        raise RenderError("Timeline media is busy, please retry")
    temporary = cache_dir / f"{output.stem}-{uuid.uuid4().hex}.tmp.png"
    try:
        if output.is_file():
            return output
        info = render.probe_media(str(source))
        if kind == "waveform":
            if not info.get("has_audio"):
                return None
            _waveform(render, str(source), clip, start, end, width, temporary)
        else:
            if not info.get("has_video"):
                return None
            columns = max(1, min(8, math.ceil(width / 96)))
            sheet = Image.new("RGB", (width, 48), "#1b2230")
            tile_width = math.ceil(width / columns)
            still = source.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".svg", ".avif")
            frame = cache_dir / f"{uuid.uuid4().hex}.frame.png"
            try:
                for i in range(columns):
                    at = 0 if still else max(0, _source_at(clip, start + (end - start) * (i + 0.5) / columns))
                    source_duration = float(info.get("duration") or 0)
                    if source_duration > 0:
                        at = min(at, max(0, source_duration - 0.04))
                    render.thumbnail_media(str(source), str(frame), time=at, width=max(64, tile_width), timeout=15)
                    with Image.open(frame) as image:
                        from PIL import ImageOps
                        tile = ImageOps.fit(image.convert("RGB"), (tile_width, 48), method=Image.Resampling.LANCZOS)
                        sheet.paste(tile, (i * tile_width, 0))
                sheet.save(temporary)
            finally:
                with contextlib.suppress(OSError):
                    frame.unlink()
        try:
            os.replace(temporary, output)
        except OSError:
            # A different HTTP/MCP process may have published the same complete
            # hash while a Windows reader holds its destination open.
            if not output.is_file() or output.stat().st_size == 0:
                raise
        _prune(cache_dir)
        return output
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink()
        _WORKERS.release()
