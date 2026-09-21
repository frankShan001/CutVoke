"""音频分析（J09 真实工具）：响度 + 节拍检测。

- 响度 I：优先用 ffmpeg ebur128（EBU R128 集成响度）；失败回退到 RMS 近似 LUFS。
- 节拍 BPM：不依赖外部 ML，纯 Python 实现——ffmpeg 抽单声道 f32le PCM →
  分帧算 RMS 能量 → 能量通量（正差分）找起拍峰 → 峰间隔估值 BPM（折叠到 40~240）。

返回字典：
    {"duration": float, "sampleRate": int, "channels": int,
     "loudnessI": float|None, "bpm": float|None}
loudnessI / bpm 在无意义场景（静音 / 纯正弦无节拍）返回 None，不伪造数字。
"""

from __future__ import annotations

import array
import json
import math
import os
import re
import subprocess
import tempfile
from typing import Optional

from .render import DEFAULT_FFMPEG, DEFAULT_FFPROBE

# BPM 估计使用的解码参数
_PCM_RATE = 44100
_FRAME = 1024
_HOP = 512


def _probe(path: str, ffprobe: str) -> tuple[float, int, int]:
    """ffprobe 读取时长 / 采样率 / 声道数。"""
    cmd = [ffprobe, "-v", "error",
           "-show_entries", "format=duration:stream=sample_rate,channels,codec_type",
           "-of", "json", path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return 0.0, 0, 0
    try:
        info = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return 0.0, 0, 0
    fmt = info.get("format", {})
    duration = 0.0
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    a = next((s for s in info.get("streams", [])
              if s.get("codec_type") == "audio"), None)
    sr = int(a.get("sample_rate", 0)) if a else 0
    ch = int(a.get("channels", 0)) if a else 0
    return duration, sr, ch


def _ebur128(path: str, ffmpeg: str) -> Optional[float]:
    """ffmpeg ebur128 集成响度（LUFS）。解析最后一条 I: 值。"""
    cmd = [ffmpeg, "-i", path, "-af", "ebur128=framelog=verbose",
           "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return None
    out = r.stderr
    # ebur128 摘要行形如 "I: -12.3 LUFS" 多次出现，取最后一次（集成响度）
    vals = re.findall(r"I:\s*([-+]?\d+(?:\.\d+)?)\s*LUFS", out)
    if vals:
        return float(vals[-1])
    m = re.search(r"Integrated loudness:\s*([-+]?\d+(?:\.\d+)?)\s*LUFS", out)
    if m:
        return float(m.group(1))
    return None


def _read_pcm(path: str, ffmpeg: str, sr: int) -> list[float]:
    """ffmpeg 抽单声道 f32le PCM 原始样本（纯 Python 解析）。"""
    fd, raw = tempfile.mkstemp(suffix=".raw")
    os.close(fd)
    cmd = [ffmpeg, "-y", "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
           "-f", "f32le", raw]
    samples: list[float] = []
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        with open(raw, "rb") as f:
            data = f.read()
        arr = array.array("f")
        arr.frombytes(data)
        samples = list(arr)
    finally:
        try:
            os.remove(raw)
        except OSError:
            pass
    return samples


def _rms_loudness(samples: list[float]) -> Optional[float]:
    """RMS 近似 LUFS（EBU R128 简化）：LKFS ≈ -0.691 + 10*log10(mean_square)。"""
    if not samples:
        return None
    mean_sq = sum(x * x for x in samples) / len(samples)
    if mean_sq <= 0:
        return None
    return round(-0.691 + 10.0 * math.log10(mean_sq), 2)


def _detect_bpm(samples: list[float], sr: int) -> Optional[float]:
    """能量通量峰检测估算 BPM（±10 粗糙可用）。无清晰起拍返回 None。"""
    n = len(samples)
    if n < _FRAME * 4:
        return None
    # 帧能量包络
    energies: list[float] = []
    i = 0
    while i + _FRAME <= n:
        s = samples[i:i + _FRAME]
        e = sum(x * x for x in s) / _FRAME
        energies.append(e)
        i += _HOP
    if len(energies) < 8:
        return None
    mx = max(energies) or 1e-9
    norm = [e / mx for e in energies]
    # 能量通量（正差分）作为起拍强度
    flux = [max(0.0, norm[k + 1] - norm[k]) for k in range(len(norm) - 1)]
    if not flux or max(flux) <= 0:
        return None
    peak_thr = max(0.05, 0.5 * max(flux))
    # 最短拍间隔 ~120ms（防倍频/半频误判）
    min_spacing = max(1, int(0.12 * sr / _HOP))
    beats: list[int] = []
    prev = -min_spacing
    for idx, fv in enumerate(flux):
        if fv < peak_thr:
            continue
        if idx - prev < min_spacing:
            continue
        beats.append(idx)
        prev = idx
    if len(beats) < 2:
        return None
    intervals = [(beats[k + 1] - beats[k]) * _HOP / sr
                 for k in range(len(beats) - 1)]
    intervals.sort()
    med = intervals[len(intervals) // 2]
    if med <= 0:
        return None
    bpm = 60.0 / med
    # 折叠到常见音乐区间
    while bpm > 240:
        bpm /= 2
    while bpm < 40:
        bpm *= 2
    return round(bpm, 1)


def analyze_audio(path: str) -> dict:
    """分析音频，返回 {duration, sampleRate, channels, loudnessI, bpm}。"""
    duration, sr, ch = _probe(path, DEFAULT_FFPROBE)
    sr = sr or _PCM_RATE
    loud = _ebur128(path, DEFAULT_FFMPEG)
    samples = _read_pcm(path, DEFAULT_FFMPEG, sr)
    if loud is None:
        loud = _rms_loudness(samples)
    bpm = _detect_bpm(samples, sr)
    return {
        "duration": round(duration, 3),
        "sampleRate": sr,
        "channels": ch,
        "loudnessI": None if loud is None else float(loud),
        "bpm": bpm,
    }
