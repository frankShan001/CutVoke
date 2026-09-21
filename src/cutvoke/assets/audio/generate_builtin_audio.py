"""CutVoke 内置音频资产生成器（J07 素材与模板内容）。

用 ffmpeg lavfi 合成一批真实可听、可进工程的音乐/音效，落到本目录
（src/cutvoke/assets/audio/）。这些资产随源码留存，首次服务启动时会经
core/builtin_assets.py 的 ensure_builtin_audio() 注册进素材账本（assets 表），
与用户上传素材共存同表、幂等、不互删。

设计约束（任务书 J07）：
- 音效类：短（1–3s）、有辨识度。
- 垫乐/氛围类：长（8–15s）、可循环使用。
- 全部用 -ar 44100 -ac 2 -c:a pcm_s16le 存 WAV（免编码警告、可试听、可渲染）。
- 不使用空占位文件；每个资产都有真实波形内容。

运行方式（在仓库根目录）：
    PYTHONPATH=src python src/cutvoke/assets/audio/generate_builtin_audio.py

注意：ffmpeg 的 aevalsrc / anoisesrc 表达式刻意避开 `=` `:` `,` 等会被滤镜
选项解析器误读的字符（仅用 + * ( ) 与数学函数），确保参数以列表形式传给
subprocess 时不被 shell/选项解析拆坏。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# (文件名, lavfi 输入表达式, 额外音频滤镜列表)
# 时长已编码进输入表达式的 d= 参数；额外滤镜用于塑形（高通/低通/淡入淡出等）。
ASSETS: list[tuple[str, str, list[str]]] = [
    # ---------------- 音效类（短、有辨识度） ----------------
    # 提示音：880Hz 主音 + 1318Hz 泛音，指数衰减，干净短促（0.7s）
    ("chime.wav",
     "aevalsrc=0.5*sin(2*PI*880*t)*exp(-5*t)+0.25*sin(2*PI*1318*t)*exp(-6*t):s=44100:d=0.7",
     []),
    # 机械声：极短白噪爆发 + 高通，像开关/按键（0.12s）
    ("click.wav",
     "anoisesrc=d=0.12:color=white:amplitude=1.0:s=44100",
     ["highpass=f=2000", "volume=0.8"]),
    # 风声：棕噪经低通 + 缓慢幅度调制（tremolo），像环境风（3s）
    ("wind.wav",
     "anoisesrc=d=3:color=brown:amplitude=0.7:s=44100",
     ["lowpass=f=600", "tremolo=f=0.25:d=0.7", "volume=0.9"]),
    # 水滴：频率随 tanh 快速下行的啁啾 + 衰减（0.9s）
    ("water_drop.wav",
     "aevalsrc=sin(2*PI*(1400-1000*tanh(3*t))*t)*exp(-5*t)*0.6:s=44100:d=0.9",
     []),
    # 电子闪烁：1200Hz 方波（tanh(40*x) 近似 sign，避免 gt 的逗号被滤镜解析拆坏）快衰减（0.25s）
    ("blip.wav",
     "aevalsrc=0.5*tanh(40*sin(2*PI*1200*t))*exp(-12*t):s=44100:d=0.25",
     []),
    # 鼓掌：白噪爆发 + 高通 + 淡入淡出 + 短回声，像拍手（1s）
    ("clap.wav",
     "anoisesrc=d=1:color=white:amplitude=1.0:s=44100",
     ["highpass=f=1200", "afade=t=in:d=0.003",
      "afade=t=out:st=0.9:d=0.1", "aecho=0.7:0.8:40:0.3", "volume=0.9"]),

    # ---------------- 垫乐/氛围类（长、可循环） ----------------
    # 环境氛围：多正弦叠加 + 极慢 LFO 起伏，柔和铺底（12s）
    ("ambient_pad.wav",
     "aevalsrc=0.12*sin(2*PI*110*t)*(0.7+0.3*sin(2*PI*0.1*t))+0.10*sin(2*PI*164.81*t)+0.08*sin(2*PI*220*t):s=44100:d=12",
     ["volume=0.9"]),
    # 轻快节奏垫乐：低频脉冲（每 0.5s）+ 中频点缀（每 0.25s）+ 高频闪烁（每 0.125s）（10s）
    # 周期性相位用 t - T*floor(t/T) 实现（避免 mod 的逗号被滤镜解析拆坏）
    ("groove.wav",
     "aevalsrc=0.35*sin(2*PI*70*t)*exp(-6*(t-0.5*floor(t/0.5)))+0.18*sin(2*PI*523.25*t)*exp(-8*(t-0.25*floor(t/0.25)))+0.12*sin(2*PI*783.99*t)*exp(-8*(t-0.5*floor(t/0.5)))+0.06*sin(2*PI*1046.5*t)*exp(-10*(t-0.125*floor(t/0.125))):s=44100:d=10",
     ["volume=0.85"]),
    # 极简科技：方波旋律（330/247Hz 门控）+ 高频方波打击（6000Hz）（12s）
    # 方波用 tanh(40*x) 近似 sign；周期相位用 t - T*floor(t/T)（均避免逗号被拆坏）
    ("tech_minimal.wav",
     "aevalsrc=0.25*tanh(40*sin(2*PI*330*t))*exp(-3*(t-0.25*floor(t/0.25)))+0.20*tanh(40*sin(2*PI*247*t))*exp(-3*(t-0.5*floor(t/0.5)))+0.08*tanh(40*sin(2*PI*6000*t))*exp(-30*(t-0.5*floor(t/0.5))):s=44100:d=12",
     ["volume=0.9"]),
]


def _ffprobe(path: str) -> dict:
    """返回 {duration, sample_rate, channels, codec}；失败抛 RuntimeError。"""
    cmd = [shutil.which("ffprobe") or "ffprobe", "-v", "error",
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
    }


def generate_all() -> list[dict]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH")
    results: list[dict] = []
    for fname, lavfi, filters in ASSETS:
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
        probe = _ffprobe(out)
        results.append({"file": fname, "size": os.path.getsize(out), **probe})
        print(f"  OK  {fname:18s} dur={probe['duration']:.2f}s "
              f"sr={probe['sample_rate']} ch={probe['channels']} "
              f"codec={probe['codec']} size={os.path.getsize(out)}")
    return results


if __name__ == "__main__":
    print(f"生成内置音频资产 → {HERE}")
    try:
        out = generate_all()
    except Exception as e:  # noqa: BLE001
        print(f"生成失败: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"完成：{len(out)} 个音频资产。")
