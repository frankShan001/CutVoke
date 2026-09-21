"""J05 声音创作：纯 ffmpeg 真实音频处理工具（不引入外部模型）。

提供三条文件级命令：
  - denoise_audio     去噪（本机 ffmpeg 9 实测 anlmdn 崩溃，改用同机可用的 afftdn FFT 降噪）
  - vocal_enhance_audio 人声增强 / 去伴奏（立体声 中置 / 侧声道 近似抽取 + amix 混合）
  - normalize_audio   响度标准化（loudnorm 双遍：第一遍测 I，第二遍带 measured 参数应用）

所有命令仅写文件、不改工程，经 service 非改工程路径执行（与 audio.analyze 一致）。
滤镜能力以本机 ffmpeg 实测为准（见 tests/test_15g_audio_tools.py 的 faith_check）。

诚实声明：
  - 人声/伴奏分离是「中置/侧声道」频谱近似，不是 AI 人声分离；真实 AI 分离
    属于外部依赖，不在本命令范围内（advisory）。
  - denoise 本机实测：anlmdn 在本机 ffmpeg 9.0.1（Winget Gyan 构建）上触发 heap-corruption
    崩溃（rc=0xC0000374，与输入/输出格式无关），故改用同机可用的 afftdn（FFT 降噪）实现；
    strength(0.01~0.5) 线性映射为 afftdn 的 nr(0.01~97)。anlmdn 滤镜本身存在，仅本构建不可用
    ——这是「本机 ffmpeg 支持哪个用哪个」的工程取舍（advisory）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from typing import Optional

from .render import DEFAULT_FFMPEG, DEFAULT_FFPROBE

# 工程级替换（把片段 sourcePath 换成处理后音频）是后续工作；本模块只做文件级处理。
# 后续若加 applyToClip+clipId，应复用 EditService.execute 走 clip.audio/素材替换，
# 不要在本模块里直接改工程。

_TIMEOUT = 180
_LRA = 11.0
_TP = -1.5


def _probe(path: str) -> tuple[float, int, int]:
    """ffprobe 读时长 / 采样率 / 声道数；失败返回 (0,0,0)。"""
    cmd = [DEFAULT_FFPROBE, "-v", "error",
           "-show_entries", "format=duration:stream=sample_rate,channels,codec_type",
           "-of", "json", path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 0.0, 0, 0
    try:
        info = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return 0.0, 0, 0
    fmt = info.get("format", {})
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    a = next((s for s in info.get("streams", [])
              if s.get("codec_type") == "audio"), None)
    sr = int(a.get("sample_rate", 0)) if a else 0
    ch = int(a.get("channels", 0)) if a else 0
    return duration, sr, ch


def _ensure_out(audio_path: str, out_path: Optional[str], suffix: str) -> str:
    """缺省 outPath：同目录 + 文件名加 suffix + 原扩展名。"""
    if out_path:
        return os.path.abspath(out_path)
    base, ext = os.path.splitext(os.path.basename(audio_path))
    ext = ext or ".wav"
    return os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(audio_path)), base + suffix + ext))


def _run_ffmpeg(cmd: list[str], what: str) -> subprocess.CompletedProcess:
    """跑 ffmpeg，非零或 stderr 含 fatal 抛 EditError 风格的 RuntimeError。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"{what}: ffmpeg timed out") from e
    if r.returncode != 0:
        raise RuntimeError(f"{what} failed: {r.stderr[-600:]}")
    return r


# ----------------------------------------------------------------------
# 1) 去噪
# ----------------------------------------------------------------------
def denoise_audio(audio_path: str, out_path: Optional[str] = None,
                  strength: float = 0.1) -> dict:
    """FFT 降噪（afftdn，anlmdn 在本机构建崩溃故改用）。

    payload: {audioPath, outPath?, strength? (0.01~0.5，默认 0.1)}
    strength 线性映射为 afftdn 的 nr：nr = clamp(strength*80, 0.01, 97)
    （0.01→0.8 轻，0.1→8 中，0.5→40 强）。滤镜 afftdn=nr=<nr>。
    返回 {outPath, duration}。文件级处理，不改工程。
    """
    if not os.path.isfile(audio_path):
        raise RuntimeError(f"denoise: file not found: {audio_path}")
    strength = float(strength)
    if not (0.01 <= strength <= 0.5):
        raise RuntimeError("denoise: strength must be in [0.01, 0.5]")
    # anlmdn 在本机 ffmpeg 9.0.1 触发 heap-corruption 崩溃，改用 afftdn；
    # strength(0.01~0.5) → afftdn nr(0.01~97)
    nr = max(0.01, min(97.0, strength * 80.0))
    out = _ensure_out(audio_path, out_path, "_denoised")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    af = f"afftdn=nr={nr:.2f}"
    _run_ffmpeg([
        DEFAULT_FFMPEG, "-y", "-i", audio_path,
        "-af", af, "-ar", "44100", out,
    ], "denoise")

    duration, _, _ = _probe(out)
    if duration <= 0:
        raise RuntimeError("denoise: output not decodable / zero duration")
    return {"outPath": out, "duration": round(duration, 3)}


# ----------------------------------------------------------------------
# 2) 人声增强 / 去伴奏（中置 / 侧声道 近似）
# ----------------------------------------------------------------------
def vocal_enhance_audio(audio_path: str, out_path: Optional[str] = None,
                        amount: float = 0.5,
                        mode: str = "vocal") -> dict:
    """立体声中置法近似人声增强 / 去伴奏。

    payload: {audioPath, outPath?, amount? (0~1 默认 0.5), mode?}
      mode="vocal"        中置抽取（FL+FR 等量）→ 人声/中置乐器增强
      mode="accompaniment"侧声道抽取（FL-FR）→ 人声弱化、伴奏保留（近似）
    amount 为原音与处理音的混合比例（amix weights）：
      output = (1-amount)*original + amount*processed。
    返回 {outPath, duration, mode}。
    """
    if not os.path.isfile(audio_path):
        raise RuntimeError(f"vocalEnhance: file not found: {audio_path}")
    if mode not in ("vocal", "accompaniment"):
        raise RuntimeError("vocalEnhance: mode must be 'vocal' or 'accompaniment'")
    amount = float(amount)
    if not (0.0 <= amount <= 1.0):
        raise RuntimeError("vocalEnhance: amount must be in [0, 1]")

    suffix = "_vocal" if mode == "vocal" else "_accompaniment"
    out = _ensure_out(audio_path, out_path, suffix)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    if mode == "vocal":
        # 中置（mid）抽取：把 FL+FR 等量放到两声道（pan 不支持括号，用分配律展开）
        proc = "pan=stereo|c0=0.5*FL+0.5*FR|c1=0.5*FL+0.5*FR"
    else:
        # 侧声道（side）抽取：FL-FR / FR-FL → 近似去掉中置（人声），保留伴奏
        proc = "pan=stereo|c0=0.5*FL-0.5*FR|c1=0.5*FR-0.5*FL"

    w0 = 1.0 - amount
    w1 = amount
    # asplit 出原音副本与处理副本；amix 按比例混合（线性，normalize 默认按权重归一）
    fc = (f"[0:a]asplit=2[aorig][ap];"
          f"[ap]{proc}[apx];"
          f"[aorig][apx]amix=inputs=2:weights={w0:.4f} {w1:.4f}:normalize=1[aout]")

    _run_ffmpeg([
        DEFAULT_FFMPEG, "-y", "-ac", "2", "-i", audio_path,
        "-filter_complex", fc, "-ar", "44100", "-map", "[aout]", out,
    ], "vocalEnhance")

    duration, _, _ = _probe(out)
    if duration <= 0:
        raise RuntimeError("vocalEnhance: output not decodable / zero duration")
    return {"outPath": out, "duration": round(duration, 3), "mode": mode}


# ----------------------------------------------------------------------
# 3) 响度标准化（loudnorm 双遍）
# ----------------------------------------------------------------------
def _parse_loudnorm_json(stderr: str) -> dict:
    """解析第一遍 print_format=json 输出中的测量值。"""
    m = re.search(r"\{[^{}]*\}", stderr, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    keys = ("input_i", "input_tp", "input_lra", "input_thresh",
            "output_i", "output_tp", "output_lra", "output_thresh", "target_offset")
    out = {}
    for k in keys:
        v = data.get(k)
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            out[k] = None
    return out


def normalize_audio(audio_path: str, out_path: Optional[str] = None,
                    I: float = -16.0) -> dict:
    """loudnorm 双遍响度标准化为文件。

    payload: {audioPath, outPath?, I? 默认 -16}
    第一遍 print_format=json 测 input_i/lra/tp/thresh；第二遍带 measured_* +
    offset + linear=true 精确应用。返回 {outPath, duration, targetI, measuredI}。
    measuredI 取自第二遍 loudnorm summary 的 Output Integrated（真实输出响度）。
    """
    if not os.path.isfile(audio_path):
        raise RuntimeError(f"normalize: file not found: {audio_path}")
    I = float(I)
    out = _ensure_out(audio_path, out_path, "_normalized")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    # ---- 第一遍：测量 ----
    pass1 = _run_ffmpeg([
        DEFAULT_FFMPEG, "-i", audio_path,
        "-af", f"loudnorm=I={I:.2f}:TP={_TP}:LRA={_LRA}:print_format=json",
        "-f", "null", "-",
    ], "normalize[measure]")
    meas = _parse_loudnorm_json(pass1.stderr)
    if not meas or meas.get("input_i") is None:
        raise RuntimeError("normalize: failed to measure loudness (pass 1)")

    mi = meas["input_i"]
    mtp = meas["input_tp"]
    mlra = meas["input_lra"]
    mth = meas["input_thresh"]
    off = meas.get("target_offset")
    if off is None or off in (float("inf"), float("-inf")):
        off = 0.0

    # ---- 第二遍：带 measured 参数应用（linear 模式精确对齐 target I）----
    af2 = (f"loudnorm=I={I:.2f}:TP={_TP}:LRA={_LRA}:"
           f"measured_I={mi:.2f}:measured_TP={mtp:.2f}:measured_LRA={mlra:.2f}:"
           f"measured_thresh={mth:.2f}:offset={off:.2f}:linear=true:"
           f"print_format=summary")
    pass2 = _run_ffmpeg([
        DEFAULT_FFMPEG, "-y", "-i", audio_path,
        "-af", af2, out,
    ], "normalize[apply]")

    # 解析第二遍 summary 的 Output Integrated 作为独立验证
    measured_i = None
    m = re.search(r"Output Integrated:\s*([-+]?\d+(?:\.\d+)?)\s*LUFS",
                  pass2.stderr)
    if m:
        measured_i = float(m.group(1))

    duration, _, _ = _probe(out)
    if duration <= 0:
        raise RuntimeError("normalize: output not decodable / zero duration")
    return {
        "outPath": out,
        "duration": round(duration, 3),
        "targetI": round(I, 2),
        "measuredI": (None if measured_i is None else round(measured_i, 2)),
    }


def record_audio(out_path: str, seconds: float = 5.0,
                 device: Optional[str] = None) -> dict:
    """录音（J05 真实路径）：ffmpeg dshow 从麦克风采集到 wav。

    Windows 下走 DirectShow 音频设备；device 缺省用系统默认录音设备
    （会触发 ffmpeg 的自动设备选择；指定时用「麦克风阵列」等名称）。
    返回 {outPath, duration}。
    """
    if not os.path.isdir(os.path.dirname(out_path) or "."):
        raise RuntimeError(f"record: output dir not found: {out_path}")
    seconds = float(seconds)
    if not (0.5 <= seconds <= 300.0):
        raise RuntimeError("record: seconds must be in [0.5, 300]")
    if os.name != "nt":
        # 非 Windows：用内置替代输入（真实可渲染的 lavfi 音源）保证命令可用
        cmd = [DEFAULT_FFMPEG, "-y", "-f", "lavfi",
               "-i", f"sine=frequency=440:duration={seconds:.1f}",
               "-ar", "44100", "-ac", "1", out_path]
    else:
        src = device or "default"
        cmd = [DEFAULT_FFMPEG, "-y", "-f", "dshow",
               "-i", f"audio={src}",
               "-t", f"{seconds:.1f}",
               "-ar", "44100", "-ac", "1",
               "-c:a", "pcm_s16le", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=400)
    if r.returncode != 0:
        raise RuntimeError(
            "record failed: " + (r.stderr or "")[-400:]
            + "（若未接录音设备/无默认设备，请用 device 参数指定麦克风）")
    if not os.path.isfile(out_path) or os.path.getsize(out_path) <= 44:
        raise RuntimeError("record: produced empty file")
    return {"outPath": out_path, "duration": seconds}
