"""J09 智能字幕（基础版）：基于 ffmpeg silencedetect 的「话语句子」自动切分。

无外部 ASR / 云服务：只用 ffmpeg 静音检测 + 能量合并，把有声段切成字幕块。
真实转写（语音 -> 文字）是后续 ASR 任务；本模块只产出时间轴占位字幕。

流程：
  1. ffmpeg `-af silencedetect=noise=<db>:d=<minSilence>` 解析出每段静音的
     start / end（stderr 文本解析，不依赖任何模型）。
  2. 有声段 = 静音间隔的补集（含首段/末段延伸），得到时间区间列表。
  3. 过滤 / 合并：
     - 中间静音时长 < MIN_MERGE_GAP(0.15s) 的两块合并，避免气声切碎；
     - 过短有声块 < MIN_SPEECH(0.4s) 并入相邻块（视为破碎噪声）。
  4. 每块生成占位字幕：text 默认「语句 N」，start/end 用 Rational。

返回字典：
    {"duration": float, "silenceCount": int, "segmentCount": int,
     "segments": [{"index","text","start":{num,den},"end":{num,den}}, ...],
     "captions": [{"index","text","start":Rational,"end":Rational}, ...]}
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Optional

from .render import DEFAULT_FFMPEG, DEFAULT_FFPROBE
from .rational import Rational


# silencedetect 的静音判定阈值（noise，相对于全标度 dBFS）
_DEFAULT_NOISE_DB = -30.0
# 相邻有声块间距（中间静音时长）小于该值则合并，避免气声切碎
_MIN_MERGE_GAP_S = 0.15
# 过短有声块（短于此）并入相邻块（视为破碎噪声）
_MIN_SPEECH_S = 0.4
# ffmpeg 单次调用超时（秒）
_FFMPEG_TIMEOUT = 180


def _probe_duration(path: str, ffprobe: str) -> float:
    """ffprobe 读取音频时长（秒）。失败回退 0.0。"""
    from .audio_analysis import _probe

    duration, _sr, _ch = _probe(path, ffprobe)
    return float(duration)


def _run_silencedetect(path: str, min_silence: float,
                       noise_db: float, ffmpeg: str) -> list[tuple[float, float]]:
    """跑 ffmpeg silencedetect，解析出静音区间 [(start, end), ...]。

    返回按时间升序的静音区间；若音频首尾无静音则可能为空列表。
    """
    cmd = [
        ffmpeg, "-i", path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
        "-f", "null", "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=_FFMPEG_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"silencedetect timeout on {path}") from e
    out = r.stderr

    starts: list[float] = []
    ends: list[float] = []
    for line in out.splitlines():
        m = re.search(r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)", line)
        if m:
            starts.append(float(m.group(1)))
            continue
        m = re.search(r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)", line)
        if m:
            ends.append(float(m.group(1)))

    # silencedetect 交错打印 silence_start / silence_end；按下标配对。
    # 若音频结尾仍在静音，会多出一个无 end 的 start —— 用 duration 收尾（由调用方补）。
    silences: list[tuple[float, float]] = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        if e is not None:
            silences.append((s, e))
    return silences


def _speech_segments(silences: list[tuple[float, float]],
                     duration: float) -> list[tuple[float, float]]:
    """由静音区间求有声段（静音的补集）。含首段 [0, 首静音) 与末段 [末静音, duration)。"""
    silences = sorted(silences)
    segs: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in silences:
        if s > cursor:
            segs.append((cursor, s))
        if e > cursor:
            cursor = e
    if cursor < duration:
        segs.append((cursor, duration))
    return segs


def _merge_tiny_gaps(segs: list[tuple[float, float]],
                     merge_gap: float) -> list[tuple[float, float]]:
    """合并中间静音 < merge_gap 的相邻有声块（气声不切）。"""
    if not segs:
        return segs
    out: list[tuple[float, float]] = [segs[0]]
    for s, e in segs[1:]:
        gap = s - out[-1][1]
        if 0 <= gap < merge_gap:
            out[-1] = (out[-1][0], e)  # 吞掉气声，延伸到下一块尾
        else:
            out.append((s, e))
    return out


def _collapse_short_speech(segs: list[tuple[float, float]],
                           min_speech: float) -> list[tuple[float, float]]:
    """过短有声块（< min_speech）并入相邻块；首块过短并入其后块。"""
    result: list[tuple[float, float]] = []
    for s, e in segs:
        if (e - s) < min_speech and result:
            result[-1] = (result[-1][0], e)  # 并入前一块
        elif (e - s) < min_speech and not result:
            result.append((s, e))  # 首块过短，暂存，待并入下一块
        else:
            result.append((s, e))
    # 处理仍挂在开头的短块：并入其后第一块（若有）
    if len(result) >= 2 and (result[0][1] - result[0][0]) < min_speech:
        s0, e0 = result[0]
        s1, e1 = result[1]
        result = [(s0, e1)] + result[2:]
    return result


def segment_audio(audio_path: str, *, min_silence: float = 0.35,
                  noise_db: float = _DEFAULT_NOISE_DB,
                  max_words: Optional[int] = None,
                  ffmpeg: str = DEFAULT_FFMPEG,
                  ffprobe: str = DEFAULT_FFPROBE) -> dict:
    """对音频做静音切分，返回占位字幕块（含 Rational 时间）。

    max_words 当前为占位提示（无 ASR 无法确知词数，留待后续转写阶段使用），
    不影响静音切分结果；结果中回显以便前端展示。
    """
    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"caption.autoSegment: audio not found: {audio_path}")

    duration = _probe_duration(audio_path, ffprobe)
    if duration <= 0:
        raise RuntimeError(f"caption.autoSegment: cannot probe duration: {audio_path}")

    silences = _run_silencedetect(audio_path, min_silence, noise_db, ffmpeg)
    segs = _speech_segments(silences, duration)
    segs = _merge_tiny_gaps(segs, _MIN_MERGE_GAP_S)
    segs = _collapse_short_speech(segs, _MIN_SPEECH_S)

    captions: list[dict] = []
    for i, (s, e) in enumerate(segs, start=1):
        captions.append({
            "index": i,
            "text": f"语句 {i}",
            "start": Rational.from_float(s),
            "end": Rational.from_float(e),
        })

    segments = [{
        "index": c["index"],
        "text": c["text"],
        "start": c["start"].to_json(),
        "end": c["end"].to_json(),
    } for c in captions]

    return {
        "duration": round(duration, 3),
        "silenceCount": len(silences),
        "segmentCount": len(captions),
        "minSilence": min_silence,
        "maxWords": max_words,
        "segments": segments,
        "captions": captions,
    }
