"""Bounded cut-local inspection of the export mixer; no retained media or cache."""
from __future__ import annotations

import base64
import io
import math
from pathlib import Path
import subprocess
import tempfile
import threading
import wave

import numpy as np

from cutvoke.rendering.render import RenderError


def inspect_audio(render, scheduler, project, at, radius):
    if (isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at)
            or isinstance(radius, bool) or not isinstance(radius, (int, float))
            or not math.isfinite(radius) or not 0.05 <= radius <= 2.5):
        raise ValueError("time must be finite; radius must be between 0.05 and 2.5 seconds")
    duration = render._sequence_duration(project.sequence)
    if not 0 <= at <= duration or duration <= 0:
        raise ValueError("time must be within a nonempty timeline (including its end)")
    rate = project.sequence.audio_sample_rate
    if not isinstance(rate, int) or not 8000 <= rate <= 192000:
        raise ValueError("audio sample rate must be between 8000 and 192000")
    first, last = round(max(0, at - radius) * rate), round(min(duration, at + radius) * rate)
    if last <= first:
        raise ValueError("audio inspection window is empty")
    cancel = threading.Event()
    timer = threading.Timer(60, cancel.set)
    timer.daemon = True
    timer.start()
    try:
        with scheduler.slot(cancel), tempfile.TemporaryDirectory(prefix="cutvoke-inspect-") as directory:
            output = Path(directory) / "mix.wav"
            result = render.render_audio_window(project, str(output), first / rate,
                                                (last - first) / rate, cancel_event=cancel)
            metadata = {"projectId": project.project_id, "revision": project.revision,
                        "time": at, "startSample": first, "endSample": last,
                        "start": first / rate, "end": last / rate, "sampleRate": rate,
                        "channels": 2, "warnings": result["warnings"],
                        "hasAudio": result["has_audio"]}
            if not result["has_audio"]:
                return {**metadata, "peaks": [], "_audio": []}
            if cancel.is_set():
                raise RenderError("audio inspection timed out")
            # Float PCM retains overload evidence in peaks; WAV content is PCM16
            # for broad MCP player compatibility. Never normalize a quiet cut.
            decoded = subprocess.run([render.ffmpeg, "-v", "error", "-nostdin", "-i", str(output),
                "-map", "0:a:0", "-t", str((last-first)/rate), "-ac", "2", "-ar", str(rate),
                "-f", "f32le", "pipe:1"], capture_output=True, timeout=10, check=True).stdout
            samples = np.frombuffer(decoded, dtype="<f4").reshape(-1, 2)
            if len(samples) != last - first or not np.isfinite(samples).all():
                raise RenderError("audio inspection returned invalid samples or duration")
            bins = min(100, len(samples))
            edges = np.linspace(0, len(samples), bins + 1, dtype=int)
            peaks = [np.max(np.abs(samples[a:b]), axis=0).tolist() for a, b in zip(edges, edges[1:])]
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav:
                wav.setnchannels(2); wav.setsampwidth(2); wav.setframerate(rate)
                wav.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
            return {**metadata, "peaks": peaks, "peakBinEdges": (edges + first).tolist(),
                    "peakUnits": "linear full scale, per channel", "mimeType": "audio/wav",
                    "sampleCount": len(samples),
                    "_audio": [{"type": "audio", "mimeType": "audio/wav",
                                "data": base64.b64encode(buffer.getvalue()).decode("ascii")}]}
    finally:
        timer.cancel()
