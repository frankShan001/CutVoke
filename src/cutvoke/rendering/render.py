"""CutVoke 媒体渲染服务（任务书 T11 / 第 12 章导出）。

把不可变工程快照（Sequence）编译成 ffmpeg filter_complex 图并真实导出为
MP4(H.264 + AAC)。

渲染范围（M0 + T16 音频混音）：
  - 视频：单轨 concat；多轨 primary 主轴 + 其余轨 overlay（位置默认左上角，可传参）
  - 音频（T16）：
      * 音频轨（kind=='audio'）的可见片段：按 source_start/时长 atrim + asetpts 裁剪，
        按时间线绝对位置延迟后用 amix 混合，保留片段之间的静音空白
      * 视频轨片段如果源文件本身带音频（如 av_sine.mp4 含 aac 音轨），默认一并提取其
        音轨，与画面一起导出（每条视频轨的嵌入音频作为一条独立音轨参与 amix）
      * 一个工程完全没有可用音频（视频 clip 无声 + 无音频轨 / 音频轨源无音轨）时，
        仍导出无声视频（不报错、不加音轨）
  - 导出先写临时文件，ffprobe 验证真实可解码 + 时长正确后再原子 rename
  - 所有 ffmpeg/ffprobe 调用都是 subprocess 参数数组，绝不拼 shell 字符串
  - 有理数时间只在「构造 ffmpeg 命令参数」这层转 float，权威运算在 model

复用 model 的 Rational / Clip / Track / Sequence / Project，不重写时间逻辑。
"""

from __future__ import annotations

import contextlib
import ctypes
import inspect
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
import copy
from functools import lru_cache, wraps
from fractions import Fraction
from typing import Any, Optional
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from cutvoke.domain.rational import Rational
from cutvoke.domain.model import Project, Sequence, Clip, AssetReference, Track, Caption
from cutvoke.domain.keyframes import evaluate as kf_evaluate
from cutvoke.domain.keyframes import expression as kf_expression
from cutvoke.rendering.mask_motion import animate_expression as animate_mask, coordinates as mask_coordinates
from cutvoke.rendering.effects import (EffectRegistry, default_registry, find_transition,
                      xfade_transition_name, collect_used_effect_ids,
                      animation_slot)
from cutvoke.rendering.caption_render import (prepare_caption_render, CaptionFontError,
                             _caption_font_instance)
from cutvoke.rendering import gpu_effects
from cutvoke.rendering.gpu_effects import acceleration_enabled
from cutvoke.rendering.render_memo import RenderMemo, file_identity

# subprocess resolves these names through PATH. Deployments that need a fixed
# binary can pass an explicit path to RenderService.
DEFAULT_FFMPEG = "ffmpeg"
DEFAULT_FFPROBE = "ffprobe"


def _process_cpu_seconds(process: subprocess.Popen) -> Optional[float]:
    """Read the Windows worker's CPU clock without an extra dependency."""
    if os.name != "nt":
        return None
    from ctypes import wintypes
    get_times = ctypes.WinDLL("kernel32", use_last_error=True).GetProcessTimes
    get_times.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    get_times.restype = wintypes.BOOL
    times = [wintypes.FILETIME() for _ in range(4)]
    if not get_times(wintypes.HANDLE(int(process._handle)), *(ctypes.byref(t) for t in times)):
        return None
    return sum((t.dwHighDateTime << 32) + t.dwLowDateTime for t in times[2:]) / 10_000_000

# 质量预设：映射到 libx264 的 crf / preset（第 12.5 章：至少支持一个 MP4 H.264 预设）
QUALITY_PRESETS: dict[str, dict[str, str]] = {
    "high": {"crf": "18", "preset": "slow"},
    "medium": {"crf": "23", "preset": "medium"},
    "low": {"crf": "28", "preset": "veryfast"},
    "preview": {"crf": "20", "preset": "veryfast"},
}

# Windows CreateProcess caps the full command line at 32,767 UTF-16 code
# units. Long multi-track timelines can produce a filter graph large enough to
# exceed that limit even when they reuse only a few source files. Keep room for
# the remaining FFmpeg arguments and pass larger graphs through a script file.
_FILTER_COMPLEX_INLINE_LIMIT = 12_000
_PREVIEW_IDLE_SECONDS = 8


@lru_cache(maxsize=8)
def _nvenc_available(ffmpeg_path: str) -> bool:
    """Verify the driver can encode, rather than trust FFmpeg's encoder list."""
    try:
        result = subprocess.run([
            ffmpeg_path, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
            "color=size=320x180:rate=24:duration=0.083333", "-an",
            "-c:v", "h264_nvenc", "-preset", "p4", "-f", "null", "-"],
            capture_output=True, timeout=8)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@lru_cache(maxsize=8)
def _filter_complex_file_option(ffmpeg_path: str) -> str:
    """Choose the installed FFmpeg syntax for reading a filtergraph file.

    FFmpeg replaced ``-filter_complex_script`` with ``-/filter_complex`` in
    newer releases. Probe the parser once per binary so packaged older builds
    can keep using the legacy spelling without making long renders fail.
    """
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".ffgraph") as graph_file:
            graph_file.write("[0:v]null[outv]")
            graph_file.flush()
            probe = subprocess.run(
                [ffmpeg_path, "-hide_banner", "-loglevel", "error",
                 "-/filter_complex", graph_file.name],
                capture_output=True, text=True, timeout=5,
                encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        # The actual render will produce the more useful startup diagnostic.
        return "-filter_complex_script"
    if "Unrecognized option '/filter_complex'" in probe.stderr:
        return "-filter_complex_script"
    return "-/filter_complex"


def _filter_graph_arguments(ffmpeg: str, graph: str, directory: Optional[str] = None):
    """Keep every preview/export entry point below Windows' command limit."""
    if len(graph.encode("utf-16-le")) // 2 <= _FILTER_COMPLEX_INLINE_LIMIT:
        return ["-filter_complex", graph], None
    descriptor, path = tempfile.mkstemp(prefix="cutvoke-filter-", suffix=".ffgraph", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as script:
            script.write(graph)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(path)
        raise
    return [_filter_complex_file_option(ffmpeg), path], path

# ---- V05 透明通道输出（alpha）----
# 只认 ProRes 4444(.mov)：它是剪辑软件交接透明素材的通用口径，且 ffprobe
# 会如实报告带 alpha 的 pix_fmt（yuva444p12le），**可验证**。
# 为什么不收 .webm/VP9（2026-09-21 实测结论，别再踩）：
#   本机 ffmpeg 9.0.1 用 libvpx-vp9 + -pix_fmt yuva420p + -auto-alt-ref 0
#   导出的 webm，容器会打上 ALPHA_MODE=1 标签（看起来"成功"），但 stream
#   pix_fmt 仍是 yuv420p，解码首帧 alpha 全 255 —— 是**假透明**。
#   VP8 同样如此。与其交付一个"看起来透明其实不透明"的文件，不如明确拒绝。
ALPHA_CONTAINERS = frozenset({".mov"})
# 带 alpha 的像素格式前缀（ffprobe 的 pix_fmt 判定用，避免"导出成功但没透明"）
_ALPHA_PIX_FMT_PREFIXES = ("yuva", "ya", "rgba", "bgra", "argb", "abgr", "gbrap")


def _pix_fmt_has_alpha(pix_fmt: Optional[str]) -> bool:
    """ffprobe 的 pix_fmt 是否带 alpha 通道（yuva420p / yuva444p10le / rgba…）。"""
    if not pix_fmt:
        return False
    pf = pix_fmt.strip().lower()
    if pf.endswith(("le", "be")):
        pf = pf[:-2]          # 去字节序：yuva444p10le -> yuva444p10
    pf = pf.rstrip("0123456789")   # 去位深：yuva444p10 -> yuva444p
    return pf.startswith(_ALPHA_PIX_FMT_PREFIXES) or pf.endswith("a")

# ---------------------------------------------------------------------------
# O06 色彩管理（色彩空间转换 + HDR→SDR 色调映射）
#
# 实测约束（2026-09-21，本机 ffmpeg 9.0.1 + zimg）：
# zscale 底层是 zimg，**要求输入色彩空间是已知的**。素材常见"未标定"
# （color_primaries/transfer/matrix 全 unknown）时，任何一个 zscale 都会
# 直接 `code 3074: no path between colorspaces` 并整条渲染链失败。
# 因此必须用输入侧选项 pin/tin/min 显式声明输入，且**链上每个 zscale
# 都要声明**（前一级的输出属性不会自动传给后一级）。
# 另注意 matrix 不接受 "bt2020"，BT.2020 非恒定亮度矩阵写作 bt2020nc。
# ---------------------------------------------------------------------------
COLOR_SPACE_TRIPLE = {
    # 目标名 -> (primaries, transfer, matrix)
    "bt709": ("bt709", "bt709", "bt709"),
    "bt601": ("smpte170m", "smpte170m", "smpte170m"),
    "bt2020": ("bt2020", "bt2020-10", "bt2020nc"),
}
# HDR 源的传输函数（PQ / HLG）
HDR_TRANSFER = {"pq": "smpte2084", "hlg": "arib-std-b67"}
TONEMAP_ALGORITHMS = frozenset({"none", "linear", "gamma", "clip",
                                "reinhard", "hable", "mobius"})


def _color_management_chain(color_space: Optional[str],
                            source_color_space: Optional[str],
                            tone_map: Optional[str],
                            source_transfer: Optional[str],
                            peak: float = 100.0) -> str:
    """构造追加在 [outv] 之后的色彩管理滤镜链（空串 = 不做任何处理）。

    色调映射（tone_map != none）语义：源按 HDR（BT.2020 + PQ/HLG）解释，
    线性化 → tonemap → 回到 SDR BT.709。映射后输出色彩空间已被锁定为
    bt709，故此时忽略 color_space。
    """
    tm = (tone_map or "none").strip().lower()
    src = (source_color_space or "bt709").strip().lower()
    parts: list[str] = []

    if tm != "none":
        tin = HDR_TRANSFER.get((source_transfer or "pq").strip().lower(),
                               "smpte2084")
        # 1) 声明输入为 BT.2020 + HDR 传输，线性化到浮点 RGB
        parts.append(f"zscale=pin=bt2020:tin={tin}:min=bt2020nc:"
                     f"p=bt2020:t=linear:m=bt2020nc:npl={peak:.1f}")
        parts.append("format=gbrpf32le")
        # 2) 线性域里做色调映射（这一步必须声明输入属性，否则 zimg 拒绝）
        parts.append("zscale=pin=bt709:tin=linear:min=bt709:p=bt709")
        parts.append(f"tonemap=tonemap={tm}:desat=2")
        # 3) 落回 SDR BT.709 并转回 8bit 4:2:0
        parts.append("zscale=pin=bt709:tin=bt709:min=bt709:"
                     "p=bt709:t=bt709:m=bt709")
        parts.append("format=yuv420p")
        return ",".join(parts)

    if color_space:
        sp, st, sm = COLOR_SPACE_TRIPLE[color_space]
        ip, it, im = COLOR_SPACE_TRIPLE.get(src, COLOR_SPACE_TRIPLE["bt709"])
        parts.append(f"zscale=pin={ip}:tin={it}:min={im}:p={sp}:t={st}:m={sm}")
        parts.append("format=yuv420p")
    return ",".join(parts)


# 图片源扩展名（1.5-A 图片素材）：这些源是单帧，渲染时须 -loop 1 续帧
# 才能在时间线上作为有持续时间的片段（F03/F07）。
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".apng",
               ".avif", ".tif", ".tiff")


def _is_image_src(src: str) -> bool:
    """按扩展名判断是否为静态图片源（1.5-A）。图片渲染需 -loop 1 续帧。"""
    return src.lower().endswith(_IMAGE_EXTS)


def _is_effect_enabled(e: dict) -> bool:
    """效果实例是否启用（J01 旁路：enabled=False 的效果不参与渲染，但保留在栈里）。

    旧工程没有该字段 → 视为启用，保证老工程渲染不变。
    """
    return e.get("enabled", True) is not False


def _find_animations(clip: Clip) -> list[dict]:
    """Return enabled animations in separate entrance/exit/loop/combo lanes."""
    found: dict[str, dict] = {}
    for e in getattr(clip, "effects", []) or []:
        slot = animation_slot(str(e.get("effectId", "")))
        if slot and slot not in found and _is_effect_enabled(e):
            found[slot] = e
    return list(found.values())


# ---------------------------------------------------------------------------
# 画面滤镜（fx）步骤表（J01：数据驱动，新增特效只加一行，不改渲染分支）
#
# 值为「由 params 生成 ffmpeg 滤镜片段」的函数；单流滤镜直接内联续接。
# 不在表里的 filter 键按「字面滤镜表达式」处理（如 colorchannelmixer=...），
# 便于内置与外部 manifest 用同一机制声明简单滤镜。
# ---------------------------------------------------------------------------
def _fx_posterize_expr(p: dict) -> str:
    """海报化（色阶压缩）。

    本机 ffmpeg 没有 posterize 滤镜（实测 Unknown filter），
    改用 lutyuv 查找表把每个通道量化到 N 级 —— 真实可渲染的等价实现。
    """
    levels = max(2, int(float(p.get("levels", 6))))
    step = 255.0 / (levels - 1)
    q = f"'round(val/{step:.4f})*{step:.4f}'"
    return f"lutyuv=y={q}:u={q}:v={q}"


def _fx_matrix_strength_expr(p: dict, target: tuple[float, ...]) -> str:
    """Blend a 3×4 color matrix from identity to the named look."""
    strength = max(0.0, min(1.0, float(p.get("strength", 1.0))))
    identity = (1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0)
    values = (base + strength * (styled - base)
              for base, styled in zip(identity, target, strict=True))
    return "colorchannelmixer=" + ":".join(f"{value:.6f}" for value in values)


def _fx_invert_strength_expr(p: dict) -> str:
    """Invert RGB with a continuous strength while keeping source alpha."""
    strength = max(0.0, min(1.0, float(p.get("strength", 1.0))))
    channel = f"'val*{1.0 - strength:.6f}+(255-val)*{strength:.6f}'"
    return f"lutrgb=r={channel}:g={channel}:b={channel}"


def _fx_edge_expr(p: dict) -> str:
    mode = "wires" if p.get("mode") == "wire" else str(p.get("mode", "colormix"))
    low = max(0.0, min(1.0, float(p.get("low", 0.0784314))))
    high = max(0.0, min(1.0, float(p.get("high", 0.196078))))
    return f"edgedetect=mode={mode}:low={low:.6f}:high={high:.6f}"


# ---------------------------------------------------------------------------
# J06 进阶画面/音频特效的滤镜表达式生成（真实可渲染的 ffmpeg 滤镜）
#
# 这些函数与 _fx_posterize_expr 同一机制，是 _FX_STEPS / _AUDIO_FX_STEPS 的
# 「数据驱动扩展」——不新增渲染分支，只在声明式滤镜表加条目。
# 注意：必须先于 _FX_STEPS 定义（模块加载顺序）。
# ---------------------------------------------------------------------------

# ffmpeg curves 的 preset 是「整数 0..10」，这里把对外友好的字符串枚举映射到整数。
_CURVES_PRESET_INT = {
    "none": 0,
    "increase_contrast": 4,
    "medium_contrast": 7,
    "strong_contrast": 9,
    "vintage": 10,
    "linear": 6,
}


def _fx_lut3d_expr(p: dict) -> str:
    """LUT 3D 调色：读取内置预设或已导入的 3D .cube 文件。

    预设文件位于 src/cutvoke/assets/luts/<preset>.cube。render.py 位于
    src/cutvoke/rendering/，须向上两级到包根再进 assets（渲染可能切换 cwd 到
    字幕临时目录，相对路径会失效，必须解析为绝对路径）。
    """
    imported = str(p.get("file", "") or "")
    if imported:
        cube = Path(imported)
        if not cube.is_absolute() or cube.suffix.lower() != ".cube" or not cube.is_file():
            raise RenderError(f"imported LUT missing: {imported}")
    else:
        preset = str(p.get("preset", "cool"))
        if preset not in ("cool", "warm", "retro"):
            raise RenderError(f"unknown built-in LUT preset: {preset}")
        cube = (Path(__file__).resolve().parent.parent / "assets" / "luts"
                / f"{preset}.cube")
    # ffmpeg 分两层解析：先解析 filtergraph，再解析 lut3d 的选项值。
    # 每层都要转义反斜杠和单引号；选项层还须转义 Windows 盘符冒号。
    option_path = (cube.as_posix().replace("\\", r"\\")
                   .replace("'", r"\'").replace(":", r"\:"))
    graph_path = option_path.replace("\\", r"\\").replace("'", r"\'")
    return f"lut3d=file={graph_path}"


def _fx_curves_expr(p: dict) -> str:
    """调色曲线：用 ffmpeg curves 的 preset 整数索引实现。"""
    preset = str(p.get("preset", "none"))
    idx = _CURVES_PRESET_INT.get(preset, 0)
    return f"curves=preset={idx}"


def _fx_trail_expr(p: dict) -> str:
    """F02 拖影：tmix 混合连续 N 帧，权重按 decay 几何衰减形成残影拖尾。

    实测约束（2026-09-21）：
    1. `weights` 的元素个数必须**恰好等于** `frames`，多一个少一个都会
       直接报 "number of weights does not match"（不是静默降级）。
    2. frames=1 退化为直通（无拖影），故下界取 2。
    """
    frames = max(2, min(20, int(float(p.get("frames", 4)))))
    decay = max(0.0, min(1.0, float(p.get("decay", 0.6))))
    weights = [decay ** i for i in range(frames)]
    # 归一化，避免整体亮度随帧数增大而漂移（tmix 默认也是归一化的）
    total = sum(weights) or 1.0
    weights = [w / total for w in weights]
    w_str = " ".join(f"{w:.6f}" for w in weights)
    return f"tmix=frames={frames}:weights='{w_str}'"


def _fx_hsl_expr(p: dict) -> str:
    """O02 HSL 调节：ffmpeg huesaturation。

    实测参数范围（`ffmpeg -h filter=huesaturation`）：
    hue -180..180（度）、saturation -1..1、intensity -1..1、
    lightness 是布尔（是否保留亮度，默认 false）。
    """
    hue = max(-180.0, min(180.0, float(p.get("hue", 0.0))))
    sat = max(-1.0, min(1.0, float(p.get("saturation", 0.0))))
    inten = max(-1.0, min(1.0, float(p.get("intensity", 0.0))))
    light = 1 if p.get("lightness", False) else 0
    return (f"huesaturation=hue={hue:.4f}:saturation={sat:.4f}:"
            f"intensity={inten:.4f}:lightness={light}")


def _smooth_closed_path(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Sample a closed Catmull-Rom path without changing its saved control points."""
    count = len(points)
    if count < 8:
        # A handful of anchors usually means an intentional polygon. Keep its corners.
        return points
    steps = max(1, min(4, 256 // count))
    sampled: list[tuple[float, float]] = []
    for index in range(count):
        p0, p1 = points[(index - 1) % count], points[index]
        p2, p3 = points[(index + 1) % count], points[(index + 2) % count]
        for step in range(steps):
            t = step / steps
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            sampled.append((max(0.0, min(1.0, x)), max(0.0, min(1.0, y)))
                           if math.isfinite(x) and math.isfinite(y) else p1)
    return sampled


def _bezier_closed_path(points: list[tuple[float, float]],
                       raw_points: list[dict]) -> list[tuple[float, float]]:
    """Sample closed cubic Bezier segments, defaulting absent handles to smooth tangents."""
    count = len(points)
    if count < 3:
        return points
    steps = max(1, min(8, 256 // count))
    handles: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for index, point in enumerate(points):
        previous, following = points[(index - 1) % count], points[(index + 1) % count]
        tangent = ((following[0] - previous[0]) / 6,
                   (following[1] - previous[1]) / 6)
        defaults = {
            "inHandle": (point[0] - tangent[0], point[1] - tangent[1]),
            "outHandle": (point[0] + tangent[0], point[1] + tangent[1]),
        }
        raw = raw_points[index] if index < len(raw_points) else {}
        controls: dict[str, tuple[float, float]] = {}
        for key, default in defaults.items():
            candidate = raw.get(key) if isinstance(raw, dict) else None
            try:
                x, y = float(candidate["x"]), float(candidate["y"])
            except (KeyError, TypeError, ValueError):
                x, y = default
            if not math.isfinite(x) or not math.isfinite(y):
                x, y = default
            controls[key] = (max(0.0, min(1.0, x)), max(0.0, min(1.0, y)))
        handles.append((controls["inHandle"], controls["outHandle"]))

    sampled: list[tuple[float, float]] = []
    for index, start in enumerate(points):
        end = points[(index + 1) % count]
        control1 = handles[index][1]
        control2 = handles[(index + 1) % count][0]
        for step in range(steps):
            t = step / steps
            inverse = 1 - t
            a, b, c, d = inverse ** 3, 3 * inverse ** 2 * t, 3 * inverse * t ** 2, t ** 3
            x = a * start[0] + b * control1[0] + c * control2[0] + d * end[0]
            y = a * start[1] + b * control1[1] + c * control2[1] + d * end[1]
            sampled.append((max(0.0, min(1.0, x)), max(0.0, min(1.0, y)))
                           if math.isfinite(x) and math.isfinite(y) else start)
    return sampled


def _fx_mask_expr(p: dict) -> str:
    """N02 几何/钢笔蒙版：按形状生成真实 Alpha，保留原画面色彩。

    `format=rgba` 先建立透明度平面；geq 的 alpha(X,Y) 让原素材自身的
    透明度与蒙版相乘。表达式逗号必须转义，避免被 filtergraph 拆开。
    """
    shape = str(p.get("shape", "rect")).strip().lower()
    feather = max(0.0, min(0.5, float(p.get("feather", 0.05))))
    invert = bool(p.get("invert", False))
    if shape == "freehand":
        raw_points = p.get("points", [])
        points: list[tuple[float, float]] = []
        valid_raw_points: list[dict] = []
        if isinstance(raw_points, list):
            for raw in raw_points:
                if not isinstance(raw, dict):
                    continue
                try:
                    px, py = float(raw["x"]), float(raw["y"])
                except (KeyError, TypeError, ValueError):
                    continue
                if math.isfinite(px) and math.isfinite(py):
                    point = (max(0.0, min(1.0, px)), max(0.0, min(1.0, py)))
                    if not points or point != points[-1]:
                        points.append(point)
                        valid_raw_points.append(raw)
        if len(points) < 3:
            raise RenderError("钢笔蒙版至少需要 3 个路径点；请在画布按住拖动绘制")
        path_mode = str(p.get("pathMode", "linear")).strip().lower()
        if path_mode in ("smooth", "bezier"):
            if len(points) > 128:
                raise RenderError("曲线钢笔蒙版最多支持 128 个控制点")
            points = (_smooth_closed_path(points) if path_mode == "smooth"
                      else _bezier_closed_path(points, valid_raw_points))

        crossings: list[str] = []
        distances: list[str] = []
        for index, (x1, y1) in enumerate(points):
            x2, y2 = points[(index + 1) % len(points)]
            dx, dy = x2 - x1, y2 - y1
            if abs(dy) > 1e-9:
                line_x = (f"{x1:.6f}*W+(Y-{y1:.6f}*H)*"
                          f"({dx:.6f}*W)/({dy:.6f}*H)")
                crossings.append(
                    f"gte(Y\\,{min(y1, y2):.6f}*H)*"
                    f"lt(Y\\,{max(y1, y2):.6f}*H)*lt(X\\,{line_x})")
            dx2, dy2 = dx * dx, dy * dy
            denominator = f"({dx2:.9f}*W*W+{dy2:.9f}*H*H)"
            projection = (
                f"clip(((X-{x1:.6f}*W)*({dx:.6f}*W)+"
                f"(Y-{y1:.6f}*H)*({dy:.6f}*H))/{denominator}\\,0\\,1)")
            distances.append(
                "sqrt(" +
                f"pow(X-({x1:.6f}*W+{projection}*{dx:.6f}*W)\\,2)+"
                f"pow(Y-({y1:.6f}*H+{projection}*{dy:.6f}*H)\\,2))")

        if not crossings:
            raise RenderError("钢笔蒙版路径不能全部落在同一水平线上")
        # FFmpeg reports ENOMEM when a long left-associated sum exceeds its
        # expression parser's recursion limit. Keep both sums and min trees balanced.
        while len(crossings) > 1:
            crossings = [f"({crossings[index]}+{crossings[index + 1]})"
                         if index + 1 < len(crossings) else crossings[index]
                         for index in range(0, len(crossings), 2)]
        inside = f"mod({crossings[0]}\\,2)"
        # A balanced min tree keeps FFmpeg's expression nesting shallow when a
        # smooth path expands into as many as 256 short edge segments.
        while len(distances) > 1:
            distances = [
                f"min({distances[index]}\\,{distances[index + 1]})"
                if index + 1 < len(distances) else distances[index]
                for index in range(0, len(distances), 2)
            ]
        nearest = distances[0]
        if feather > 0:
            mask = (f"clip(0.5+(2*{inside}-1)*({nearest})/"
                    f"max({feather:.6f}*min(W\\,H)\\,0.5)\\,0\\,1)")
        else:
            mask = f"if({inside}\\,1\\,0)"
        if invert:
            mask = f"(1-{mask})"
        mask = animate_mask(mask, p)
        return ("format=rgba,geq="
                "r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                f"a='alpha(X,Y)*({mask})'")

    if shape not in ("rect", "circle", "wipe", "ink"):
        shape = "rect"
    x = max(0.0, min(1.0, float(p.get("x", 0.15))))
    y = max(0.0, min(1.0, float(p.get("y", 0.15))))
    w = max(0.01, min(1.0, float(p.get("w", 0.7))))
    h = max(0.01, min(1.0, float(p.get("h", 0.7))))
    if shape == 'ink':
        seed=float(p.get('inkSeed',17))
        detail=float(p.get('inkDetail',1))
        noise=(f'0.50*sin(X/W*{23*detail:.6f}+{seed:.6f})*sin(Y/H*{19*detail:.6f}-{seed:.6f})+'
               f'0.25*sin((X+Y)/W*{57*detail:.6f})*cos((X-Y)/H*{41*detail:.6f})+'
               f'0.125*sin(X/W*{113*detail:.6f}+Y/H*{89*detail:.6f}+{seed*2:.6f})')
        radius=f'sqrt(pow((X/W-{x:.6f})/0.85\\,2)+pow((Y/H-{y:.6f})/0.85\\,2))'
        q='ld(1)' if p.get('motion','none')!='none' else '1'
        mask=(f'if(lte({q}\\,0)\\,0\\,if(gte({q}\\,1)\\,1\\,'
              f'clip((2.0*{q}-{radius}+({noise})*0.25)/'
              f'max({feather:.6f}\\,0.015)\\,0\\,1)))')
    elif shape == "wipe":
        angle = math.radians(float(p.get("angle", 0)))
        distance = (f"({x:.6f}*W-X)*{math.cos(angle):.9f}+"
                    f"({y:.6f}*H-Y)*{math.sin(angle):.9f}")
        mask = f"clip(0.5+({distance})/max({feather:.6f}*min(W\\,H)\\,0.5)\\,0\\,1)"
    elif shape == "rect":
        x0, x1 = f"{x:.6f}*W", f"{x + w:.6f}*W"
        y0, y1 = f"{y:.6f}*H", f"{y + h:.6f}*H"
        fw = f"max({feather * w:.6f}*W\\,0.5)"
        fh = f"max({feather * h:.6f}*H\\,0.5)"
        mask = (f"clip(min(X-{x0}\\,{x1}-X)/{fw}\\,0\\,1)"
                f"*clip(min(Y-{y0}\\,{y1}-Y)/{fh}\\,0\\,1)")
    else:
        cx, cy = f"{x:.6f}*W", f"{y:.6f}*H"
        rx = f"max({w / 2.0:.6f}*W\\,0.5)"
        ry = f"max({h / 2.0:.6f}*H\\,0.5)"
        if p.get("aspectMode", "bounds") == "circle":
            # Existing normalized ellipse bounds remain the default. Curated
            # circular masks opt in to one pixel radius on either axis.
            rx = ry = f"max(min({w / 2.0:.6f}*W\\,{h / 2.0:.6f}*H)\\,0.5)"
        dist = (f"sqrt(pow((X-{cx})/{rx}\\,2)+pow((Y-{cy})/{ry}\\,2))")
        f_r = f"max({feather:.6f}\\,0.0001)"
        mask = f"clip((1-{dist})/{f_r}\\,0\\,1)"

    if invert:
        mask = f"(1-{mask})"
    mask = animate_mask(mask, p)
    return ("format=rgba,geq="
            "r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            f"a='alpha(X,Y)*({mask})'")


def _escape_drawtext(value: str) -> str:
    """Escape literal text for FFmpeg drawtext with expansion disabled."""
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    escaped = normalized.replace("\\", "\\\\")
    for character in ("'", ":", ",", "[", "]"):
        escaped = escaped.replace(character, "\\" + character)
    return escaped.replace("\n", "\\n")


# R02 形状/标注调色板——固定色板而非任意色值：
# 参数校验器只支持 JSON Schema 子集（无 pattern），用 enum 才能让越界色值在
# 命令层就被拒绝；固定色板对"标注"这类场景也更统一。
SHAPE_PALETTE: dict[str, tuple[int, int, int]] = {
    "#FF3B30": (255, 59, 48),      # 红（默认：标注/警示）
    "#FFD60A": (255, 214, 10),     # 黄
    "#34C759": (52, 199, 89),      # 绿
    "#0A84FF": (10, 132, 255),     # 蓝
    "#FF9F0A": (255, 159, 10),     # 橙
    "#32ADE6": (50, 173, 230),     # 青
    "#FFFFFF": (255, 255, 255),    # 白
    "#000000": (0, 0, 0),          # 黑
}


def _fx_shape_expr(p: dict) -> str:
    """R02 形状标注：在画面上叠加矩形/椭圆/直线/箭头（填充或描边）。

    实现路线（实测 2026-09-21）：
    - **矩形/直线走 `drawbox`**：ffmpeg 原生支持 `color=0xRRGGBB@alpha` 与像素级
      `t=<厚度>`，**不需要转换色彩空间**，质量无损，是首选路径。直线复用同一机制，
      按 w/h 谁大决定横/纵，中心仍是 (x,y)，线宽复用 strokeWidth。
    - **椭圆走 `geq`**：ffmpeg 没有画椭圆的滤镜。`geq` 转 rgb24 后可直接对
      r/g/b 三个通道写值（`r(X,Y)` 取原像素），因此能做到任意色 + 环描边 +
      透明度混色。代价是一次 rgb24 往返；圆形/椭圆描边只能这样画。
    - **箭头 = 直线段(`drawbox`) + 箭头三角(`geq` 写 RGB)**：三角 apex 在长边
      末端、base 在长边 65% 处，半宽=短边一半，条件由 `between`×`lte` 推出
      （ffmpeg eval **没有 `and()`**，逻辑与用乘法）。末端 `format=yuv420p`
      （会丢掉 alpha 通道）。
      注意：geq 路径末端强制 `format=yuv420p`，所以**不要在透明导出里叠加形状**
      （会丢掉 alpha 通道）。
    """
    shape = str(p.get("shape", "rect")).strip().lower()
    mode = str(p.get("mode", "outline")).strip().lower()
    if shape not in ("rect", "ellipse", "line", "arrow"):
        raise RenderError(f"shape must be rect|ellipse|line|arrow, got {shape!r}")
    if mode not in ("fill", "outline"):
        raise RenderError(f"mode must be fill|outline, got {mode!r}")

    cx = max(0.0, min(1.0, float(p.get("x", 0.5))))
    cy = max(0.0, min(1.0, float(p.get("y", 0.5))))
    w = max(0.01, min(1.0, float(p.get("w", 0.3))))
    h = max(0.01, min(1.0, float(p.get("h", 0.3))))
    thick = max(1.0, min(40.0, float(p.get("strokeWidth", 4.0))))
    alpha = max(0.05, min(1.0, float(p.get("opacity", 1.0))))
    color = str(p.get("color", "#FF3B30")).strip().upper()
    rgb = SHAPE_PALETTE.get(color)
    if rgb is None:
        raise RenderError(f"unsupported shape color {color!r}; "
                          f"allowed: {sorted(SHAPE_PALETTE)}")

    x0 = cx - w / 2.0
    y0 = cy - h / 2.0
    col = f"0x{color.lstrip('#')}@{alpha:.3f}"

    if shape == "rect":
        thickness = "fill" if mode == "fill" else f"{thick:.0f}"
        return (f"drawbox=x=iw*{x0:.6f}:y=ih*{y0:.6f}"
                f":w=iw*{w:.6f}:h=ih*{h:.6f}"
                f":color={col}:t={thickness}")

    if shape == "line":
        # 直线：用 drawbox 画细长矩形。按 w/h 谁大决定横/纵；中心仍是 (x,y)，
        # 线宽复用 strokeWidth。直线恒为实心（它本身就是 1D 笔画，mode 无额外语义）。
        horizontal = w >= h
        if horizontal:
            return (f"drawbox=x=iw*{x0:.6f}:y=ih*{cy:.6f}-{thick:.1f}/2"
                    f":w=iw*{w:.6f}:h={thick:.1f}:color={col}:t=fill")
        return (f"drawbox=x=iw*{cx:.6f}-{thick:.1f}/2:y=ih*{y0:.6f}"
                f":w={thick:.1f}:h=ih*{h:.6f}:color={col}:t=fill")

    if shape == "ellipse":
        # 椭圆：归一化半径，用 geq 直接写 RGB
        rx, ry = w / 2.0, h / 2.0
        d2 = (f"pow((X/W-{cx:.6f})/{rx:.6f}\\,2)"
              f"+pow((Y/H-{cy:.6f})/{ry:.6f}\\,2)")
        if mode == "fill":
            cond = f"lte({d2}\\,1)"
        else:
            # 环：内边界按"最薄半轴上的像素厚度"换算成归一化缩放系数
            inner = f"max(0\\,1-{thick:.4f}/min({rx:.6f}*W\\,{ry:.6f}*H))"
            cond = f"lte({d2}\\,1)*gt({d2}\\,pow({inner}\\,2))"

        def plane(accessor: str, value: int) -> str:
            mixed = f"{accessor}*(1-{alpha:.3f})+{value}*{alpha:.3f}"
            return f"if({cond}\\,{mixed}\\,{accessor})"

        red = plane(r"r(X\,Y)", rgb[0])
        green = plane(r"g(X\,Y)", rgb[1])
        blue = plane(r"b(X\,Y)", rgb[2])
        return (f"format=rgb24,"
                f"geq=r='{red}'"
                f":g='{green}'"
                f":b='{blue}'"
                f",format=yuv420p")

    # arrow：直线段（drawbox）+ 箭头三角（geq 写 RGB）。按 w/h 谁大决定横/纵，
    # 横线指右、竖线指下；三角 apex 在长边末端、base 在长边 65% 处，半宽=短边一半。
    horizontal = w >= h
    if horizontal:
        x_apex = cx + w / 2.0
        head_len = 0.35 * w
        x_base = x_apex - head_len
        line_w = w - head_len
        line = (f"drawbox=x=iw*{x0:.6f}:y=ih*{cy:.6f}-{thick:.1f}/2"
                f":w=iw*{line_w:.6f}:h={thick:.1f}:color={col}:t=fill")
        head_h = h / 2.0
        cond = (f"between(X/W\\,{x_base:.6f}\\,{x_apex:.6f})"
                f"*lte(abs(Y/H-{cy:.6f})\\,{head_h:.6f}*({x_apex:.6f}-X/W)/{head_len:.6f})")
    else:
        y_apex = cy + h / 2.0
        head_len = 0.35 * h
        y_base = y_apex - head_len
        line_h = h - head_len
        line = (f"drawbox=x=iw*{cx:.6f}-{thick:.1f}/2:y=ih*{y0:.6f}"
                f":w={thick:.1f}:h=ih*{line_h:.6f}:color={col}:t=fill")
        head_w = w / 2.0
        cond = (f"between(Y/H\\,{y_base:.6f}\\,{y_apex:.6f})"
                f"*lte(abs(X/W-{cx:.6f})\\,{head_w:.6f}*({y_apex:.6f}-Y/H)/{head_len:.6f})")

    def plane(accessor: str, value: int) -> str:
        mixed = f"{accessor}*(1-{alpha:.3f})+{value}*{alpha:.3f}"
        return f"if({cond}\\,{mixed}\\,{accessor})"

    red = plane(r"r(X\,Y)", rgb[0])
    green = plane(r"g(X\,Y)", rgb[1])
    blue = plane(r"b(X\,Y)", rgb[2])
    return (line + ",format=rgb24,"
            f"geq=r='{red}'"
            f":g='{green}'"
            f":b='{blue}'"
            f",format=yuv420p")


def _fx_colorbalance_expr(p: dict) -> str:
    """O02 色彩平衡：ffmpeg colorbalance 的三档（阴影/中间调/高光）x RGB。

    实测参数范围：rs/gs/bs/rm/gm/bm/rh/gh/bh 均 -1..1。
    参数名后缀：s=shadows、m=midtones、h=highlights。
    """
    def g(key: str) -> float:
        return max(-1.0, min(1.0, float(p.get(key, 0.0))))

    # ffmpeg 选项名 = 通道(r/g/b) + 档位(s=阴影/m=中间调/h=高光)
    levels = "".join(
        f"{ch}{tier}={g(level + ch.upper()):.4f}:"
        for level, tier in (("shadows", "s"), ("midtones", "m"),
                            ("highlights", "h"))
        for ch in ("r", "g", "b")
    )
    pl = 1 if p.get("preserveLightness", False) else 0
    return f"colorbalance={levels}pl={pl}"


def _fx_deband_expr(p: dict) -> str:
    """去色带：四个平面共享受 schema 约束的阈值，避免颜色平面处理不一致。"""
    threshold = max(0.00003, min(0.5, float(p.get("threshold", 0.02))))
    sample_range = max(1, min(64, int(p.get("range", 16))))
    blur = 1 if p.get("blur", True) else 0
    return (f"deband=1thr={threshold:.6f}:2thr={threshold:.6f}:"
            f"3thr={threshold:.6f}:4thr={threshold:.6f}:"
            f"range={sample_range}:blur={blur}")


def _fx_lens_expr(p: dict) -> str:
    """镜头畸变：固定中心与双线性插值，仅开放安全的两档畸变参数。"""
    k1 = max(-1.0, min(1.0, float(p.get("k1", -0.15))))
    k2 = max(-1.0, min(1.0, float(p.get("k2", 0.0))))
    return (f"lenscorrection=cx=0.5:cy=0.5:k1={k1:.6f}:k2={k2:.6f}:"
            "i=bilinear:fc=black@1")


def _fx_film_grain_expr(p: dict) -> str:
    """固定种子使同一工程的预览和导出一致，temporal 决定颗粒是否逐帧变化。"""
    strength = max(0, min(100, int(p.get("strength", 8))))
    flags = "t+u" if p.get("temporal", True) else "u"
    return f"noise=all_seed=314159:alls={strength}:allf={flags}"


def _fx_grid_expr(p: dict) -> str:
    """网格的颜色来自 manifest enum，避免把任意滤镜语法透传给 ffmpeg。"""
    cell = max(16, min(512, int(p.get("cell", 96))))
    thickness = max(1, min(12, int(p.get("thickness", 1))))
    opacity = max(0.05, min(1.0, float(p.get("opacity", 0.25))))
    color = str(p.get("color", "white"))
    colors = {
        "white": "white",
        "black": "black",
        "#0A84FF": "0x0A84FF",
        "#FFD60A": "0xFFD60A",
    }
    return (f"drawgrid=width={cell}:height={cell}:thickness={thickness}:"
            f"color={colors.get(color, 'white')}@{opacity:.3f}")


def _fx_swing_expr(p: dict) -> str:
    """Rotate a slightly enlarged frame so the oscillation has no black corners."""
    degrees = max(1.0, min(12.0, float(p.get("degrees", 7.0))))
    hz = max(0.3, min(5.0, float(p.get("hz", 1.5))))
    return ("scale=w=iw*1.25:h=ih*1.25,"
            f"rotate=angle={degrees:.4f}*PI/180*sin(2*PI*t*{hz:.4f}):fillcolor=black,"
            "crop=w=iw/1.25:h=ih/1.25:x=(iw-ow)/2:y=(ih-oh)/2")


def _fx_shake_expr(p: dict) -> str:
    """Sample a moving inner window, then restore canvas size without borders."""
    pixels = max(2, min(18, int(round(float(p.get("pixels", 10.0))))))
    hz = max(0.5, min(10.0, float(p.get("hz", 4.0))))
    margin = max(4, int(round(pixels * 1.3)))
    return (f"crop=w=iw-{2 * margin}:h=ih-{2 * margin}:"
            f"x={margin}+{pixels}*sin(2*PI*t*{hz:.4f}):"
            f"y={margin}+{pixels}*cos(2*PI*t*{hz:.4f}),"
            f"scale=w=iw+{2 * margin}:h=ih+{2 * margin}:flags=bicubic")


def _build_squeeze_transition(outgoing: str, incoming: str, output: str,
                              axis: str, duration: float, offset: float,
                              parts: list[str]) -> None:
    """Compress only the outgoing seam segment, with finite safe endpoints.

    Native xfade squeeze can divide by zero at its final progress frame and
    uses names for the retained band rather than the compressed axis. Scaling
    once per frame also avoids a costly custom expression for every pixel.
    """
    if axis not in ("horizontal", "vertical"):
        raise RenderError(f"unknown transition squeeze axis: {axis}")
    factor = f"max(0,min(1,1-t/{duration:.6f}))"
    width = f"max(2,trunc(iw*({factor})/2)*2)" if axis == "horizontal" else "iw"
    height = f"max(2,trunc(ih*({factor})/2)*2)" if axis == "vertical" else "ih"
    compressed = f"{output}squeezed"
    background = f"{output}incoming"
    parts.append(
        f"[{outgoing}]trim=start={offset:.6f}:duration={duration:.6f},"
        f"setpts=PTS-STARTPTS,format=rgba,"
        f"scale=w='{width}':h='{height}':eval=frame,setsar=1[{compressed}]")
    parts.append(f"[{incoming}]setpts=PTS-STARTPTS[{background}]")
    # A zero-width/height frame is never created. At t=duration the outgoing
    # layer is explicitly disabled and B is exact, including its alpha plane.
    parts.append(
        f"[{background}][{compressed}]overlay=x=(W-w)/2:y=(H-h)/2:"
        f"shortest=0:eof_action=pass:repeatlast=0:format=auto:"
        f"enable='lt(t,{duration:.6f})'[{output}]")


def _xfade_blur_expr(pattern: str, *, has_alpha: bool = False) -> str:
    """Bounded transition-local blur: sharp at both ends, spatially varied midway.

    xfade evaluates the expression independently for each color plane. Only
    built-in pattern names reach this function; project data cannot inject an
    FFmpeg expression. Relative tap distances keep the blur consistent across
    preview and export sizes; clamping prevents out-of-frame samples from
    pulling the transition toward black.
    """
    def axis(base: str, dimension: str, fraction: float) -> str:
        if not fraction:
            return base
        sign = "+" if fraction > 0 else "-"
        return f"{base}{sign}{dimension}*{abs(fraction):.4f}"

    def tap(dx: float, dy: float, weight: int) -> tuple[str, str, int]:
        return axis("X", "W", dx), axis("Y", "H", dy), weight

    positions: dict[str, tuple[tuple[str, str, int], ...]] = {
        "vertical": tuple(tap(0, offset, weight)
                          for offset, weight in ((-.05, 1), (-.034, 2), (-.017, 3),
                                                 (0, 4), (.017, 3), (.034, 2), (.05, 1))),
        "diagonalDown": tuple(tap(dx, dy, weight)
                              for dx, dy, weight in ((-.05, -.028, 1), (-.034, -.019, 2),
                                                     (-.017, -.009, 3), (0, 0, 4),
                                                     (.017, .009, 3), (.034, .019, 2),
                                                     (.05, .028, 1))),
        "diagonalUp": tuple(tap(dx, dy, weight)
                            for dx, dy, weight in ((-.05, .028, 1), (-.034, .019, 2),
                                                   (-.017, .009, 3), (0, 0, 4),
                                                   (.017, -.009, 3), (.034, -.019, 2),
                                                   (.05, -.028, 1))),
        "cross": (tap(-.05, 0, 1), tap(-.025, 0, 2), tap(0, -.05, 1),
                  tap(0, -.025, 2), tap(0, 0, 8), tap(0, .025, 2),
                  tap(0, .05, 1), tap(.025, 0, 2), tap(.05, 0, 1)),
        "radial": tuple((f"X{factor:+.3f}*(X-W/2)" if factor else "X",
                         f"Y{factor:+.3f}*(Y-H/2)" if factor else "Y", weight)
                        for factor, weight in ((-.10, 1), (-.067, 2), (-.033, 3),
                                               (0, 4), (.033, 3), (.067, 2), (.10, 1))),
        "edgeFocus": (tap(-.04, 0, 1), tap(0, -.04, 1), tap(0, 0, 4),
                      tap(0, .04, 1), tap(.04, 0, 1)),
        "centerFocus": (tap(-.04, 0, 1), tap(0, -.04, 1), tap(0, 0, 4),
                        tap(0, .04, 1), tap(.04, 0, 1)),
    }
    if pattern not in positions:
        raise RenderError(f"unknown transition blur pattern: {pattern}")
    taps = positions[pattern]

    def blurred(source: str) -> str:
        def plane(index: int) -> str:
            return ("(" + "+".join(
                        f"{weight}*{source}{index}(clip({x},0,W-1),clip({y},0,H-1))"
                                   for x, y, weight in taps)
                    + f")/{sum(weight for _, _, weight in taps)}")

        # xfade evaluates the expression once per plane. Select the matching
        # source plane, including alpha for transparent exports.
        if has_alpha:
            return (f"if(eq(PLANE,0),{plane(0)},"
                    f"if(eq(PLANE,1),{plane(1)},"
                    f"if(eq(PLANE,2),{plane(2)},{plane(3)})))")
        return (f"if(eq(PLANE,0),{plane(0)},"
                f"if(eq(PLANE,1),{plane(1)},{plane(2)}))")

    weight = "4*P*(1-P)"
    if pattern in ("edgeFocus", "centerFocus"):
        distance = "min(1,2*(abs(X-W/2)/W+abs(Y-H/2)/H))"
        weight += f"*({distance})" if pattern == "edgeFocus" else f"*(1-({distance}))"
    # xfade's custom P decreases from 1 (old shot) to 0 (new shot).
    original = "(A*P+B*(1-P))"
    mixed_blur = f"(({blurred('a')})*P+({blurred('b')})*(1-P))"
    return f"{original}*(1-({weight}))+{mixed_blur}*({weight})"


_FX_STEPS: dict[str, "Callable[[dict], str]"] = {
    "gblur": lambda p: f"gblur=sigma={float(p.get('sigma', 5.0)):.2f}",
    "grayscale": lambda p: f"hue=s={1.0 - float(p.get('strength', 1.0)):.6f}",
    "chromatic": lambda p: (
        f"rgbashift=rh={int(p.get('offset', 4))}:rv={int(p.get('offset', 4))}:"
        f"bh=-{int(p.get('offset', 4))}:bv=-{int(p.get('offset', 4))}"),
    "glitch": lambda p: (
        f"noise=all_seed=271828:alls={max(1, int(float(p.get('amount', 0.4)) * 100))}:allf=u,"
        f"rgbashift=rh=2:bh=-2"),
    "swing": _fx_swing_expr,
    "shake": _fx_shake_expr,
    "invert": _fx_invert_strength_expr,
    "sepia": lambda p: _fx_matrix_strength_expr(p, (
        .393, .769, .189, 0, .349, .686, .168, 0, .272, .534, .131, 0)),
    "vintage": lambda p: _fx_matrix_strength_expr(p, (
        .9, .1, 0, 0, .1, .9, .1, 0, 0, .1, .8, .1)),
    "posterize": _fx_posterize_expr,
    "sharpen": lambda p: (
        f"unsharp=5:5:{float(p.get('amount', 1.0)):.2f}:5:5:0"),
    "edge": _fx_edge_expr,
    "mirror": lambda p: {
        "horizontal": "hflip", "vertical": "vflip", "both": "hflip,vflip",
    }.get(str(p.get("axis", "horizontal")), "hflip"),
    "vignette": lambda p: f"vignette=angle={p.get('angle', 'PI/5')}",
    # ---- J06 进阶画面特效（真实可渲染的 ffmpeg 滤镜，数据驱动扩展）----
    "chromakey": lambda p: (
        f"chromakey=color={p.get('color', '0x00FF00')}:"
        f"similarity={float(p.get('similarity', 0.2)):.3f}:"
        f"blend={float(p.get('blend', 0.0)):.3f}"),
    "lut3d": _fx_lut3d_expr,
    "curves": _fx_curves_expr,
    "hqdn3d": lambda p: (
        f"hqdn3d=luma_spatial={float(p.get('luma_spatial', 4.0)):.2f}"),
    "rgbsplit": lambda p: (
        f"rgbashift=rh={int(p.get('offset', 4))}:gv=0:"
        f"bh=-{int(p.get('offset', 4))}"),
    "crop": lambda p: (
        f"crop=w={float(p.get('w', 1.0)):.3f}*in_w:"
        f"h={float(p.get('h', 1.0)):.3f}*in_h:"
        f"x={float(p.get('x', 0.0)):.3f}*in_w:"
        f"y={float(p.get('y', 0.0)):.3f}*in_h"),
    # F02 闪烁：eq 亮度按时间表达式振荡（2*PI*t*Hz），eval=frame 逐帧求值才生效
    "flicker": lambda p: (
        f"eq=brightness=0.3*sin(2*PI*t*{float(p.get('hz', 3.0)):.3f}):eval=frame"),
    # ---- 纯工程批次（2026-09-21）：F02 拖影 / O02 HSL / O02 色彩平衡 ----
    "trail": _fx_trail_expr,
    "hsl": _fx_hsl_expr,
    "colorbalance": _fx_colorbalance_expr,
    # ---- 纯工程批次：N02 几何蒙版（矩形/椭圆 + 羽化 + 反转）----
    "mask": _fx_mask_expr,
    # ---- 纯工程批次：R02 形状标注（矩形 drawbox / 椭圆 geq）----
    "shape": _fx_shape_expr,
    # ---- P1 实用效果扩充：每个键映射到本机 ffmpeg 已确认存在的滤镜 ----
    "vibrance": lambda p: (
        f"vibrance=intensity={max(-2.0, min(2.0, float(p.get('intensity', 0.35)))):.4f}"),
    "colorize": lambda p: (
        f"colorize=hue={max(0.0, min(360.0, float(p.get('hue', 210.0)))):.3f}:"
        f"saturation={max(0.0, min(1.0, float(p.get('saturation', 0.55)))):.3f}:"
        f"lightness={max(0.0, min(1.0, float(p.get('lightness', 0.5)))):.3f}:"
        f"mix={max(0.0, min(1.0, float(p.get('mix', 0.7)))):.3f}"),
    "deband": _fx_deband_expr,
    "lens": _fx_lens_expr,
    "cas": lambda p: (
        f"cas=strength={max(0.0, min(1.0, float(p.get('strength', 0.45)))):.4f}"),
    "vflip": lambda p: {
        "horizontal": "hflip", "vertical": "vflip", "both": "hflip,vflip",
    }.get(str(p.get("axis", "vertical")), "vflip"),
    "filmgrain": _fx_film_grain_expr,
    "grid": _fx_grid_expr,
}

# 需要多输入建图的滤镜（不能内联续接），由 RenderService._fx_one 单独实现
_FX_MULTI = frozenset({"glow"})

# These filters depend on the current source frame and fixed parameters only.
# Transition-handle streams can safely run the same filter on frames after the
# selected range. Time-driven, random, and temporal-context filters deliberately
# stay on the last-frame fallback path.
_TRANSITION_HANDLE_STATIC_FX = frozenset({
    "cas", "chromakey", "chromatic", "colorbalance", "colorize", "crop",
    "curves", "deband", "edge", "glow", "grayscale", "grid", "gblur",
    "invert", "lens", "lut3d", "mask", "mirror", "posterize", "rgbsplit",
    "sepia", "shape", "sharpen", "vflip", "vibrance", "vignette", "vintage",
})


# ---------------------------------------------------------------------------
# J06 进阶画面/音频特效的滤镜表达式生成（真实可渲染的 ffmpeg 滤镜）
#
# 这些函数与 _fx_posterize_expr 同一机制，是 _FX_STEPS / _AUDIO_FX_STEPS 的
# 「数据驱动扩展」——不新增渲染分支，只在声明式滤镜表加条目。
# ---------------------------------------------------------------------------

def _fx_pan_expr(p: dict) -> str:
    """L01 声像平衡：balance -1 全左 / 0 居中 / +1 全右。

    实测约束（2026-09-21）：
    1. pan 滤镜的输入声道必须用**布局名** FL/FR——写 c0/left 会直接报
       `Expected in channel name, got "left+1*r"`，整条渲染链失败（不是静默失效）。
    2. 单声道源没有 FL/FR，先 `aformat=channel_layouts=stereo` 上混再 pan。
    3. 平衡语义 = 衰减远端声道（b>0 衰减左、b<0 衰减右），不是左右相加。
    """
    b = max(-1.0, min(1.0, float(p.get("balance", 0.0))))
    left_gain = 1.0 - max(b, 0.0)
    right_gain = 1.0 + min(b, 0.0)
    return (
        f"aformat=channel_layouts=stereo,"
        f"pan=stereo|FL={left_gain:.4f}*FL|FR={right_gain:.4f}*FR"
    )


# 音频特效（loudnorm 等）：视频特效链只处理视频流，音频特效在音频编译链应用。
_AUDIO_FX_STEPS: dict[str, "Callable[[dict], str]"] = {
    "sidechainDuck": lambda p: "anull",  # wired to key streams after collection
    "loudnorm": lambda p: (
        f"loudnorm=I={float(p.get('I', -16.0)):.2f}:"
        f"TP={float(p.get('TP', -1.5)):.2f}"),
    # L01 均衡器：单段 peaking EQ（频率/增益/带宽 Q）
    "equalizer": lambda p: (
        f"equalizer=f={float(p.get('frequency', 1000.0)):.1f}:"
        f"t={p.get('type', 'q')}:w={float(p.get('width', 1.0)):.2f}:"
        f"g={float(p.get('gainDb', 0.0)):+.1f}"),
    # L01 压限器：阈值(dB→线性)/压缩比/起音/释音
    "acompressor": lambda p: (
        f"acompressor=threshold={pow(10.0, float(p.get('threshold', -20.0)) / 20.0):.6f}:"
        f"ratio={float(p.get('ratio', 4.0)):.2f}:"
        f"attack={float(p.get('attack', 20.0)):.1f}:"
        f"release={float(p.get('release', 250.0)):.1f}"),
    # L01 声像（pan）：-1 全左，0 中，+1 全右
    "pan": _fx_pan_expr,
}


def _flatten_compound(clips: list[Clip], depth: int = 0) -> tuple[list[Clip], list[str]]:
    """把带 nested（复合片段）的外层片段递归展开为其内部子序列片段。

    语义（J08 展开）：外层复合片段 C（绝对时间 [C.start, C.end]）在成片上
    等价于其 nested 子序列内的片段按「相对时间 + C.start」平铺到外层时轴。
    返回 (展开后的片段列表, 警告列表)。深度上限防循环构造。
    """
    warnings: list[str] = []
    if depth > 4:
        warnings.append("复合片段嵌套过深，已截断展开（depth>4）")
        return [c for c in clips if c.nested is None], warnings
    out: list[Clip] = []
    had_nested = False
    for c in clips:
        sub = c.nested
        if sub is None:
            out.append(c)
            continue
        had_nested = True
        # 取子序列第一条可见视频轨（多轨复合简化为首视频轨 + 警告）
        vs = [t for t in sub.tracks if t.kind == "video" and t.visible]
        if not vs:
            warnings.append(f"复合片段 {c.id} 无视频轨，已忽略")
            continue
        if len(vs) > 1:
            warnings.append(f"复合片段 {c.id} 含多条视频轨，仅展开首轨（其余忽略）")
        inner = sorted((x for x in vs[0].clips if not x.hidden),
                       key=lambda x: x.timeline_start)
        # 复合片段可能自身带效果/动画（transform/动画），这样不是纯容器：
        # 保守做法是当前忽略外层变换，只把内部画面展开（剪映展开语义）。
        # 内层片段绝对时间 = 外层 C.start + 内部相对时间
        base = c.timeline_start
        for x in inner:
            lifted = _lift_clip(x, base)
            out.append(lifted)
        if not inner:
            warnings.append(f"复合片段 {c.id} 内部无片段，已展开为空")
    # 无任何嵌套时直接返回，避免深度递归生成假警告
    if not had_nested:
        return out, warnings
    # 递归深嵌套（内层片段可能又是复合片段）
    flattened, deeper = _flatten_compound(out, depth + 1)
    return flattened, warnings + deeper


def _lift_clip(c: Clip, base: Rational) -> Clip:
    """把子序列片段 x 平移到外层绝对时轴（时间平移渲染语义相同）。"""
    from copy import deepcopy
    n = deepcopy(c)
    n.timeline_start = base + c.timeline_start
    n.timeline_end = base + c.timeline_end
    n.id = f"{c.id}__{base.num}_{base.den}"  # 防跨轨外 id 冲突
    if c.nested is not None:
        sub = deepcopy(c.nested)
        # 内层时间整体再平移 base
        for t in sub.tracks:
            for k in t.clips:
                k.timeline_start = base + k.timeline_start
                k.timeline_end = base + k.timeline_end
        n.nested = sub
    return n


def _text_clip_params(clip: Clip) -> Optional[dict]:
    """从片段效果栈里取 cutvoke.text 效果的参数；无则 None。"""
    from cutvoke.domain.model import EFFECT_TEXT
    for e in clip.effects or []:
        if e.get("effectId") == EFFECT_TEXT:
            return e.get("params") or {}
    return None


def _abs_rat(r: Rational) -> Rational:
    """返回有理数的绝对值（Rational 是 NamedTuple，无 __neg__）。"""
    return Rational.of(abs(r.num), r.den)


def _duration_to_float(d: Any) -> float:
    """把效果参数里的时长转 float（接受数字或有理数 dict {"num","den"}）。"""
    if isinstance(d, dict) and "num" in d and "den" in d:
        return float(Rational.from_json(**d).to_fraction())
    return float(d)


def _atempo_chain(speed_f: float) -> list[str]:
    """把任意正倍速拆成 ffmpeg atempo 可接受的链（单段范围 0.5~2.0）。

    例如 speed=4 -> ['atempo=2.0','atempo=2.0']；speed=0.25 -> 两个 0.5。
    speed=1 -> ['atempo=1.0']（无害透传）。
    """
    parts: list[str] = []
    remaining = float(speed_f)
    while remaining > 2.0 + 1e-9:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.6f}")
    return parts


@lru_cache(maxsize=8)
def _has_rubberband(ffmpeg_path: str) -> bool:
    """The time-varying, pitch-preserving speed curve needs this FFmpeg filter."""
    try:
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-h", "filter=rubberband"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "rubberband AVOptions" in result.stdout


@lru_cache(maxsize=8)
def _has_minterpolate(ffmpeg_path: str) -> bool:
    """Check the optional FFmpeg filter used for motion-compensated slow motion."""
    try:
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-h", "filter=minterpolate"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "minterpolate AVOptions" in result.stdout


class RenderError(Exception):
    """渲染失败（工程不合法、ffmpeg 非零、ffprobe 验证不通过等）。"""


@lru_cache(maxsize=8)
def _setpts_clears_frame_rate(ffmpeg_path: str) -> bool:
    """Probe link metadata rather than guessing from a distributor's version.

    FFmpeg 7 clears CFR metadata in setpts; FFmpeg 6 preserves it. Adding fps
    unnecessarily on 6 discards short handles and the final alpha frame because
    that version forwards overlay EOF at the last picture's timestamp.
    """
    try:
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-nostdin", "-filter_threads", "1",
             "-f", "lavfi", "-i", "color=s=16x16:r=30:d=0.1",
             "-vf", "setpts=N,showinfo", "-frames:v", "1", "-f", "null", "-"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RenderError(f"cannot inspect FFmpeg setpts frame clock: {error}") from error
    for line in result.stderr.splitlines():
        if result.returncode == 0 and "config in time_base:" in line and "frame_rate:" in line:
            rate = line.split("frame_rate:", 1)[1].split(",", 1)[0].strip()
            return rate in {"1/0", "0/0", "0/1"}
    raise RenderError("FFmpeg did not report its setpts frame clock: " + result.stderr[-400:])


class RenderCancelled(RenderError):
    """渲染被取消（cancel_event 触发），不留下误导性的成功成片。"""


def _prepare_static_masks(method):
    signature = inspect.signature(method)
    @wraps(method)
    def prepared(self, project, *args, **kwargs):
        arguments = signature.bind(self, project, *args, **kwargs)
        cancel = arguments.arguments.get("cancel_event")
        values = arguments.arguments
        original_project=project
        from cutvoke.rendering.compound_sources import prepare as prepare_compounds
        project = prepare_compounds(self, project, cancel)
        previous_scale=getattr(self._render_ctx,'preview_scale',1.0)
        preview_scale=1.0
        if method.__name__=='render_preview_window':
            from cutvoke.rendering.preview_quality import snapshot
            project,preview_scale=snapshot(project,values.get('preview_max_dimension',1280))
        elif method.__name__=='extract_frame' and values.get('size') and max(values['size']) in {320,640,960,1280,1920}:
            # The editor's quick poster is a display preview too. Scaling only
            # its final PNG still pays full-resolution effect cost on every seek.
            # Explicit full-size frames and still-image export retain authority.
            from cutvoke.rendering.preview_quality import snapshot
            project,preview_scale=snapshot(project,max(values["size"]))
        self._render_ctx.preview_scale=preview_scale
        window = None
        if "timeline_time" in values:
            at, step = self._static_frame_time(project.sequence, values["timeline_time"])
            window = (at, at + 2 * step)
        elif "start" in values and "duration" in values:
            window = (values["start"], values["start"] + values["duration"])
        elif values.get("output_duration") is not None:
            start = values.get("output_start", 0.0)
            window = (start, start + values["output_duration"])
        previous_clocks=getattr(self._render_ctx,'clip_clocks',{})
        previous_origin=getattr(self._render_ctx,'bounded_origin',None)
        previous_audio_sequence=getattr(self._render_ctx,'export_audio_sequence',None)
        previous_audio_delta=getattr(self._render_ctx,'audio_origin_delta',0.)
        previous_verified=getattr(self._render_ctx,'verified_input_paths',set())
        self._render_ctx.export_audio_sequence=project.sequence if method.__name__=='render' else None
        self._render_ctx.audio_origin_delta=0.
        self._render_ctx.verified_input_paths=set()
        self._render_ctx.clip_clocks={}
        self._render_ctx.bounded_origin=None
        if (method.__name__ in {'extract_frame','render_preview_window'} or
                (method.__name__=='render' and window is not None and window[1]-window[0] <= 8)):
            from cutvoke.rendering.preview_window import bounded
            project,clocks,origin=bounded(project,window,self)
            self._render_ctx.clip_clocks=clocks
            self._render_ctx.bounded_origin=origin
            self._render_ctx.verified_input_paths={os.path.normcase(os.path.abspath(c.asset_ref.source_path))
                for t in project.sequence.tracks for c in t.clips if c.id in clocks}
        previous = getattr(self._render_ctx, 'gpu', None)
        previous_pixel=getattr(self._render_ctx,'pixel_gpu',None)
        previous_clips=getattr(self._render_ctx,'gpu_clips',{})
        needed=self._preparation_clip_ids(project.sequence,window)
        self._render_ctx.verified_input_paths.difference_update(
            os.path.normcase(os.path.abspath(c.asset_ref.source_path))
            for t in project.sequence.tracks if t.visible and t.kind=='video'
            for c in t.clips if c.id in needed and c.id not in self._render_ctx.clip_clocks)
        use_gpu=any(self._gpu_candidate(effect) for track in project.sequence.tracks if track.visible
                    for clip in track.clips if clip.id in needed and not clip.hidden
                    for effect in self._enabled_fx(clip))
        if (method.__name__ in {'extract_frame','render_preview_window'} and
                getattr(self._render_ctx,'bounded_origin',None) is not None):
            layers=sum(1 for t in project.sequence.tracks if t.kind=='video' and t.visible
                       and any(c.id in needed and not c.hidden for c in t.clips))
            use_gpu=use_gpu or layers>1 or any(
                e.get('effectId')=='cutvoke.fx.mask' for t in project.sequence.tracks
                if t.visible for c in t.clips if c.id in needed for e in self._enabled_fx(c))
        try:
            with gpu_effects.compiler(self.ffmpeg, project.sequence.fps.to_fraction(),
                                     use_gpu and self.gpu_effects and not self._gpu_failed) as gpu:
                self._render_ctx.gpu = gpu
                self._render_ctx.pixel_gpu = None
                self._render_ctx.gpu_clips = {}
                with self._procedural_sources(project, cancel, window) as procedural:
                    with self._raster_mask_sources(procedural, cancel, window) as mattes:
                        with self._static_mask_sources(mattes, cancel, window) as snapshot:
                            try:
                                result=method(self, snapshot, *args, **kwargs)
                                if isinstance(result,dict):
                                    result['effectProcessor']=('opencl:'+gpu.device['vendor']
                                        if gpu is not None and gpu.kernels else 'cpu')
                                    result['gpuEffects']=sorted(set(gpu.kernels)) if gpu else []
                                return result
                            except RenderCancelled:
                                raise
                            except RenderError:
                                if gpu is None or not gpu.kernels:
                                    raise
                                # Device loss or a driver/compiler limitation must
                                # rebuild the graph, not retry its GPU filters on CPU.
                                self._gpu_failed = True
                                return prepared(self, original_project, *args, **kwargs)
        finally:
            self._render_ctx.gpu = previous
            self._render_ctx.pixel_gpu=previous_pixel
            self._render_ctx.gpu_clips=previous_clips
            self._render_ctx.preview_scale=previous_scale
            self._render_ctx.clip_clocks=previous_clocks
            self._render_ctx.bounded_origin=previous_origin
            self._render_ctx.export_audio_sequence=previous_audio_sequence
            self._render_ctx.audio_origin_delta=previous_audio_delta
            self._render_ctx.verified_input_paths=previous_verified
    return prepared


class RenderService:
    def __init__(self, ffmpeg_path: str = DEFAULT_FFMPEG,
                 ffprobe_path: str = DEFAULT_FFPROBE,
                 registry: Optional[EffectRegistry] = None,
                 gpu_effects: bool = True) -> None:
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path
        # 效果注册表（T05/T17）：转场等效果由清单声明驱动，内核不写死 effectId
        self.effects = registry or default_registry()
        # V05 透明导出的"渲染期标志"（thread-local，避免并发请求互相串味）。
        # 为什么用线程局部而不是层层传参：画布底 `color=c=black` 深埋在
        # _composite_on_canvas / 关键帧子段 / 转场三条分支里，漏传任何一处
        # 都会"静默变成黑底"（alpha 存在但内容不透明），比报错更难发现。
        self._render_ctx = threading.local()
        self.gpu_acceleration = acceleration_enabled()
        self.gpu_effects = gpu_effects and self.gpu_acceleration
        self._gpu_failed = False
        self._probe_cache = RenderMemo()
        self._clock_cache = RenderMemo()
        # Compressed RGBA stills only; no live textures or temporary paths survive
        # their request. Metadata and pixels have separate bounded LRU budgets.
        self._graphic_cache = RenderMemo(max_entries=128, max_bytes=64 * 1024**2)
        self._procedural_cache = RenderMemo(max_entries=32, max_bytes=64 * 1024**2)
        self._procedural_item_bytes = 16 * 1024**2

    def _gpu_arguments(self, graph):
        gpu = getattr(self._render_ctx, 'gpu', None)
        if gpu is not None and 'program_opencl=' in graph:
            return ['-init_hw_device', gpu.device['specification'],
                    '-filter_hw_device', 'cutvoke']
        return []

    def _canvas_source_filter(self, clip):
        mode = getattr(self._render_ctx, 'reframe', 'stretch')
        if mode == 'stretch' or clip.role == 'sticker': return ''
        W, H = self._render_ctx.canvas_width, self._render_ctx.canvas_height
        if mode == 'cover':
            return f',scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1'
        return (f',scale={W}:{H}:force_original_aspect_ratio=decrease,format=rgba,'
                f'pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black@0,setsar=1')

    def _preview_source_filter(self, clip):
        scale=getattr(self._render_ctx,'preview_scale',1.0)
        # A folded static graphic has already been sampled at preview size.
        if scale==1 or Path(clip.asset_ref.source_path).parent.name.startswith('cutvoke-static-mask-'):
            return ''
        return f',scale=w=max(2\\,trunc(iw*{scale:.12f}/2)*2):h=max(2\\,trunc(ih*{scale:.12f}/2)*2)'

    def _gpu_candidate(self, effect):
        key,params=self._fx_key_params(effect)
        if key=='shatter':
            return True
        if key=='mask' and params.get('motion','none')!='none':
            if params.get('shape')=='freehand':
                return True
            if params.get('shape')=='text':
                return True
            if params.get('shape')=='ink':
                from cutvoke.rendering.vfx_matte import supports
                return not supports(params)
        return False

    def _gpu_clip(self, clip, fps):
        """Frame-index shaders require the same regular clock as the input.

        Speed curves and variable-rate sources retain presentation-time CPU
        formulas. Geometry has its own explicit output fps and is unrestricted.
        """
        if (getattr(self._render_ctx,'gpu',None) is None or
                clip.speed.to_fraction()!=1 or clip.speed_curve is not None):
            return None
        if _is_image_src(clip.asset_ref.source_path) or clip.freeze_at is not None:
            return self._render_ctx.gpu
        key=(clip.asset_ref.source_path,clip.source_start,clip.duration,str(fps))
        cache=getattr(self._render_ctx,'gpu_clips',{})
        if key in cache:
            return self._render_ctx.gpu if cache[key] else None
        info=self.probe_media(clip.asset_ref.source_path)
        if info.get('avg_frame_rate')==str(fps) and info.get('r_frame_rate')==str(fps):
            cache[key]=self._regular_timestamps(clip.asset_ref.source_path,
                    float(clip.source_start.to_fraction()),float(clip.duration.to_fraction()),fps)
            if cache[key]:
                return self._render_ctx.gpu
        cache[key]=False
        return None

    @contextlib.contextmanager
    def _raster_mask_sources(self, project, cancel_event=None, window=None):
        """Share vectorized ink mattes only within this immutable render request."""
        from cutvoke.rendering.vfx_matte import supports, write_source
        if (getattr(self._render_ctx,'bounded_origin',None) is not None
                and getattr(self._render_ctx,'gpu',None) is not None):
            yield project
            return
        needed = self._preparation_clip_ids(project.sequence, window)
        if not any(e.get('effectId') == 'cutvoke.fx.mask' and supports(e.get('params', {}))
                   for t in project.sequence.tracks if t.visible and t.kind == 'video'
                   for c in t.clips if c.id in needed and not c.hidden
                   for e in self._enabled_fx(c)):
            yield project
            return
        snapshot = copy.deepcopy(project)
        fps = project.sequence.fps.to_fraction()
        probes, sources, clocks = {}, {}, {}
        with tempfile.TemporaryDirectory(prefix='cutvoke-ink-mattes-') as directory:
            for track in snapshot.sequence.tracks:
                if not track.visible or track.kind != 'video':
                    continue
                for clip in track.clips:
                    if (clip.id not in needed or clip.speed.to_fraction() != 1
                            or float(clip.duration.to_fraction()) > 30
                            or clip.speed_curve is not None or clip.freeze_at is not None):
                        continue
                    dimensions_changed = False
                    for effect in self._enabled_fx(clip):
                        key = effect.get('effectId', '').rsplit('.', 1)[-1]
                        if key == 'crop':
                            dimensions_changed = True
                        params = effect.get('params', {})
                        if key != 'mask' or dimensions_changed or not supports(params):
                            continue
                        source = clip.asset_ref.source_path
                        if source not in probes:
                            probes[source] = self.probe_media(source)
                        info = probes[source]
                        if not _is_image_src(source) and (
                                info.get('avg_frame_rate') != str(fps)
                                or info.get('r_frame_rate') != str(fps)):
                            continue
                        width, height = info.get('width', 0), info.get('height', 0)
                        scale=getattr(self._render_ctx,'preview_scale',1.0)
                        if scale!=1:
                            width,height=max(2,int(width*scale)//2*2),max(2,int(height*scale)//2*2)
                        if not width or not height:
                            continue
                        duration = float(clip.duration.to_fraction())
                        if not _is_image_src(source):
                            clock = (source, float(clip.source_start.to_fraction()), duration)
                            if clock not in clocks:
                                clocks[clock] = self._regular_timestamps(*clock, fps)
                            if not clocks[clock]:
                                continue
                        identity = json.dumps([params, width, height, str(fps), duration], sort_keys=True)
                        if identity not in sources:
                            path = Path(directory)/f'matte-{len(sources)}.mkv'
                            try:
                                write_source(path, params, width, height, fps, duration, self.ffmpeg,
                                             cancel_event.is_set if cancel_event else None,
                                             start_time=getattr(self._render_ctx,'clip_clocks',{}).get(clip.id,(0,None))[0])
                            except InterruptedError as exc:
                                raise RenderCancelled(str(exc)) from exc
                            sources[identity] = str(path)
                        params['_renderedInkMattePath'] = sources[identity]
                        params['_renderedInkMatteTimeBase'] = f'{fps.denominator}/{fps.numerator}'
            yield snapshot

    def _preparation_clip_ids(self, seq, window):
        """Keep preparation bounded without changing the editable/render clock.

        A transition can read the outgoing clip after its nominal end. Retain
        that predecessor just as _compile does, including its optical layers.
        """
        needed = set()
        for track in seq.tracks:
            if not track.visible:
                continue
            clips = sorted((c for c in track.clips if not c.hidden),
                           key=lambda c: c.timeline_start)
            for i, clip in enumerate(clips):
                if window is not None and not (
                    float(clip.timeline_start.to_fraction()) < window[1] and
                    float(clip.timeline_end.to_fraction()) > window[0]):
                    continue
                needed.add(clip.id)
                if window is not None and i and find_transition(clip, self.effects):
                    previous = clips[i - 1]
                    if previous.timeline_end == clip.timeline_start:
                        needed.add(previous.id)
        return needed

    @contextlib.contextmanager
    def _procedural_sources(self, project, cancel_event=None, render_window=None):
        """Cache generated optical layers without modifying editable authority."""
        needed = self._preparation_clip_ids(project.sequence, render_window)
        targets=[(c,e) for t in project.sequence.tracks if t.visible for c in t.clips if c.id in needed
                 if not c.hidden for e in c.effects if _is_effect_enabled(e)
                 and e.get('effectId') in ('cutvoke.fx.lightWeave','cutvoke.fx.opticalField')]
        if not targets:
            yield project
            return
        from cutvoke.rendering.vfx_light import cached_source
        snapshot=copy.deepcopy(project)
        tasks = []
        for track in snapshot.sequence.tracks:
            if not track.visible:continue
            for clip in track.clips:
                if clip.hidden or clip.id not in needed:continue
                for effect in clip.effects:
                    if not _is_effect_enabled(effect) or effect.get('effectId') not in ('cutvoke.fx.lightWeave','cutvoke.fx.opticalField'):continue
                    params=effect.setdefault('params',{})
                    if params.get('_renderedLightPath') and Path(params['_renderedLightPath']).is_file():continue
                    tasks.append((clip,effect,params))
        source_directory=tempfile.TemporaryDirectory(prefix='cutvoke-optical-frame-')
        clip_clocks=getattr(self._render_ctx,'clip_clocks',{})
        def prepare(index_task):
            index,task=index_task
            clip,effect,params=task
            try:
                if cancel_event and cancel_event.is_set():
                    raise RenderCancelled('light layer preparation cancelled')
                extra={}
                if effect.get('effectId')=='cutvoke.fx.opticalField':
                    from cutvoke.rendering.vfx_optics import frame as optical_frame, VERSION as optical_version
                    extra=dict(renderer=optical_frame,kind=f'optics-{optical_version}')
                if clip.id in clip_clocks:
                    extra['start_time']=clip_clocks[clip.id][0]
                    extra['transient_directory']=source_directory.name
                if render_window is not None:
                    extra['transient_directory']=source_directory.name
                    extra['prefix_duration']=max(2/float(project.sequence.fps.to_fraction()),
                        render_window[1]-float(clip.timeline_start.to_fraction())+
                        2/float(project.sequence.fps.to_fraction()))
                identity=(self.ffmpeg, json.dumps(params, sort_keys=True),
                    project.sequence.width, project.sequence.height,
                    str(project.sequence.fps.to_fraction()), str(clip.duration),
                    extra.get('kind','light'), extra.get('start_time',0.),
                    extra.get('prefix_duration'))
                generated=[]
                def generate():
                    path=cached_source(
                        params,project.sequence.width,project.sequence.height,
                        project.sequence.fps.to_fraction(),float(clip.duration.to_fraction()),
                        self.ffmpeg,lambda:bool(cancel_event and cancel_event.is_set()),**extra)
                    generated.append(path)
                    if Path(path).stat().st_size > self._procedural_item_bytes:
                        return RenderMemo.UNCACHED
                    return Path(path).read_bytes()
                cancelled=lambda:bool(cancel_event and cancel_event.is_set())
                try:
                    media=self._procedural_cache.get_or_compute(identity,generate,cancelled=cancelled)
                except RuntimeError as exc:
                    # A superseded producer may have shared this key with the
                    # current seek. Retry locally unless this request is stale too.
                    if 'cancelled' not in str(exc) or cancelled():
                        raise
                    media=self._procedural_cache.get_or_compute(identity,generate,cancelled=cancelled)
                if cancel_event and cancel_event.is_set():
                    raise RenderCancelled('light layer preparation cancelled')
                if media is RenderMemo.UNCACHED and not generated:
                    # Oversized results remain on disk and cannot share the
                    # producer's temporary path with another request's lifetime.
                    generate()
                # Keep the producer's existing file on a miss; a cache hit gets
                # its own request-owned file, so cancellation/cleanup stay local.
                if generated:
                    params['_renderedLightPath']=generated[0]
                else:
                    path=Path(source_directory.name)/f'reused-{index}.mkv'
                    path.write_bytes(media)
                    params['_renderedLightPath']=str(path)
            except InterruptedError as exc:
                raise RenderCancelled(str(exc)) from exc
            except RuntimeError as exc:
                if cancel_event and cancel_event.is_set():raise RenderCancelled(str(exc)) from exc
                raise RenderError('light layer preparation failed: '+str(exc)) from exc
        # Independent layers share a bounded pool; do not serialize every glow
        # movie or launch an unbounded encoder per timeline item.
        workers=min(4,max(1,(os.cpu_count() or 2)//2),max(1,len(tasks)),
                    max(1,8_388_608//max(1,project.sequence.width*project.sequence.height)))
        try:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                list(executor.map(prepare,enumerate(tasks)))
            yield snapshot
        finally:
            source_directory.cleanup()

    @contextlib.contextmanager
    def _static_mask_sources(self, project: Project, cancel_event=None, render_window=None):
        """Evaluate static masks/graphic stacks once; retain editable source effects.

        Frame-invariant shapes and light on a still should not repeat expensive
        pixel equations on every animation frame. Only an explicit static
        allowlist is folded, before clip transforms and their keyframes.
        """
        static_sources = {}
        needed = self._preparation_clip_ids(project.sequence, render_window)
        def is_static(source):
            if source in static_sources:
                return static_sources[source]
            suffix = Path(source).suffix.lower()
            result = suffix in {".jpg", ".jpeg", ".bmp"}
            if suffix == ".png":
                # APNG may use .png too. Its acTL precedes IDAT; never flatten
                # animated media into a single masked picture.
                try:
                    with open(source, "rb") as image:
                        if image.read(8) == b"\x89PNG\r\n\x1a\n":
                            while header := image.read(8):
                                if len(header) != 8:
                                    break
                                size, kind = int.from_bytes(header[:4], "big"), header[4:]
                                if kind == b"acTL":
                                    break
                                if kind == b"IDAT":
                                    result = True
                                    break
                                image.seek(size + 4, os.SEEK_CUR)
                except OSError:
                    pass
            static_sources[source] = result
            return result
        def eligible(clip):
            if clip.id not in needed:
                return False
            effects = self._enabled_fx(clip)
            path_mask = (len(effects) == 1 and effects[0].get("range") is None
                    and self._fx_key_params(effects[0])[0] == "mask"
                    and effects[0].get("params", {}).get("shape") in {"rect", "circle", "freehand", "wipe"}
                    and effects[0].get("params", {}).get("motion", "none") == "none")
            static_keys = {"shape", "glow", "chromatic", "chromakey"}
            graphic = (any(self._fx_key_params(e)[0] == "shape" for e in effects)
                       and all(e.get("range") is None and self._fx_key_params(e)[0] in static_keys
                               for e in effects)
                       and not any(e.get("effectId") == "cutvoke.color" and _is_effect_enabled(e)
                                   for e in clip.effects))
            return (path_mask or graphic) and is_static(clip.asset_ref.source_path)
        if not any(eligible(c) for t in project.sequence.tracks if t.visible for c in t.clips if not c.hidden):
            yield project
            return
        snapshot = copy.deepcopy(project)
        with tempfile.TemporaryDirectory(prefix="cutvoke-static-mask-") as directory:
            prepared = {}
            for track in snapshot.sequence.tracks:
                if not track.visible:
                    continue
                for clip in track.clips:
                    if clip.hidden or not eligible(clip):
                        continue
                    effects = self._enabled_fx(clip)
                    source = clip.asset_ref.source_path
                    if not os.path.isfile(source):
                        raise RenderError(f"source file missing for clip {clip.id}: {source}")
                    if cancel_event is not None and cancel_event.is_set():
                        raise RenderCancelled("mask preparation cancelled")
                    key = json.dumps([source, effects], sort_keys=True)
                    if key not in prepared:
                        output = str(Path(directory) / f"mask-{len(prepared)}.png")
                        parts, counter, current = [], [0], "0:v"
                        self._render_ctx.pixel_gpu = None
                        scale_filter=self._preview_source_filter(clip)
                        if scale_filter:
                            parts.append(f'[0:v]{scale_filter[1:]}[previewgraphic]')
                            current='previewgraphic'
                        for effect in effects:
                            filter_key, params = self._fx_key_params(effect)
                            current = self._fx_one(filter_key, params, current, parts, counter, 0)
                        parts.append(f"[{current}]format=rgba[staticgraphic]")
                        graph = ';'.join(parts)
                        identity = (self.ffmpeg, file_identity(source), graph)
                        def render_graphic():
                            filter_args, filter_file = _filter_graph_arguments(self.ffmpeg, graph, directory)
                            command = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                                       "-filter_threads", "2", "-filter_complex_threads", "2", "-threads", "2", "-i", source,
                                       *filter_args, "-map", "[staticgraphic]", "-frames:v", "1",
                                       "-threads", "2", "-pix_fmt", "rgba", output]
                            try:
                                result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                                        capture_output=True, timeout=30)
                            finally:
                                if filter_file:
                                    with contextlib.suppress(OSError):
                                        os.remove(filter_file)
                            if result.returncode:
                                raise RenderError("static mask preparation failed: " + result.stderr.decode("utf-8", "replace")[-800:])
                            return Path(output).read_bytes()
                        pixels = self._graphic_cache.get_or_compute(identity, render_graphic)
                        Path(output).write_bytes(pixels)
                        prepared[key] = output
                    clip.asset_ref.source_path = prepared[key]
                    clip.effects = [e for e in clip.effects if not any(e is folded for folded in effects)]
            if cancel_event is not None and cancel_event.is_set():
                raise RenderCancelled("mask preparation cancelled")
            yield snapshot

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------
    @_prepare_static_masks
    def render(self, project: Project, out_path: str, quality: str = "high",
               overlay_position: tuple[int, int] = (0, 0),
               overwrite: bool = False,
               cancel_event: Optional[threading.Event] = None,
               allow_unknown_effects: bool = False,
               video_bitrate_kbps: Optional[int] = None,
               audio_bitrate_kbps: Optional[int] = None,
               alpha: bool = False,
               color_space: Optional[str] = None,
               source_color_space: Optional[str] = None,
               tone_map: Optional[str] = None,
               source_transfer: Optional[str] = None,
               output_start: Optional[float] = None,
               output_duration: Optional[float] = None,
               progress_callback=None) -> dict[str, Any]:
        """把工程渲染为 MP4(H.264 + AAC) 视频。

        返回 dict：output_path, duration, width, height, has_audio, warnings。
        失败/取消：只写临时文件 (.tmp.mp4)，成功验证后原子 rename；失败则清理临时文件，
        绝不留下误导性成片（第 12.5 章）。

        效果策略（AC19）：工程引用了未注册的效果时**严格拒绝**（默认），
        避免"导出成功但效果被静默丢弃"；显式传 allow_unknown_effects=True
        才降级导出，并在 warnings 里留明确记录。
        """
        seq = project.sequence
        if progress_callback:
            progress_callback({"phase": "preparing"})
        requested_range = output_start is not None or output_duration is not None
        preflight_range = None
        if requested_range:
            if (output_start is None or output_duration is None
                    or not math.isfinite(output_start) or not math.isfinite(output_duration)
                    or output_start < 0 or output_duration <= 0):
                raise RenderError("output range requires finite start >= 0 and duration > 0")
            preflight_range = (output_start, output_start + output_duration)

        # V05 透明通道：容器必须支持 alpha，否则编码器会静默丢 alpha。
        # 只认 ProRes 4444(.mov) 与 VP9(.webm) 两个"真带 alpha"的口径。
        if alpha:
            ext = os.path.splitext(out_path)[1].lower()
            if ext not in ALPHA_CONTAINERS:
                raise RenderError(
                    f"transparent export only supports "
                    f"{sorted(ALPHA_CONTAINERS)} (ProRes 4444) — got "
                    f"{ext or 'no extension'}. MP4/H.264 cannot carry alpha, "
                    f"and WebM/VP9 silently writes a file that looks "
                    f"transparent but decodes with alpha=255 everywhere.")

        # 1. 预检（第 12.5 章：导出先预检工程合法性 / 原件 / 效果可用性 / 目标目录）
        preflight_warnings = self._preflight(
            project, out_path, overwrite,
            allow_unknown_effects=allow_unknown_effects, output_range=preflight_range)

        # O06 色彩管理：参数先校验（未知枚举直接报错，不静默忽略），再建滤镜链。
        if color_space is not None and color_space not in COLOR_SPACE_TRIPLE:
            raise RenderError(
                f"unknown colorSpace {color_space!r} "
                f"(available: {sorted(COLOR_SPACE_TRIPLE)})")
        if source_color_space is not None and \
                source_color_space not in COLOR_SPACE_TRIPLE:
            raise RenderError(
                f"unknown sourceColorSpace {source_color_space!r} "
                f"(available: {sorted(COLOR_SPACE_TRIPLE)})")
        tm = (tone_map or "none").strip().lower()
        if tm not in TONEMAP_ALGORITHMS:
            raise RenderError(
                f"unknown toneMap {tone_map!r} "
                f"(available: {sorted(TONEMAP_ALGORITHMS)})")
        st = (source_transfer or "pq").strip().lower()
        if st not in HDR_TRANSFER:
            raise RenderError(
                f"unknown sourceTransfer {source_transfer!r} "
                f"(available: {sorted(HDR_TRANSFER)})")
        color_chain = _color_management_chain(color_space, source_color_space,
                                              tm, st)
        # 透明导出与色彩管理互斥：色彩链末端会 format=yuv420p，把 alpha 丢掉，
        # 产出"看着成功其实不透明"的文件——直接拒绝，不静默降级。
        if alpha and color_chain:
            raise RenderError(
                "transparent export cannot be combined with color management: "
                "the color chain forces an opaque pixel format and would "
                "silently drop alpha")

        requested_range = output_start is not None or output_duration is not None
        render_window = None
        if requested_range:
            if (output_start is None or output_duration is None
                    or not math.isfinite(output_start) or not math.isfinite(output_duration)
                    or output_start < 0 or output_duration <= 0):
                raise RenderError("output range requires finite start >= 0 and duration > 0")
            full_duration = self._sequence_duration(seq)
            if output_start + output_duration > full_duration + 1e-6:
                raise RenderError(
                    f"output range ends at {output_start + output_duration:.6f}s, "
                    f"after timeline duration {full_duration:.6f}s")
            render_window = (output_start, output_start + output_duration)
        if cancel_event is not None and cancel_event.is_set():
            raise RenderCancelled("render cancelled before compilation")
        # Only dense long timelines pay for temporary lossless media and a
        # final encoding pass. Small projects retain their direct export path.
        clip_count = sum(len(_flatten_compound([c for c in t.clips if not c.hidden])[0])
                         for t in seq.tracks if t.visible and t.kind in {"video", "audio"})
        if clip_count > 24:
            full_duration = self._sequence_duration(seq)
            start = output_start or 0.0
            duration = output_duration if requested_range else full_duration
            if duration > 8:
                return self._render_in_windows(
                    seq, out_path, quality, overlay_position, cancel_event,
                    alpha, color_chain, color_space if tm == "none" else None,
                    video_bitrate_kbps, audio_bitrate_kbps, start, duration,
                    preflight_warnings,
                    **({"progress_callback": progress_callback} if progress_callback else {}))

        # 2. 编译 filter_complex 图 + 输入列表（有理数→float 仅在此层）
        #    _compile 在含字幕时会生成 ASS 临时目录（caption_cwd），需调用方清理。
        graph, input_paths, expected_duration, has_audio, warnings, caption_cwd = (
            self._compile(seq, overlay_position, alpha=alpha, render_window=render_window))
        warnings = preflight_warnings + warnings
        verify_duration = expected_duration
        if output_start is not None or output_duration is not None:
            verify_duration = output_duration

        # 3. 临时文件 + 最终路径（后缀跟随容器，编码器靠后缀选）
        out_dir = os.path.dirname(os.path.abspath(out_path))
        tmp_path = out_path + ".tmp" + os.path.splitext(out_path)[1]
        # 清理可能残留的临时文件，保证原子发布语义不被旧脏数据干扰
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

        try:
            # 4. 先把成片写到临时文件
            self._run_ffmpeg(seq, graph, input_paths, tmp_path, quality,
                             cancel_event, has_audio, caption_cwd=caption_cwd,
                             video_bitrate_kbps=video_bitrate_kbps,
                             audio_bitrate_kbps=audio_bitrate_kbps,
                             alpha=alpha, color_chain=color_chain,
                             output_start=(output_start - self._render_ctx.compile_origin
                                           if output_start is not None else None),
                             output_duration=output_duration,
                             progress_callback=progress_callback,
                             progress_duration=verify_duration)
            # 5. ffprobe 验证真实可解码 + 时长/尺寸正确（透明导出另验 alpha；
            #    色彩管理另验输出 primaries 真的落到位）
            # 色调映射会把输出锁成 bt709，此时不该再按 color_space 断言。
            probed_space = color_space if (tm == "none" and color_space) else None
            if progress_callback:
                progress_callback({"phase": "verifying"})
            probe = self._verify(tmp_path, verify_duration, seq.width,
                                 seq.height, expect_alpha=alpha,
                                 expect_primaries=probed_space)
        except BaseException:
            # 任何失败都清理临时文件，避免误导
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise
        finally:
            # 清理字幕 ASS 临时目录（无论成功失败）
            if caption_cwd and os.path.isdir(caption_cwd):
                shutil.rmtree(caption_cwd, ignore_errors=True)

        # 6. 验证通过 → 原子发布到最终文件名（同文件系统内 os.replace 原子）
        if cancel_event is not None and cancel_event.is_set():
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
            raise RenderCancelled("render cancelled before publication")
        os.replace(tmp_path, out_path)

        return {
            "output_path": out_path,
            "duration": probe["duration"],
            "width": probe["width"],
            "height": probe["height"],
            "has_audio": has_audio,
            "has_alpha": _pix_fmt_has_alpha(probe.get("pix_fmt")),
            "pix_fmt": probe.get("pix_fmt"),
            "color_primaries": probe.get("color_primaries"),
            "color_transfer": probe.get("color_transfer"),
            "color_space": probe.get("color_space"),
            "warnings": warnings,
            "videoEncoder": getattr(self._render_ctx, "video_encoder", "libx264"),
            "encoderFallback": getattr(self._render_ctx, "encoder_fallback", False),
        }

    def _render_in_windows(
            self, seq: Sequence, out_path: str, quality: str,
            overlay_position: tuple[int, int], cancel_event: Optional[threading.Event],
            alpha: bool, color_chain: str, expected_primaries: Optional[str],
            video_bitrate_kbps: Optional[int], audio_bitrate_kbps: Optional[int],
            start: float, duration: float, preflight_warnings: list[str],
            progress_callback=None) -> dict[str, Any]:
        """Render dense long timelines with at most one window's inputs alive.

        FFV1/PCM intermediates avoid repeated lossy video/AAC encoding and AAC
        priming gaps at window boundaries. The final concat has one media input,
        applies export color management once, and publishes only after verify.
        All intermediates belong to this request's unique temporary directory.
        """
        out_dir = os.path.dirname(os.path.abspath(out_path))
        warnings = list(preflight_warnings)
        has_audio = False
        chunks = []
        stage_started = time.monotonic()
        completed_seconds = 0.0
        if progress_callback:
            progress_callback({"phase": "rendering", "fraction": 0.0,
                               "processedSeconds": 0.0, "totalSeconds": duration})
        def check_cancel() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise RenderCancelled("render cancelled between windows")
        with tempfile.TemporaryDirectory(prefix="cutvoke-export-windows-", dir=out_dir) as directory:
            temporary = Path(directory)
            # All chunks share an audio stream whenever the project contains
            # one; windows in silence receive PCM silence, never a missing track.
            probed = {}
            for track in seq.tracks:
                if not track.visible or track.muted or track.kind not in {"audio", "video"}:
                    continue
                clips, _ = _flatten_compound([c for c in track.clips if not c.hidden])
                for clip in clips:
                    check_cancel()
                    if (float(clip.timeline_end.to_fraction()) <= start or
                            float(clip.timeline_start.to_fraction()) >= start + duration):
                        continue
                    source = clip.asset_ref.source_path
                    if source not in probed:
                        probed[source] = self.probe_media(source)
                    if probed[source].get("has_audio"):
                        has_audio = True
                        break
                if has_audio:
                    break
            end = start + duration
            frame_rate = seq.fps.to_fraction()
            # Fragment graphs contain many independently timed alpha branches.
            # Keep fewer simultaneous shot/effect clocks alive in FFmpeg.
            fragment_graph = any(
                effect.get("effectId") == "cutvoke.fx.shatter"
                for track in seq.tracks if track.visible
                for clip in track.clips if not clip.hidden
                for effect in clip.effects)
            window_seconds = 4 if fragment_graph else 8
            window_duration = max(1, round(float(frame_rate) * window_seconds)) / float(frame_rate)
            inherited_gpu=getattr(self._render_ctx,'gpu',None)
            def render_chunk(interval):
                cursor,chunk_duration=interval
                self._render_ctx.gpu=inherited_gpu
                self._render_ctx.gpu_clips={}
                self._render_ctx.pixel_gpu=None
                check_cancel()
                graph, inputs, _expected, chunk_audio, messages, caption_cwd = self._compile(
                    seq, overlay_position, alpha=alpha,
                    render_window=(cursor, cursor + chunk_duration))
                origin = self._render_ctx.compile_origin
                if has_audio and not chunk_audio:
                    graph += (f";anullsrc=r={seq.audio_sample_rate}:cl=stereo:"
                              f"d={cursor + chunk_duration - origin:.9f}[outa]")
                path = temporary / f"window-{round(cursor*float(frame_rate)):09d}.mkv"
                try:
                    self._run_ffmpeg(seq, graph, inputs, str(path), quality,
                        cancel_event, has_audio, caption_cwd=caption_cwd,
                        alpha=alpha, output_start=cursor - origin,
                        output_duration=chunk_duration, lossless=True)
                    self._verify(str(path), chunk_duration, seq.width, seq.height,
                                 expect_alpha=alpha)
                except RenderError as exc:
                    if ("stopped making progress" not in str(exc)
                            or chunk_duration <= 2):
                        raise
                    # Retry only the stalled interval with fewer active inputs;
                    # do not reset the source/effect clocks or reuse partial media.
                    half=max(1,round(float(frame_rate)*chunk_duration/2))/float(frame_rate)
                    recovered=(render_chunk((cursor,half))+
                               render_chunk((cursor+half,chunk_duration-half)))
                    recovered[0][2].append(f'Encoder stall: reduced export windows to {half:.3f}s')
                    return recovered
                finally:
                    if caption_cwd and os.path.isdir(caption_cwd):
                        shutil.rmtree(caption_cwd, ignore_errors=True)
                return [(path,chunk_duration,messages)]
            intervals=[]
            cursor=start
            while cursor<end-1e-7:
                step=min(window_duration,end-cursor)
                intervals.append((cursor,step))
                cursor+=step
            # Two bounded workers keep a many-core workstation busy without
            # opening the full timeline or an encoder per clip at once.
            workers=min(2,max(1,(os.cpu_count() or 2)//8),
                        max(1,2_097_152//max(1,seq.width*seq.height)))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                for result in executor.map(render_chunk,intervals):
                    for path,chunk_duration,messages in result:
                        chunks.append((path,chunk_duration))
                        warnings.extend(m for m in messages if m not in warnings)
                        completed_seconds += chunk_duration
                        if progress_callback:
                            elapsed = time.monotonic() - stage_started
                            speed = completed_seconds / elapsed if elapsed > 0 else None
                            progress_callback({"phase": "rendering",
                                "fraction": min(1.0, completed_seconds / duration),
                                "processedSeconds": completed_seconds, "totalSeconds": duration,
                                "speed": speed,
                                "etaSeconds": max(0.0, duration-completed_seconds)/speed if speed else None})
            check_cancel()
            manifest = temporary / "windows.ffconcat"
            lines = ["ffconcat version 1.0"]
            for path, chunk_duration in chunks:
                escaped = path.as_posix().replace("'", "'\\''")
                lines.extend((f"file '{escaped}'", f"duration {chunk_duration:.9f}"))
            manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
            # fps already produces zero-based, exact frame ticks. A following
            # identity setpts would strip rate/duration metadata on FFmpeg 7
            # and make its encoder drop/retime frames across chunk boundaries.
            graph = f"[0:v]fps={frame_rate.numerator}/{frame_rate.denominator}[outv]"
            if not _setpts_clears_frame_rate(self.ffmpeg):
                graph = graph.replace("[outv]", ",setpts=N/FRAME_RATE/TB[outv]")
            if has_audio:
                graph += (f";[0:a]aresample={seq.audio_sample_rate}:async=1:first_pts=0,"
                          f"atrim=duration={duration:.9f},asetpts=PTS-STARTPTS[outa]")
            final = temporary / ("final" + os.path.splitext(out_path)[1])
            self._run_ffmpeg(seq, graph, [str(manifest)], str(final), quality,
                cancel_event, has_audio, video_bitrate_kbps=video_bitrate_kbps,
                audio_bitrate_kbps=audio_bitrate_kbps, alpha=alpha,
                color_chain=color_chain, output_duration=duration, concat_input=True,
                progress_callback=progress_callback, progress_duration=duration,
                progress_phase="encoding")
            if progress_callback:
                progress_callback({"phase": "verifying"})
            probe = self._verify(str(final), duration, seq.width, seq.height,
                                 expect_alpha=alpha, expect_primaries=expected_primaries)
            check_cancel()
            os.replace(final, out_path)
        return {"output_path":out_path, "duration":probe["duration"],
                "width":probe["width"], "height":probe["height"],
                "has_audio":has_audio, "has_alpha":_pix_fmt_has_alpha(probe.get("pix_fmt")),
                "pix_fmt":probe.get("pix_fmt"), "color_primaries":probe.get("color_primaries"),
                "color_transfer":probe.get("color_transfer"), "color_space":probe.get("color_space"),
                "warnings":warnings, "renderStrategy":"lossless-windows", "windowCount":len(chunks),
                "videoEncoder":getattr(self._render_ctx,"video_encoder","libx264"),
                "encoderFallback":getattr(self._render_ctx,"encoder_fallback",False)}

    @_prepare_static_masks
    def render_preview_window(self, project: Project, out_path: str,
                              start: float, duration: float,
                              render_modes: Optional[list[dict[str, Any]]] = None,
                              cancel_event: Optional[threading.Event] = None,
                              preview_max_dimension: Optional[int] = 1280
                              ) -> dict[str, Any]:
        """Encode only the requested timeline interval for interactive playback.

        The same compiled video/audio graph drives export. Output-side seeking
        keeps effects and transitions exact even when the requested interval
        begins inside a transition or a long clip.
        """
        if not (0 <= start and 0 < duration <= 8):
            raise RenderError("preview window must have start >= 0 and duration in (0, 8]")
        self._preflight(project, out_path, True, output_range=(start, start + duration))
        seq = project.sequence
        expected = self._sequence_duration(seq)
        if start >= expected - 1e-6:
            raise RenderError("preview window begins after the end of the project")
        actual_duration = min(duration, expected - start)
        if cancel_event is not None and cancel_event.is_set():
            raise RenderCancelled("preview cancelled before compilation")
        graph, inputs, expected, has_audio, _warnings, caption_cwd = self._compile(
            seq, (0, 0), render_modes=render_modes,
            render_window=(start, start + actual_duration), frame_aligned_origin=True)
        temp_path = out_path + ".tmp.mp4"
        try:
            self._run_ffmpeg(
                seq, graph, inputs, temp_path, "preview", cancel_event, has_audio,
                caption_cwd=caption_cwd, output_start=start - self._render_ctx.compile_origin,
                output_duration=actual_duration,
                preview_source_duration=expected,
            )
            probe = self._verify(temp_path, actual_duration, seq.width, seq.height)
            if cancel_event is not None and cancel_event.is_set():
                raise RenderCancelled("preview cancelled before publication")
            os.replace(temp_path, out_path)
        except BaseException:
            if os.path.isfile(temp_path):
                os.remove(temp_path)
            raise
        finally:
            if caption_cwd and os.path.isdir(caption_cwd):
                shutil.rmtree(caption_cwd, ignore_errors=True)
        return {"output_path": out_path, "duration": probe["duration"],
                "width":seq.width,"height":seq.height,
                "windowStart": start, "has_audio": has_audio,
                "retryCount": getattr(self._render_ctx, "window_retry_count", 0),
                "recoveryMode": getattr(self._render_ctx, "window_recovery_mode", None),
                "videoEncoder": getattr(self._render_ctx, "video_encoder", "libx264"),
                "encoderFallback": getattr(self._render_ctx, "encoder_fallback", False)}

    def render_audio_only(self, project: Project, out_path: str,
                          overwrite: bool = False,
                          cancel_event: Optional[threading.Event] = None,
                          allow_unknown_effects: bool = False) -> dict[str, Any]:
        """只导出工程混音后的音频流（V04 独立声音输出，m4a/aac）。

        复用 _compile 生成的 filter_complex 图，ffmpeg 只 map [outa] 音轨，
        不 map 视频——成片为纯音频。返回 {output_path, duration, has_audio,
        warnings}。工程无音轨时抛 RenderError（不产出空音频误导）。
        """
        seq = project.sequence
        graph, input_paths, expected_duration, has_audio, warnings, caption_cwd = (
            self._compile(seq, (0, 0)))
        if not has_audio:
            if caption_cwd and os.path.isdir(caption_cwd):
                shutil.rmtree(caption_cwd, ignore_errors=True)
            raise RenderError("工程没有可导出的音轨（render_audio_only）")

        out_dir = os.path.dirname(os.path.abspath(out_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        if os.path.exists(out_path) and not overwrite:
            raise RenderError(f"output exists (overwrite=False): {out_path}")
        tmp_path = out_path + ".tmp.m4a"

        cmd: list[str] = [self.ffmpeg, "-y", "-nostdin"]
        for src in input_paths:
            if _is_image_src(src):
                fps_rat = seq.fps.to_fraction()
                cmd += ["-loop", "1", "-framerate",
                        f"{fps_rat.numerator}/{fps_rat.denominator}", "-i", src]
            else:
                cmd += ["-i", src]
        filter_args, filter_script_path = _filter_graph_arguments(self.ffmpeg, graph, caption_cwd)
        cmd += [*filter_args,
                # 视频流必须被 filtergraph 消耗（-f null - 丢弃），否则
                # outv 未连接 ffmpeg 报错；真正输出的只有音频 [outa]
                "-map", "[outv]", "-f", "null", "-",
                "-map", "[outa]",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                tmp_path]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=caption_cwd if caption_cwd else None)
            try:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise RenderCancelled("audio preview superseded")
                    try:
                        _, err = proc.communicate(timeout=.05)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate(timeout=10)
                    with contextlib.suppress(OSError):
                        os.remove(tmp_path)
        finally:
            if filter_script_path:
                with contextlib.suppress(OSError):
                    os.remove(filter_script_path)
            if caption_cwd and os.path.isdir(caption_cwd):
                shutil.rmtree(caption_cwd, ignore_errors=True)
        if proc.returncode != 0:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise RenderError(f"audio-only render failed: {err[-500:]}")

        probe = self.probe_media(tmp_path)
        os.replace(tmp_path, out_path)
        return {
            "output_path": out_path,
            "duration": probe["duration"],
            "has_audio": probe["has_audio"],
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # 预览帧抽取（F32 准确预览的最小形态：单帧）
    # ------------------------------------------------------------------
    @_prepare_static_masks
    def extract_frame(self, project: Project, timeline_time: float,
                      out_png: str, size: Optional[tuple[int, int]] = None,
                      timeout: float = 30.0,
                      include_captions: bool = True,
                      cancel_event: Optional[threading.Event] = None,
                      alpha: bool = False) -> Optional[str]:
        """抽取工程在 timeline_time 时刻的预览帧（PNG）。

        与导出同语义（A05 / 指导 4.2 第一步）：
          - 时间映射：sourceStart + (t - timelineStart) 定位源时间。
          - 复用完整 _compile 图，因此多轨、变换、关键帧、调色、转场与
            变速都和导出走同一条路径，不再使用只支持单片段的简化路径。
          - include_captions=False 供 Web 预览使用：字幕由前端可编辑叠层
            绘制，避免字幕修改触发视频重新渲染，也避免字幕重复绘制。
        无覆盖片段返回 None（黑帧语义）。

        成功返回 out_png 路径；失败抛 RenderError。
        """
        # 先保留旧的「无覆盖片段返回 None」契约。完整 _compile 图会把时间线
        # 当成一条连续输出流；如果不在这里拦截，时间线末尾没有素材时 ffmpeg
        # 仍可能吐出最后一帧/黑帧，调用方就无法区分「没有画面」和「画面本身是黑的」。
        source_seq = project.sequence
        t_rat = Rational.from_float(timeline_time)
        multicam = getattr(source_seq, "multicam", None)
        from cutvoke.domain.multicam import camera_at
        active_track_id = camera_at(multicam, timeline_time)

        def covers(track: Any) -> bool:
            if getattr(track, "kind", "video") not in {"video", "text"}:
                return False
            if not getattr(track, "visible", True):
                return False
            if (track.kind == "video" and active_track_id and
                    track.id in multicam.get("trackIds", [active_track_id]) and track.id != active_track_id):
                return False
            return any(
                not getattr(clip, "hidden", False)
                and clip.timeline_start <= t_rat < clip.timeline_end
                for clip in track.clips
            )

        if not any(covers(track) for track in source_seq.tracks):
            return None

        # Frame previews do not run the full export preflight because an
        # uncovered playhead is a valid black frame. Still report a missing
        # active source directly: _compile probes video streams and would
        # otherwise skip a missing file, then report that the timeline has no
        # visible video at all.
        for track in source_seq.tracks:
            if track.kind != "video" or not covers(track):
                continue
            for clip in track.clips:
                if (clip.hidden or clip.nested is not None or
                        not clip.timeline_start <= t_rat < clip.timeline_end):
                    continue
                source_path = clip.asset_ref.source_path
                if not source_path:
                    raise RenderError(f"clip {clip.id} has empty source_path")
                if not os.path.exists(source_path):
                    raise RenderError(
                        f"source file missing for clip {clip.id}: {source_path}")

        preview_project = copy.deepcopy(project)
        if not include_captions:
            # 多序列模型中 sequence 与 sequences 中的活动序列是两个引用视图，
            # 两边都清空，避免 to_dict / _compile 又把字幕带回来。
            for sequence in preview_project.sequences:
                sequence.captions = []
            preview_project.sequence.captions = []
        seq = preview_project.sequence
        for track in seq.tracks:
            track.muted=True
            if track.kind=='audio':
                track.visible=False
        frame_time, frame_duration = self._static_frame_time(seq, timeline_time)
        graph, input_paths, _expected, has_audio, _warnings, caption_cwd = (
            self._compile(seq, (0, 0), alpha=alpha, render_window=(frame_time,
                frame_time + 2 * frame_duration)))
        frame_offset = frame_time - self._render_ctx.compile_origin
        first_frame=round(frame_offset*float(seq.fps.to_fraction()))
        graph += f";[outv]trim=start_frame={first_frame},setpts=PTS-STARTPTS[previewbase]"
        video_label = "[previewbase]"
        if alpha:
            graph += ";[previewbase]format=rgba[previewalpha]"
            video_label = "[previewalpha]"
        filter_script_path = None
        try:
            if size is not None:
                graph = (graph + f";{video_label}scale={size[0]}:{size[1]}:"
                         f"force_original_aspect_ratio=decrease[previewv]")
                video_label = "[previewv]"
            cmd: list[str] = [self.ffmpeg, "-y", "-nostdin",
                              "-filter_complex_threads", str(min(8,max(2,(os.cpu_count() or 2)//2))),
                              "-filter_threads", "2",
                              *self._gpu_arguments(graph)]
            for src in input_paths:
                cmd += ["-threads", "2"]
                if _is_image_src(src):
                    fps_rat = seq.fps.to_fraction()
                    cmd += ["-loop", "1", "-framerate",
                            f"{fps_rat.numerator}/{fps_rat.denominator}",
                            "-i", src]
                else:
                    seek = getattr(self._render_ctx, 'input_seeks', {}).get(os.path.abspath(src))
                    if seek:
                        cmd += ['-ss', str(seek)]
                    cmd += ["-i", src]
            if has_audio:
                graph += ";[outa]anullsink"
            filter_args, filter_script_path = _filter_graph_arguments(self.ffmpeg, graph, caption_cwd)
            cmd += filter_args
            cmd += ["-map", video_label,
                    "-frames:v", "1", "-threads", "2",
                    "-f", "image2", out_png]
            process=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                cwd=caption_cwd if caption_cwd else None)
            deadline=time.monotonic()+timeout
            try:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise RenderCancelled("preview frame superseded")
                    remaining=deadline-time.monotonic()
                    if remaining<=0:
                        raise subprocess.TimeoutExpired(cmd,timeout)
                    try:
                        stdout,stderr=process.communicate(timeout=min(.05,remaining))
                        r=subprocess.CompletedProcess(cmd,process.returncode,
                            stdout.decode('utf-8','replace'),stderr.decode('utf-8','replace'))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=10)
        except subprocess.TimeoutExpired as e:
            raise RenderError(f"preview frame timed out: {e}") from e
        finally:
            if filter_script_path:
                with contextlib.suppress(OSError):
                    os.remove(filter_script_path)
            # 清理字幕 ASS 临时目录（无论成功失败）
            if caption_cwd and os.path.isdir(caption_cwd):
                shutil.rmtree(caption_cwd, ignore_errors=True)
        if r.returncode != 0 or not os.path.isfile(out_png):
            raise RenderError(f"preview frame failed: {r.stderr[-400:]}")
        return out_png

    @staticmethod
    def _static_frame_time(seq: Sequence, timeline_time: float) -> tuple[float, float]:
        """Select the sequence frame containing the playhead, including EOF."""
        fps = seq.fps.to_fraction()
        frame_duration = float(1 / fps)
        frame_index = max(0, math.floor(timeline_time * float(fps) + 1e-7))
        end = max((float(c.timeline_end.to_fraction()) for t in seq.tracks
                   if t.visible and t.kind in {"video", "audio", "text"}
                   for c in t.clips if not c.hidden), default=0)
        if end > 0:
            last_frame = max(0, math.ceil(end * float(fps) - 1e-7) - 1)
            frame_index = min(frame_index, last_frame)
        return float(frame_index / fps), frame_duration

    @_prepare_static_masks
    def extract_still(self, project: Project, timeline_time: float,
                      out_path: str, alpha: bool = False,
                      timeout: float = 60.0) -> dict[str, Any]:
        """V03 静帧/封面导出：把时间线某时刻导出为图片文件（PNG/JPG…）。

        与 extract_frame 的关键区别：
          1. **恒定走 _compile 全图**（含效果/转场/字幕），因此「导出图 == 成片该帧」；
          2. `alpha=True` 时画布底为全透明（复用 V05 的 alpha 编译模式）并在
             输出前转 rgba，PNG 才能真正保留透明——抠像/留白区域导出为透明像素，
             而不是黑块。

        返回 {output_path, width, height, has_alpha, pix_fmt}；
        失败抛 RenderError，且不留下半成品文件。
        """
        seq = project.sequence
        frame_time, frame_duration = self._static_frame_time(seq, timeline_time)
        graph, input_paths, _expected, _has_audio, _warnings, caption_cwd = (
            self._compile(seq, (0, 0), alpha=alpha, render_window=(frame_time,
                frame_time + 2 * frame_duration)))
        frame_offset = frame_time - self._render_ctx.compile_origin
        graph += f";[outv]trim=start={frame_offset:.9f},setpts=PTS-STARTPTS[outv]"
        tmp_path = out_path + ".tmp" + (os.path.splitext(out_path)[1] or ".png")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        filter_script_path = None
        try:
            if alpha:
                # 静帧容器（PNG）原生支持 alpha；显式转 rgba 避免编码器选 rgb24
                graph = graph + ";[outv]format=rgba[outv]"
            else:
                # 明确 rgb24：否则 PNG 默认落在 rgba，会让 API 消费者误以为带透明
                graph = graph + ";[outv]format=rgb24[outv]"
            cmd: list[str] = [self.ffmpeg, "-y", "-nostdin", *self._gpu_arguments(graph)]
            for src in input_paths:
                if _is_image_src(src):
                    fps_rat = seq.fps.to_fraction()
                    cmd += ["-loop", "1", "-framerate",
                            f"{fps_rat.numerator}/{fps_rat.denominator}",
                            "-i", src]
                else:
                    cmd += ["-i", src]
            if _has_audio:
                graph += ";[outa]anullsink"
            filter_args, filter_script_path = _filter_graph_arguments(self.ffmpeg, graph, caption_cwd)
            cmd += filter_args
            cmd += ["-map", "[outv]",
                    "-frames:v", "1",
                    "-f", "image2", tmp_path]
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
                cwd=caption_cwd if caption_cwd else None)
            if r.returncode != 0 or not os.path.isfile(tmp_path):
                raise RenderError(f"still export failed: {(r.stderr or '')[-400:]}")
            probe = self.probe_media(tmp_path)
            pix_fmt = probe.get("pix_fmt")
            if alpha and not _pix_fmt_has_alpha(pix_fmt):
                raise RenderError(
                    f"transparent still export produced an image without "
                    f"alpha (pix_fmt={pix_fmt!r})")
            os.replace(tmp_path, out_path)
        except BaseException:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise
        finally:
            if filter_script_path:
                with contextlib.suppress(OSError):
                    os.remove(filter_script_path)
            if caption_cwd and os.path.isdir(caption_cwd):
                shutil.rmtree(caption_cwd, ignore_errors=True)

        return {
            "output_path": out_path,
            "width": probe.get("width") or seq.width,
            "height": probe.get("height") or seq.height,
            "has_alpha": _pix_fmt_has_alpha(pix_fmt),
            "pix_fmt": pix_fmt,
        }

    # ------------------------------------------------------------------
    # 预检
    # ------------------------------------------------------------------
    def _preflight(self, project: Project, out_path: str, overwrite: bool,
                   allow_unknown_effects: bool = False,
                   output_range: Optional[tuple[float, float]] = None) -> list[str]:
        """导出前预检。返回非阻断性 warning 列表（阻断性问题直接抛 RenderError）。"""
        seq = project.sequence
        video_tracks = [t for t in seq.tracks if t.kind == "video"]
        has_visible_video = any(t.visible and any(not c.hidden for c in t.clips)
                                for t in video_tracks)
        has_audible_audio = any(t.kind == "audio" and t.visible and not t.muted and
                                any(not c.hidden for c in t.clips) for t in seq.tracks)
        has_text = any(t.visible and t.kind == "text" and any(not c.hidden for c in t.clips)
                       for t in seq.tracks)
        if not has_visible_video and not has_audible_audio and not has_text:
            raise RenderError(
                "EMPTY_TIMELINE: no visible video or audible audio clips to render")

        from cutvoke.domain.project_health import project_preflight
        dependency_report = project_preflight(project, output_range=output_range)
        required_clip_ids = {reference["clipId"] for item in dependency_report["files"]
                             for reference in item["references"] if reference["required"]}
        for error in dependency_report["errors"]:
            if error.get("resourceKind") == "media":
                required_clip_ids.update(error.get("clipIds", []))

        # 所有视频 clip 必须指向真实存在的源文件（M0 不做代理回退）。
        # 复合片段（clip.nested 非空）是容器：内容在内部子序列，源在展开
        # 后的内层片段上（渲染收集时由 _flatten_compound 展开），此处跳过。
        for t in video_tracks:
            for c in t.clips:
                if c.hidden or c.id not in required_clip_ids:
                    continue
                if c.nested is not None:
                    continue  # 复合片段：容器，源校验交给内部片段
                src = c.asset_ref.source_path
                if not src:
                    raise RenderError(f"clip {c.id} has empty source_path")
                if not os.path.exists(src):
                    raise RenderError(f"source file missing for clip {c.id}: {src}")

        # 音频轨片段同样要求源文件真实存在（避免 ffmpeg 后期才失败）
        for t in seq.tracks:
            if t.kind != "audio":
                continue
            for c in t.clips:
                if c.hidden or c.id not in required_clip_ids:
                    continue
                src = c.asset_ref.source_path
                if not src:
                    raise RenderError(f"audio clip {c.id} has empty source_path")
                if not os.path.exists(src):
                    raise RenderError(
                        f"audio source file missing for clip {c.id}: {src}")

        # Include nested media, empty/non-file sources and custom LUT files.
        # This guard belongs to real rendering, so MCP/queue exports receive
        # the same missing-dependency rejection as the Web preflight. Keep
        # _compile injectable for graph tests with mocked media probes.
        dependency_errors = [error for error in dependency_report["errors"]
                             if error["code"] in {"MISSING_SOURCE_PATH", "MISSING_FILE",
                                                  "EMPTY_FILE", "NOT_A_FILE"}]
        if dependency_errors:
            error = dependency_errors[0]
            raise RenderError(f"{error['code']}: {error['message']} "
                              f"{error.get('path', '')}; clips={error.get('clipIds', [])}")

        # 目标目录必须可写；覆盖需显式授权
        out_dir = os.path.dirname(os.path.abspath(out_path))
        if not os.path.isdir(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except OSError as e:
                raise RenderError(f"cannot create output dir {out_dir}: {e}")
        if os.path.exists(out_path) and not overwrite:
            raise RenderError(
                f"output already exists (set overwrite=True to replace): {out_path}")

        # ---- 效果可用性（AC19）----
        # 工程引用了未注册的效果时严格拒绝导出，避免"看起来成功但效果被静默丢弃"；
        # 只有显式降级（allow_unknown_effects=True）才继续，并留下明确 warning 记录。
        warnings: list[str] = []
        used = collect_used_effect_ids(project)
        unknown = self.effects.unknown_ids(used)
        if unknown:
            known = ", ".join(sorted(self.effects.ids()))
            msg = (f"UNKNOWN_EFFECT: 工程引用了未注册的效果 {', '.join(unknown)}"
                   f"（当前可用: {known}）")
            if allow_unknown_effects:
                warnings.append(msg + " —— 已按降级模式导出，这些效果不会生效")
            else:
                raise RenderError(
                    msg + "；请安装对应效果，或显式传 allow_unknown_effects=True 降级导出")

        # Person FX works on the transparent layer produced by the cutout job.
        # Reject opaque sources early so preview and export cannot silently look unchanged.
        probed: dict[str, dict[str, Any]] = {}
        for track in video_tracks:
            for clip in track.clips:
                if clip.hidden or clip.nested is not None or clip.id not in required_clip_ids:
                    continue
                for effect in clip.effects:
                    spec = self.effects.find(str(effect.get("effectId", "")))
                    if not spec or "person-cutout-alpha" not in spec.dependencies:
                        continue
                    src = clip.asset_ref.source_path
                    if src not in probed:
                        probed[src] = self.probe_media(src)
                    if not _pix_fmt_has_alpha(probed[src].get("pix_fmt")):
                        raise RenderError(
                            f"PERSON_FX_REQUIRES_ALPHA: {spec.label()} 需要人物抠像生成的透明视频；"
                            "请先抠像并在时间线上选中带 Alpha 的人物层")

        return warnings

    # ------------------------------------------------------------------
    # 编译：工程快照 -> ffmpeg filter_complex + 输入列表
    # ------------------------------------------------------------------
    def _sequence_duration(self, seq: Sequence) -> float:
        """Find the real timeline end without probing every earlier source.

        A visual clip's declared duration remains authoritative, as in the full
        compiler. Audio tails stop at the source's actual available duration.
        Inspect latest candidates first; earlier audio cannot extend a later
        known picture/audio end and needs no probe for this duration query.
        """
        visual = []
        audible = []
        multicam = getattr(seq, "multicam", None)
        active = multicam.get("activeTrackId") if isinstance(multicam, dict) else None
        info: dict[str, dict] = {}
        for track in seq.tracks:
            if not track.visible or track.kind not in {"video", "audio"}:
                continue
            clips, _warnings = _flatten_compound([c for c in track.clips if not c.hidden])
            if track.kind == "video":
                visual.extend(clips)
            if not track.muted:
                audible.extend(clips)
        def media(clip: Clip) -> dict:
            src = clip.asset_ref.source_path
            if src not in info:
                try:
                    info[src] = self.probe_media(src)
                except RenderError:
                    # Missing media outside an authorized range is a warning.
                    # Keep its declared video extent for range bounds without
                    # requiring that unrelated source to decode successfully.
                    info[src] = {"has_video":True, "has_audio":False, "duration":0}
            return info[src]
        end = max((float(c.timeline_end.to_fraction()) for t in seq.tracks
                   if t.visible and t.kind == 'text' for c in t.clips if not c.hidden), default=0.0)
        for clip in sorted(visual, key=lambda c:c.timeline_end, reverse=True):
            if media(clip).get("has_video"):
                end = max(end, float(clip.timeline_end.to_fraction()))
                break
        for clip in sorted(audible, key=lambda c:c.timeline_end, reverse=True):
            if float(clip.timeline_end.to_fraction()) <= end:
                continue
            source = media(clip)
            if not source.get("has_audio"):
                continue
            available = max(0.0, float(source.get("duration") or 0) -
                            float(clip.source_start.to_fraction()))
            if clip.speed_curve is not None:
                available = clip.speed_curve.timeline_at_source(available)
            else:
                available /= abs(float(clip.speed.to_fraction())) or 1.0
            duration = min(float(clip.duration.to_fraction()), available)
            end = max(end, float(clip.timeline_start.to_fraction()) + duration)
        return end

    def _compile(self, seq: Sequence,
                 overlay_position: tuple[int, int],
                 alpha: bool = False,
                 render_modes: Optional[list[dict[str, Any]]] = None,
                 render_window: Optional[tuple[float, float]] = None,
                 frame_aligned_origin: bool = False
                 ) -> tuple[str, list[str], float, bool, list[str], Optional[str]]:
        warnings: list[str] = []
        # 渲染期标志（见 __init__ 注释）：供深层画布构建读取，避免层层传参漏传
        self._render_ctx.alpha = alpha
        self._render_ctx.sequence_fps = seq.fps.to_fraction()
        self._render_ctx.canvas_height = seq.height
        self._render_ctx.canvas_width = seq.width
        self._render_ctx.reframe = getattr(seq, 'reframe', 'stretch')
        self._render_ctx.window = render_window
        self._render_ctx.input_seeks = {}
        canvas_color = getattr(seq, "background_color", "#000000")
        if (not isinstance(canvas_color, str) or len(canvas_color) != 7
                or canvas_color[0] != "#"
                or any(char not in "0123456789abcdefABCDEF" for char in canvas_color[1:])):
            raise RenderError("canvas background must be an opaque #RRGGBB value")
        canvas_fill = "0x" + canvas_color[1:]
        def in_window(clip: Clip) -> bool:
            return (render_window is None or
                    (float(clip.timeline_start.to_fraction()) < render_window[1]
                     and float(clip.timeline_end.to_fraction()) > render_window[0]))
        original_video_tracks = sorted(
            (t for t in seq.tracks if t.kind == "video" and t.visible
             and any(not c.hidden for c in t.clips)),
            key=lambda t:t.role == "sticker")
        original_track_positions = {t.id:i for i,t in enumerate(original_video_tracks)}
        original_canvas = {t.id:any(self._clip_needs_canvas(c) for c in t.clips if not c.hidden)
                           for t in original_video_tracks}

        # 媒体流探测缓存：同一源文件只 probe 一次。除了音轨提取，也用于
        # 容错历史工程中“纯音频被误放进视频轨”的脏数据；这种片段保留声音，
        # 但不再生成不存在的 [i:v] 引用拖垮整个预览/导出。
        media_cache: dict[str, dict[str, Any]] = {}

        def media_info(src: str) -> dict[str, Any]:
            if src in media_cache:
                return media_cache[src]
            try:
                info = self.probe_media(src)
            except Exception:  # noqa: BLE001 — 预检/后续编译负责给出具体源文件错误
                info = {"duration": 0.0, "has_video": False, "has_audio": False}
            media_cache[src] = info
            return info

        def has_audio(src: str) -> bool:
            return bool(media_info(src).get("has_audio"))

        def has_video(src: str) -> bool:
            return bool(media_info(src).get("has_video"))

        def video_duration(src: str) -> float:
            info = media_info(src)
            return float(info.get("video_duration") or info.get("duration") or 0.0)

        # 源素材时长缓存（2026-09-21 修）：音轨的"期望时长"必须按**实际可渲染
        # 时长**算，而不是片段在时间线上的声明时长。反例：模板把 BGM 片段声明成
        # 覆盖 0→16.0s，但内置 ambient_pad.wav 只有 12.0s——atrim 只能取到 12.0s，
        # 实际音轨就是 12.0s。若 expected 仍按 16.0 算，成片校验会拿一个永远达不
        # 到的数字去比，导出被判失败（且用户看到的错误信息毫无指向性）。
        src_dur_cache: dict[str, float] = {}

        def source_duration(src: str) -> float:
            if src in src_dur_cache:
                return src_dur_cache[src]
            d = float(media_info(src).get("duration") or 0.0)
            src_dur_cache[src] = d
            return d

        def effective_audio_dur(seg: tuple) -> float:
            """音频段实际可渲染时长 = min(声明时长, 源可用时长 / |speed|)。"""
            src, ss, tl_dur, speed = seg[0], seg[1], seg[2], seg[3]
            declared = float(tl_dur.to_fraction())
            total = source_duration(src)
            if total <= 0:
                return declared
            curve = seg[11]
            if curve is not None:
                avail = curve.timeline_at_source(max(0.0, total - float(ss)))
            else:
                sp = abs(float(speed.to_fraction())) or 1.0
                avail = max(0.0, total - float(ss)) / sp
            return min(declared, avail)

        # 音频轨是否存在可见片段（用于「无可用音轨」warning 判定）
        audio_tracks = [t for t in seq.tracks if t.kind == "audio"]
        audio_track_present = any(
            any(not c.hidden for c in t.clips) for t in audio_tracks)

        # 统一输入索引：源文件只进 -i 一次，视频用 [i:v]、音频用 [i:a] 复用
        src_index: dict[str, int] = {}
        input_paths: list[str] = []

        def get_input(src: str) -> int:
            if src in src_index:
                return src_index[src]
            i = len(input_paths)
            # 绝对化：_run_ffmpeg 会切 cwd 到字幕临时目录，相对源路径会失效
            input_paths.append(os.path.abspath(src))
            src_index[src] = i
            return i

        # ---- 视频轨收集（同 M0：按 timeline_start 排序，尊重时间线顺序）----
        track_clips: list[tuple[Track, list[Clip]]] = []
        # 多机位（J08）：seq.multicam 存在时，只渲染 activeTrackId 那一轨
        # （其余机位轨是备选画面，不参与成片）——输出即「剪辑师当前切换机位」。
        multicam = getattr(seq, "multicam", None)
        multicam_active = (multicam or {}).get("activeTrackId") if isinstance(multicam, dict) else None
        for t in seq.tracks:
            if t.kind != "video" or not t.visible:
                continue
            clips = sorted((c for c in t.clips if not c.hidden),
                           key=lambda c: c.timeline_start)
            # 复合片段（J08）：把 nested 子序列片段提升到外层时轴
            clips, flat_warnings = _flatten_compound(clips)
            warnings.extend(flat_warnings)
            if render_window is not None:
                selected = {i for i,c in enumerate(clips) if in_window(c)}
                # An incoming transition mixes the outgoing picture after the
                # cut. Keep its true original predecessor, even when that clip
                # itself ends before the requested window begins.
                for i in tuple(selected):
                    if i == 0:
                        continue
                    outgoing, incoming = clips[i - 1], clips[i]
                    transition = find_transition(incoming, self.effects)
                    if transition is None or outgoing.timeline_end != incoming.timeline_start:
                        continue
                    d = min(_duration_to_float(transition.get("params", {}).get("duration", 0)),
                            float(outgoing.duration.to_fraction()) / 2,
                            float(incoming.duration.to_fraction()) / 2)
                    if float(incoming.timeline_start.to_fraction()) + d > render_window[0]:
                        selected.add(i - 1)
                clips = [c for i,c in enumerate(clips) if i in selected]
            renderable: list[Clip] = []
            for clip in clips:
                src = clip.asset_ref.source_path
                if has_video(src):
                    renderable.append(clip)
                    continue
                if has_audio(src):
                    warnings.append(
                        f"video track {t.id!r} clip {clip.id!r} has no video "
                        "stream; picture skipped and its audio kept")
            if renderable:
                track_clips.append((t, renderable))

        # Sticker lanes are visual overlays. Keep them above ordinary video
        # regardless of where the lane was inserted in the editable track list.
        track_clips.sort(key=lambda item: item[0].role == "sticker")
        # Recent short clips do not need hundreds of seconds of generated black
        # frames before a late preview. Keep clip-local source/effect clocks,
        # but give this compiled timeline a nearby presentation origin.
        origin = min(render_window[0], min((float(c.timeline_start.to_fraction())
                     for _track, clips in track_clips for c in clips),
                     default=render_window[0])) if render_window else 0.0
        bounded_origin=getattr(self._render_ctx,'bounded_origin',None)
        if bounded_origin is not None:
            origin=bounded_origin
        if render_window is not None and frame_aligned_origin:
            # Cropping at a clip's exact second can shift a fractional-rate
            # frame lattice (2s is not a frame boundary at 30000/1001). Keep
            # the nearby origin on the sequence grid so trims retain all frames.
            rate = float(seq.fps.to_fraction())
            origin = math.floor(origin * rate + 1e-7) / rate
        self._render_ctx.compile_origin = origin
        audio_sequence=getattr(self._render_ctx,'export_audio_sequence',None)
        audio_origin=origin
        if audio_sequence is not None and bounded_origin is not None:
            active_ids={c.id for _track, clips in track_clips for c in clips}
            audio_origin=min(render_window[0], min((float(c.timeline_start.to_fraction())
                for t in audio_sequence.tracks for c in t.clips if c.id in active_ids),
                default=render_window[0]))
        self._render_ctx.audio_origin_delta=origin-audio_origin

        parts: list[str] = []

        # 视频分段 + 每轨编译（F14 变换 / F18 调色 / F20 转场 在此生效）
        vseg = [0]  # 用列表包一层，便于内层闭包自增

        track_streams: list[tuple[str, float]] = []
        mode_by_track_id = {
            str(mode.get("trackId")): mode for mode in (render_modes or [])
            if isinstance(mode, dict) and mode.get("trackId") is not None
        }
        for n, (track, clips) in enumerate(track_clips):
            mode = mode_by_track_id.get(track.id, {})
            original_position = original_track_positions[track.id] if render_window is not None else n
            transparent_layer = alpha or bool(mode.get(
                "overlay", original_position > 0 or track.role == "sticker"))
            self._render_ctx.alpha = transparent_layer
            try:
                label, dur = self._build_video_track(
                    clips, seq, n, get_input, parts, vseg, overlay_position,
                    transparent_layer=transparent_layer,
                    video_duration=video_duration,
                    force_canvas=bool(mode.get("canvas", False) or
                        (render_window is not None and original_canvas[track.id])))
            finally:
                self._render_ctx.alpha = alpha
            track_streams.append((label, dur))

        if not track_streams:
            # Audio-only timelines still need a video canvas for the editor's
            # synchronized preview and MP4 export. The real audio mix below
            # remains authoritative; only the configured canvas fill is shown.
            audio_end = max((float(clip.timeline_end.to_fraction())
                             for track in seq.tracks if track.visible and
                             (track.kind == "text" or (track.kind == "audio" and not track.muted))
                             for clip in track.clips if not clip.hidden), default=0.0)
            if render_window is not None:
                audio_end = render_window[1] - origin
            if audio_end <= 0:
                raise RenderError("no visible video or audible audio clips")
            frame_rate = seq.fps.to_fraction()
            fps_value = f"{frame_rate.numerator}/{frame_rate.denominator}"
            audio_only_color = "black@0.0" if alpha else canvas_fill
            audio_only_format = ",format=yuva420p" if alpha else ""
            parts.append(f"color=c={audio_only_color}:s={seq.width}x{seq.height}:"
                         f"r={fps_value}:d={audio_end:.6f}"
                         f"{audio_only_format}[audioonlyv]")
            track_streams.append(("audioonlyv", audio_end))

        track_labels = [lab for lab, _ in track_streams]
        track_durs = [dur for _, dur in track_streams]

        # A full-duration base preserves absolute timeline duration without
        # swapping layer order when an upper sticker lane runs longer.
        x, y = int(overlay_position[0]), int(overlay_position[1])
        fps = seq.fps.to_fraction()
        fps_expr = f"{fps.numerator}/{fps.denominator}"
        title_end = max((float(c.timeline_end.to_fraction()) for t in seq.tracks
                         if t.kind == "text" and t.visible for c in t.clips if not c.hidden), default=0)
        canvas_dur = max(max(track_durs), render_window[1] - origin if render_window else title_end)
        base_color = "black@0.0" if alpha else canvas_fill
        base_format = ",format=yuva420p" if alpha else ""
        parts.append(
            f"color=c={base_color}:s={seq.width}x{seq.height}:"
            f"r={fps_expr}:d={canvas_dur:.6f}{base_format}[trackbase]")
        current = "trackbase"
        gpu=getattr(self._render_ctx,'gpu',None)
        if (gpu is not None and not multicam and getattr(self._render_ctx,'bounded_origin',None) is not None
                and len(track_labels)>1 and (x,y)==(0,0)
                and all(c.id in getattr(self._render_ctx,'clip_clocks',{})
                        for _track, clips in track_clips for c in clips)):
            padded=[]
            for n,(label,duration) in enumerate(track_streams):
                if duration<canvas_dur-1e-7:
                    full=f'gpulane{n}'
                    frames=max(1,math.ceil((canvas_dur-duration)*float(fps)-1e-7))
                    parts.append(f'[{label}]format=rgba,tpad=stop_mode=add:stop={frames}:'
                                 f'color=black@0,trim=end_frame={round(canvas_dur*float(fps))}[{full}]')
                    label=full
                padded.append(label)
            current=gpu.composite(['trackbase',*padded],parts,'ovout')
        else:
            for n, t_label in enumerate(track_labels):
                out_label = "ovout" if n == len(track_labels) - 1 else f"ov{n}"
                enable = ''
                if isinstance(multicam, dict) and n < len(track_clips):
                    from cutvoke.domain.multicam import visibility_expression
                    expression = visibility_expression(multicam, track_clips[n][0].id, origin)
                    if expression is not None: enable = f":enable='{expression}'"
                parts.append(
                    f"[{current}][{t_label}]overlay=shortest=0:eof_action=pass:"
                    f"format=auto:x={x}:y={y}{enable}[{out_label}]")
                current = out_label
        # 统一缩放到序列画布并规范帧率（输出符合 sequence 设置）
        parts.append(
            f"[{current}]scale={seq.width}:{seq.height},"
            f"null[outv]")

        # ---- 字幕叠加（F22/F24/M1 基础字幕进入成片）----
        # 与预览 extract_frame 复用同一段逻辑（prepare_caption_render 生成 ASS +
        # 字体副本临时目录；滤镜用相对文件名 + cwd 规避 Windows 绝对路径冒号冲突）。
        # 缺字体时 prepare_caption_render 抛 CaptionFontError（绝不静默丢字幕）。
        caption_cwd: Optional[str] = None
        # J04 文字图层轨：把 text 轨上的 cutvoke.text 片段转成 Caption，
        # 与 sequence.captions 合并进 ASS 字幕轨道（文字叠加到画面）。
        merge_captions = [c for c in seq.captions if render_window is None or
                          (float(c.start.to_fraction()) < render_window[1] and
                           float(c.end.to_fraction()) > render_window[0])]
        title_motion = {}
        from cutvoke.rendering.title_motion import needs_title_motion
        for _t in seq.tracks:
            if _t.kind != "text" or not _t.visible:
                continue
            for _c in sorted(_t.clips, key=lambda c: c.timeline_start):
                if _c.hidden:
                    continue
                if not in_window(_c):
                    continue
                _p = _text_clip_params(_c)
                if _p is None:
                    continue
                try:
                    _cap = Caption(
                        id=f"text_{_c.id}",
                        text=_p.get("content", ""),
                        start=_c.timeline_start,
                        end=_c.timeline_end,
                        fontSize=float(_p.get("fontSize", 48.0)),
                        fontFamily=str(_p.get("fontFamily", "Noto Sans SC")),
                        lineSpacing=float(_p.get("lineSpacing", 1.0)),
                        color=str(_p.get("color", "#ffffff")),
                        strokeColor=str(_p.get("strokeColor", "#000000")),
                        strokeWidth=float(_p.get("strokeWidth", 2.0)),
                        background=str(_p.get("background", "")),
                        panelWidth=float(_p.get("panelWidth", 0.0)),
                        panelHeight=float(_p.get("panelHeight", 0.0)),
                        align=str(_p.get("align", "center")),
                        bold=bool(_p.get("bold", False)),
                        x=float(_p.get("x", 0.5)),
                        y=float(_p.get("y", 0.5)),
                        scale=float(_p.get("scale", 1.0)),
                        rotation=float(_p.get("rotation", 0.0)),
                        shadow=int(_p.get("shadow", 1)),
                        animIn=int(_p.get("animIn", 0)),
                        animOut=int(_p.get("animOut", 0)),
                        animInStyle=str(_p.get("animInStyle", "fade")),
                        animOutStyle=str(_p.get("animOutStyle", "fade")),
                        animLoopStyle=str(_p.get("animLoopStyle", "none")),
                        animLoopMs=int(_p.get("animLoopMs", 1000)),
                    )
                except (TypeError, ValueError):
                    continue  # 参数异常：跳过该文字片段，不阻断渲染
                if _cap.text and _cap.end > _cap.start:
                    merge_captions.append(_cap)
                    if needs_title_motion(_p, _c.keyframes):
                        title_motion[_cap.id] = {"params": _p, "keyframes": _c.keyframes,
                                                 "fps": seq.fps, "window": render_window}
        if merge_captions:
            caption_cwd = prepare_caption_render(
                merge_captions, seq.width, seq.height,
                css_caption_ids={caption.id for caption in seq.captions},
                title_motion=title_motion)
            # ASS animation/fades retain their original absolute timeline clock.
            # Rebase only around libass; geometry/source clocks remain local.
            ticks=round(origin*float(fps))
            before = f"settb=expr={fps.denominator}/{fps.numerator},setpts=PTS+{ticks}," if origin else ""
            after = f",setpts=PTS-{ticks}" if origin else ""
            # libass otherwise leaves a transparent canvas's alpha untouched:
            # visible glyph RGB would export with zero alpha and disappear in an NLE.
            ass_alpha = ':alpha=1' if alpha else ''
            alpha_format = 'format=rgba,' if alpha else ''
            parts.append(f"[outv]{before}{alpha_format}ass=cutvoke_captions.ass:fontsdir=.{ass_alpha}{after}[outv]")

        # 主轴时长：取所有视频轨的绝对结束时间最大值（A04：偏移轨不能截断总长）
        video_expected = canvas_dur

        # ---- 音频轨收集（T16 混音）----
        # 每条「音轨」= 一组按绝对时间放置的音频分段：
        #   * 音频轨（kind=='audio'，未静音、可见）的可见片段
        #   * 视频轨的可见片段，若其源文件本身含音轨则一并提取（每条视频轨成一轨道）
        audio_groups: list[list[tuple[str, float, float]]] = []
        from cutvoke.rendering.audio_mixer import audible_track_ids, finish_mix
        mixer_sequence = audio_sequence or seq
        audible_ids = audible_track_ids(mixer_sequence)
        audio_group_track_ids = []

        def _collect_audio_segs(clips: list[Clip]) -> list[tuple]:
            segs: list[tuple] = []
            for c in sorted(clips, key=lambda c: c.timeline_start):
                if not in_window(c):
                    continue
                src = c.asset_ref.source_path
                if not has_audio(src):
                    continue
                ss = float(c.source_start.to_fraction())
                # 该片段上的音频特效（如 loudnorm），在逐段音频链应用
                afx = self._enabled_audio_fx(c)
                segs.append((src, ss, c.duration, c.speed, c.preserve_pitch, c.volume,
                             c.fade_in, c.fade_out, c.pitch, afx,
                             c.timeline_start, c.speed_curve, c.id, c.keyframes.get("volume", []),
                             getattr(c, "audio_fade_offset", Rational.of(0)),
                             getattr(c, "audio_fade_duration", None)))
            return segs

        # 音频轨（静音轨不产生音轨）
        for t in audio_tracks:
            if not t.visible or t.muted or t.id not in audible_ids:
                continue
            segs = _collect_audio_segs([c for c in t.clips if not c.hidden])
            if segs:
                audio_groups.append(segs)
                audio_group_track_ids.append(t.id)

        # 视频轨：默认提取嵌入音轨
        for t in (audio_sequence or seq).tracks:
            if t.kind != "video" or not t.visible or t.muted or t.id not in audible_ids:
                continue
            if (isinstance(multicam, dict) and t.id in multicam.get('trackIds', [])
                    and t.id != multicam.get('audioTrackId', multicam.get('activeTrackId'))):
                continue
            segs = _collect_audio_segs([c for c in t.clips if not c.hidden])
            if segs:
                audio_groups.append(segs)
                audio_group_track_ids.append(t.id)

        has_audio = bool(audio_groups)
        audio_expected = 0.0
        if has_audio:
            sr = int(seq.audio_sample_rate)
            substreams: list[str] = []
            audio_group_labels = []
            audio_labels_by_clip = {}
            duck_requests = []
            aseg = 0
            for g, segs in enumerate(audio_groups):
                labels: list[str] = []
                for (src, ss, tl_dur, speed, preserve_pitch, vol, fade_in, fade_out, pitch, afx,
                     timeline_start, curve, clip_id, volume_keyframes, fade_offset, fade_duration) in segs:
                    ai = get_input(src)
                    seg = f"aseg{aseg}"
                    aseg += 1
                    # 变速：源素材覆盖时长 = 时间线时长 * |speed|（有理数精确）
                    src_dur = (float(curve.source_duration.to_fraction()) if curve is not None
                               else float((tl_dur * _abs_rat(speed)).to_fraction()))
                    speed_f = float(speed.to_fraction())
                    if curve is not None:
                        if not preserve_pitch:
                            raise RenderError("curve speed requires preservePitch=true")
                        if not _has_rubberband(self.ffmpeg):
                            raise RenderError(
                                "curve speed requires an FFmpeg build with the rubberband filter")
                        initial, commands = curve.audio_commands(f"ramp{aseg}")
                        speed_filters = ""
                        if commands.strip():
                            if caption_cwd is None:
                                caption_cwd = tempfile.mkdtemp(prefix="cutvoke_render_")
                            command_file = f"curve{aseg}.cmd"
                            with open(os.path.join(caption_cwd, command_file), "w",
                                      encoding="utf-8") as script:
                                script.write(commands)
                            speed_filters += f",asendcmd=f={command_file}"
                        speed_filters += f",rubberband@ramp{aseg}=tempo={initial:.9f}"
                    else:
                        speed_filters = ",areverse,asetpts=N/SR/TB" if speed_f < 0 else ""
                    abs_f = abs(speed_f)
                    if curve is None and preserve_pitch:
                        # atempo changes duration without changing the tone.
                        speed_filters += "," + ",".join(_atempo_chain(abs_f))
                    elif curve is None:
                        # Changing the sample rate changes duration and pitch
                        # together. Resample back before mixing at project rate.
                        speed_filters += f",asetrate={round(sr * abs_f)},aresample={sr}"
                    # 音频音量 / 淡入淡出（D06 F27/F28）
                    audio_fx = ""
                    vol_f = float(vol.to_fraction())
                    if abs(vol_f - 1.0) > 1e-6:
                        audio_fx += f",volume={vol_f:.4f}"
                    if volume_keyframes:
                        from cutvoke.domain.keyframes import expression as keyframe_expression
                        volume_expression = keyframe_expression(volume_keyframes, "t", 1.)
                        audio_fx += f",volume='{volume_expression}':eval=frame"
                    fade_in_f = float(fade_in.to_fraction())
                    fade_out_f = float(fade_out.to_fraction())
                    # 输出的音频段真实时长 = 时间线时长（变速/倒放后仍等于 tl_dur）
                    seg_dur = float(tl_dur.to_fraction())
                    fade_offset_f = float(fade_offset.to_fraction())
                    if fade_offset_f or fade_duration is not None:
                        original_duration = float(fade_duration.to_fraction()) if fade_duration is not None else seg_dur
                        original_fi = min(fade_in_f, max(original_duration - .01, 0.))
                        original_fo = min(fade_out_f, max(original_duration - .01, 0.))
                        envelopes = []
                        if original_fi > 0:
                            envelopes.append(f"min(1,max(0,(t+{fade_offset_f:.9f})/{original_fi:.9f}))")
                        if original_fo > 0:
                            envelopes.append(f"min(1,max(0,({original_duration:.9f}-t-{fade_offset_f:.9f})/{original_fo:.9f}))")
                        if envelopes:
                            expression = "*".join(envelopes)
                            audio_fx += f",aeval='val(ch)*({expression})':c=same"
                        fade_in_f = fade_out_f = 0.
                    # afade 时长不能超过段时长
                    if fade_in_f > 0:
                        fi = min(fade_in_f, max(seg_dur - 0.01, 0.0))
                        if fi > 0:
                            audio_fx += f",afade=t=in:st=0:d={fi:.4f}"
                    if fade_out_f > 0:
                        fo = min(fade_out_f, max(seg_dur - 0.01, 0.0))
                        if fo > 0:
                            audio_fx += f",afade=t=out:st={max(seg_dur - fo, 0):.4f}:d={fo:.4f}"
                    # 独立变调：asetrate 改音高和时长，再用 1/pitch 的
                    # atempo 补回时长；它与变速时是否保调是两项独立设置。
                    pitch_f = float(pitch.to_fraction())
                    if abs(pitch_f - 1.0) > 1e-6:
                        audio_fx += (f",asetrate={round(sr * pitch_f)},aresample={sr},"
                                     + ",".join(_atempo_chain(1 / pitch_f)))
                    # 音频特效（J06：loudnorm 响度标准化等）：逐段应用，
                    # 单遍近似（不依赖双遍测量），作为片段级滤镜生效。
                    for e in (afx or []):
                        key, params = self._fx_key_params(e)
                        if key == "sidechainDuck":
                            duck_requests.append((seg, params))
                            continue
                        fn = _AUDIO_FX_STEPS.get(key)
                        if fn is not None:
                            audio_fx += "," + fn(params)
                    relative_start = float(timeline_start.to_fraction()) - audio_origin
                    if relative_start < 0:
                        # Run original fades/pitch/temporal audio FX before the
                        # crop, preserving a long music clip's phase/history.
                        audio_fx += f",atrim=start={-relative_start:.9f},asetpts=PTS-STARTPTS"
                    delay_ms = max(0, round(relative_start * 1000))
                    if delay_ms:
                        audio_fx += f",adelay={delay_ms}:all=1"
                    parts.append(
                        f"[{ai}:a]atrim=start={ss}:duration={src_dur},"
                        f"asetpts=PTS-STARTPTS{speed_filters}{audio_fx}"
                        f",aresample={sr},"
                        f"aformat=sample_fmts=fltp:sample_rates={sr}:"
                        f"channel_layouts=stereo[{seg}]")
                    labels.append(seg)
                    audio_labels_by_clip[clip_id] = seg
                gtag = f"atk{g}"
                audio_group_labels.append((gtag, labels))
                substreams.append(gtag)
                # 音轨时长按绝对起点 + 实际可用源时长计算；片段之间的
                # 空白是静音，不能被 concat 吞掉。
                audio_expected = max(
                    audio_expected,
                    max(float(s[10].to_fraction()) - audio_origin + effective_audio_dur(s)
                        for s in segs))

            from cutvoke.rendering.travel_fx import build_sidechain_ducks
            has_ducking = build_sidechain_ducks(audio_group_labels, audio_labels_by_clip,
                                               duck_requests, parts, canvas_dur + origin - audio_origin)
            def mixer_effect_filter(effect):
                key, params = self._fx_key_params(effect)
                fn = _AUDIO_FX_STEPS.get(key)
                if fn is None:
                    raise RenderError(f"unsupported mixer effect {effect.get('effectId')}")
                return fn(params)
            finish_mix(mixer_sequence, audio_group_track_ids, substreams, parts,
                       audio_origin, mixer_effect_filter)
            if has_ducking:
                parts.append("[outa]alimiter=limit=0.794328:level=0:latency=1[outa]")

        # 音频轨存在但没提取出任何可用音轨 -> 明确 warning，不静默丢音频
        if audio_track_present and not has_audio:
            warnings.append(
                "audio track(s) present but no decodable audio stream found in "
                "their source files; output is silent (no audio mixed)")

        if audio_expected - (origin-audio_origin) > video_expected + 1e-6:
            # Keep a real black picture stream while audio continues after the
            # last visual clip. A frozen last frame would imply a clip still
            # exists, and short preview windows need decodable video here.
            tail = audio_expected - (origin-audio_origin) - video_expected
            color = "black@0.0" if alpha else "black"
            parts.append(
                f"[outv]tpad=stop_mode=add:stop_duration={tail:.6f}:"
                f"color={color}[outv]")

        # V05：透明导出在最后一跳统一回到带 alpha 的格式，避免中途任何
        # 一步（overlay/fps/字幕合成）把 alpha 悄悄降成 yuv420p 后被编码器
        # 静默丢弃——宁可这里显式转换，也不让成片"看起来成功但没透明"。
        if alpha:
            parts.append("[outv]format=yuva420p[outv]")

        if render_window is not None:
            # Keep original clocks inside clips/effects/ASS/audio delays. Only
            # terminate the final streams at the window end; output-side seek
            # below still selects the absolute requested start.
            local_end = render_window[1] - origin
            # The canvas already starts at local PTS 0. An identity setpts
            # would clear CFR metadata/frame duration on FFmpeg 7 and lose the
            # last picture of a bounded preview or window intermediate.
            clock = "" if _setpts_clears_frame_rate(self.ffmpeg) else ",setpts=PTS-STARTPTS"
            parts.append(f"[outv]trim=end={local_end:.9f}{clock}[outv]")
            if has_audio:
                audio_end=render_window[1]-audio_origin
                parts.append(f"[outa]apad=whole_dur={audio_end:.9f},"
                             f"atrim=end={audio_end:.9f},asetpts=PTS-STARTPTS[outa]")

        graph = ";".join(parts)
        graph = self._preview_seek_inputs(graph, input_paths, seq.fps.to_fraction())
        # 期望总时长取视频主轴与音频最长轨的较大者；amix duration=longest 与之对齐
        expected = max(video_expected, audio_expected)
        if render_window is not None:
            expected = render_window[1] - origin
        return graph, input_paths, expected, has_audio, warnings, caption_cwd

    def _preview_seek_inputs(self, graph, paths, fps):
        """Skip irrelevant decoding without changing clip/effect clocks.

        Only verified CFR sources use this path. Every use of an input
        must begin with a trim; shared audio/video reads choose the earliest
        trim. Leave one second of decoder preroll and retain the exact residual
        trim, including audio sample alignment. Unusual stream origins keep the
        original path. Trimming precedes every effect, so decoder preroll cannot
        skip effect history, including in exports and animated preview graphs.
        """
        import re
        if fps.denominator != 1:
            return graph
        seeks = {}
        for index, path in enumerate(paths):
            if _is_image_src(path):
                continue
            token = re.compile(r'\[' + str(index) + r':([va])\]')
            trim = re.compile(r'\[' + str(index) + r':([va])\](a?trim)=start=([0-9.eE+\-]+)')
            references, uses = list(token.finditer(graph)), list(trim.finditer(graph))
            if not uses or len(references) != len(uses):
                continue
            info = self.probe_media(path)
            if (info.get('avg_frame_rate') != str(fps) or info.get('r_frame_rate') != str(fps)
                    or info.get('video_start_time') != 0
                    or (any(m[1] == 'a' for m in uses) and info.get('audio_start_time') != 0)):
                continue
            seek = max(0, math.floor(min(float(m[3]) for m in uses)) - 1)
            if seek <= 0:
                continue
            video_uses=[m for m in uses if m[1]=='v']
            if not video_uses:
                continue
            first=min(float(m[3]) for m in video_uses)
            ends=[]
            for use in video_uses:
                length=re.match(r':duration=([0-9.eE+\-]+)', graph[use.end():])
                ends.append(float(use[3])+float(length[1]) if length else
                            float(info.get('video_duration') or info.get('duration') or 0.))
            duration=max(ends)-first
            verified=os.path.normcase(os.path.abspath(path)) in getattr(self._render_ctx,'verified_input_paths',set())
            if duration <= 0 or (not verified and not self._regular_timestamps(path, first, duration, fps)):
                continue
            graph = trim.sub(lambda m: f'[{index}:{m[1]}]{m[2]}=start={float(m[3])-seek:.12f}', graph)
            seeks[os.path.abspath(path)] = seek
        self._render_ctx.input_seeks = seeks
        return graph

    # ------------------------------------------------------------------
    # 视频轨编译（F14/F18/F20）
    # ------------------------------------------------------------------
    def _clip_needs_canvas(self, clip: Clip) -> bool:
        """片段是否需要「画布合成」路径（位置/缩放/旋转/透明度/转场在帧内定位）。

        任何非默认的变换、透明度关键帧、或转场都会触发整轨走画布合成，
        以保证 concat/xfade 尺寸一致（不回退旧的源尺寸拼接路径）。
        静态图片源（1.5-A）也强制走画布：图片原生尺寸与视频不同，
        直接 concat 会尺寸不匹配，须先 scale 到画布。
        含动画效果的片段（1.5-B 视频动画）同样强制走画布——动画是逐帧
        变换，必须走能定位的画布路径，否则会被静默忽略。
        """
        if _is_image_src(clip.asset_ref.source_path):
            return True
        if _find_animations(clip):
            return True
        tr = clip.transform()
        if (tr["opacity"] != 1.0 or tr["scale"] != 1.0 or tr["rotation"] != 0.0
                or tr["position"]["x"] != 0 or tr["position"]["y"] != 0):
            return True
        if clip.keyframes.get("opacity"):
            return True
        if find_transition(clip, self.effects) is not None:
            return True
        return False

    def _build_video_track(self, clips: list[Clip], seq: Sequence, n: int,
                            get_input, parts: list[str], vseg: list[int],
                            overlay_position: tuple[int, int],
                            transparent_layer: bool = False,
                            video_duration=None,
                            force_canvas: bool = False,
                            ) -> tuple[str, float]:
        """编译一条视频轨为单条视频流 [vtk{n}]，返回 (label, 时长秒)。

        - 普通片段（无色/无变换）：沿用旧路径——trim + 变速后直接 concat（不回退）
        - 含调色(eq)：尺寸不变，inline 插入 eq 滤镜
        - 含变换/透明度/转场：整轨走「画布合成 + xfade」，位置/缩放/旋转/透明度生效
        """
        fps = seq.fps.to_fraction()
        fps_expr = f"{fps.numerator}/{fps.denominator}"
        origin = getattr(self._render_ctx, "compile_origin", 0.0)
        needs_canvas = (transparent_layer or force_canvas or
                        any(self._clip_needs_canvas(c) for c in clips))
        if video_duration is None:
            video_duration = lambda _path: 0.0

        clip_labels: list[str] = []
        clip_durs: list[float] = []

        for c in clips:
            dur = float(c.duration.to_fraction())
            clip_durs.append(dur)
            if needs_canvas:
                label = self._build_clip_canvas(
                    c, seq, get_input, parts, vseg, fps_expr)
            else:
                label = self._build_clip_plain(
                    c, seq, get_input, parts, vseg, fps_expr)
            clip_labels.append(label)

        # Reuse unused source frames after an outgoing clip's selected range as
        # its transition handle. Clips without real handle frames retain the
        # last-frame hold fallback so existing projects preserve their timing.
        transition_durations: dict[int, float] = {}
        for i in range(1, len(clips)):
            outgoing, incoming = clips[i - 1], clips[i]
            start = float(incoming.timeline_start.to_fraction())
            outgoing_end = float(outgoing.timeline_end.to_fraction())
            if abs(start - outgoing_end) > 1e-6:
                continue
            transition = find_transition(incoming, self.effects)
            if transition is None:
                continue
            requested = _duration_to_float(transition.get("params", {}).get("duration", 0.0))
            d = min(requested, clip_durs[i - 1] / 2.0, clip_durs[i] / 2.0)
            if requested > 0 and d > 0:
                transition_durations[i - 1] = d

        transition_handles: dict[int, str] = {}
        fps_value = float(fps)
        for clip_index, d in transition_durations.items():
            outgoing = clips[clip_index]
            speed = float(outgoing.speed.to_fraction())
            curve = outgoing.speed_curve
            # Continue static picture treatment over the source handle. Timed
            # effect strips and freeze frames need their own extension semantics.
            effect_ids = {str(effect.get("effectId", ""))
                          for effect in outgoing.effects}
            effect_ids.discard("")
            effect_ids = {effect_id for effect_id in effect_ids
                          if not effect_id.startswith("cutvoke.transition.")}
            safe_static_fx_ids = {
                str(effect.get("effectId", ""))
                for effect in self._enabled_fx(outgoing)
                if (effect.get("range") is None
                    and self._fx_key_params(effect)[0] in _TRANSITION_HANDLE_STATIC_FX)
            }
            unsupported_effect_ids = (
                effect_ids - {"cutvoke.color", "cutvoke.transform"} - safe_static_fx_ids)
            if (speed <= 0 or outgoing.freeze_at is not None
                    or unsupported_effect_ids):
                continue

            source_end = outgoing.source_start + outgoing.consumed_source_duration
            source_end_seconds = float(source_end.to_fraction())
            tail_speed = (curve.speed_at_source(
                float(curve.source_duration.to_fraction())) if curve is not None else speed)
            added_source_seconds = d * tail_speed
            available_video = video_duration(outgoing.asset_ref.source_path)
            # Motion-compensated interpolation needs a few source frames to
            # estimate motion. A short transition handle can be shorter than
            # that window (for example, two 10 fps frames); minterpolate then
            # emits no frames. Render extra source context and trim the handle
            # back to the transition duration when it is connected below.
            handle_source_seconds = max(
                added_source_seconds,
                0.3 if outgoing.frame_interpolation == "motion" else 0.0,
            )
            handle_timeline_seconds = handle_source_seconds / tail_speed
            needed_source_end = source_end_seconds + handle_source_seconds
            handle_tolerance = max(0.001, 0.51 / fps_value)
            if (available_video <= 0
                    or needed_source_end > available_video + handle_tolerance):
                continue

            extended_curve = None
            if curve is not None:
                # The handle is a new clip beginning at the selected source
                # end, so its curve is just the outgoing curve's terminal
                # speed across the extra source range.
                handle_source_duration = Rational.of(
                    round(handle_source_seconds * 1_000_000), 1_000_000)
                points = [{"at": Rational.of(0),
                           "speed": Rational.from_float(tail_speed)},
                          {"at": Rational.of(1),
                           "speed": Rational.from_float(tail_speed)}]
                try:
                    extended_curve = type(curve).from_points(
                        handle_source_duration, points)
                except (TypeError, ValueError, ZeroDivisionError):
                    continue

            handle_clip = copy.copy(outgoing)
            handle_clip.id = f"{outgoing.id}__transition_handle_{clip_index}"
            handle_clip.timeline_start = outgoing.timeline_end
            handle_clip.timeline_end = outgoing.timeline_end + Rational.of(
                round(handle_timeline_seconds * 1_000_000), 1_000_000)
            handle_clip.source_start = source_end
            handle_clip.effects = [
                effect for effect in outgoing.effects
                if not str(effect.get("effectId", "")).startswith(
                    "cutvoke.transition.")
            ]
            handle_clip.keyframes = {}
            for name, keyframes in outgoing.keyframes.items():
                if not keyframes:
                    continue
                terminal = copy.copy(max(
                    keyframes, key=lambda keyframe: keyframe.time.to_fraction()))
                terminal.time = Rational.of(0)
                terminal.value = kf_evaluate(keyframes, outgoing.duration)
                handle_clip.keyframes[name] = [terminal]
            handle_clip.speed_curve = extended_curve
            handle_clip.freeze_at = None
            handle_clip.frame_interpolation = outgoing.frame_interpolation
            # Resolve the handle through the same canvas/effect pipeline as the
            # selected outgoing clip, including transparent overlay tracks.
            handle_label = self._build_clip_canvas(
                handle_clip, seq, get_input, parts, vseg, fps_expr)
            transition_handles[clip_index] = handle_label

        # FFmpeg's concat filter drops clip-level changes from later
        # alpha-bearing segments (for example, a mirrored sticker or PersonFX
        # layer). Composite transparent clips on a shared timeline bed instead
        # so each clip's rendered pixels survive the segment boundary. Explicit
        # transitions keep the xfade path below.
        if transparent_layer and not any(
                find_transition(clips[i], self.effects) is not None
                for i in range(1, len(clips))):
            first_start = float(clips[0].timeline_start.to_fraction()) - origin
            offsets = [first_start]
            total_dur = first_start + clip_durs[0]
            tl = total_dur + origin
            for i in range(1, len(clips)):
                abs_start = float(clips[i].timeline_start.to_fraction())
                gap = max(0.0, abs_start - tl)
                if gap > 0:
                    total_dur += gap
                    tl = abs_start
                offsets.append(total_dur)
                total_dur += clip_durs[i]
                tl += clip_durs[i]

            bed = f"tlbed{n}"
            parts.append(
                f"color=c=black@0.0:s={seq.width}x{seq.height}:"
                f"r={fps_expr}:d={total_dur:.6f},format=yuva420p[{bed}]")
            current = bed
            for i, (label, offset) in enumerate(zip(clip_labels, offsets)):
                shifted = f"tlclip{n}_{i}"
                clock = f",fps={fps_expr}" if _setpts_clears_frame_rate(self.ffmpeg) else ""
                parts.append(
                    f"[{label}]trim=duration={clip_durs[i]:.6f},"
                    f"setpts=PTS-STARTPTS+{offset:.6f}/TB{clock}[{shifted}]")
                out = f"tlmix{n}_{i}"
                parts.append(
                    f"[{current}][{shifted}]overlay=shortest=0:eof_action=pass:"
                    f"format=auto[{out}]")
                current = out
            out_label = f"vtk{n}"
            parts.append(f"[{current}]null[{out_label}]")
            return out_label, total_dur

        # 组装轨内片段（A04：按绝对时间线位置，中间/开头空白必须保留）
        #
        # 两个游标必须分开（2026-09-21 修）：旧实现只用一个 cum 兼作两种语义，
        # 造成带转场的时间线导出直接失败 + 画面错位。
        #   tl   = 时间线游标：只按片段绝对时间线位置推进，用来判断"真实空白"；
        #   film = 成片游标：始终与绝对时间线同长，是 xfade 的 offset 基准。
        # 转场不能把工程压短：相邻片段在时间线上没有重叠时，给前段末帧补 d 秒，
        # 再从剪切点开始与后段前 d 秒混合。这样既不读取源素材范围之外，也保证
        # 播放器、标尺和导出的绝对时间坐标完全一致。
        current = clip_labels[0]
        first_start = float(clips[0].timeline_start.to_fraction())
        tl = first_start
        film = first_start - origin
        # 开头空白：第一片段不在 t=0 → 补在流**前面**
        if first_start > origin:
            current = self._pad_black(current, first_start - origin, parts, fps_expr,
                                      at_start=True, transparent=transparent_layer,
                                      canvas_size=(seq.width, seq.height),
                                      total_duration=first_start - origin + clip_durs[0])
        tl += clip_durs[0]
        film += clip_durs[0]
        for i in range(1, len(clip_labels)):
            # 绝对时间起点与**时间线游标**的差 = 真实空白（转场不产生空白）
            abs_start = float(clips[i].timeline_start.to_fraction())
            gap = max(0.0, abs_start - tl)
            if abs_start > tl + 1e-6:
                # 中间空白 → 必须补在流**后面**，否则前面内容整体后移、音画不同步
                current = self._pad_black(current, gap, parts, fps_expr,
                                          transparent=transparent_layer)
                film += gap
                tl = abs_start
            # A transition belongs to the seam. If a later trim/speed/move has
            # opened a real gap, keep the effect instance editable but do not
            # blend the incoming clip with the black gap or a frozen old frame.
            tr = find_transition(clips[i], self.effects) if gap <= 1e-6 else None
            if tr is not None:
                # 转场时长 D 钳制到相邻两段较短者显示时长的一半（剪映硬规则：
                # 4s+8s 两段转场最长 2s；保证转场不超出较短的素材）。
                # duration 参数支持数字或有理数 dict {"num","den"}
                d_param = _duration_to_float(tr.get("params", {}).get("duration", 0.0))
                d = min(d_param, clip_durs[i - 1] / 2.0, clip_durs[i] / 2.0)
                if d_param <= 0 or d <= 0:
                    current = self._concat2(current, clip_labels[i], parts)
                    film += clip_durs[i]
                    tl += clip_durs[i]
                    continue
                # 有源素材余量时用 A 的真实后续帧作为把手；源文件到头或剪辑处理
                # 不支持延伸时才复制末帧。两种路径都从剪切点开始，工程总时长不变。
                handle = transition_handles.get(i - 1)
                if handle:
                    transition_outgoing = f"trhandle{n}_{i}"
                    parts.append(
                        f"[{handle}]trim=duration={d:.6f},"
                        f"setpts=PTS-STARTPTS[{transition_outgoing}]")
                    offset = 0.0
                else:
                    current = self._pad_last_frame(current, d, parts, fps_expr, film + d)
                    transition_outgoing = current
                    offset = film
                out = f"xf{n}_{i}"
                incoming = clip_labels[i]
                transition_spec = self.effects.get(str(tr.get("effectId", "")))
                blur_pattern = transition_spec.implementation.get("blurPattern")
                camera_pattern = transition_spec.implementation.get("cameraPattern")
                vlog_pattern = transition_spec.implementation.get("vlogPattern")
                squeeze_axis = transition_spec.implementation.get("squeezeAxis")
                if squeeze_axis is not None and not handle:
                    # Both the squeeze and timeline bed consume the held stream.
                    # Explicitly split it; older FFmpeg cannot reuse an output pad.
                    squeeze_source = f"squeezesrc{n}_{i}"
                    timeline_source = f"squeezebed{n}_{i}"
                    parts.append(f"[{current}]split=2[{squeeze_source}][{timeline_source}]")
                    transition_outgoing, current = squeeze_source, timeline_source
                # FFmpeg only evaluates expr when transition=custom. Keep all
                # other transition names manifest-driven; blur presets provide
                # their own bounded, spatially shaped expression.
                xname = ("custom" if any(p is not None for p in (blur_pattern, camera_pattern, vlog_pattern)) else
                         xfade_transition_name(clips[i], self.effects))
                has_alpha = bool(getattr(self._render_ctx, "alpha", False))
                xfade_expr = (_xfade_blur_expr(str(blur_pattern), has_alpha=has_alpha)
                              if blur_pattern is not None else None)
                if camera_pattern is not None:
                    from cutvoke.rendering.travel_fx import camera_transition_expr
                    xfade_expr = camera_transition_expr(str(camera_pattern), tr.get("params", {}),
                                                        has_alpha=has_alpha)
                if vlog_pattern is not None:
                    from cutvoke.rendering.vlog_fx import transition_expr
                    xfade_expr = transition_expr(str(vlog_pattern), tr.get("params", {}),
                                                 has_alpha=has_alpha)
                expr_option = f":expr='{xfade_expr}'" if xfade_expr else ""
                incoming_zoom = float(transition_spec.implementation.get("incomingZoom", 1.0))
                if 1.0 < incoming_zoom <= 1.5:
                    # A bounded zoom on B avoids xfade=zoomin's near-solid
                    # midpoint while keeping both source shots legible. d=1
                    # emits one frame per incoming frame, so timeline length and
                    # audio alignment remain unchanged.
                    zoom_frames = max(1, round(d * float(fps)))
                    incoming = f"zoom{n}_{i}"
                    parts.append(
                        f"[{clip_labels[i]}]zoompan="
                        f"z='max(1\\,{incoming_zoom:.4f}-"
                        f"{incoming_zoom - 1.0:.4f}*on/{zoom_frames})':"
                        f"d=1:s={seq.width}x{seq.height}:fps={fps_expr},"
                        f"setpts=PTS-STARTPTS[{incoming}]")
                # Concat may report VFR, and FFmpeg 7 setpts unconditionally
                # marks its output rate as 1/0. Normalize frame spacing, rebuild
                # the clocks, then restore CFR metadata AFTER the last setpts.
                # xfade requires matching, valid rates on both input links.
                # Concat uses a microsecond time base. Rebuild both clocks on
                # the exact sequence frame lattice before xfade; rounding a
                # 1/30 frame to microseconds otherwise changes the transition
                # end frame when a window's presentation origin is shifted.
                # The manual xfade blur expression samples every plane by X/Y.
                # Convert subsampled YUV420/alpha inputs to full-resolution
                # planes first; otherwise chroma/alpha samples use incompatible
                # coordinates and the midpoint collapses toward dark colors.
                xfade_format = (f"format={'yuva444p' if has_alpha else 'yuv444p'},"
                                if any(p is not None for p in (blur_pattern, camera_pattern, vlog_pattern)) else "")
                if squeeze_axis is not None:
                    _build_squeeze_transition(transition_outgoing, incoming, out,
                                              str(squeeze_axis), d, offset, parts)
                else:
                    clock = f"settb=expr={fps.denominator}/{fps.numerator},setpts=N"
                    clears_rate = _setpts_clears_frame_rate(self.ffmpeg)
                    if clears_rate:
                        clock = f"fps={fps_expr},{clock},fps={fps_expr}"
                    # Older xfade can forward EOF before mixing the outgoing
                    # final picture. Keep a lookahead picture beyond the seam;
                    # the completed lane is still bounded to its real duration.
                    lookahead = "" if clears_rate else "tpad=stop_mode=clone:stop=1,"
                    parts.append(
                        f"[{transition_outgoing}]{xfade_format}{lookahead}{clock}[inA];"
                        f"[{incoming}]{xfade_format}{clock}[inB];"
                        f"[inA][inB]"
                        f"xfade=transition={xname}:offset={offset:.6f}:"
                        f"duration={d:.6f}{expr_option}[{out}]")
                if handle or squeeze_axis is not None:
                    # Canvas clips may carry repeated EOF frames. Concatenating
                    # a short transition segment after such a stream can leave
                    # it waiting forever at the seam. Place the seam and the
                    # incoming tail at their timeline timestamps instead.
                    current = self._pad_last_frame(
                        current, clip_durs[i], parts, fps_expr, film + clip_durs[i])
                    transition_at_seam = f"trseg{n}_{i}"
                    parts.append(
                        f"[{out}]setpts=PTS-STARTPTS+{film:.6f}/TB"
                        f"[{transition_at_seam}]")
                    seam_mix = f"trmix{n}_{i}"
                    parts.append(
                        f"[{current}][{transition_at_seam}]overlay=shortest=0:"
                        f"eof_action=pass:format=auto[{seam_mix}]")
                    bounded = f"trbounded{n}_{i}"
                    parts.append(
                        f"[{seam_mix}]trim=duration={film + clip_durs[i]:.6f},"
                        f"setpts=PTS-STARTPTS[{bounded}]")
                    current = bounded
                else:
                    current = out
                film += clip_durs[i]
                tl += clip_durs[i]
                continue
            # 无转场：concat（画布合成轨尺寸已一致）
            current = self._concat2(current, clip_labels[i], parts)
            film += clip_durs[i]
            tl += clip_durs[i]

        out_label = f"vtk{n}"
        # Concat timestamps are rounded to microseconds. Align the completed
        # lane to the canvas frame clock before framesync: otherwise a rounded
        # timestamp just after a canvas tick repeats its previous picture, and
        # the transition endpoint differs after a window origin shift.
        # FFmpeg 6.1 forwards overlay EOF at the last frame's timestamp. fps
        # would discard that frame (and every one-frame animation segment).
        # Supply one lookahead frame, then bound the lane to its actual duration.
        clock = ("" if _setpts_clears_frame_rate(self.ffmpeg) else
                 f",settb=expr={fps.denominator}/{fps.numerator},setpts=N")
        parts.append(f"[{current}]tpad=stop_mode=clone:stop=1,fps={fps_expr},"
                     f"trim=duration={film:.9f}{clock}[{out_label}]")
        return out_label, film

    def _pad_last_frame(self, label: str, pad_secs: float, parts: list[str],
                        fps_expr: str, total_duration: float) -> str:
        """为无重叠素材的转场复制前段末帧，且不改变绝对时间轴总时长。"""
        if pad_secs <= 0:
            return label
        out = f"hold{len(parts)}"
        frame_rate = Fraction(fps_expr)
        lookahead = float(1 / frame_rate)
        clears_rate = _setpts_clears_frame_rate(self.ffmpeg)
        before = f"fps={fps_expr}," if clears_rate else ""
        after = "" if clears_rate else ",setpts=PTS-STARTPTS"
        parts.append(
            # tpad computes its clone count from link.frame_rate too; repair
            # concat/setpts metadata before it, not only on its output.
            f"[{label}]{before}tpad=stop_mode=clone:stop_duration={pad_secs + lookahead:.9f}"
            f",fps={fps_expr},trim=duration={total_duration:.9f}{after}[{out}]")
        return out

    def _pad_black(self, label: str, pad_secs: float, parts: list[str],
                   fps_expr: str, at_start: bool = False,
                   transparent: bool = False,
                   canvas_size: Optional[tuple[int, int]] = None,
                   total_duration: Optional[float] = None) -> str:
        """补 pad_secs 秒黑帧（A04：绝对时间空白）。

        `at_start=True` → 补在该流**前面**（仅用于"首片段不在 t=0"的开头空白）；
        `at_start=False` → 补在该流**后面**（用于片段之间的中间空白）。

        必须区分方向（2026-09-21 修）：`tpad=start_mode=add` 是在**已合成流头部**
        插黑。中间空白若也走 start_mode，会把之前所有内容整体后移 —— 成片里片段
        被推到空白之后，而音轨按绝对时间对齐、不会跟着移，结果是画面错位 +
        音画不同步。旧实现在 `A[0,2s) + B[4,7s)` 这种有时间线空白的工程上，导出
        得到的其实是 `[黑2s][A 2s][B 3s]`：总长 7s 看起来"对"，但内容整体晚了 2s。
        """
        if pad_secs <= 0:
            return label
        out = f"tpad{len(parts)}"
        pad_color = ":color=black@0.0" if transparent else ""
        clears_rate = _setpts_clears_frame_rate(self.ffmpeg)
        before = f"fps={fps_expr}," if clears_rate else ""
        if at_start and canvas_size is not None:
            prefix = f"padstart{len(parts)}"
            width, height = canvas_size
            fmt = ",format=yuva420p" if transparent else ""
            color = "black@0.0" if transparent else "black"
            parts.append(f"color=c={color}:s={width}x{height}:r={fps_expr}:"
                         f"d={pad_secs:.9f}{fmt}[{prefix}]")
            lookahead = "" if clears_rate else ",tpad=stop_mode=clone:stop=1"
            clock = ""
            if not clears_rate:
                if total_duration is None:
                    raise RenderError("leading canvas padding requires its stream duration")
                rate = Fraction(fps_expr)
                clock = (f",trim=duration={total_duration:.9f},"
                         f"settb=expr={rate.denominator}/{rate.numerator},setpts=N")
            parts.append(f"[{prefix}][{label}]concat=n=2:v=1:a=0{lookahead},"
                         f"fps={fps_expr}{clock}[{out}]")
        elif at_start:
            parts.append(
                f"[{label}]{before}tpad=start_mode=add:start_duration={pad_secs:.6f}{pad_color}"
                f",fps={fps_expr},setpts=PTS-STARTPTS[{out}]")
        else:
            parts.append(
                f"[{label}]{before}tpad=stop_mode=add:stop_duration={pad_secs:.6f}{pad_color}"
                f",fps={fps_expr},setpts=PTS-STARTPTS[{out}]")
        return out

    def _concat2(self, a: str, b: str, parts: list[str]) -> str:
        """concat 两路视频为一个流（用于轨内无转场拼接）。"""
        out = f"cc{len(parts)}"
        parts.append(f"[{a}][{b}]concat=n=2:v=1:a=0[{out}]")
        return out

    def _build_clip_plain(self, clip: Clip, seq: Sequence, get_input, parts: list[str],
                          vseg: list[int], fps_expr: str) -> str:
        """普通路径单片段：变速、画面效果与画布归一化后再参加轨道拼接。"""
        def fit_to_canvas(label: str) -> str:
            return self._scale_to_canvas(
                label, seq.width, seq.height, 1.0, 1.0, parts, vseg)

        vi = get_input(clip.asset_ref.source_path)
        seg = f"vseg{vseg[0]}"
        vseg[0] += 1
        speed = clip.speed
        # 定格帧（B06）：只重复 freeze_at 对应的一帧。
        if clip.freeze_at is not None:
            chain = self._frozen_clip_chain(clip, vi, fps_expr)
            chain += self._preview_source_filter(clip) + self._canvas_source_filter(clip)
            col = clip.color_grade()
            animated_color = self._animated_color(clip, col)
            if animated_color:
                chain += "," + animated_color
            elif (col["brightness"] != 0.0 or col["contrast"] != 1.0
                    or col["saturation"] != 1.0):
                chain += (f",eq=brightness={col['brightness']:.6f}:"
                          f"contrast={col['contrast']:.6f}:"
                          f"saturation={col['saturation']:.6f}")
            if self._has_fx(clip):
                mid = f"vfx{vseg[0]}"
                vseg[0] += 1
                parts.append(f"{chain}[{mid}]")
                return fit_to_canvas(self._apply_fx(clip, mid, parts, vseg))
            parts.append(f"{chain}[{seg}]")
            return fit_to_canvas(seg)
        src_dur = float(clip.consumed_source_duration.to_fraction())
        ss = float(clip.source_start.to_fraction())
        speed_f = float(speed.to_fraction())
        if clip.speed_curve is not None:
            speed_expr = clip.speed_curve.video_setpts()
        elif speed_f >= 0:
            speed_expr = f"setpts=PTS/{speed_f}"
        else:
            abs_f = float(_abs_rat(speed).to_fraction())
            speed_expr = (f"reverse,setpts=PTS-STARTPTS,"
                          f"setpts=PTS/{abs_f}")
        chain = (f"[{vi}:v]trim=start={ss}:duration={src_dur},"
                 f"setpts=PTS-STARTPTS,{speed_expr}")
        chain += self._slow_motion_interpolation_filter(clip, fps_expr)
        chain += self._preview_source_filter(clip) + self._canvas_source_filter(clip)
        # F18 调色（尺寸不变，inline）
        col = clip.color_grade()
        animated_color = self._animated_color(clip, col)
        if animated_color:
            chain += "," + animated_color
        elif (col["brightness"] != 0.0 or col["contrast"] != 1.0
                or col["saturation"] != 1.0):
            chain += (f",eq=brightness={col['brightness']:.6f}:"
                      f"contrast={col['contrast']:.6f}:"
                      f"saturation={col['saturation']:.6f}")
        # J01 效果栈：fx 画面滤镜按效果顺序应用（可能多流，走显式建图）
        if self._has_fx(clip):
            mid = f"vfx{vseg[0]}"
            vseg[0] += 1
            parts.append(f"{chain}[{mid}]")
            return fit_to_canvas(self._apply_fx(clip, mid, parts, vseg))
        parts.append(f"{chain}[{seg}]")
        return fit_to_canvas(seg)

    def _scale_to_canvas(self, cur: str, W: int, H: int,
                         sx: float, sy: float,
                         parts: list[str], vseg: list[int],
                         sticker: bool = False) -> str:
        """把片段流归一到画布尺寸。

        语义：`scale` 是**画布占比**（1.0 = 铺满画布）；transform 的用途是
        画中画与多轨合成，0<scale<1 即缩小成 PiP。

        必须**始终**插入 scale，不能因 scale==1.0 就跳过：源尺寸与画布尺寸不等时
        （如 320x240 素材放进 1920x1080 工程），跳过会让片段以**原生像素**贴在画布
        左上角——实测只占 3.7% 面积、其余全黑；而且与普通路径末尾的
        `scale=W:H`（铺满）行为不一致，会出现"给片段加一个转场，画面尺寸就变了"。
        """
        unit = min(W, H) if sticker else None
        sw = max(1, round((unit or W) * sx))
        sh = max(1, round((unit or H) * sy))
        nxt = f"vs{vseg[0]}"
        vseg[0] += 1
        # Canvas coordinates describe square pixels. Crop/pad effects can
        # change SAR during scale; normalize before concat/xfade joins.
        parts.append(f"[{cur}]scale={sw}:{sh},setsar=1[{nxt}]")
        return nxt

    def _build_clip_canvas(self, clip: Clip, seq: Sequence, get_input,
                           parts: list[str], vseg: list[int],
                           fps_expr: str) -> str:
        """画布合成单片段：把片段定位到 WxH 画布（F14 位置/缩放/旋转/透明度 + F18 调色）。

        返回 WxH 的单条视频流 label。透明度<1 用 format=rgba + colorchannelmixer；
        透明度关键帧则把片段按关键帧时刻切成常数透明度的子段后拼接。
        """
        W, H = seq.width, seq.height
        color = clip.color_grade()

        # 1.5-A 入场动画：把动画效果动态展开成关键帧（不污染工程存储）。
        # 动画时长默认 1s（或片段时长，取小），起始帧在 t=0，结束帧在 t=duration。
        animations = _find_animations(clip)
        if animations:
            return self._build_animated_clip(
                clip, seq, get_input, parts, vseg, fps_expr, color, W, H, animations)

        animated_keyframes = any(
            clip.keyframes.get(param)
            for param in ("opacity", "x", "y", "scale", "rotation")
        )
        if animated_keyframes:
            return self._build_keyframed_clip(
                clip, seq, get_input, parts, vseg, fps_expr, color, W, H)

        # 常数透明度（来自 transform 的 opacity 参数；默认 1.0）
        tr = clip.transform()
        opacity = tr["opacity"]

        # 1) trim + 变速 + 调色（源尺寸）
        base = self._trim_speed_color(clip, get_input, parts, vseg, color, fps_expr)
        cur = base

        # 2) 归一到画布（必须无条件做，见 _scale_to_canvas 的说明）
        cur = self._scale_to_canvas(cur, W, H, tr["scale"], tr["scale"],
                                    parts, vseg, sticker=clip.role == "sticker")

        # 3) 旋转（度 -> 弧度）
        if tr["rotation"] != 0.0:
            rad = math.radians(tr["rotation"])
            nxt = f"vr{vseg[0]}"
            vseg[0] += 1
            parts.append(f"[{cur}]rotate=angle={rad:.6f}:c=none[{nxt}]")
            cur = nxt

        # 4) 透明度（<1 时转 RGBA 并按 alpha 混合）
        if opacity < 1.0:
            nxt = f"va{vseg[0]}"
            vseg[0] += 1
            parts.append(
                f"[{cur}]format=rgba,colorchannelmixer=aa={opacity:.6f}[{nxt}]")
            cur = nxt

        # 5) 叠加到黑色画布（位置 x,y；非变换片段 x=y=0 即满画布）
        out = self._composite_on_canvas(cur, clip, seq, parts, vseg,
                                          fps_expr, W, H, tr["position"],
                                          float(clip.duration.to_fraction()))
        return out

    def _build_keyframed_clip(self, clip: Clip, seq: Sequence, get_input,
                             parts: list[str], vseg: list[int], fps_expr: str,
                             color: dict, W: int, H: int) -> str:
        """Resample transforms at every output frame with subpixel geometry.

        A single fixed-size perspective stream avoids the old 15 fps staircase,
        integer scale/position jumps, and one filter branch per motion sample.
        Transparent padding prevents perspective's edge clamp from extending
        the picture into uncovered canvas or lower tracks.
        """
        tr = clip.transform()
        fps = seq.fps.to_fraction()
        # perspective increments `on` before evaluating the first frame.
        offset=getattr(self._render_ctx,'clip_clocks',{}).get(clip.id,(0,None))[0]
        time = f"((on-1)*{fps.denominator}/{fps.numerator}+{offset:.12f})"
        x = kf_expression(clip.keyframes.get("x", []), time, tr["position"]["x"])
        y = kf_expression(clip.keyframes.get("y", []), time, tr["position"]["y"])
        scale = kf_expression(clip.keyframes.get("scale", []), time, tr["scale"])
        scale = f"max(0.000001,({scale}))"
        sx = sy = scale
        if clip.role == "sticker":
            unit = min(W, H)
            sx, sy = f"({scale})*{unit}/{W}", f"({scale})*{unit}/{H}"
        rotation = kf_expression(clip.keyframes.get("rotation", []), time, tr["rotation"])
        has_rotation = tr["rotation"] != 0 or any(
            keyframe.value != 0 for keyframe in clip.keyframes.get("rotation", []))
        corners = []
        for index, (px, py) in enumerate(((-2, -2), (W + 2, -2),
                                         (-2, H + 2), (W + 2, H + 2))):
            if has_rotation:
                angle = f"({rotation})*PI/180"
                dx, dy = f"({px}-{W}/2)*({sx})", f"({py}-{H}/2)*({sy})"
                cx = f"({x})+{W}/2*({sx})+({dx})*cos({angle})-({dy})*sin({angle})"
                cy = f"({y})+{H}/2*({sy})+({dx})*sin({angle})+({dy})*cos({angle})"
            else:
                cx, cy = f"({x})+{px}*({sx})", f"({y})+{py}*({sy})"
            corners.extend((f"x{index}='{cx}'", f"y{index}='{cy}'"))

        base = self._trim_speed_color(clip, get_input, parts, vseg, color, fps_expr)
        cur = f"kgeom{vseg[0]}"; vseg[0] += 1
        parts.append(
            f"[{base}]fps={fps_expr},scale={W}:{H},setsar=1,format=yuva444p,"
            f"pad={W + 4}:{H + 4}:2:2:color=black@0,"
            f"perspective={':'.join(corners)}:sense=destination:eval=frame:"
            f"interpolation=cubic,crop={W}:{H}:0:0[{cur}]")
        opacity_lane = clip.keyframes.get("opacity", [])
        if tr["opacity"] < 1 or any(keyframe.value != 1 for keyframe in opacity_lane):
            nxt = f"kop{vseg[0]}"; vseg[0] += 1
            parts.append(f"[{cur}]{self._opacity_filter(clip, fps, nxt, offset)}[{nxt}]")
            cur = nxt
        return self._composite_on_canvas(
            cur, clip, seq, parts, vseg, fps_expr, W, H, {"x": 0, "y": 0},
            float(clip.duration.to_fraction()))

    @staticmethod
    def _opacity_filter(clip: Clip, fps: Fraction, name: str, offset: float = 0.) -> str:
        """Evaluate uniform opacity once per frame, then apply an alpha LUT.

        geq used to evaluate the whole easing expression for every pixel and
        copy all four planes through its expression interpreter. Keep Y/U/V
        explicitly unchanged: LUT's defaults clamp chroma to the nominal range.
        Commands run upstream of their target on the same local frame clock.
        """
        lane = clip.keyframes.get("opacity", [])
        default = clip.transform()["opacity"]
        duration = clip.duration.to_fraction()
        last_change = min(duration, max((k.time.to_fraction() for k in lane),
                                        default=Fraction(0)))
        count = max(1, math.ceil(last_change * fps) + 1)
        # Retain a bounded graph for pathological multi-hour animated clips.
        if count > 10_000:
            opacity = kf_expression(lane, f"(N*{fps.denominator}/{fps.numerator}+{offset:.12f})", default)
            return ("geq=lum='p(X,Y)':cb='p(X,Y)':cr='p(X,Y)':"
                    f"a='alpha(X,Y)*clip({opacity},0,1)'")
        target = f"lut@{name}"
        commands = []
        previous = None
        initial = None
        for n in range(count):
            value = kf_evaluate(lane, Rational.of(n * fps.denominator, fps.numerator)+Rational.from_float(offset)) if lane else default
            value = min(1.0, max(0.0, value))
            expression = f"val*{value:.17g}"
            if initial is None:
                initial = expression
            if expression != previous:
                # Place each event between adjacent frame timestamps. Rounding
                # a rational frame boundary upward must not delay it one frame.
                timestamp = max(Fraction(0), Fraction(4 * n - 1, 4) / fps)
                commands.append(f"{float(timestamp):.9f} {target} a {expression}")
                previous = expression
        lut = f"{target}=y=val:u=val:v=val:a='{initial}'"
        if len(commands) == 1:
            return lut
        return f"sendcmd=c='{';'.join(commands)}',{lut}"

    def _build_animated_clip(self, clip: Clip, seq: Sequence, get_input,
                            parts: list[str], vseg: list[int], fps_expr: str,
                            color: dict, W: int, H: int,
                            animations: list[dict]) -> str:
        """入场动画（1.5-A/B）：把动画效果渲染成时间插值的画布流。

        统一机制：按序列帧率把动画切成子段，每帧按插值取中点常数
        (opacity/scale/position)，逐段渲染后 concat。fadeIn/zoomIn/slideIn
        都走同一条路径，得到真实的时间维运动，而非静态起始状态。
        普通不透明纯循环使用固定画布的逐帧仿射流，避免长片段创建
        数百个 scale/rotate 上下文而占用数 GB 内存。

        缓动：easing 参数影响插值曲线（linear / ease-in / ease-out）。
        """
        dur_f = float(clip.duration.to_fraction())
        tr = clip.transform()

        def _ease(t: float, easing: str) -> float:
            if easing == "ease-in":
                return t * t
            if easing == "ease-out":
                return 1.0 - (1.0 - t) * (1.0 - t)
            return t  # linear

        def _state_at(anim: dict, t: float) -> dict:
            """返回片段局部时间 t 的合成状态。

            入场/出场动画：t 是**动画进度 p∈[0,1]**（已缓动）。
            循环动画：t 是**绝对秒**（用于正弦相位）。

            返回 opacity / scale / sx / sy / rot / x / y。
            sx/sy 为分轴缩放（翻转、遮罩显现用），缺省时渲染层回退到 scale。
            """
            eid = str(anim.get("effectId", ""))
            params = anim.get("params", {}) or {}
            p = _ease(t, str(params.get("easing", "linear")))
            st = {"opacity": 1.0, "scale": 1.0, "sx": None, "sy": None,
                  "rot": 0.0, "x": 0, "y": 0}
            if eid == "cutvoke.anim.fadeIn":
                st["opacity"] = p
            elif eid == "cutvoke.anim.zoomIn":
                from_scale = float(params.get("fromScale", 0.8))
                st["scale"] = from_scale + (1.0 - from_scale) * p
            elif eid == "cutvoke.anim.slideIn":        # 自左滑入
                dist = float(params.get("distance", 0.3))
                st["x"] = int(-W * dist * (1.0 - p))
            elif eid == "cutvoke.anim.slideUp":        # 自下而上
                dist = float(params.get("distance", 0.3))
                st["y"] = int(H * dist * (1.0 - p))
            elif eid == "cutvoke.anim.slideDown":      # 自上而下
                dist = float(params.get("distance", 0.3))
                st["y"] = int(-H * dist * (1.0 - p))
            elif eid == "cutvoke.anim.slideRight":     # 自右滑入
                dist = float(params.get("distance", 0.3))
                st["x"] = int(W * dist * (1.0 - p))
            elif eid == "cutvoke.anim.rotateIn":
                ang = float(params.get("fromAngle", -12.0))
                st["rot"] = ang * (1.0 - p)
                st["opacity"] = min(1.0, p * 1.6)
                # 旋转时略微放大，保证四角不露黑边
                st["scale"] = 1.0 + 0.18 * (1.0 - p)
            elif eid == "cutvoke.anim.flipIn":         # 水平轴翻展（以中线为轴）
                sxv = max(0.02, p)
                st["sx"] = sxv
                st["sy"] = 1.0
                st["x"] = int(W * (1.0 - sxv) / 2.0)
            elif eid == "cutvoke.anim.backIn":         # 回弹（过冲后归位）
                o = float(params.get("overshoot", 0.08))
                st["scale"] = (1.0 - 2.0 * o * (1.0 - p)
                               + 2.0 * o * math.sin(math.pi * p))
                st["opacity"] = min(1.0, p * 2.0)
            elif eid == "cutvoke.anim.reveal":         # 揭开遮罩，保持素材原比例
                st["reveal"] = (str(params.get("direction", "left")), p)
            elif eid == "cutvoke.anim.fadeOut":
                st["opacity"] = 1.0 - p
            elif eid == "cutvoke.anim.zoomOut":
                to_scale = float(params.get("toScale", 0.8))
                st["scale"] = 1.0 - (1.0 - to_scale) * p
            elif eid == "cutvoke.anim.slideOutLeft":
                st["x"] = int(-W * float(params.get("distance", 0.3)) * p)
            elif eid == "cutvoke.anim.slideOutRight":
                st["x"] = int(W * float(params.get("distance", 0.3)) * p)
            elif eid == "cutvoke.anim.slideOutUp":
                st["y"] = int(-H * float(params.get("distance", 0.3)) * p)
            elif eid == "cutvoke.anim.slideOutDown":
                st["y"] = int(H * float(params.get("distance", 0.3)) * p)
            elif eid == "cutvoke.anim.rotateOut":
                st["rot"] = float(params.get("toAngle", 18.0)) * p
                st["opacity"] = 1.0 - p
                st["scale"] = 1.0 + 0.18 * p
            elif eid == "cutvoke.anim.flipOut":
                sxv = max(0.02, 1.0 - p)
                st["sx"] = sxv
                st["sy"] = 1.0
                st["x"] = int(W * (1.0 - sxv) / 2.0)
            elif eid in ("cutvoke.anim.breathe", "cutvoke.anim.float",
                         "cutvoke.anim.sway", "cutvoke.anim.rock",
                         "cutvoke.anim.bounce", "cutvoke.anim.orbit",
                         "cutvoke.anim.heartbeat", "cutvoke.anim.blink"):
                # 循环：t 为绝对秒；period 秒一个周期
                period = float(params.get("period", 2.0))
                amp = float(params.get("amplitude", 0.03))
                phase = (t / period) * 2.0 * math.pi if period > 0 else 0.0
                if eid == "cutvoke.anim.breathe":
                    st["scale"] = 1.0 + amp * (1.0 + math.sin(phase))
                elif eid == "cutvoke.anim.float":
                    st["scale"] = 1.0 + 2.2 * amp
                    st["y"] = int(H * amp * math.sin(phase))
                elif eid == "cutvoke.anim.sway":
                    st["scale"] = 1.0 + 2.2 * amp
                    st["x"] = int(W * amp * math.sin(phase))
                elif eid == "cutvoke.anim.rock":
                    angle = min(12.0, amp * 100.0) * math.sin(phase)
                    theta = math.radians(abs(angle))
                    aspect = max(W / H, H / W)
                    st["rot"] = angle
                    st["scale"] = min(2.5, 1.0 / max(0.4,
                        math.cos(theta) - aspect * math.sin(theta)))
                elif eid == "cutvoke.anim.bounce":
                    lift = max(0.0, math.sin(phase))
                    st["scale"] = 1.0 + 2.2 * amp
                    st["y"] = -int(H * amp * lift)
                    if lift < 0.1:
                        st["sx"], st["sy"] = 1.0 + amp, 1.0 - amp * 0.6
                elif eid == "cutvoke.anim.orbit":
                    st["scale"] = 1.0 + 2.2 * amp
                    st["x"] = int(W * amp * math.cos(phase))
                    st["y"] = int(H * amp * math.sin(phase))
                elif eid == "cutvoke.anim.heartbeat":
                    cycle = (t / period) % 1.0 if period > 0 else 0.0
                    first = math.exp(-((cycle - 0.18) / 0.065) ** 2)
                    second = math.exp(-((cycle - 0.39) / 0.075) ** 2)
                    st["scale"] = 1.0 + amp * (first + 0.7 * second)
                else:  # blink: a short dark interval followed by a full return
                    cycle = (t / period) % 1.0 if period > 0 else 0.0
                    st["opacity"] = 1.0 - amp if cycle < 0.3 else 1.0
                # _scale_to_canvas anchors its enlarged image at top-left.
                # Center the extra pixels before applying intentional motion,
                # so a pan or rotation does not reveal the black base canvas.
                axis_x = st["scale"] * (st["sx"] if st["sx"] is not None else 1.0)
                axis_y = st["scale"] * (st["sy"] if st["sy"] is not None else 1.0)
                base_w = (min(W, H) if clip.role == "sticker" else W) * tr["scale"]
                base_h = (min(W, H) if clip.role == "sticker" else H) * tr["scale"]
                st["x"] -= round(base_w * (axis_x - 1.0) / 2.0)
                st["y"] -= round(base_h * (axis_y - 1.0) / 2.0)
            elif eid.startswith("cutvoke.anim.combo"):   # 组合动画 / 组合运镜预设（E04/D04）
                # primary/secondary 各自按 p 计算基础状态，然后合成：
                # opacity/scale/axis scale 相乘，x/y/rot 相加；翻牌保留横轴折叠。
                # D04：预设效果（cutvoke.anim.comboXxx）复用同一合成逻辑，
                # 仅把 primary/secondary 在 spec 里固化为预设值，内核零新增分支。
                def _combo_base(sel: str, dist: float, fscale: float,
                                toScale: float = 0.8) -> dict:
                    s = {"opacity": 1.0, "scale": 1.0, "sx": 1.0, "sy": 1.0,
                         "x": 0, "y": 0, "rot": 0.0}
                    if sel == "fadeIn":
                        s["opacity"] = p
                    elif sel == "zoomIn":
                        s["scale"] = fscale + (1.0 - fscale) * p
                    elif sel == "zoomOut":
                        # A combination entrance must finish at its normal
                        # transform. Pull back from a closer view instead of
                        # shrinking below 1 and snapping back at the lane end.
                        s["scale"] = 1.0 + (1.0 - toScale) * (1.0 - p)
                    elif sel == "slideIn":
                        s["x"] = int(-W * dist * (1.0 - p))
                    elif sel == "slideUp":
                        s["y"] = int(H * dist * (1.0 - p))
                    elif sel == "slideDown":
                        s["y"] = int(-H * dist * (1.0 - p))
                    elif sel == "slideRight":
                        s["x"] = int(W * dist * (1.0 - p))
                    elif sel == "rotateIn":
                        s["rot"] = float(params.get("fromAngle", -12.0)) * (1.0 - p)
                    elif sel == "flipIn":
                        s["sx"] = max(0.02, p)
                        s["x"] = int(W * (1.0 - s["sx"]) / 2.0)
                    return s
                pri = str(params.get("primary", "fadeIn"))
                sec = str(params.get("secondary", "slideUp"))
                dist = float(params.get("distance", 0.3))
                fscale = float(params.get("fromScale", 0.8))
                toScale = float(params.get("toScale", 0.8))
                a, b = (_combo_base(pri, dist, fscale, toScale),
                        _combo_base(sec, dist, fscale, toScale))
                st["opacity"] = a["opacity"] * b["opacity"]
                st["scale"] = a["scale"] * b["scale"]
                # Axis factors are relative to the uniform scale; the lane
                # composer below multiplies that uniform factor exactly once.
                st["sx"] = a["sx"] * b["sx"]
                st["sy"] = a["sy"] * b["sy"]
                st["x"] = a["x"] + b["x"]
                st["y"] = a["y"] + b["y"]
                st["rot"] = a["rot"] + b["rot"]
            return st

        # Each lane contributes its own transform. Segment boundaries are the
        # union of their frame-aligned sample points, so an exit animation no
        # longer hides an entrance or loop animation on the same clip.
        sub_labels: list[str] = []
        boundaries: list[tuple[float, float, dict]] = []

        # 帧对齐切分（1.5-B 教训）：子段边界必须落在整数帧上，否则 concat 会
        # 逐段向上取整到整帧，累加出额外时长（曾出现 5s 动画导出成 5.333s）。
        fps_frac = seq.fps.to_fraction()
        frame_dur = float(fps_frac.denominator) / float(fps_frac.numerator)

        def _frame_edges(total: float, target_parts: int) -> list[float]:
            """把 total 秒切成整数帧对齐的边界时间（升序，含 0 与末边界）。"""
            nf = max(1, int(round(total / frame_dur)))
            parts_n = max(1, min(target_parts, nf))
            idx = [0]
            for i in range(1, parts_n):
                v = int(round(nf * i / parts_n))
                if v > idx[-1]:
                    idx.append(v)
            if idx[-1] != nf:
                idx.append(nf)
            return [i * frame_dur for i in idx]

        rest = {"opacity": 1.0, "scale": 1.0, "sx": None, "sy": None,
                "rot": 0.0, "x": 0, "y": 0}
        total_frames = max(1, int(round(dur_f / frame_dur)))
        cut_frames: set[int] = {0, total_frames}
        keyframe_lanes = {
            param: clip.keyframes.get(param, [])
            for param in ("opacity", "x", "y", "scale", "rotation")
        }
        keyframe_lanes = {param: lane for param, lane in keyframe_lanes.items() if lane}
        sample_step = 1
        for keyframes in keyframe_lanes.values():
            frames = sorted({
                max(0, min(total_frames,
                           int(round(float(kf.time.to_fraction()) / frame_dur))))
                for kf in keyframes
            })
            cut_frames.update(frames)
            # A smooth animation must update its composed keyframes every
            # sequence frame, too; sparse samples visibly hold then jump.
            for left, right in zip(frames, frames[1:]):
                for frame in range(left + sample_step, right, sample_step):
                    cut_frames.add(frame)
        lanes: list[tuple[dict, str, float, float]] = []
        for animation in animations:
            slot = animation_slot(str(animation.get("effectId", ""))) or "入场"
            params = animation.get("params", {}) or {}
            duration = min(max(float(params.get("duration", 1.0)), 0.05), dur_f)
            if slot == "循环":
                edges = _frame_edges(dur_f, total_frames)
                start_frame = 0
            else:
                edges = _frame_edges(duration, max(1, int(round(duration / frame_dur))))
                animation_frames = min(total_frames, int(round(edges[-1] / frame_dur)))
                start_frame = total_frames - animation_frames if slot == "出场" else 0
            edge_frames = [min(total_frames, start_frame + int(round(edge / frame_dur)))
                           for edge in edges]
            cut_frames.update(edge_frames)
            lane_start = start_frame * frame_dur
            lane_duration = max(frame_dur, (edge_frames[-1] - start_frame) * frame_dur)
            lanes.append((animation, slot, lane_start, lane_duration))

        frame_points = sorted(cut_frames)
        for left, right in zip(frame_points, frame_points[1:]):
            if right <= left:
                continue
            a, b = left * frame_dur, right * frame_dur
            mid = (a + b) / 2.0
            state = dict(rest)
            axis_x = axis_y = 1.0
            has_axis = False
            position = dict(tr["position"])
            base_scale = tr["scale"]
            base_rotation = tr["rotation"]
            base_opacity = tr["opacity"]
            for animation, slot, lane_start, lane_duration in lanes:
                if slot == "循环":
                    contribution = _state_at(animation, mid)
                elif lane_start <= mid < lane_start + lane_duration:
                    contribution = _state_at(animation, (mid - lane_start) / lane_duration)
                else:
                    continue
                state["opacity"] *= contribution["opacity"]
                state["scale"] *= contribution["scale"]
                state["x"] += contribution["x"]
                state["y"] += contribution["y"]
                state["rot"] += contribution["rot"]
                if "reveal" in contribution:
                    state["reveal"] = contribution["reveal"]
                if contribution["sx"] is not None or contribution["sy"] is not None:
                    has_axis = True
                    axis_x *= contribution["sx"] if contribution["sx"] is not None else 1.0
                    axis_y *= contribution["sy"] if contribution["sy"] is not None else 1.0
            opacity_keyframes = keyframe_lanes.get("opacity", [])
            if opacity_keyframes:
                keyframed_opacity = kf_evaluate(
                    opacity_keyframes, Rational.from_float(mid))
                base_opacity = max(0.0, min(1.0, keyframed_opacity))
            for param, keyframes in keyframe_lanes.items():
                if param == "opacity":
                    continue
                value = kf_evaluate(keyframes, Rational.from_float(mid))
                if param == "x":
                    position["x"] = int(round(value))
                elif param == "y":
                    position["y"] = int(round(value))
                elif param == "scale":
                    base_scale = value
                elif param == "rotation":
                    base_rotation = value
            if has_axis:
                state["sx"] = state["scale"] * axis_x
                state["sy"] = state["scale"] * axis_y
            state["base_position"] = position
            state["base_scale"] = base_scale
            state["base_rotation"] = base_rotation
            state["base_opacity"] = base_opacity
            # Blink and zero-amplitude loops often have many identical frames.
            # Keep source timing continuous but share their geometry context.
            if boundaries and boundaries[-1][2] == state:
                boundaries[-1] = (boundaries[-1][0], b, state)
            else:
                boundaries.append((a, b, state))

        speed = clip.speed
        curve = clip.speed_curve
        src_dur = float(clip.consumed_source_duration.to_fraction())
        ss = float(clip.source_start.to_fraction())
        speed_f = float(speed.to_fraction())
        if curve is not None:
            speed_expr = curve.video_setpts()
        elif speed_f >= 0:
            speed_expr = f"setpts=PTS/{speed_f}"
        else:
            abs_f = float(_abs_rat(speed).to_fraction())
            speed_expr = f"reverse,setpts=PTS-STARTPTS,setpts=PTS/{abs_f}"
        if clip.freeze_at is not None:
            chain = self._frozen_clip_chain(
                clip, get_input(clip.asset_ref.source_path), fps_expr)
        else:
            chain = (f"[{get_input(clip.asset_ref.source_path)}:v]"
                     f"trim=start={ss:.6f}:duration={src_dur:.6f},"
                     f"setpts=PTS-STARTPTS,{speed_expr}")
            chain += self._slow_motion_interpolation_filter(clip, fps_expr)
        chain += self._preview_source_filter(clip) + self._canvas_source_filter(clip)
        animated_color = self._animated_color(clip, color)
        if animated_color:
            chain += "," + animated_color
        elif (color["brightness"] != 0.0 or color["contrast"] != 1.0
                or color["saturation"] != 1.0):
            chain += (f",eq=brightness={color['brightness']:.6f}:"
                      f"contrast={color['contrast']:.6f}:"
                      f"saturation={color['saturation']:.6f}")
        # Evaluate temporal filters once over the whole clip. Restarting tmix
        # for each one-frame geometry segment erased trails; splitting a shared
        # timeline stream also avoids duplicating source resampling per frame.
        full = f"animsrc{vseg[0]}"; vseg[0] += 1
        parts.append(f"{chain},fps={fps_expr},"
                     f"tpad=stop_mode=clone:stop_duration={dur_f:.6f},"
                     f"trim=end_frame={total_frames},setpts=N/({fps_expr})/TB[{full}]")
        if self._has_fx(clip):
            full = self._apply_fx(clip, full, parts, vseg)
        streaming = self._build_streaming_loop(
            clip, animations, full, W, H, frame_dur, total_frames, parts, vseg, fps_expr)
        if streaming is not None:
            return streaming
        if len(boundaries) > 64:
            # One filter context per sampled frame can exceed 12 GiB for an
            # eight-second 1080p layer. Keep the exact sampled state clock but
            # evaluate its geometry in a single fixed-size alpha-bearing stream.
            sampled_boundaries = boundaries
            window = getattr(self._render_ctx, "window", None)
            if window is not None:
                local_start = window[0] - float(clip.timeline_start.to_fraction())
                local_end = window[1] - float(clip.timeline_start.to_fraction())
                sampled_boundaries = [(a,b,state) for a,b,state in boundaries
                                      if b > local_start and a < local_end]
                if not sampled_boundaries:
                    sampled_boundaries = boundaries[-1:]
            return self._build_sampled_animation_stream(
                clip, seq, full, sampled_boundaries, W, H, frame_dur, total_frames,
                parts, vseg, fps_expr)
        segment_inputs = [f"animpart{vseg[0] + i}" for i in range(len(boundaries))]
        vseg[0] += len(segment_inputs)
        if len(segment_inputs) == 1:
            parts.append(f"[{full}]null[{segment_inputs[0]}]")
        else:
            outputs = "".join(f"[{label}]" for label in segment_inputs)
            parts.append(f"[{full}]split={len(segment_inputs)}{outputs}")
        for segment_input, (a, b, st) in zip(segment_inputs, boundaries):
            sub_local = b - a
            if sub_local <= 0:
                continue
            cur = f"va{vseg[0]}"; vseg[0] += 1
            first_frame = int(round(a / frame_dur))
            end_frame = int(round(b / frame_dur))
            parts.append(f"[{segment_input}]trim=start_frame={first_frame}:"
                         f"end_frame={end_frame},setpts=PTS-STARTPTS[{cur}]")
            sc = float(st.get("scale", 1.0))
            sx = st.get("sx")
            sy = st.get("sy")
            base_scale = float(st.get("base_scale", tr["scale"]))
            sx = base_scale * (sc if sx is None else float(sx))
            sy = base_scale * (sc if sy is None else float(sy))
            cur = self._scale_to_canvas(cur, W, H, sx, sy, parts, vseg,
                                        sticker=clip.role == "sticker")
            if "reveal" in st:
                direction, progress = st["reveal"]
                unit = min(W, H) if clip.role == "sticker" else None
                sw = max(1, round((unit or W) * sx))
                sh = max(1, round((unit or H) * sy))
                cw, ch, cx, cy = sw, sh, 0, 0
                if direction in ("up", "down"):
                    ch = max(1, min(sh, round(sh * progress)))
                    cy = sh - ch if direction == "up" else 0
                else:
                    cw = max(1, min(sw, round(sw * progress)))
                    cx = sw - cw if direction == "right" else 0
                revealed = f"vreveal{vseg[0]}"; vseg[0] += 1
                parts.append(
                    f"[{cur}]format=rgba,crop={cw}:{ch}:{cx}:{cy}:exact=1,"
                    f"pad={sw}:{sh}:{cx}:{cy}:color=black@0[{revealed}]")
                cur = revealed
            rot = float(st.get("base_rotation", tr["rotation"])) + float(st.get("rot", 0.0))
            if abs(rot) > 1e-6:
                nxt = f"vr{vseg[0]}"; vseg[0] += 1
                parts.append(
                    f"[{cur}]rotate={math.radians(rot):.6f}:"
                    f"ow=iw:oh=ih:c=none[{nxt}]")
                cur = nxt
            opacity = float(st.get("base_opacity", tr["opacity"])) * st["opacity"]
            if opacity < 1.0:
                nxt = f"va{vseg[0]}"; vseg[0] += 1
                parts.append(f"[{cur}]format=rgba,"
                             f"colorchannelmixer=aa={opacity:.6f}[{nxt}]")
                cur = nxt
            canvas = self._composite_on_canvas(
                cur, clip, seq, parts, vseg, fps_expr, W, H,
                {"x": st["x"] + int(st.get("base_position", tr["position"])["x"]),
                 "y": st["y"] + int(st.get("base_position", tr["position"])["y"])},
                sub_local)
            # A one-frame subsegment can lose its last frame when color/overlay
            # rounds a decimal duration down. Give concat an exact frame count
            # for every segment, including adjacent animation lane boundaries.
            exact = f"cfix{vseg[0]}"; vseg[0] += 1
            frame_count = int(round(sub_local / frame_dur))
            timestamps = (f"(N+1)/({fps_expr})/TB" if frame_count == 1
                          else f"N/({fps_expr})/TB")
            parts.append(
                f"[{canvas}]tpad=stop_mode=clone:stop_duration={sub_local:.6f},"
                f"trim=end_frame={frame_count},"
                f"setpts={timestamps}[{exact}]")
            sub_labels.append(exact)

        if len(sub_labels) == 1:
            out = f"canim{vseg[0]}"; vseg[0] += 1
            parts.append(f"[{sub_labels[0]}]setpts=PTS-STARTPTS[{out}]")
            return out
        ins = "".join(f"[{s}]" for s in sub_labels)
        out = f"canim{vseg[0]}"; vseg[0] += 1
        parts.append(f"{ins}concat=n={len(sub_labels)}:v=1:a=0,"
                     f"tpad=stop_mode=clone:stop_duration={frame_dur:.9f},"
                     f"trim=end_frame={total_frames},"
                     f"setpts=N/({fps_expr})/TB[{out}]")
        return out

    @staticmethod
    def _sampled_state_expression(samples: list[tuple[int, float]], variable: str) -> str:
        """A balanced decision tree selects a held frame state in O(log n).

        Adjacent equal values collapse before building the expression. Unlike
        a nested linear lookup, long clips do not exceed FFmpeg's expression
        nesting limit or require scanning every preceding frame per pixel.
        """
        compact: list[tuple[int, float]] = []
        for first, value in samples:
            if not compact or value != compact[-1][1]:
                compact.append((first, value))

        def build(items: list[tuple[int, float]]) -> str:
            if len(items) == 1:
                return f"{items[0][1]:.9f}"
            middle = len(items) // 2
            return (f"if(lt({variable},{items[middle][0]}),"
                    f"{build(items[:middle])},{build(items[middle:])})")

        return build(compact)

    def _build_sampled_animation_stream(
            self, clip: Clip, seq: Sequence, source: str,
            boundaries: list[tuple[float, float, dict]], W: int, H: int,
            frame_dur: float, total_frames: int, parts: list[str],
            vseg: list[int], fps_expr: str) -> str:
        """Retain composed animation/FX/alpha semantics with bounded contexts.

        Source trim, speed, interpolation and temporal FX have already run once
        upstream. Only geometry and opacity are sampled here. Transparent source
        padding prevents perspective's edge clamp from stretching source pixels
        into uncovered canvas. A destination mask retains rotate's original
        fixed scaled-image crop; reveal uses an upstream source alpha mask.
        """
        coordinates = [[] for _ in range(8)]
        rectangle = [[] for _ in range(4)]
        opacity_samples = []
        reveal_samples = [[] for _ in range(4)]
        needs_rectangle = False
        needs_opacity = False
        needs_reveal = False
        unit = min(W, H) if clip.role == "sticker" else None
        for a, _b, state in boundaries:
            frame = int(round(a / frame_dur))
            uniform = float(state["scale"])
            base = float(state["base_scale"])
            sx = base * (uniform if state["sx"] is None else float(state["sx"]))
            sy = base * (uniform if state["sy"] is None else float(state["sy"]))
            sw, sh = max(1, round((unit or W) * sx)), max(1, round((unit or H) * sy))
            x = int(state["base_position"]["x"]) + state["x"]
            y = int(state["base_position"]["y"]) + state["y"]
            rotation = float(state["base_rotation"]) + float(state["rot"])
            angle = float(f"{math.radians(rotation):.6f}") if abs(rotation) > 1e-6 else 0.0
            cosine, sine = math.cos(angle), math.sin(angle)
            for index, (px, py) in enumerate(((-2, -2), (W + 2, -2),
                                              (-2, H + 2), (W + 2, H + 2))):
                dx, dy = px * sw / W - sw / 2, py * sh / H - sh / 2
                coordinates[index * 2].append((frame, x + sw / 2 + dx * cosine - dy * sine))
                coordinates[index * 2 + 1].append((frame, y + sh / 2 + dx * sine + dy * cosine))
            for lane, value in zip(rectangle, (x, x + sw - 1, y, y + sh - 1)):
                lane.append((frame, value))
            needs_rectangle |= x > 0 or y > 0 or x + sw < W or y + sh < H
            opacity = float(state["base_opacity"]) * float(state["opacity"])
            opacity_samples.append((frame, opacity))
            needs_opacity |= opacity < 1
            left, right, top, bottom = 0.0, 1.0, 0.0, 1.0
            if "reveal" in state:
                direction, progress = state["reveal"]
                needs_reveal = True
                if direction in ("up", "down"):
                    fraction = max(1, min(sh, round(sh * progress))) / sh
                    if direction == "up": top = 1 - fraction
                    else: bottom = fraction
                else:
                    fraction = max(1, min(sw, round(sw * progress))) / sw
                    if direction == "right": left = 1 - fraction
                    else: right = fraction
            for lane, value in zip(reveal_samples, (left, right, top, bottom)):
                lane.append((frame, value))
        cur = f"astreamsrc{vseg[0]}"; vseg[0] += 1
        parts.append(f"[{source}]scale={W}:{H},setsar=1,format=yuva444p,"
                     f"pad={W + 4}:{H + 4}:2:2:color=black@0[{cur}]")
        if needs_reveal:
            bounds = [self._sampled_state_expression(lane, "N") for lane in reveal_samples]
            mask = (f"gte(X-2,({bounds[0]})*{W})*lt(X-2,({bounds[1]})*{W})*"
                    f"gte(Y-2,({bounds[2]})*{H})*lt(Y-2,({bounds[3]})*{H})")
            nxt = f"astreveal{vseg[0]}"; vseg[0] += 1
            parts.append(f"[{cur}]geq=lum='p(X,Y)':cb='p(X,Y)':cr='p(X,Y)':"
                         f"a='alpha(X,Y)*({mask})'[{nxt}]")
            cur = nxt
        geometry = [self._sampled_state_expression(lane, "in") for lane in coordinates]
        options = ":".join(f"{'x' if i % 2 == 0 else 'y'}{i // 2}='{value}'"
                           for i, value in enumerate(geometry))
        nxt = f"astgeom{vseg[0]}"; vseg[0] += 1
        parts.append(f"[{cur}]perspective={options}:sense=destination:eval=frame:"
                     f"interpolation=linear,crop={W}:{H}:0:0[{nxt}]")
        cur = nxt
        if needs_rectangle or needs_opacity:
            mask = self._sampled_state_expression(opacity_samples, "N")
            if needs_rectangle:
                bounds = [self._sampled_state_expression(lane, "N") for lane in rectangle]
                mask += (f"*between(X,({bounds[0]}),({bounds[1]}))*"
                         f"between(Y,({bounds[2]}),({bounds[3]}))")
            nxt = f"astalpha{vseg[0]}"; vseg[0] += 1
            parts.append(f"[{cur}]geq=lum='p(X,Y)':cb='p(X,Y)':cr='p(X,Y)':"
                         f"a='alpha(X,Y)*({mask})'[{nxt}]")
            cur = nxt
        out = f"astout{vseg[0]}"; vseg[0] += 1
        parts.append(f"[{cur}]trim=end_frame={total_frames},"
                     f"setpts=N/({fps_expr})/TB[{out}]")
        return out

    def _build_streaming_loop(self, clip: Clip, animations: list[dict],
                              source: str, W: int, H: int, frame_dur: float,
                              total_frames: int, parts: list[str],
                              vseg: list[int], fps_expr: str) -> Optional[str]:
        """Use one fixed-size affine filter for an ordinary opaque loop.

        Dynamic scale dimensions followed by rotate/overlay crash some FFmpeg
        builds. Perspective keeps every frame W x H while sampling the same
        loop trajectory at sequence-frame midpoints. Its one-pass interpolation
        can differ slightly from the segmented scale-then-rotate path.

        Preserve the general path for transparent/sticker/FX layers, keyframes
        and combined lanes; those require clipping/alpha semantics beyond this
        full-frame affine shortcut. Source speed and color remain upstream.
        """
        geometric = {"breathe", "float", "sway", "rock", "bounce", "orbit", "heartbeat"}
        if (len(animations) != 1 or clip.role == "sticker" or clip.keyframes
                or self._has_fx(clip) or getattr(self._render_ctx, "alpha", False)):
            return None
        animation = animations[0]
        effect_id = str(animation.get("effectId", ""))
        key = effect_id.removeprefix("cutvoke.anim.")
        if not effect_id.startswith("cutvoke.anim.") or key not in geometric:
            return None
        transform = clip.transform()
        if (transform["scale"] != 1.0 or transform["rotation"] != 0.0
                or transform["opacity"] != 1.0
                or transform["position"]["x"] != 0
                or transform["position"]["y"] != 0):
            return None
        pixel_format = self.probe_media(clip.asset_ref.source_path).get("pix_fmt")
        # A paletted image may carry palette alpha even though its format name
        # contains no alpha marker. Keep unknown/transparent sources on the
        # compositing path so transparent pixels expose lower tracks correctly.
        if (not pixel_format or pixel_format == "pal8"
                or _pix_fmt_has_alpha(pixel_format)):
            return None
        params = animation.get("params", {}) or {}
        amplitude = float(params.get("amplitude", 0.03))
        period = float(params.get("period", 2.0))
        local_time = f"((in+0.5)*{frame_dur:.12f})"
        phase = f"({local_time}*2*PI/{period:.12f})" if period > 0 else "0"
        sine = f"sin({phase})"
        constant_scale = f"{1 + 2.2 * amplitude:.12f}"
        sx = sy = "1"
        dx = dy = "0"
        angle = "0"
        if key == "breathe":
            sx = sy = f"(1+{amplitude:.12f}*(1+{sine}))"
        elif key in {"float", "sway", "orbit"}:
            sx = sy = constant_scale
            if key in {"sway", "orbit"}:
                wave = f"cos({phase})" if key == "orbit" else sine
                dx = f"trunc(W*{amplitude:.12f}*{wave})"
            if key in {"float", "orbit"}:
                dy = f"trunc(H*{amplitude:.12f}*{sine})"
        elif key == "rock":
            raw_angle = f"({min(12.0, amplitude * 100.0):.12f}*PI/180*{sine})"
            angle = f"(round({raw_angle}*1000000)/1000000)"
            sx = sy = (f"min(2.5,1/max(0.4,cos(abs({raw_angle}))-"
                       f"{max(W / H, H / W):.12f}*sin(abs({raw_angle}))))")
        elif key == "bounce":
            lift = f"max(0,{sine})"
            sx = f"({constant_scale}*if(lt({lift},0.1),{1 + amplitude:.12f},1))"
            sy = f"({constant_scale}*if(lt({lift},0.1),{1 - amplitude * .6:.12f},1))"
            dy = f"(-trunc(H*{amplitude:.12f}*{lift}))"
        elif key == "heartbeat":
            cycle = f"mod({local_time}/{period:.12f},1)" if period > 0 else "0"
            first = f"exp(-pow(({cycle}-0.18)/0.065,2))"
            second = f"exp(-pow(({cycle}-0.39)/0.075,2))"
            sx = sy = f"(1+{amplitude:.12f}*({first}+0.7*{second}))"
        coordinates = []
        for index, (x, y) in enumerate((("0", "0"), ("W", "0"),
                                        ("0", "H"), ("W", "H"))):
            rx, ry = f"(({x})-W/2-({dx}))", f"(({y})-H/2-({dy}))"
            xexpr = f"W/2+({rx}*cos({angle})+{ry}*sin({angle}))/({sx})"
            yexpr = f"H/2+(-{rx}*sin({angle})+{ry}*cos({angle}))/({sy})"
            coordinates.extend((f"x{index}='{xexpr}'", f"y{index}='{yexpr}'"))
        output = f"loopstream{vseg[0]}"; vseg[0] += 1
        parts.append(
            f"[{source}]scale={W}:{H},setsar=1,perspective="
            + ":".join(coordinates)
            + f":sense=source:eval=frame:interpolation=linear,"
              f"trim=end_frame={total_frames},setpts=N/({fps_expr})/TB[{output}]")
        return output

    def _frozen_clip_chain(self, clip: Clip, vi: int, fps_expr: str) -> str:
        """Select exactly one source frame and keep the sequence frame clock.

        Canvas, masked and animated clips must freeze before their effects,
        just like plain clips. A fixed 33ms source window can contain several
        frames at high source rates; looping one decoded frame also avoids
        relying on frame-rate metadata surviving setpts on every FFmpeg build.
        """
        at = float(clip.freeze_at.to_fraction())
        duration = float(clip.duration.to_fraction())
        return (f"[{vi}:v]trim=start={at:.9f},trim=end_frame=1,"
                f"loop=loop=-1:size=1:start=0,"
                f"setpts=N/(({fps_expr})*TB),fps={fps_expr},"
                f"trim=duration={duration:.9f}")

    def _trim_speed_color(self, clip: Clip, get_input, parts: list[str],
                          vseg: list[int], color: dict, fps_expr: str) -> str:
        """生成 trim + 变速（+ eq 调色）的源尺寸分段，返回 label。"""
        vi = get_input(clip.asset_ref.source_path)
        seg = f"vseg{vseg[0]}"
        vseg[0] += 1
        speed = clip.speed
        ss = float(clip.source_start.to_fraction())
        src_dur = float((clip.duration * _abs_rat(speed)).to_fraction())
        speed_f = float(speed.to_fraction())
        if clip.speed_curve is not None:
            speed_expr = clip.speed_curve.video_setpts()
        elif speed_f >= 0:
            speed_expr = f"setpts=PTS/{speed_f}"
        else:
            abs_f = float(_abs_rat(speed).to_fraction())
            speed_expr = (f"reverse,setpts=PTS-STARTPTS,"
                          f"setpts=PTS/{abs_f}")
        if clip.freeze_at is not None:
            chain = self._frozen_clip_chain(clip, vi, fps_expr)
        else:
            chain = (f"[{vi}:v]trim=start={ss}:duration={src_dur},"
                     f"setpts=PTS-STARTPTS,{speed_expr}")
            chain += self._slow_motion_interpolation_filter(clip, fps_expr)
        chain += self._preview_source_filter(clip) + self._canvas_source_filter(clip)
        animated_color = self._animated_color(clip, color)
        if animated_color:
            chain += "," + animated_color
        elif (color["brightness"] != 0.0 or color["contrast"] != 1.0
                or color["saturation"] != 1.0):
            chain += (f",eq=brightness={color['brightness']:.6f}:"
                      f"contrast={color['contrast']:.6f}:"
                      f"saturation={color['saturation']:.6f}")
        # J01 效果栈：fx 画面滤镜按效果顺序应用（可能多流，走显式建图）
        if self._has_fx(clip):
            mid = f"vfx{vseg[0]}"
            vseg[0] += 1
            parts.append(f"{chain}[{mid}]")
            return self._apply_fx(clip, mid, parts, vseg)
        parts.append(f"{chain}[{seg}]")
        return seg

    def _slow_motion_interpolation_filter(self, clip: Clip, fps_expr: str) -> str:
        mode = clip.frame_interpolation
        if mode == "none":
            return ""
        if mode != "motion":
            raise RenderError(f"unknown frame interpolation mode {mode!r}")
        if clip.freeze_at is not None or not clip.has_slow_motion:
            raise RenderError(
                "motion frame interpolation requires a non-frozen clip with a speed interval below 1x")
        if not _has_minterpolate(self.ffmpeg):
            raise RenderError(
                "this FFmpeg build does not include minterpolate; choose 'none' or configure an FFmpeg build with that filter")
        return (f",minterpolate=fps={fps_expr}:mi_mode=mci:mc_mode=aobmc:"
                "me_mode=bidir:vsbmc=1")

    # ------------------------------------------------------------------
    # J01 效果栈渲染：fx 画面滤镜（顺序 = 效果栈顺序，旁路 = enabled=False）
    # ------------------------------------------------------------------
    @staticmethod
    def _enabled_fx(clip: Clip) -> list[dict]:
        """片段上启用的**视频** fx 效果实例，按效果栈顺序返回。

        音频特效（filter 键命中 _AUDIO_FX_STEPS，如 loudnorm）不在此列——
        它们走音频编译链（见 _enabled_audio_fx），不应被当作字面滤镜误接到视频流。
        """
        out: list[dict] = []
        reg = None
        for e in getattr(clip, "effects", []) or []:
            if not _is_effect_enabled(e):
                continue
            eid = e.get("effectId", "")
            if not isinstance(eid, str):
                continue
            if reg is None:
                try:
                    reg = default_registry()
                except Exception:
                    reg = None
            spec = reg.find(eid) if reg is not None else None
            if not eid.startswith("cutvoke.fx.") and not (spec and spec.category == "fx"):
                continue
            key = (str(spec.implementation.get("filter", ""))
                   if spec is not None
                   else eid.rsplit(".", 1)[-1])
            if key in _AUDIO_FX_STEPS:
                continue
            out.append(e)
        return out

    @staticmethod
    def _enabled_audio_fx(clip: Clip) -> list[dict]:
        """片段上启用的**音频** fx 效果实例（filter 键命中 _AUDIO_FX_STEPS）。

        与 _enabled_fx 对应：音频特效（如 loudnorm）在当前渲染管线里不经视频
        特效链，而是在音频编译链逐段应用，因此单独抽取并过滤到音频滤镜集合。
        """
        out: list[dict] = []
        reg = None
        for e in getattr(clip, "effects", []) or []:
            if not _is_effect_enabled(e):
                continue
            eid = e.get("effectId", "")
            if not (isinstance(eid, str) and eid.startswith("cutvoke.fx.")):
                continue
            if reg is None:
                try:
                    reg = default_registry()
                except Exception:
                    reg = None
            spec = reg.find(eid) if reg is not None else None
            key = (str(spec.implementation.get("filter", ""))
                   if spec is not None
                   else eid.rsplit(".", 1)[-1])
            if key in _AUDIO_FX_STEPS:
                out.append(e)
        return out

    def _has_fx(self, clip: Clip) -> bool:
        return bool(self._enabled_fx(clip))

    def _animated_color(self, clip: Clip, color: dict) -> str:
        from cutvoke.domain.keyframes import effect_instance_id
        selected = [(index,e) for index,e in enumerate(clip.effects) if e.get("effectId") == "cutvoke.color"]
        if not selected:
            return ""
        index,effect = selected[-1]
        prefix = f"effect:{effect_instance_id(effect,index)}:"
        if not any(clip.keyframes.get(prefix+p) for p in ("brightness","contrast","saturation")):
            return ""
        offset = getattr(self._render_ctx,'clip_clocks',{}).get(clip.id,(0,None))[0]
        time_expr = f"(t+{offset:.12f})"
        return "eq=" + ":".join(f"{name}='{kf_expression(clip.keyframes.get(prefix+name,[]),time_expr,color[name])}'"
                                for name in ("brightness","contrast","saturation")) + ":eval=frame"

    def _animated_blur(self, clip: Clip, effect: dict, params: dict, name: str) -> str:
        from cutvoke.domain.keyframes import effect_instance_id
        index = next(i for i,e in enumerate(clip.effects) if e is effect)
        lane = clip.keyframes.get(f"effect:{effect_instance_id(effect,index)}:sigma",[])
        if not lane or effect.get("effectId") != "cutvoke.fx.blur":
            return ""
        fps = getattr(self._render_ctx,"sequence_fps",Fraction(30))
        offset = getattr(self._render_ctx,'clip_clocks',{}).get(clip.id,(0,None))[0]
        count = max(1,math.ceil(clip.duration.to_fraction()*fps))
        if count > 100_000:
            raise RenderError("animated blur is limited to 100000 frames per clip")
        target = f"gblur@{name}"
        commands=[]
        previous=None
        initial=5.
        for n in range(count):
            time = Rational.of(n*fps.denominator,fps.numerator)+Rational.from_float(offset)
            sigma = kf_evaluate(lane,time)*params.get("_previewScale",1.)
            if n == 0: initial = sigma
            if sigma != previous:
                timestamp=max(offset,offset+(n-.25)/float(fps))
                commands.append(f"{timestamp:.9f} {target} sigma {sigma:.12g}")
                previous=sigma
        return f"fps={fps.numerator}/{fps.denominator},sendcmd=c='{';'.join(commands)}',{target}=sigma={initial:.12g}"

    def _apply_fx(self, clip: Clip, cur: str, parts: list[str],
                  vseg: list[int], time_offset: float = 0.0) -> str:
        """按效果栈顺序把片段上的 fx 滤镜接到标签 cur 上，返回末标签。"""
        gpu=getattr(self._render_ctx,'gpu',None)
        candidate=(getattr(self._render_ctx,'bounded_origin',None) is not None or
                   any(self._gpu_candidate(effect) for effect in self._enabled_fx(clip)))
        self._render_ctx.pixel_gpu=(gpu if clip.id in getattr(self._render_ctx,'clip_clocks',{})
                                    else self._gpu_clip(clip,gpu.fps)) if gpu and candidate else None
        local_offset,original_duration=getattr(self._render_ctx,'clip_clocks',{}).get(clip.id,(0.,clip.duration))
        if self._render_ctx.pixel_gpu is not None:
            self._render_ctx.pixel_gpu.time_offset=local_offset
        if local_offset:
            shifted=f'fxclock{vseg[0]}';vseg[0]+=1
            parts.append(f'[{cur}]setpts=PTS+{local_offset:.12f}/TB[{shifted}]')
            cur=shifted
        self._render_ctx.fx_offset=local_offset
        for i, e in enumerate(self._enabled_fx(clip)):
            key, params = self._fx_key_params(e)
            def apply_one(source):
                animated = self._animated_blur(clip,e,params,f"ak{vseg[0]}")
                if not animated:
                    return self._fx_one(key,params,source,parts,vseg,i)
                out = f"akout{vseg[0]}"; vseg[0]+=1
                parts.append(f"[{source}]{animated}[{out}]")
                return out
            effect_range = e.get("range")
            if effect_range is None:
                cur = apply_one(cur)
                continue
            try:
                start = Rational.from_json(**effect_range["start"])
                end = Rational.from_json(**effect_range["end"])
            except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
                raise RenderError(f"invalid temporal range for {e.get('effectId')}") from exc
            if start < Rational.of(0) or end > original_duration or start >= end:
                raise RenderError(f"temporal range is outside clip {clip.id}")
            if start == Rational.of(0) and end == original_duration:
                cur = apply_one(cur)
                continue

            # Keep an unmodified branch beside the effected branch, then switch
            # between them at the clip-local boundaries. This gives previews and
            # exports the same frame-accurate interval without altering clip timing.
            base = f"fxrange_base{vseg[0]}"
            branch = f"fxrange_src{vseg[0]}"
            parts.append(f"[{cur}]split=2[{base}][{branch}]")
            processed = apply_one(branch)
            if key == "crop":
                # crop changes frame dimensions. Scale only its active branch
                # back to the unchanged branch dimensions before frame blending.
                blend_base = f"fxrange_blend_base{vseg[0]}"
                scale_ref = f"fxrange_scale_ref{vseg[0]}"
                scaled = f"fxrange_crop_scaled{vseg[0]}"
                parts.append(f"[{base}]split=2[{blend_base}][{scale_ref}]")
                unused = f"fxrange_scale_unused{vseg[0]}"
                # scale's second reference input was added after FFmpeg 6.1.
                # scale2ref supports the same geometry on our oldest CI renderer.
                parts.append(
                    f"[{processed}][{scale_ref}]scale2ref=w=iw:h=ih[{scaled}][{unused}]")
                parts.append(f"[{unused}]nullsink")
                base, processed = blend_base, scaled
            out = f"fxrange_out{vseg[0]}"
            vseg[0] += 1
            start_s = float(start.to_fraction())
            end_s = float(end.to_fraction())
            local_time = f"(T+{time_offset:.12f})"
            expr = (f"if(gte({local_time},{start_s:.12f})*"
                    f"lt({local_time},{end_s:.12f}),B,A)")
            parts.append(f"[{base}][{processed}]blend=all_expr='{expr}':shortest=1[{out}]")
            cur = out
        if local_offset:
            shifted=f'fxreset{vseg[0]}';vseg[0]+=1
            parts.append(f'[{cur}]setpts=PTS-STARTPTS[{shifted}]')
            cur=shifted
        return cur

    def _fx_key_params(self, e: dict) -> tuple[str, dict]:
        """从效果实例解析出（滤镜键, 参数）。

        优先用注册表的 implementation.filter（内置与外部 manifest 同一机制）；
        注册表不可用时回退到 effectId 尾段，保证老工程仍可渲染。
        """
        eid = e.get("effectId", "")
        params = e.get("params", {}) or {}
        spec = None
        try:
            reg = default_registry()
            spec = reg.find(eid)
        except Exception:
            spec = None
        if spec is not None:
            key = str(spec.implementation.get("filter", "")) or eid.rsplit(".", 1)[-1]
        else:
            key = eid.rsplit(".", 1)[-1]
        return key, params

    def _fx_one(self, key: str, params: dict, cur: str, parts: list[str],
                vseg: list[int], idx: int) -> str:
        """应用单个滤镜：多流滤镜显式建图，其余内联续接。"""
        gpu=getattr(self._render_ctx,'pixel_gpu',None)
        if (key=='mask' and gpu is not None and (params.get('shape') in {'freehand','ink'} or getattr(self._render_ctx,'bounded_origin',None) is not None)
                and params.get('shape')!='text' and not params.get('_renderedInkMattePath')
                and (params.get('motion','none')!='none' or getattr(self._render_ctx,'bounded_origin',None) is not None)):
            formula=_fx_mask_expr(params).split("a='alpha(X,Y)*(",1)[1][:-2]
            out=f'gpu_mask{vseg[0]}'
            vseg[0]+=1
            parts.append(f'[{cur}]{gpu.matte(formula)}[{out}]')
            return out
        if key == 'mask' and params.get('_renderedInkMattePath'):
            path = str(params['_renderedInkMattePath']).replace('\\', '/').replace(':', '\\:').replace("'", "\\'")
            stem = f'ink{vseg[0]}'
            vseg[0] += 1
            time_base = params['_renderedInkMatteTimeBase']
            offset=getattr(self._render_ctx,'fx_offset',0.)
            clock=f'N+{offset:.12f}/TB'
            parts.extend([
                f"[{cur}]format=rgba,split=2[{stem}_rgb][{stem}_src]",
                f"[{stem}_src]alphaextract,format=gray16le[{stem}_alpha]",
                f"movie=filename='{path}':dec_threads=2,settb=expr={time_base},setpts={clock}[{stem}_matte]",
                # Disable swscale's version-dependent dithering when reducing
                # coverage to eight bits; it can add two alpha units on FFmpeg 6.
                f"[{stem}_alpha][{stem}_matte]blend=all_mode=multiply:shortest=0:repeatlast=1,"
                f"scale=flags=neighbor:sws_dither=none,format=gray[{stem}_coverage]",
                f"[{stem}_rgb][{stem}_coverage]alphamerge,format=gbrap[{stem}_out]",
            ])
            return f'{stem}_out'
        if key in ("vlogSubjectLock", "vlogPunchIn"):
            from cutvoke.rendering.vlog_fx import geometry_filter
            out = f"vlogfx{vseg[0]}"
            vseg[0] += 1
            parts.append(f"[{cur}]{geometry_filter(key, params)}[{out}]")
            return out
        if key == "travelMotionBlur":
            from cutvoke.rendering.travel_fx import build_motion_blur
            return build_motion_blur(params, cur, parts, vseg)
        if (key == "mask" and params.get("shape") == "freehand"
                and params.get("pathMode") == "smooth" and len(params.get("points", [])) >= 8
                and params.get("motion", "none") == "none"):
            from cutvoke.rendering.travel_fx import build_cached_smooth_mask
            return build_cached_smooth_mask(params, cur, parts, vseg)
        if key == 'shatter':
            if gpu is not None:
                width = getattr(self._render_ctx, 'canvas_width', 1920)
                height = getattr(self._render_ctx, 'canvas_height', 1080)
                out = f'gpu_shatter{vseg[0]}'
                vseg[0] += 1
                parts.append(f'[{cur}]fps={gpu.fps},scale={width}:{height},'
                             f'{gpu.fragments(params,width,height)}[{out}]')
                return out
            from cutvoke.rendering.vfx_fragments import build
            return build(params,cur,parts,vseg,
                         getattr(self._render_ctx,'canvas_width',1920),
                         getattr(self._render_ctx,'canvas_height',1080))
        if key in ('lightWeave','opticalField'):
            path=params.get('_renderedLightPath')
            if not path:
                # Keep the single-effect compiler usable for catalog thumbnails
                # and smoke previews outside a complete project snapshot.
                from cutvoke.rendering.vfx_light import cached_source
                extra={}
                if key=='opticalField':
                    from cutvoke.rendering.vfx_optics import frame as optical_frame, VERSION as optical_version
                    extra=dict(renderer=optical_frame,kind=f'optics-{optical_version}')
                path=cached_source(params,getattr(self._render_ctx,'canvas_width',1920),
                    getattr(self._render_ctx,'canvas_height',1080),Fraction(30,1),
                    float(params.get('start',0))+float(params.get('duration',1.7))+.35,self.ffmpeg,**extra)
            escaped=str(path).replace('\\','/').replace(':','\\:').replace("'", "\\'")
            stem=f'lightweave{vseg[0]}';vseg[0]+=1
            offset=getattr(self._render_ctx,'fx_offset',0.)
            parts.append(f"movie=filename='{escaped}':dec_threads=2,setpts=PTS-STARTPTS+{offset:.12f}/TB[{stem}_light]")
            parts.append(f'[{cur}][{stem}_light]overlay=shortest=1:eof_action=pass:format=auto[{stem}_out]')
            return f'{stem}_out'
        if key == "person_halo":
            return self._fx_person_halo(params, cur, parts, vseg, idx)
        if key in _FX_MULTI:
            return self._fx_glow(params, cur, parts, vseg, idx)
        if key == "mask" and str(params.get("shape", "rect")) == "text":
            return self._fx_text_mask(params, cur, parts, vseg)
        fn = _FX_STEPS.get(key)
        expr = fn(params) if fn is not None else key  # 表外键按字面滤镜表达式
        if not expr:
            return cur
        nxt = f"vfx{idx}_{vseg[0]}"
        vseg[0] += 1
        parts.append(f"[{cur}]{expr}[{nxt}]")
        return nxt

    def _fx_text_mask(self, params: dict, cur: str,
                      parts: list[str], vseg: list[int]) -> str:
        """Render literal text to a grayscale matte and multiply source alpha."""
        content = str(params.get("content", "文字"))
        if not content.strip():
            raise RenderError("文字蒙版内容不能为空")
        if len(content) > 240:
            raise RenderError("文字蒙版最多 240 个字符")
        font_size = max(0.02, min(0.5, float(params.get("fontSize", 0.16))))
        x = max(0.0, min(1.0, float(params.get("x", 0.15))))
        y = max(0.0, min(1.0, float(params.get("y", 0.15))))
        feather = max(0.0, min(0.5, float(params.get("feather", 0.05))))
        invert = bool(params.get("invert", False))
        try:
            # FreeType versions disagree on a variable font's default weight.
            # Use the same explicit Regular instance for mask glyphs everywhere.
            _family, font_path = _caption_font_instance(str(params.get("fontFamily", "Noto Sans SC")), bool(params.get("bold", False)))
        except CaptionFontError as error:
            raise RenderError(f"无法准备文字蒙版字体: {error}") from error
        font_path = font_path.replace("\\", "/").replace(":", "\\:")
        font_path = font_path.replace("'", "\\'")
        literal = _escape_drawtext(content)

        stem = f"masktext{vseg[0]}"
        vseg[0] += 1
        source, matte, alpha_source = f"{stem}_src", f"{stem}_matte", f"{stem}_alpha_src"
        original_alpha, mask, combined_alpha, output = (
            f"{stem}_original_alpha", f"{stem}_mask", f"{stem}_combined", f"{stem}_out")
        parts.append(f"[{cur}]format=rgba,split=3[{source}][{matte}][{alpha_source}]")
        parts.append(f"[{alpha_source}]alphaextract[{original_alpha}]")

        base_luma, glyph_color = ("255", "black") if invert else ("0", "white")
        drawtext = (
            f"drawtext=fontfile='{font_path}':text='{literal}':expansion=none:"
            f"fontsize=h*{font_size:.6f}:fontcolor={glyph_color}:"
            f"x=w*{x:.6f}:y=h*{y:.6f}")
        if params.get('tileText',False):
            spacing=float(params.get('tileSpacing',1.25))
            drift=float(params.get('tileDrift',0))
            # An overscanned text field stays filled while the lines drift.
            repetitions=max(2,min(32,math.ceil(5/(max(1,len(content))*font_size))))
            repeated=_escape_drawtext((content+'   ')*repetitions)
            row_count=min(64,math.ceil(1.25/(font_size*spacing))+1)
            drawtext=','.join(
                f"drawtext=fontfile='{font_path}':text='{repeated}':expansion=none:"
                f"fontsize=h*{font_size:.6f}:fontcolor={glyph_color}:"
                f"x=-w*0.45+{row%2}*h*{font_size*3:.6f}+t*w*{drift:.6f}:"
                f"y=h*{row*font_size*spacing-.10:.9f}"
                for row in range(row_count))
        sigma = font_size * feather * float(getattr(self._render_ctx, "canvas_height", 1080))
        blur = f",gblur=sigma={max(0.1, sigma):.3f}:steps=2" if feather > 0 else ""
        initial, mx, my = mask_coordinates(params)
        motion = ""
        if initial:
            gpu=getattr(self._render_ctx,'pixel_gpu',None)
            if gpu is not None:
                motion=','+gpu.warp_matte(initial,mx,my,base_luma)
            else:
                sample = (f"if(gte({mx}\\,0)*lt({mx}\\,W)*gte({my}\\,0)*lt({my}\\,H)"
                          f"\\,lum({mx}\\,{my})\\,{base_luma})")
                motion = f",geq=lum='{initial}{sample}'"
        # FFmpeg 6 drawtext uses limited-range gray (white=235, black=16).
        # Draw in RGB first so matte coverage spans 0..255 on every version.
        parts.append(
            f"[{matte}]format=rgb24,lutrgb=r={base_luma}:g={base_luma}:b={base_luma},"
            f"{drawtext},format=gray{blur}{motion}[{mask}]")
        parts.append(
            f"[{original_alpha}][{mask}]blend=all_expr='A*B/255':shortest=1"
            f"[{combined_alpha}]")
        parts.append(f"[{source}][{combined_alpha}]alphamerge[{output}]")
        return output

    def _fx_glow(self, params: dict, cur: str, parts: list[str],
                 vseg: list[int], idx: int) -> str:
        """Screen-blend RGB light without screening YUV chroma or source alpha."""
        intensity = float(params.get("intensity", 1.0))
        a = f"vga{idx}_{vseg[0]}"
        vseg[0] += 1
        b = f"vgb{idx}_{vseg[0]}"
        vseg[0] += 1
        blurred = f"vgc{idx}_{vseg[0]}"
        vseg[0] += 1
        out = f"vgo{idx}_{vseg[0]}"
        vseg[0] += 1
        # Screen on YUV U/V raises the chroma planes and turns a monochrome
        # glow magenta. Work in planar RGB; keep the original alpha component.
        parts.append(f"[{cur}]format=gbrap,split=2[{a}][{b}]")
        parts.append(f"[{b}]gblur=sigma={12*params.get('_previewScale',1):.6f}[{blurred}]")
        opacity = max(0.05, min(1.0, intensity / 2.0))
        parts.append(
            f"[{a}][{blurred}]blend=c0_mode=screen:c1_mode=screen:"
            f"c2_mode=screen:c3_mode=normal:c0_opacity={opacity:.3f}:"
            f"c1_opacity={opacity:.3f}:c2_opacity={opacity:.3f}[{out}]")
        return out

    def _fx_person_halo(self, params: dict, cur: str, parts: list[str],
                        vseg: list[int], idx: int) -> str:
        """Blur a transparent cutout below itself to create a colored silhouette halo."""
        sigma = max(2.0, min(28.0, float(params.get("sigma", 9.0))))
        hue = max(-180.0, min(180.0, float(params.get("hueShift", 30.0))))
        saturation = max(0.0, min(2.0, float(params.get("saturation", 1.0))))
        opacity = max(0.1, min(1.0, float(params.get("opacity", 0.75))))
        base = f"phb{idx}_{vseg[0]}"
        vseg[0] += 1
        source = f"phs{idx}_{vseg[0]}"
        vseg[0] += 1
        alpha = f"pha{idx}_{vseg[0]}"
        vseg[0] += 1
        matte = f"phm{idx}_{vseg[0]}"
        vseg[0] += 1
        colored = f"phc{idx}_{vseg[0]}"
        vseg[0] += 1
        halo_alpha = f"phx{idx}_{vseg[0]}"
        vseg[0] += 1
        halo = f"phh{idx}_{vseg[0]}"
        vseg[0] += 1
        out = f"pho{idx}_{vseg[0]}"
        vseg[0] += 1
        parts.append(f"[{cur}]format=gbrap,split=3[{base}][{source}][{alpha}]")
        parts.append(
            f"[{alpha}]alphaextract,gblur=sigma={sigma:.3f}:steps=2,format=gray[{matte}]")
        parts.append(
            f"[{source}]lutrgb=r=255:g=0:b=0,hue=h={hue:.3f}:s={saturation:.3f},"
            f"format=gbrap[{colored}]")
        parts.append(f"[{colored}][{matte}]alphamerge=shortest=1[{halo_alpha}]")
        parts.append(f"[{halo_alpha}]colorchannelmixer=aa={opacity:.3f}[{halo}]")
        parts.append(
            f"[{halo}][{base}]overlay=eof_action=pass:shortest=1:format=auto[{out}]")
        return out

    def _composite_on_canvas(self, src_label: str, clip: Clip, seq: Sequence,
                             parts: list[str], vseg: list[int], fps_expr: str,
                             W: int, H: int, position: dict,
                             layer_dur: float) -> str:
        """把 src_label 叠加到 WxH 画布 (x,y)，输出统一 fps 的画布流。

        layer_dur 为该层（单片段或关键帧子段）的实际时长，用于背景长度，
        避免背景比内容长导致拼接后时长膨胀。

        画布底色：默认不透明黑；**透明导出（V05）时为全透明**
        （color=black@0.0 + yuva420p），否则抠像/留白区域会变成黑块。
        """
        x = int(position["x"])
        y = int(position["y"])
        dur = layer_dur
        bg = f"bg{vseg[0]}"
        vseg[0] += 1
        if getattr(self._render_ctx, "alpha", False):
            parts.append(
                f"color=c=black@0.0:s={W}x{H}:r={fps_expr}:d={dur:.6f},"
                f"format=yuva420p[{bg}]")
        else:
            parts.append(
                f"color=c=black:s={W}x{H}:r={fps_expr}:d={dur:.6f}[{bg}]")
        out = f"cv{vseg[0]}"
        vseg[0] += 1
        # 源帧率低于工程帧率时，动画子段可能只有一帧输入而有两帧画布。
        # 保持该子段末帧至画布结束，避免 eof_action=pass 在边界闪出黑帧。
        # format=auto：跟随画布 alpha 格式，透明导出时不要把 alpha 压掉。
        parts.append(
            f"[{bg}][{src_label}]overlay=shortest=0:eof_action=repeat:"
            f"format=auto:x={x}:y={y}[{out}]")
        # The color bed already emits the sequence frame clock. Resampling it
        # again loses its final frame on FFmpeg 6.1, including one-frame beds.
        fin = f"cf{vseg[0]}"
        vseg[0] += 1
        parts.append(f"[{out}]null[{fin}]")
        return fin

    def _build_clip_opacity_keyframes(self, clip: Clip, seq: Sequence,
                                      get_input, parts: list[str],
                                      vseg: list[int], fps_expr: str,
                                      color: dict, W: int, H: int) -> str:
        """透明度关键帧：按关键帧时刻把片段切成常数透明度的子段，拼接成画布流。

        关键帧时间为「片段局部呈现时间」(Rational)，映射到源线性均匀；
        speed>=0 时子段源窗 = ss + (t/dur)*src_dur。speed<0 暂退化为整体中点透明度。
        """
        kfs = clip.keyframes["opacity"]
        tr = clip.transform()
        speed = clip.speed
        dur = clip.duration
        dur_f = float(dur.to_fraction())
        src_dur = float((dur * _abs_rat(speed)).to_fraction())
        ss = float(clip.source_start.to_fraction())
        curve = clip.speed_curve

        if float(speed.to_fraction()) < 0:
            # 倒放 + 透明度关键帧：当前退化为整体中点常数透明度
            op = kf_evaluate(kfs, dur / 2)
            base = self._trim_speed_color(clip, get_input, parts, vseg, color, fps_expr)
            cur = self._scale_to_canvas(base, W, H, tr["scale"], tr["scale"],
                                        parts, vseg, sticker=clip.role == "sticker")
            if op < 1.0:
                nxt = f"va{vseg[0]}"; vseg[0] += 1
                parts.append(f"[{cur}]format=rgba,colorchannelmixer=aa={op:.6f}[{nxt}]")
                cur = nxt
            return self._composite_on_canvas(cur, clip, seq, parts, vseg,
                                              fps_expr, W, H, tr["position"],
                                              float(clip.duration.to_fraction()))

        # 边界：0, 关键帧时刻（夹紧到 (0,dur)）, dur
        times = sorted({Rational.of(0, 1), dur}
                       | {kf.time for kf in kfs if Rational.of(0, 1) < kf.time < dur})
        sub_labels: list[str] = []
        for a_t, b_t in zip(times, times[1:]):
            # 子段局部时长
            sub_local = float((b_t - a_t).to_fraction())
            # 子段源窗（线性映射，speed>=0）
            if curve is not None:
                source_a = curve.source_at_timeline(float(a_t.to_fraction()))
                source_b = curve.source_at_timeline(float(b_t.to_fraction()))
                sub_ss = ss + source_a
                sub_src_dur = source_b - source_a
                speed_expr = curve.slice(source_a, source_b).video_setpts()
            else:
                sub_ss = ss + float((a_t.to_fraction() / dur_f)) * src_dur
                sub_src_dur = sub_local * (src_dur / dur_f) if dur_f > 0 else 0.0
                speed_f = float(speed.to_fraction())
                if speed_f >= 0:
                    speed_expr = f"setpts=PTS/{speed_f}"
                else:
                    abs_f = float(_abs_rat(speed).to_fraction())
                    speed_expr = (f"reverse,setpts=PTS-STARTPTS,"
                                  f"setpts=PTS/{abs_f}")
            # 子段常数透明度 = 中点求值
            mid = a_t + (b_t - a_t) * Rational.of(1, 2)
            op = float(kf_evaluate(kfs, mid))
            # 生成该子段的 trim 源窗
            seg = f"vseg{vseg[0]}"
            vseg[0] += 1
            chain = (f"[{get_input(clip.asset_ref.source_path)}:v]"
                     f"trim=start={sub_ss:.6f}:duration={sub_src_dur:.6f},"
                     f"setpts=PTS-STARTPTS,{speed_expr}")
            chain += self._slow_motion_interpolation_filter(clip, fps_expr)
            if (color["brightness"] != 0.0 or color["contrast"] != 1.0
                    or color["saturation"] != 1.0):
                chain += (f",eq=brightness={color['brightness']:.6f}:"
                          f"contrast={color['contrast']:.6f}:"
                          f"saturation={color['saturation']:.6f}")
            chain += f"[{seg}]"
            parts.append(chain)
            cur = self._scale_to_canvas(seg, W, H, tr["scale"], tr["scale"],
                                        parts, vseg, sticker=clip.role == "sticker")
            if op < 1.0:
                nxt = f"va{vseg[0]}"; vseg[0] += 1
                parts.append(f"[{cur}]format=rgba,colorchannelmixer=aa={op:.6f}[{nxt}]")
                cur = nxt
            sub_labels.append(
                self._composite_on_canvas(cur, clip, seq, parts, vseg,
                                          fps_expr, W, H, tr["position"],
                                          sub_local))

        if len(sub_labels) == 1:
            return sub_labels[0]
        ins = "".join(f"[{s}]" for s in sub_labels)
        out = f"ckf{vseg[0]}"
        vseg[0] += 1
        parts.append(f"{ins}concat=n={len(sub_labels)}:v=1:a=0[{out}]")
        return out

    # ------------------------------------------------------------------
    # 音轨探测：源文件是否含可解码音频流
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 素材探测（T04 / 导入 UX：拿到真实时长替代「默认 10 秒」）
    # ------------------------------------------------------------------
    def _regular_timestamps(self, source, start, duration, fps):
        from cutvoke.rendering.vfx_matte import regular_timestamps
        identity = (self.ffprobe, file_identity(source), start, duration, str(fps))
        return self._clock_cache.get_or_compute(identity, lambda: regular_timestamps(
            self.ffprobe, source, start, duration, fps, exact_interval=True))

    def probe_media(self, src: str, timeout: float = 30.0) -> dict[str, Any]:
        """Probe current source metadata once, sharing concurrent reads safely."""
        if not os.path.isfile(src):
            raise RenderError(f"media file not found: {src!r}")
        identity = (self.ffprobe, file_identity(src))
        return dict(self._probe_cache.get_or_compute(
            identity, lambda: self._probe_media_uncached(src, timeout)))

    def _probe_media_uncached(self, src: str,
                    timeout: float = 30.0) -> dict[str, Any]:
        """用 ffprobe 读取素材真实信息。

        返回：{"duration": float, "width": int, "height": int,
                "has_video": bool, "has_audio": bool, "format": str}
        失败抛 RenderError（文件不存在 / ffprobe 失败 / 无媒体流）。
        """
        import os as _os
        if not _os.path.isfile(src):
            raise RenderError(f"media file not found: {src!r}")
        cmd = [
            self.ffprobe, "-v", "error",
            "-show_entries", "format=duration,format_name",
            "-show_entries", "stream=codec_type,width,height,pix_fmt,duration,start_time,avg_frame_rate,r_frame_rate",
            "-of", "json", src,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise RenderError(f"probe timed out on {src!r}") from e
        if r.returncode != 0:
            raise RenderError(f"ffprobe failed on {src!r}: {r.stderr[-400:]}")
        try:
            info = json.loads(r.stdout or "{}")
        except json.JSONDecodeError as e:
            raise RenderError(f"ffprobe invalid output for {src!r}") from e
        streams = info.get("streams", [])
        v = next((s for s in streams if s.get("codec_type") == "video"), None)
        a = next((s for s in streams if s.get("codec_type") == "audio"), None)
        fmt = info.get("format", {})
        duration = None
        try:
            duration = float(fmt.get("duration"))
        except (TypeError, ValueError):
            # 有些素材 format.duration 缺失，退而从视频流 r_frame_rate 估算
            if v and v.get("avg_frame_rate"):
                try:
                    num, den = v["avg_frame_rate"].split("/")
                    dur = (float(num) / float(den)) * float(v.get("nb_frames", 0))
                    if dur > 0:
                        duration = dur
                except (ValueError, ZeroDivisionError):
                    pass
        if duration is None:
            # 最后手段：ffprobe 无时长则用 ffmpeg -t 试探不可靠，标记未知
            duration = 0.0
        try:
            video_duration = float(v.get("duration") or duration or 0.0) if v else 0.0
        except (TypeError, ValueError):
            video_duration = float(duration or 0.0)
        def stream_start(stream):
            try:
                value = float(stream.get('start_time')) if stream else None
                return value if value is not None and math.isfinite(value) else None
            except (TypeError, ValueError):
                return None
        result = {
            "duration": duration,
            "video_duration": video_duration,
            "video_start_time": stream_start(v),
            "audio_start_time": stream_start(a),
            "avg_frame_rate": str(Fraction(v.get("avg_frame_rate", "0/1"))) if v and v.get("avg_frame_rate") not in (None, "0/0") else "0",
            "r_frame_rate": str(Fraction(v.get("r_frame_rate", "0/1"))) if v and v.get("r_frame_rate") not in (None, "0/0") else "0",
            "width": int(v.get("width", 0)) if v else 0,
            "height": int(v.get("height", 0)) if v else 0,
            "has_video": v is not None,
            "has_audio": a is not None,
            "format": str(fmt.get("format_name", "")),
            # V03：静帧/透明导出要判 alpha 是否真的在（PNG 常被判成 rgba/rgb24）
            "pix_fmt": v.get("pix_fmt") if v else None,
        }
        return result

    def thumbnail_media(self, src: str, out_png: str, *,
                        time: float = 0.0, width: int = 320,
                        timeout: float = 15.0) -> str:
        """抽取单个素材源文件在 time 时刻的一帧作为缩略图（PNG）。

        与工程预览帧不同：这里直接对源文件抽帧（不经过时间线/效果），
        供素材列表展示「图片/视频首帧缩略图」（D02）。宽度按 width 缩放、
        高度锁定为偶数自动等比，前端用 object-fit 裁切显示。

        成功返回 out_png 路径；失败抛 RenderError。
        """
        import os as _os
        if not _os.path.isfile(src):
            raise RenderError(f"thumbnail: source file not found: {src!r}")
        cmd = [
            self.ffmpeg, "-y", "-nostdin",
            "-ss", f"{time:.4f}",
            "-i", src,
            "-frames:v", "1",
            "-vf", f"scale={width}:-2",
            "-f", "image2", out_png,
        ]
        try:
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise RenderError(f"thumbnail timed out on {src!r}") from e
        if r.returncode != 0 or not os.path.isfile(out_png):
            raise RenderError(f"thumbnail failed on {src!r}: {r.stderr[-400:]}")
        return out_png

    def waveform_media(self, src: str, out_png: str, *, width: int = 320,
                       timeout: float = 60.0) -> str:
        """Decode an audio asset into a compact, full-duration waveform PNG."""
        if not os.path.isfile(src):
            raise RenderError(f"waveform: source file not found: {src!r}")
        width = max(160, min(960, int(width)))
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-nostdin",
               "-i", src, "-filter_complex",
               f"[0:a:0]aformat=channel_layouts=mono,"
               f"showwavespic=s={width}x48:colors=0x58dcc7[wave]",
               "-map", "[wave]", "-frames:v", "1", out_png]
        try:
            result = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace",
                                    timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise RenderError(f"waveform timed out on {src!r}") from error
        if result.returncode != 0 or not os.path.isfile(out_png):
            raise RenderError(f"waveform failed on {src!r}: {result.stderr[-400:]}")
        return out_png

    def _has_audio_stream(self, src: str) -> bool:
        cmd = [
            self.ffprobe, "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "json", src,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        if r.returncode != 0:
            return False
        try:
            info = json.loads(r.stdout or "{}")
        except json.JSONDecodeError:
            return False
        return any(s.get("codec_type") == "audio"
                   for s in info.get("streams", []))

    # ------------------------------------------------------------------
    # 执行 ffmpeg
    # ------------------------------------------------------------------
    def _run_ffmpeg(self, seq: Sequence, graph: str, input_paths: list[str],
                    out_path: str, quality: str,
                    cancel_event: Optional[threading.Event],
                    has_audio: bool = False,
                    caption_cwd: Optional[str] = None,
                    video_bitrate_kbps: Optional[int] = None,
                    audio_bitrate_kbps: Optional[int] = None,
                    alpha: bool = False,
                    color_chain: str = "",
                    output_start: Optional[float] = None,
                    output_duration: Optional[float] = None,
                    preview_source_duration: Optional[float] = None,
                    lossless: bool = False, concat_input: bool = False,
                    progress_callback=None, progress_duration: Optional[float] = None,
                    progress_phase: str = "encoding") -> None:
        preset = QUALITY_PRESETS.get(quality)
        if preset is None:
            raise RenderError(f"unknown quality preset: {quality!r} "
                              f"(available: {list(QUALITY_PRESETS)})")

        # O06 色彩管理：把滤镜链追加在 [outv] 之后，产出新的输出标签 [outvcm]。
        # 这样色彩管理是"导出级后处理"，完全不侵入既有效果/转场/字幕编译逻辑。
        out_label = "[outv]"
        original_graph = graph
        if color_chain:
            graph = f"{graph};[outv]{color_chain}[outvcm]"
            out_label = "[outvcm]"
        audio_label = "[outa]"
        if output_duration is not None:
            # Terminate both filter outputs at the requested interval. Output
            # -ss/-t alone leaves looping-image graphs alive while the encoder
            # stops pulling frames, which can stall a dense graph at frame 0.
            # Trim after effects/subtitles so their original clocks are intact.
            start = output_start or 0.0
            end = start + output_duration
            graph += (f";{out_label}trim=start_frame={math.ceil(start*float(seq.fps.to_fraction())-1e-7)}:end_frame={math.ceil(end*float(seq.fps.to_fraction())-1e-7)},"
                      "setpts=PTS-STARTPTS[boundedv]")
            out_label = "[boundedv]"
            if _setpts_clears_frame_rate(self.ffmpeg):
                fps = seq.fps.to_fraction()
                graph = graph.replace("setpts=PTS-STARTPTS[boundedv]",
                    f"setpts=PTS-STARTPTS,fps={fps.numerator}/{fps.denominator}[boundedv]")
            if has_audio:
                audio_offset=getattr(self._render_ctx,'audio_origin_delta',0.)
                graph += (f";[outa]atrim=start={start+audio_offset:.9f}:end={end+audio_offset:.9f},"
                          "asetpts=PTS-STARTPTS[boundeda]")
                audio_label = "[boundeda]"

        # This is a background worker, not an interactive terminal command.
        # Inherited stdin can stop processing while other tools use its parent
        # session; never let FFmpeg consume that session's input.
        # Bound per-input decoder and filter pools. On many-core Windows hosts,
        # auto-sized pools for every clip can exhaust memory during preparation.
        # Decoder pools stay small per input. Filters share one bounded pool;
        # two threads waste most of a multi-core CPU on dense effect graphs.
        # Reduce the pool for large canvases to bound per-thread frame buffers.
        filter_threads = min(8, max(2, (os.cpu_count() or 2) // 2),
                             max(2, 16_777_216 // max(1, seq.width * seq.height)))
        cmd: list[str] = [self.ffmpeg, "-y", "-nostdin",
                          "-filter_complex_threads", str(filter_threads),
                          "-filter_threads", str(filter_threads), *self._gpu_arguments(graph)]
        for src in input_paths:
            cmd += ["-threads", "2"]
            if concat_input:
                cmd += ["-f", "concat", "-safe", "0", "-i", src]
            elif _is_image_src(src):
                # 静态图片：-loop 1 无限循环 + 显式帧率，使单帧源能作为
                # 有持续时间的片段参与时间线（1.5-A 图片素材，F03/F07）。
                fps_rat = seq.fps.to_fraction()
                cmd += ["-loop", "1", "-framerate",
                        f"{fps_rat.numerator}/{fps_rat.denominator}", "-i", src]
            else:
                seek = getattr(self._render_ctx, 'input_seeks', {}).get(os.path.abspath(src))
                if seek and not concat_input:
                    cmd += ['-ss', str(seek)]
                cmd += ["-i", src]

        filter_args, filter_script_path = _filter_graph_arguments(self.ffmpeg, graph, caption_cwd)

        ext = os.path.splitext(out_path)[1].lower()
        use_nvenc = (self.gpu_acceleration and not alpha and not lossless and seq.width >= 144 and seq.height >= 144
                     and not getattr(self._render_ctx, "hardware_disabled", False)
                     and _nvenc_available(self.ffmpeg))
        self._render_ctx.video_encoder = "ffv1" if lossless else "prores_ks" if alpha else (
            "h264_nvenc" if use_nvenc else "libx264")
        if lossless:
            cmd += [*filter_args, "-map", out_label, "-c:v", "ffv1", "-level", "3",
                    "-threads", "2", "-pix_fmt", "yuva444p" if alpha else "yuv420p"]
            if has_audio:
                cmd += ["-map", audio_label, "-c:a", "pcm_s16le"]
            if output_start is not None and output_duration is None:
                cmd += ["-ss", f"{output_start:.6f}"]
            cmd += [out_path]
        elif alpha:
            # V05：ProRes 4444（剪辑软件交接透明素材的通用口径）。
            # 容器白名单已在 render() 入口收敛，这里只可能有 .mov。
            cmd += [*filter_args, "-map", out_label,
                    "-c:v", "prores_ks", "-profile:v", "4",
                    "-pix_fmt", "yuva444p10le", "-threads", "4"]
            if has_audio:
                cmd += ["-map", audio_label, "-c:a", "pcm_s16le"]
            if output_start is not None and output_duration is None:
                cmd += ["-ss", f"{output_start:.6f}"]
            cmd += [out_path]
        else:
            cmd += [
                *filter_args,
                "-map", out_label,
                "-c:v", "h264_nvenc" if use_nvenc else "libx264", "-pix_fmt", "yuv420p",
                "-threads", "4",
            ]
            # V01 导出预设：给 video_bitrate_kbps 用 -b:v 目标码率，否则走 crf 画质档
            if use_nvenc:
                cmd += ["-preset", {"high":"p6", "medium":"p4", "low":"p1", "preview":"p1"}[quality],
                        "-tune", "hq", "-rc", "vbr", "-spatial-aq", "1"]
                if video_bitrate_kbps:
                    cmd += ["-b:v", f"{video_bitrate_kbps}k"]
                else:
                    cmd += ["-cq", preset["crf"], "-b:v", "0"]
            elif video_bitrate_kbps:
                cmd += ["-b:v", f"{video_bitrate_kbps}k"]
            else:
                cmd += ["-crf", preset["crf"], "-preset", preset["preset"]]
            # 有音频才加音轨，保证「无音频工程导出无声视频」且不报错
            if has_audio:
                cmd += ["-map", audio_label, "-c:a", "aac",
                        "-b:a", f"{audio_bitrate_kbps}k" if audio_bitrate_kbps else "192k"]
            if output_start is not None and output_duration is None:
                cmd += ["-ss", f"{output_start:.6f}"]
            cmd += ["-movflags", "+faststart", out_path]

        # FFmpeg reports encoded media time. Keep it separate from stderr and
        # never manufacture a percentage from elapsed wall-clock time.
        progress_path = None
        progress_stop = threading.Event()
        progress_thread = None
        if progress_callback:
            handle, progress_path = tempfile.mkstemp(prefix="cutvoke-progress-", suffix=".txt")
            os.close(handle)
            cmd[1:1] = ["-progress", progress_path, "-stats_period", "0.25", "-nostats"]
            total = progress_duration or output_duration
            progress_callback({"phase": progress_phase,
                               "processedSeconds": 0.0, "totalSeconds": total,
                               "fraction": 0.0 if total else None})
            def watch_progress() -> None:
                values = {}
                with open(progress_path, "r", encoding="utf-8", errors="replace") as stream:
                    while True:
                        line = stream.readline()
                        if not line:
                            if progress_stop.is_set():
                                return
                            progress_stop.wait(.1)
                            continue
                        key, _, value = line.strip().partition("=")
                        values[key] = value
                        if key != "progress":
                            continue
                        try:
                            processed = max(0.0, float(values.get("out_time_us", "0")) / 1_000_000)
                            raw_speed = values.get("speed", "").rstrip("x")
                            speed = float(raw_speed) if raw_speed not in {"", "N/A"} else None
                            if speed is not None and (not math.isfinite(speed) or speed <= 0):
                                speed = None
                            payload = {"phase": progress_phase, "processedSeconds": processed,
                                "totalSeconds": total, "fraction": min(1.0, processed/total) if total else None,
                                "complete": value == "end",
                                "speed": speed,
                                "etaSeconds": max(0.0, total-processed)/speed if total and speed else None}
                            progress_callback(payload)
                        except (ValueError, TypeError):
                            pass
            progress_thread = threading.Thread(target=watch_progress, daemon=True)
            progress_thread.start()
        # 取消支持：用 Popen + 看门狗线程，在 cancel_event 置位时终止 ffmpeg
        try:
            self._render_ctx.window_retry_count = 0
            self._render_ctx.window_recovery_mode = None
            for attempt in range(2 if output_duration is not None else 1):
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        cwd=caption_cwd if caption_cwd else None)
                watcher = None
                if cancel_event is not None:
                    def _watch() -> None:
                        while proc.poll() is None:
                            if cancel_event.is_set():
                                with contextlib.suppress(OSError):
                                    proc.terminate()
                                return
                            cancel_event.wait(0.2)
                    watcher = threading.Thread(target=_watch, daemon=True)
                    watcher.start()
                cpu = _process_cpu_seconds(proc) if output_duration is not None else None
                previous_size = 0
                idle_since = time.monotonic()
                stalled = False
                while True:
                    try:
                        stdout, stderr = proc.communicate(timeout=2 if cpu is not None else None)
                        break
                    except subprocess.TimeoutExpired:
                        current_cpu = _process_cpu_seconds(proc)
                        current_size = os.path.getsize(out_path) if os.path.isfile(out_path) else 0
                        if current_cpu is None:
                            cpu = None
                            continue
                        if current_cpu - cpu > 0.05 or current_size != previous_size:
                            idle_since = time.monotonic()
                        cpu, previous_size = current_cpu, current_size
                        if time.monotonic() - idle_since >= _PREVIEW_IDLE_SECONDS:
                            # Observed FFmpeg 9.0.1 windows can stop at frame=0
                            # indefinitely. Retry the same immutable graph once
                            # only when both CPU work and file growth stop.
                            proc.terminate()
                            stdout, stderr = proc.communicate(timeout=5)
                            stalled = True
                            break
                if watcher is not None:
                    watcher.join(timeout=5)
                if cancel_event is not None and cancel_event.is_set():
                    break
                if not stalled:
                    break
                if (attempt == 0 and not alpha and cancel_event is None
                        and preview_source_duration is not None
                        and preview_source_duration <= 8):
                    # Short complex graphs with looping images can stall when
                    # FFmpeg stops them mid-stream. Render the finite graph to
                    # a lossless intermediate, then seek a single media input.
                    # Keep this bounded to short previews, never full projects.
                    self._render_ctx.window_retry_count = 1
                    self._recover_preview_window(cmd, out_path, preset, has_audio,
                                                 caption_cwd, 0 if output_duration is not None else output_start or 0,
                                                 output_duration)
                    self._render_ctx.window_recovery_mode = "finite-intermediate"
                    return
                if attempt == 1:
                    raise RenderError("preview encoder stopped making progress after one retry")
                self._render_ctx.window_retry_count = 1
        finally:
            if progress_thread:
                progress_stop.set()
                progress_thread.join(timeout=2)
            if progress_path:
                with contextlib.suppress(OSError):
                    os.remove(progress_path)
            if filter_script_path:
                with contextlib.suppress(OSError):
                    os.remove(filter_script_path)

        if cancel_event is not None and cancel_event.is_set():
            raise RenderCancelled("render cancelled before completion")
        if proc.returncode != 0:
            if self._gpu_arguments(original_graph):
                tail = (stderr or b'').decode('utf-8', 'replace')[-1500:]
                raise RenderError('GPU effect execution failed: ' + tail)
            if use_nvenc:
                # A device can become unavailable after the successful probe.
                # Retry the immutable request on CPU; cancellation still wins.
                self._render_ctx.hardware_disabled = True
                self._render_ctx.encoder_fallback = True
                return self._run_ffmpeg(
                    seq, original_graph, input_paths, out_path, quality,
                    cancel_event, has_audio, caption_cwd=caption_cwd,
                    video_bitrate_kbps=video_bitrate_kbps,
                    audio_bitrate_kbps=audio_bitrate_kbps, alpha=alpha,
                    color_chain=color_chain, output_start=output_start,
                    output_duration=output_duration,
                    preview_source_duration=preview_source_duration,
                    lossless=lossless, concat_input=concat_input,
                    progress_callback=progress_callback, progress_duration=progress_duration,
                    progress_phase=progress_phase)
            tail = (stderr or b"").decode("utf-8", "replace")[-1500:]
            raise RenderError(
                f"ffmpeg exited with code {proc.returncode}\n{tail}")

    def _recover_preview_window(self, cmd: list[str], out_path: str,
                                preset: dict[str, str], has_audio: bool,
                                caption_cwd: Optional[str], start: float,
                                duration: float) -> None:
        """Recover a stalled short preview without changing its effect clock."""
        intermediate = out_path + ".recovery.mkv"
        # All inputs and the compiled graph precede the first output mapping.
        # Reuse them, including caption cwd and export color management.
        first_map = cmd.index("-map")
        full_cmd = cmd[:first_map] + [
            "-map", cmd[first_map + 1], "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-crf", "0", "-preset", "veryfast"]
        if has_audio:
            audio_map = cmd.index("-map", first_map + 1)
            full_cmd += ["-map", cmd[audio_map + 1], "-c:a", "flac"]
        full_cmd += [intermediate]
        cut_cmd = [self.ffmpeg, "-y", "-nostdin", "-ss", f"{start:.6f}",
                   "-i", intermediate, "-t", f"{duration:.6f}",
                   "-map", "0:v:0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-crf", preset["crf"], "-preset", preset["preset"]]
        if has_audio:
            cut_cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"]
        cut_cmd += ["-movflags", "+faststart", out_path]
        try:
            for command in (full_cmd, cut_cmd):
                result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                        capture_output=True, cwd=caption_cwd,
                                        timeout=180)
                if result.returncode:
                    tail = result.stderr.decode("utf-8", "replace")[-1500:]
                    raise RenderError(f"preview recovery failed: {tail}")
        except subprocess.TimeoutExpired as exc:
            raise RenderError("preview recovery timed out") from exc
        finally:
            with contextlib.suppress(OSError):
                os.remove(intermediate)

    # ------------------------------------------------------------------
    # ffprobe 验证真实可解码 + 时长/尺寸正确
    # ------------------------------------------------------------------
    def _verify(self, path: str, expected_duration: float,
                width: int, height: int,
                expect_alpha: bool = False,
                expect_primaries: Optional[str] = None) -> dict[str, Any]:
        cmd = [
            self.ffprobe, "-v", "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height,pix_fmt,"
            "color_primaries,color_transfer,color_space",
            "-of", "json", path,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
        except subprocess.TimeoutExpired as e:
            raise RenderError(f"ffprobe timed out on {path}") from e
        if r.returncode != 0:
            raise RenderError(
                f"ffprobe failed (file not decodable): {r.stderr[-800:]}")

        info = json.loads(r.stdout or "{}")
        streams = info.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        if video is None:
            raise RenderError("ffprobe found no video stream in output")

        dur = info.get("format", {}).get("duration")
        if dur is None or dur == "N/A":
            raise RenderError("ffprobe could not determine duration")
        dur = float(dur)

        # 时长容差：0.1s，覆盖编解码/帧边界误差且足以区分真实时长差异
        if abs(dur - expected_duration) > 0.1:
            raise RenderError(
                f"exported duration {dur:.3f}s != expected {expected_duration:.3f}s")

        got_w = int(video.get("width", 0))
        got_h = int(video.get("height", 0))
        if (got_w, got_h) != (width, height):
            raise RenderError(
                f"exported size {got_w}x{got_h} != expected {width}x{height}")

        pix_fmt = video.get("pix_fmt")
        # V05：透明导出必须真的带 alpha。编码器/容器不支持时会**静默降级**
        # 成无 alpha 的格式，产出"看起来成功但其实不透明"的文件——所以在
        # 这里硬性拦下，绝不放过（这是本能力唯一的可信证据）。
        if expect_alpha and not _pix_fmt_has_alpha(pix_fmt):
            raise RenderError(
                f"transparent export produced a stream without alpha "
                f"(pix_fmt={pix_fmt!r}); the container/codec dropped it")

        # O06 色彩管理：输出流的 primaries 必须真的是请求的目标值。
        # 若 zscale 被静默跳过（例如输入未标定导致 zimg 找不到路径），
        # 文件会"导出成功但色彩没转"，这里硬性拦下。
        if expect_primaries:
            got = (video.get("color_primaries") or "").lower()
            want = COLOR_SPACE_TRIPLE[expect_primaries][0]
            if got != want:
                raise RenderError(
                    f"color management did not take effect: expected "
                    f"color_primaries={want!r} ({expect_primaries}), "
                    f"got {got or 'unknown'!r}")

        return {"duration": dur, "width": got_w, "height": got_h,
                "codec": video.get("codec_name"), "pix_fmt": pix_fmt,
                "color_primaries": video.get("color_primaries"),
                "color_transfer": video.get("color_transfer"),
                "color_space": video.get("color_space")}
