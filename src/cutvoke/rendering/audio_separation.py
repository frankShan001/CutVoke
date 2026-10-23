"""Real local Demucs separation. Never substitute center/side extraction."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from cutvoke.rendering.render import DEFAULT_FFMPEG, DEFAULT_FFPROBE


def separation_status():
    python = os.environ.get("CUTVOKE_SEPARATION_PYTHON", sys.executable)
    try:
        result = subprocess.run([python, "-c", "import demucs,torch; print(torch.cuda.is_available())"],
                                capture_output=True, text=True, timeout=30)
        return {"available": result.returncode == 0, "cuda": result.stdout.strip() == "True",
                "install": "pip install cutvoke[separation]", "python": python}
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "cuda": False, "install": "pip install cutvoke[separation]", "python": python}


def separate_audio(audio_path, out_dir, *, model="htdemucs", device="auto", stems="vocals"):
    source, target = Path(audio_path).resolve(), Path(out_dir).resolve()
    if not audio_path or not source.is_file():
        raise ValueError("separate: input audio/video file does not exist")
    if not out_dir or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
        raise ValueError("separate: choose a new or empty output directory")
    if model not in {"htdemucs", "htdemucs_ft"} or device not in {"cpu", "cuda", "auto"} or stems not in {"vocals", "all"}:
        raise ValueError("separate: invalid model/device/stems")
    status = separation_status()
    if not status["available"]:
        raise RuntimeError("Real source separation requires cutvoke[separation], or set CUTVOKE_SEPARATION_PYTHON to a Python environment with Demucs installed")
    if device == "cuda" and not status["cuda"]:
        raise RuntimeError("separate: CUDA requested but unavailable")
    device = ("cuda" if status["cuda"] else "cpu") if device == "auto" else device
    target.parent.mkdir(parents=True, exist_ok=True)
    # FFmpeg handles video containers and Unicode paths; Demucs sees a normalized WAV.
    with tempfile.TemporaryDirectory(prefix="cutvoke-separate-", dir=target.parent) as directory:
        work = Path(directory)
        decoded = work / "input.wav"
        decode = subprocess.run([DEFAULT_FFMPEG, "-v", "error", "-y", "-i", str(source),
                                 "-vn", "-ar", "44100", "-ac", "2", "-c:a", "pcm_f32le", str(decoded)],
                                capture_output=True, text=True, timeout=1800)
        if decode.returncode:
            raise RuntimeError("separate: cannot decode source audio: " + decode.stderr[-700:])
        command = [status["python"], "-m", "demucs", "-n", model, "-d", device, "--float32",
                   "--shifts", "1", "-o", str(work / "results")]
        if stems == "vocals":
            command += ["--two-stems", "vocals"]
        command += [str(decoded)]
        env = os.environ.copy()
        env["PATH"] = str(Path(DEFAULT_FFMPEG).parent) + os.pathsep + env.get("PATH", "")
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=7200, env=env)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("separate: local model exceeded two hours") from error
        if result.returncode:
            raise RuntimeError("separate: Demucs failed: " + result.stderr[-1400:])
        generated = work / "results" / model / "input"
        names = ["vocals", "no_vocals"] if stems == "vocals" else ["vocals", "drums", "bass", "other"]
        outputs = {}
        for name in names:
            file = generated / f"{name}.wav"
            if not file.is_file() or file.stat().st_size <= 44:
                raise RuntimeError(f"separate: missing decoded {name} stem")
            probe = subprocess.run([DEFAULT_FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(file)],
                                   capture_output=True, text=True, timeout=30)
            duration = float(json.loads(probe.stdout).get("format", {}).get("duration", 0))
            if duration <= 0:
                raise RuntimeError(f"separate: invalid {name} stem")
            outputs[name] = {"path": str(target / f"{name}.wav"), "duration": duration}
        if target.exists():
            if any(target.iterdir()):
                raise RuntimeError("separate: output directory changed during inference")
            target.rmdir()
        generated.replace(target)
    return {"model": model, "engine": "demucs", "device": device, "stems": outputs,
            "sourcePath": str(source)}
