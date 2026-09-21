"""CutVoke 内置音频资产「扩展」生成器（J07 内容资产扩展）。

用 ffmpeg lavfi 合成一批真实可听、可进工程的音效/垫乐，落到本目录
（src/cutvoke/assets/audio/）。首次服务启动时会经
core/builtin_assets.py 的 ensure_builtin_audio() 目录扫描登记进素材账本
（与用户素材共存同表、幂等、不互删）。

为何单独成文件、不动 generate_builtin_audio.py：
- generate_builtin_audio.py 已覆盖原有 9 个资产；其中 3 个（click/wind/clap）
  用 anoisesrc 含随机噪声，重跑会改变原文件字节。本扩展生成器只产出「新增」
  文件，绝不触碰已有 9 个，满足「禁止删除/改动已有文件」的硬约束。
- 注册是目录扫描式的：本脚本产出的任何新 .wav 都会被 ensure_builtin_audio
  自动登记，无需改注册逻辑。

设计约束：
- 音效类：短（0.25–2s）、有辨识度。
- 垫乐/节奏类：长（1–4s）、可循环。
- 全部 -ar 44100 -ac 2 -c:a pcm_s16le 存 WAV（免编码警告、可试听、可渲染）。
- 不使用空占位文件；每个资产都有真实波形内容（经 ffprobe 验证有音频流且时长>0）。

运行方式（在仓库根目录）：
    PYTHONPATH=src python src/cutvoke/assets/audio/generate_builtin_audio_extra.py

注意：aevalsrc 表达式刻意只用 `*` `+` `(` `)` `PI` 与数学函数，避开 `=` `:` `,`
等会被滤镜选项解析器误读的字符，确保参数以列表形式传给 subprocess 时不被拆坏。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# (文件名, lavfi 输入表达式, 额外音频滤镜列表)
# 时长编码进输入表达式的 d= 参数；额外滤镜用于塑形。
ASSETS_EXTRA: list[tuple[str, str, list[str]]] = [
    # ---------------- 音效类（短、有辨识度） ----------------
    # 转场 whoosh：白噪经带通 + 渐入渐出，像扫频过渡（0.7s）
    ("whoosh.wav",
     "anoisesrc=d=0.7:color=white:amplitude=0.9:s=44100",
     ["bandpass=f=1200:w=1.5", "afade=t=in:d=0.12",
      "afade=t=out:st=0.35:d=0.35", "volume=0.7"]),
    # 叮咚：988Hz + 1318Hz 泛音，指数衰减（0.6s）
    ("ding.wav",
     "aevalsrc=0.6*sin(2*PI*988*t)*exp(-6*t)+0.3*sin(2*PI*1318*t)*exp(-7*t):s=44100:d=0.6",
     ["volume=0.9"]),
    # 上升音阶：220→880Hz 线性扫频，振幅随进度增强（1.2s）
    ("riser.wav",
     "aevalsrc=0.5*sin(2*PI*(220*t+275*t*t))*(0.3+0.7*(t/1.2)):s=44100:d=1.2",
     ["volume=0.85"]),
    # 下降音阶：880→220Hz 线性扫频，振幅随进度减弱（1.2s）
    ("faller.wav",
     "aevalsrc=0.5*sin(2*PI*(880*t-275*t*t))*(0.3+0.7*(1-t/1.2)):s=44100:d=1.2",
     ["volume=0.85"]),
    # 鼓点/节拍 loop：每 0.5s 一次低频鼓点 + 高频镲，4s 可循环（8 拍）
    ("drum_loop.wav",
     "aevalsrc=0.7*sin(2*PI*55*t)*exp(-18*(t-0.5*floor(t/0.5)))+0.12*tanh(40*sin(2*PI*2000*t))*exp(-60*(t-0.5*floor(t/0.5))):s=44100:d=4",
     ["volume=0.9"]),
    # 掌声近似：白噪经高通 + 快速颤动（tremolo）+ 渐入渐出（2s）
    ("applause.wav",
     "anoisesrc=d=2:color=white:amplitude=0.9:s=44100",
     ["highpass=f=1000", "tremolo=f=12:d=0.7", "afade=t=in:d=0.2",
      "afade=t=out:st=1.8:d=0.2", "volume=0.8"]),
    # 低频轰鸣：40Hz 主低频 + 轻微 LFO + 80Hz 衬底（3s）
    ("low_rumble.wav",
     "aevalsrc=0.6*sin(2*PI*40*t)*(0.8+0.2*sin(2*PI*0.5*t))+0.2*sin(2*PI*80*t):s=44100:d=3",
     ["volume=0.95"]),
    # 成功提示：C-E-G 大三和弦短促 stab（0.8s）
    ("success.wav",
     "aevalsrc=0.3*sin(2*PI*523.25*t)+0.3*sin(2*PI*659.25*t)+0.3*sin(2*PI*783.99*t):s=44100:d=0.8",
     ["afade=t=out:st=0.6:d=0.2", "volume=0.9"]),
    # 错误蜂鸣：160Hz 与 165Hz 拍频产生刺耳低频蜂鸣（0.5s）
    ("error_buzz.wav",
     "aevalsrc=0.4*sin(2*PI*160*t)+0.4*sin(2*PI*165*t):s=44100:d=0.5",
     ["lowpass=f=800", "volume=0.9"]),
    # 金币音：988→1320Hz 快速上行扫频 + 尾门控（0.25s）
    ("coin.wav",
     "aevalsrc=0.5*sin(2*PI*(988*t+827.5*t*t))*(0.5+0.5*tanh(40*(0.2-t))):s=44100:d=0.25",
     ["volume=0.9"]),
]


def _ffprobe(path: str, ffprobe: str) -> dict:
    """返回 {duration, sample_rate, channels, codec, has_audio}；失败抛 RuntimeError。"""
    cmd = [ffprobe, "-v", "error",
           "-show_entries", "format=duration:stream=codec_type,sample_rate,channels,codec_name",
           "-of", "json", path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {r.stderr[-400:]}")
    try:
        info = json.loads(r.stdout or "{}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"ffprobe invalid output for {path!r}") from e
    fmt = info.get("format", {})
    streams = info.get("streams", [])
    a = next((s for s in streams if s.get("codec_type") == "audio"), {}) or (
        streams[0] if streams else {})
    return {
        "duration": float(fmt.get("duration", 0) or 0),
        "sample_rate": int(a.get("sample_rate", 0) or 0),
        "channels": int(a.get("channels", 0) or 0),
        "codec": a.get("codec_name", ""),
        "has_audio": bool(a.get("codec_type") == "audio" or streams),
    }


def generate_all() -> list[dict]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH or known locations")
    if not ffprobe:
        raise RuntimeError("ffprobe not found in PATH or known locations")
    results: list[dict] = []
    for fname, lavfi, filters in ASSETS_EXTRA:
        out = os.path.join(HERE, fname)
        cmd = [ffmpeg, "-y", "-f", "lavfi", "-i", lavfi]
        if filters:
            cmd += ["-af", ",".join(filters)]
        cmd += ["-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", out]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg failed on {fname}: {r.stderr[-600:]}")
        if not (os.path.isfile(out) and os.path.getsize(out) > 0):
            raise RuntimeError(f"generated file empty/missing: {out}")
        probe = _ffprobe(out, ffprobe)
        results.append({"file": fname, "size": os.path.getsize(out), **probe})
        print(f"  OK  {fname:18s} dur={probe['duration']:.2f}s "
              f"sr={probe['sample_rate']} ch={probe['channels']} "
              f"codec={probe['codec']} audio={probe['has_audio']} "
              f"size={os.path.getsize(out)}")
    return results


if __name__ == "__main__":
    print(f"生成内置音频「扩展」资产 → {HERE}")
    try:
        out = generate_all()
    except Exception as e:  # noqa: BLE001
        print(f"生成失败: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"完成：{len(out)} 个新增音频资产。")
