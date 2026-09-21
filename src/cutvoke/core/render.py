"""CutVoke 媒体渲染服务（任务书 T11 / 第 12 章导出）。

把不可变工程快照（Sequence）编译成 ffmpeg filter_complex 图并真实导出为
MP4(H.264 + AAC)。

渲染范围（M0 + T16 音频混音）：
  - 视频：单轨 concat；多轨 primary 主轴 + 其余轨 overlay（位置默认左上角，可传参）
  - 音频（T16）：
      * 音频轨（kind=='audio'）的可见片段：按 source_start/时长 atrim + asetpts 裁剪，
        一条音轨内 concat；多条音轨用 amix 混合
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

import json
import math
import os
import shutil
import subprocess
import threading
import copy
from typing import Any, Optional
from pathlib import Path

from .rational import Rational
from .model import Project, Sequence, Clip, AssetReference, Track, Caption
from .keyframes import evaluate as kf_evaluate
from .effects import (EffectRegistry, default_registry, find_transition,
                      xfade_transition_name, collect_used_effect_ids)
from .caption_render import prepare_caption_render, CaptionFontError

# subprocess resolves these names through PATH. Deployments that need a fixed
# binary can pass an explicit path to RenderService.
DEFAULT_FFMPEG = "ffmpeg"
DEFAULT_FFPROBE = "ffprobe"

# 质量预设：映射到 libx264 的 crf / preset（第 12.5 章：至少支持一个 MP4 H.264 预设）
QUALITY_PRESETS: dict[str, dict[str, str]] = {
    "high": {"crf": "18", "preset": "slow"},
    "medium": {"crf": "23", "preset": "medium"},
    "low": {"crf": "28", "preset": "veryfast"},
}

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


def _find_animation(clip: Clip) -> Optional[dict]:
    """返回 clip 上的动画效果实例（J01），无则 None。

    动画效果 effectId 以 cutvoke.anim. 开头；一个片段只取第一个**启用**的；
    被旁路（enabled=False）的动画跳过，按顺序找下一个。
    """
    for e in getattr(clip, "effects", []) or []:
        eid = e.get("effectId", "")
        if isinstance(eid, str) and eid.startswith("cutvoke.anim."):
            if _is_effect_enabled(e):
                return e
    return None


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
    """LUT 3D 调色：读取随包分发的内置 .cube 预设（cool/warm/retro）。

    预设文件位于 src/cutvoke/assets/luts/<preset>.cube。render.py 位于
    src/cutvoke/core/，须向上两级到包根再进 assets（渲染可能切换 cwd 到
    字幕临时目录，相对路径会失效，必须解析为绝对路径）。
    """
    preset = str(p.get("preset", "cool"))
    if preset not in ("cool", "warm", "retro"):
        preset = "cool"
    cube = (Path(__file__).resolve().parent.parent / "assets" / "luts"
            / f"{preset}.cube")
    # Windows 盘符冒号是 filtergraph 的选项分隔符——引号内的冒号也须转义
    # （实测 file='E:/...' 会报 "No option name near '/...'"，file='E\:/...' 才合法）
    posix = cube.as_posix().replace(":", r"\:")
    return f"lut3d=file='{posix}'"


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


def _fx_mask_expr(p: dict) -> str:
    """N02 几何蒙版：只保留画面某个矩形/椭圆区域，其余压黑。

    实测实现路线（2026-09-21）：用 `geq` 就地改亮度，**不产生 alpha**。
    这一点很关键——抠像类 alpha 在非画布合成路径上会被编码器静默丢弃，
    而蒙版只改 luma、保持 yuv420p，因此能安全穿过后续的缩放/合成/转场链。

    geq 表达式里 X/Y/W/H 都是像素，参数按归一化比例换算；
    函数调用里的逗号在 filtergraph 中必须转义成 `\\,`（实测不转义会被
    当作滤镜分隔符，直接报 "Unable to parse"）。
    """
    shape = str(p.get("shape", "rect")).strip().lower()
    if shape not in ("rect", "circle"):
        shape = "rect"
    x = max(0.0, min(1.0, float(p.get("x", 0.15))))
    y = max(0.0, min(1.0, float(p.get("y", 0.15))))
    w = max(0.01, min(1.0, float(p.get("w", 0.7))))
    h = max(0.01, min(1.0, float(p.get("h", 0.7))))
    feather = max(0.0, min(0.5, float(p.get("feather", 0.05))))
    invert = bool(p.get("invert", False))

    if shape == "rect":
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
        dist = (f"sqrt(pow((X-{cx})/{rx}\\,2)+pow((Y-{cy})/{ry}\\,2))")
        f_r = f"max({feather:.6f}\\,0.0001)"
        mask = f"clip((1-{dist})/{f_r}\\,0\\,1)"

    if invert:
        mask = f"(1-{mask})"
    return (f"geq=lum='lum(X,Y)*{mask}':cb='cb(X,Y)':cr='cr(X,Y)'")


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


_FX_STEPS: dict[str, "Callable[[dict], str]"] = {
    "gblur": lambda p: f"gblur=sigma={float(p.get('sigma', 5.0)):.2f}",
    "grayscale": lambda p: "hue=s=0",
    "chromatic": lambda p: (
        f"rgbashift=rh={int(p.get('offset', 4))}:bh=-{int(p.get('offset', 4))}"),
    "glitch": lambda p: (
        f"noise=alls={max(1, int(float(p.get('amount', 0.4)) * 100))}:allf=t,"
        f"rgbashift=rh=2:bh=-2"),
    "invert": lambda p: "negate",
    "posterize": _fx_posterize_expr,
    "sharpen": lambda p: (
        f"unsharp=5:5:{float(p.get('amount', 1.0)):.2f}:5:5:0"),
    "edge": lambda p: f"edgedetect=mode={p.get('mode', 'colormix')}",
    "mirror": lambda p: "hflip",
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
}

# 需要多输入建图的滤镜（不能内联续接），由 RenderService._fx_one 单独实现
_FX_MULTI = frozenset({"glow"})


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
    from .model import EFFECT_TEXT
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


class RenderError(Exception):
    """渲染失败（工程不合法、ffmpeg 非零、ffprobe 验证不通过等）。"""


class RenderCancelled(RenderError):
    """渲染被取消（cancel_event 触发），不留下误导性的成功成片。"""


class RenderService:
    def __init__(self, ffmpeg_path: str = DEFAULT_FFMPEG,
                 ffprobe_path: str = DEFAULT_FFPROBE,
                 registry: Optional[EffectRegistry] = None) -> None:
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path
        # 效果注册表（T05/T17）：转场等效果由清单声明驱动，内核不写死 effectId
        self.effects = registry or default_registry()
        # V05 透明导出的"渲染期标志"（thread-local，避免并发请求互相串味）。
        # 为什么用线程局部而不是层层传参：画布底 `color=c=black` 深埋在
        # _composite_on_canvas / 关键帧子段 / 转场三条分支里，漏传任何一处
        # 都会"静默变成黑底"（alpha 存在但内容不透明），比报错更难发现。
        self._render_ctx = threading.local()

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------
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
               source_transfer: Optional[str] = None) -> dict[str, Any]:
        """把工程渲染为 MP4(H.264 + AAC) 视频。

        返回 dict：output_path, duration, width, height, has_audio, warnings。
        失败/取消：只写临时文件 (.tmp.mp4)，成功验证后原子 rename；失败则清理临时文件，
        绝不留下误导性成片（第 12.5 章）。

        效果策略（AC19）：工程引用了未注册的效果时**严格拒绝**（默认），
        避免"导出成功但效果被静默丢弃"；显式传 allow_unknown_effects=True
        才降级导出，并在 warnings 里留明确记录。
        """
        seq = project.sequence

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
            allow_unknown_effects=allow_unknown_effects)

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

        # 2. 编译 filter_complex 图 + 输入列表（有理数→float 仅在此层）
        #    _compile 在含字幕时会生成 ASS 临时目录（caption_cwd），需调用方清理。
        graph, input_paths, expected_duration, has_audio, warnings, caption_cwd = (
            self._compile(seq, overlay_position, alpha=alpha))
        warnings = preflight_warnings + warnings

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
                             alpha=alpha, color_chain=color_chain)
            # 5. ffprobe 验证真实可解码 + 时长/尺寸正确（透明导出另验 alpha；
            #    色彩管理另验输出 primaries 真的落到位）
            # 色调映射会把输出锁成 bt709，此时不该再按 color_space 断言。
            probed_space = color_space if (tm == "none" and color_space) else None
            probe = self._verify(tmp_path, expected_duration, seq.width,
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
        }

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

        cmd: list[str] = [self.ffmpeg, "-y"]
        for src in input_paths:
            if _is_image_src(src):
                fps_rat = seq.fps.to_fraction()
                cmd += ["-loop", "1", "-framerate",
                        f"{fps_rat.numerator}/{fps_rat.denominator}", "-i", src]
            else:
                cmd += ["-i", src]
        cmd += ["-filter_complex", graph,
                # 视频流必须被 filtergraph 消耗（-f null - 丢弃），否则
                # outv 未连接 ffmpeg 报错；真正输出的只有音频 [outa]
                "-map", "[outv]", "-f", "null", "-",
                "-map", "[outa]",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                tmp_path]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=caption_cwd if caption_cwd else None)
        _, err = proc.communicate()
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
    def extract_frame(self, project: Project, timeline_time: float,
                      out_png: str, size: Optional[tuple[int, int]] = None,
                      timeout: float = 30.0,
                      include_captions: bool = True) -> Optional[str]:
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
        active_track_id = getattr(multicam, "active_track_id", None)

        def covers(track: Any) -> bool:
            if getattr(track, "kind", "video") != "video":
                return False
            if not getattr(track, "visible", True):
                return False
            if active_track_id and getattr(track, "track_id", None) != active_track_id:
                return False
            return any(
                not getattr(clip, "hidden", False)
                and clip.timeline_start <= t_rat < clip.timeline_end
                for clip in track.clips
            )

        if not any(covers(track) for track in source_seq.tracks):
            return None

        preview_project = copy.deepcopy(project)
        if not include_captions:
            # 多序列模型中 sequence 与 sequences 中的活动序列是两个引用视图，
            # 两边都清空，避免 to_dict / _compile 又把字幕带回来。
            for sequence in preview_project.sequences:
                sequence.captions = []
            preview_project.sequence.captions = []
        seq = preview_project.sequence
        graph, input_paths, _expected, has_audio, _warnings, caption_cwd = (
            self._compile(seq, (0, 0)))
        video_label = "[outv]"
        try:
            if size is not None:
                graph = (graph + f";[outv]scale={size[0]}:{size[1]}:"
                         f"force_original_aspect_ratio=decrease[previewv]")
                video_label = "[previewv]"
            t_sec = float(t_rat.to_fraction())
            cmd: list[str] = [self.ffmpeg, "-y"]
            for src in input_paths:
                if _is_image_src(src):
                    fps_rat = seq.fps.to_fraction()
                    cmd += ["-loop", "1", "-framerate",
                            f"{fps_rat.numerator}/{fps_rat.denominator}",
                            "-i", src]
                else:
                    cmd += ["-i", src]
            cmd += ["-filter_complex", graph]
            if has_audio:
                # 完整编译图包含 [outa]；静帧不输出音频，但必须消费它，
                # 否则 ffmpeg 会因未连接的 filter 输出而失败。
                cmd += ["-map", "[outa]", "-f", "null", "-"]
            cmd += ["-map", video_label,
                    "-ss", f"{t_sec:.6f}", "-frames:v", "1",
                    "-f", "image2", out_png]
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                cwd=caption_cwd if caption_cwd else None,
                encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired as e:
            raise RenderError(f"preview frame timed out: {e}") from e
        finally:
            # 清理字幕 ASS 临时目录（无论成功失败）
            if caption_cwd and os.path.isdir(caption_cwd):
                shutil.rmtree(caption_cwd, ignore_errors=True)
        if r.returncode != 0 or not os.path.isfile(out_png):
            raise RenderError(f"preview frame failed: {r.stderr[-400:]}")
        return out_png

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
        t_rat = Rational.from_float(timeline_time)
        graph, input_paths, _expected, _has_audio, _warnings, caption_cwd = (
            self._compile(seq, (0, 0), alpha=alpha))
        tmp_path = out_path + ".tmp" + (os.path.splitext(out_path)[1] or ".png")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        try:
            if alpha:
                # 静帧容器（PNG）原生支持 alpha；显式转 rgba 避免编码器选 rgb24
                graph = graph + ";[outv]format=rgba[outv]"
            else:
                # 明确 rgb24：否则 PNG 默认落在 rgba，会让 API 消费者误以为带透明
                graph = graph + ";[outv]format=rgb24[outv]"
            t_sec = float(t_rat.to_fraction())
            cmd: list[str] = [self.ffmpeg, "-y"]
            for src in input_paths:
                if _is_image_src(src):
                    fps_rat = seq.fps.to_fraction()
                    cmd += ["-loop", "1", "-framerate",
                            f"{fps_rat.numerator}/{fps_rat.denominator}",
                            "-i", src]
                else:
                    cmd += ["-i", src]
            cmd += ["-filter_complex", graph]
            if _has_audio:
                # 编译图里若生成了音轨，必须被消耗掉，否则 ffmpeg 报
                # "Filter 'anull' has output 0 (outa) unconnected"（静帧不要声音）
                cmd += ["-map", "[outa]", "-f", "null", "-"]
            cmd += ["-map", "[outv]",
                    "-ss", f"{t_sec:.6f}", "-frames:v", "1",
                    "-f", "image2", tmp_path]
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
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
                   allow_unknown_effects: bool = False) -> list[str]:
        """导出前预检。返回非阻断性 warning 列表（阻断性问题直接抛 RenderError）。"""
        seq = project.sequence
        video_tracks = [t for t in seq.tracks if t.kind == "video"]
        # 至少一条带可见片段的视频轨
        if not any(any(not c.hidden for c in t.clips) for t in video_tracks):
            raise RenderError(
                "EMPTY_TIMELINE: no visible video clips to render "
                "(empty timeline or all clips hidden)")

        # 所有视频 clip 必须指向真实存在的源文件（M0 不做代理回退）。
        # 复合片段（clip.nested 非空）是容器：内容在内部子序列，源在展开
        # 后的内层片段上（渲染收集时由 _flatten_compound 展开），此处跳过。
        for t in video_tracks:
            for c in t.clips:
                if c.hidden:
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
                if c.hidden:
                    continue
                src = c.asset_ref.source_path
                if not src:
                    raise RenderError(f"audio clip {c.id} has empty source_path")
                if not os.path.exists(src):
                    raise RenderError(
                        f"audio source file missing for clip {c.id}: {src}")

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

        return warnings

    # ------------------------------------------------------------------
    # 编译：工程快照 -> ffmpeg filter_complex + 输入列表
    # ------------------------------------------------------------------
    def _compile(self, seq: Sequence,
                 overlay_position: tuple[int, int],
                 alpha: bool = False
                 ) -> tuple[str, list[str], float, bool, list[str], Optional[str]]:
        warnings: list[str] = []
        # 渲染期标志（见 __init__ 注释）：供深层画布构建读取，避免层层传参漏传
        self._render_ctx.alpha = alpha

        # 音频源探测缓存：同一源文件只 probe 一次
        audio_cache: dict[str, bool] = {}

        def has_audio(src: str) -> bool:
            if src in audio_cache:
                return audio_cache[src]
            ok = self._has_audio_stream(src)
            audio_cache[src] = ok
            return ok

        # 源素材时长缓存（2026-09-21 修）：音轨的"期望时长"必须按**实际可渲染
        # 时长**算，而不是片段在时间线上的声明时长。反例：模板把 BGM 片段声明成
        # 覆盖 0→16.0s，但内置 ambient_pad.wav 只有 12.0s——atrim 只能取到 12.0s，
        # 实际音轨就是 12.0s。若 expected 仍按 16.0 算，成片校验会拿一个永远达不
        # 到的数字去比，导出被判失败（且用户看到的错误信息毫无指向性）。
        src_dur_cache: dict[str, float] = {}

        def source_duration(src: str) -> float:
            if src in src_dur_cache:
                return src_dur_cache[src]
            try:
                d = float(self.probe_media(src).get("duration") or 0.0)
            except Exception:  # noqa: BLE001 — 探测失败时回退到声明时长
                d = 0.0
            src_dur_cache[src] = d
            return d

        def effective_audio_dur(seg: tuple) -> float:
            """音频段实际可渲染时长 = min(声明时长, 源可用时长 / |speed|)。"""
            src, ss, tl_dur, speed = seg[0], seg[1], seg[2], seg[3]
            declared = float(tl_dur.to_fraction())
            total = source_duration(src)
            if total <= 0:
                return declared
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
        track_clips: list[list[Clip]] = []
        # 多机位（J08）：seq.multicam 存在时，只渲染 activeTrackId 那一轨
        # （其余机位轨是备选画面，不参与成片）——输出即「剪辑师当前切换机位」。
        multicam = getattr(seq, "multicam", None)
        multicam_active = (multicam or {}).get("activeTrackId") if isinstance(multicam, dict) else None
        for t in seq.tracks:
            if t.kind != "video" or not t.visible:
                continue
            if multicam_active is not None and t.id != multicam_active:
                continue  # 非活动机位轨：跳过渲染
            clips = sorted((c for c in t.clips if not c.hidden),
                           key=lambda c: c.timeline_start)
            # 复合片段（J08）：把 nested 子序列片段提升到外层时轴
            clips, flat_warnings = _flatten_compound(clips)
            warnings.extend(flat_warnings)
            if clips:
                track_clips.append(clips)

        if not track_clips:
            raise RenderError("no visible video track with clips")

        parts: list[str] = []

        # 视频分段 + 每轨编译（F14 变换 / F18 调色 / F20 转场 在此生效）
        vseg = [0]  # 用列表包一层，便于内层闭包自增

        track_streams: list[tuple[str, float]] = []
        for n, clips in enumerate(track_clips):
            label, dur = self._build_video_track(
                clips, seq, n, get_input, parts, vseg, overlay_position)
            track_streams.append((label, dur))

        track_labels = [lab for lab, _ in track_streams]
        track_durs = [dur for _, dur in track_streams]

        # 多轨：主轴取「绝对结束时间最长」的轨（A04：偏移轨不截断总长），
        # 其余轨作为叠加层 overlay 上去（shortest=0 跟随主轴时长）。
        longest_idx = track_durs.index(max(track_durs))
        track_labels[0], track_labels[longest_idx] = (
            track_labels[longest_idx], track_labels[0])
        track_durs[0], track_durs[longest_idx] = (
            track_durs[longest_idx], track_durs[0])

        # 多轨：primary 主轴叠加其余轨（位置可传参，默认左上角）
        x, y = int(overlay_position[0]), int(overlay_position[1])
        fps = seq.fps.to_fraction()
        fps_expr = f"{fps.numerator}/{fps.denominator}"

        if alpha:
            # V05 透明通道（2026-09-21）：透明导出**不能把主轴当底**——
            # 底层的像素 alpha 恒为 255，抠像/空白区域导出后仍是黑块。
            # 正确做法：底 = 全透明画布（color=black@0.0 + format=yuva420p），
            # 所有视频轨都 overlay 上去。实测：角落 alpha=0、前景 alpha=255，
            # 即 alpha 真实保留（配 prores_ks -profile:v 4 / yuva420p vp9）。
            canvas_dur = max(track_durs)
            layer_labels = list(track_labels)
            parts.append(
                f"color=c=black@0.0:s={seq.width}x{seq.height}:"
                f"r={fps_expr}:d={canvas_dur:.6f},format=yuva420p[alphabase]")
            current = "alphabase"
            for n, t_label in enumerate(layer_labels):
                out_label = ("ovout" if n == len(layer_labels) - 1
                             else f"aov{n}")
                # format=auto：让 overlay 跟随底层 alpha 格式，不强制 yuv420
                parts.append(
                    f"[{current}][{t_label}]overlay=shortest=0:"
                    f"eof_action=pass:format=auto:"
                    f"x={x}:y={y}[{out_label}]")
                current = out_label
        else:
            current = track_labels[0]
            for n, t_label in enumerate(track_labels[1:], start=1):
                out_label = "ovout" if n == len(track_labels) - 1 else f"ov{n}"
                # 主轴定长：shortest=0 让输出长度跟随 main（primary）轨；
                # eof_action=pass 使 overlay 片段结束后不再叠加（消失而非冻结末帧）
                parts.append(
                    f"[{current}][{t_label}]overlay=shortest=0:eof_action=pass:"
                    f"x={x}:y={y}[{out_label}]")
                current = out_label

        # 统一缩放到序列画布并规范帧率（输出符合 sequence 设置）
        parts.append(
            f"[{current}]scale={seq.width}:{seq.height},"
            f"fps={fps_expr}[outv]")

        # ---- 字幕叠加（F22/F24/M1 基础字幕进入成片）----
        # 与预览 extract_frame 复用同一段逻辑（prepare_caption_render 生成 ASS +
        # 字体副本临时目录；滤镜用相对文件名 + cwd 规避 Windows 绝对路径冒号冲突）。
        # 缺字体时 prepare_caption_render 抛 CaptionFontError（绝不静默丢字幕）。
        caption_cwd: Optional[str] = None
        # J04 文字图层轨：把 text 轨上的 cutvoke.text 片段转成 Caption，
        # 与 sequence.captions 合并进 ASS 字幕轨道（文字叠加到画面）。
        merge_captions = list(seq.captions)
        for _t in seq.tracks:
            if _t.kind != "text" or not _t.visible:
                continue
            for _c in sorted(_t.clips, key=lambda c: c.timeline_start):
                if _c.hidden:
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
                        color=str(_p.get("color", "#ffffff")),
                        strokeColor=str(_p.get("strokeColor", "#000000")),
                        strokeWidth=float(_p.get("strokeWidth", 2.0)),
                        background="",
                        align=str(_p.get("align", "center")),
                        bold=bool(_p.get("bold", False)),
                    )
                except (TypeError, ValueError):
                    continue  # 参数异常：跳过该文字片段，不阻断渲染
                if _cap.text and _cap.end > _cap.start:
                    merge_captions.append(_cap)
        if merge_captions:
            caption_cwd = prepare_caption_render(
                merge_captions, seq.width, seq.height)
            parts.append(
                f"[outv]ass=cutvoke_captions.ass:fontsdir=.[outv]")

        # 主轴时长：取所有视频轨的绝对结束时间最大值（A04：偏移轨不能截断总长）
        video_expected = max(track_durs)

        # ---- 音频轨收集（T16 混音）----
        # 每条「音轨」= 一组可 concat 的音频分段：
        #   * 音频轨（kind=='audio'，未静音、可见）的可见片段
        #   * 视频轨的可见片段，若其源文件本身含音轨则一并提取（每条视频轨成一轨道）
        audio_groups: list[list[tuple[str, float, float]]] = []

        def _collect_audio_segs(clips: list[Clip]) -> list[tuple]:
            segs: list[tuple] = []
            for c in sorted(clips, key=lambda c: c.timeline_start):
                src = c.asset_ref.source_path
                if not has_audio(src):
                    continue
                ss = float(c.source_start.to_fraction())
                # 该片段上的音频特效（如 loudnorm），在逐段音频链应用
                afx = self._enabled_audio_fx(c)
                segs.append((src, ss, c.duration, c.speed, c.volume,
                             c.fade_in, c.fade_out, c.pitch, afx))
            return segs

        # 音频轨（静音轨不产生音轨）
        for t in audio_tracks:
            if not t.visible or t.muted:
                continue
            segs = _collect_audio_segs([c for c in t.clips if not c.hidden])
            if segs:
                audio_groups.append(segs)

        # 视频轨：默认提取嵌入音轨
        for t in seq.tracks:
            if t.kind != "video" or not t.visible:
                continue
            segs = _collect_audio_segs([c for c in t.clips if not c.hidden])
            if segs:
                audio_groups.append(segs)

        has_audio = bool(audio_groups)
        audio_expected = 0.0
        if has_audio:
            sr = int(seq.audio_sample_rate)
            substreams: list[str] = []
            aseg = 0
            for g, segs in enumerate(audio_groups):
                labels: list[str] = []
                for (src, ss, tl_dur, speed, vol, fade_in, fade_out, pitch, afx) in segs:
                    ai = get_input(src)
                    seg = f"aseg{aseg}"
                    aseg += 1
                    # 变速：源素材覆盖时长 = 时间线时长 * |speed|（有理数精确）
                    src_dur = float((tl_dur * _abs_rat(speed)).to_fraction())
                    speed_f = float(speed.to_fraction())
                    if speed_f >= 0:
                        # 正向：atempo 链缩放播放速度（speed=1 无害透传）
                        atempo = _atempo_chain(speed_f)
                        speed_filters = "," + ",".join(atempo) if atempo else ""
                    else:
                        # 倒放：areverse 翻转，asetpts=N/SR/TB 复位单调 PTS，
                        # 再 atempo 链按 |speed| 缩放时长
                        abs_f = float(_abs_rat(speed).to_fraction())
                        atempo = _atempo_chain(abs_f)
                        speed_filters = (",areverse,asetpts=N/SR/TB,"
                                         + ",".join(atempo))
                    # 音频音量 / 淡入淡出（D06 F27/F28）
                    audio_fx = ""
                    vol_f = float(vol.to_fraction())
                    if abs(vol_f - 1.0) > 1e-6:
                        audio_fx += f",volume={vol_f:.4f}"
                    fade_in_f = float(fade_in.to_fraction())
                    fade_out_f = float(fade_out.to_fraction())
                    # 输出的音频段真实时长 = 时间线时长（变速/倒放后仍等于 tl_dur）
                    seg_dur = float(tl_dur.to_fraction())
                    # afade 时长不能超过段时长
                    if fade_in_f > 0:
                        fi = min(fade_in_f, max(seg_dur - 0.01, 0.0))
                        if fi > 0:
                            audio_fx += f",afade=t=in:st=0:d={fi:.4f}"
                    if fade_out_f > 0:
                        fo = min(fade_out_f, max(seg_dur - 0.01, 0.0))
                        if fo > 0:
                            audio_fx += f",afade=t=out:st={max(seg_dur - fo, 0):.4f}:d={fo:.4f}"
                    # 音高（1.5-C 变声）：asetrate 改采样率（变调）→ aresample 还原采样率
                    # → atempo 补偿时长（保持原时长不变，只变音高）。
                    pitch_f = float(pitch.to_fraction())
                    if abs(pitch_f - 1.0) > 1e-6:
                        audio_fx += (f",asetrate={int(sr * pitch_f)},"
                                     f"atempo={pitch_f:.4f}")
                    # 音频特效（J06：loudnorm 响度标准化等）：逐段应用，
                    # 单遍近似（不依赖双遍测量），作为片段级滤镜生效。
                    for e in (afx or []):
                        key, params = self._fx_key_params(e)
                        fn = _AUDIO_FX_STEPS.get(key)
                        if fn is not None:
                            audio_fx += "," + fn(params)
                    parts.append(
                        f"[{ai}:a]atrim=start={ss}:duration={src_dur},"
                        f"asetpts=PTS-STARTPTS{speed_filters}{audio_fx}"
                        f",aresample={sr},"
                        f"aformat=sample_fmts=fltp:sample_rates={sr}:"
                        f"channel_layouts=stereo[{seg}]")
                    labels.append(seg)
                gtag = f"atk{g}"
                if len(labels) == 1:
                    parts.append(f"[{labels[0]}]anull[{gtag}]")
                else:
                    ins = "".join(f"[{s}]" for s in labels)
                    parts.append(
                        f"{ins}concat=n={len(labels)}:v=0:a=1[{gtag}]")
                substreams.append(gtag)
                # 该轨道时长 = 各段**实际可渲染**时长之和（受源素材长度与变速约束，
                # 不是时间线声明时长——见 effective_audio_dur 的说明）
                audio_expected = max(
                    audio_expected, sum(effective_audio_dur(s) for s in segs))

            # 多条音轨 amix；单条直接透传 [outa]
            if len(substreams) == 1:
                parts.append(f"[{substreams[0]}]anull[outa]")
            else:
                ins = "".join(f"[{s}]" for s in substreams)
                parts.append(
                    f"{ins}amix=inputs={len(substreams)}:"
                    f"duration=longest:normalize=0[outa]")

        # 音频轨存在但没提取出任何可用音轨 -> 明确 warning，不静默丢音频
        if audio_track_present and not has_audio:
            warnings.append(
                "audio track(s) present but no decodable audio stream found in "
                "their source files; output is silent (no audio mixed)")

        # V05：透明导出在最后一跳统一回到带 alpha 的格式，避免中途任何
        # 一步（overlay/fps/字幕合成）把 alpha 悄悄降成 yuv420p 后被编码器
        # 静默丢弃——宁可这里显式转换，也不让成片"看起来成功但没透明"。
        if alpha:
            parts.append("[outv]format=yuva420p[outv]")

        graph = ";".join(parts)
        # 期望总时长取视频主轴与音频最长轨的较大者；amix duration=longest 与之对齐
        expected = max(video_expected, audio_expected)
        return graph, input_paths, expected, has_audio, warnings, caption_cwd

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
        if _find_animation(clip) is not None:
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
                            overlay_position: tuple[int, int]
                            ) -> tuple[str, float]:
        """编译一条视频轨为单条视频流 [vtk{n}]，返回 (label, 时长秒)。

        - 普通片段（无色/无变换）：沿用旧路径——trim + 变速后直接 concat（不回退）
        - 含调色(eq)：尺寸不变，inline 插入 eq 滤镜
        - 含变换/透明度/转场：整轨走「画布合成 + xfade」，位置/缩放/旋转/透明度生效
        """
        fps = seq.fps.to_fraction()
        fps_expr = f"{fps.numerator}/{fps.denominator}"
        needs_canvas = any(self._clip_needs_canvas(c) for c in clips)

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
                    c, get_input, parts, vseg)
            clip_labels.append(label)

        # 组装轨内片段（A04：按绝对时间线位置，中间/开头空白必须保留）
        #
        # 两个游标必须分开（2026-09-21 修）：旧实现只用一个 cum 兼作两种语义，
        # 造成带转场的时间线导出直接失败 + 画面错位。
        #   tl   = 时间线游标：只按片段绝对时间线位置推进，用来判断"真实空白"；
        #   film = 成片游标：按本轨实际已编长度推进，是 xfade 的 offset 基准与返回时长。
        # 旧逻辑在转场分支做 `cum += dur - d`（成片确实因两段重叠 d 而少 d），
        # 随后下一片段的 `abs_start > cum` 判断就把它误当成"时间线空白"而调
        # _pad_black —— 而 _pad_black 用 tpad start_mode=add 在**已合成流头部**插黑，
        # 会把前面所有内容整体后移，既产生非预期黑场错位，又因末段转场之后
        # 没有下一片段来"补"而让成片比时间线短 d（5×3.2s + 4×0.6s 转场 =>
        # 3.2+4×(3.2-0.6)=15.4s，而时间线是 16.0s）。
        current = clip_labels[0]
        first_start = float(clips[0].timeline_start.to_fraction())
        tl = first_start
        film = first_start
        # 开头空白：第一片段不在 t=0 → 补在流**前面**
        if first_start > 0:
            current = self._pad_black(current, first_start, parts, fps_expr,
                                      at_start=True)
        tl += clip_durs[0]
        film += clip_durs[0]
        for i in range(1, len(clip_labels)):
            # 绝对时间起点与**时间线游标**的差 = 真实空白（转场不产生空白）
            abs_start = float(clips[i].timeline_start.to_fraction())
            if abs_start > tl + 1e-6:
                gap = abs_start - tl
                # 中间空白 → 必须补在流**后面**，否则前面内容整体后移、音画不同步
                current = self._pad_black(current, gap, parts, fps_expr)
                film += gap
                tl = abs_start
            tr = find_transition(clips[i], self.effects)
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
                # offset 是成片坐标下 A 流开始混合的时间点；两段重叠 d 秒，
                # 故成片只增长 (dur - d)，而时间线照常推进 dur。
                offset = film - d
                out = f"xf{n}_{i}"
                # xfade 的 transition 名来自效果清单声明（AC18：新增转场无需改内核）
                xname = xfade_transition_name(clips[i], self.effects)
                # xfade 要求两路同 timebase；concat 输出默认微秒时基(1/1000000)，
                # 与单片段 fps= 的 1/fps 时基不一致会直接报错。这里两路统一
                # settb=AVTB（ffmpeg 通用微秒时基），offset/duration 仍按秒解析。
                parts.append(
                    f"[{current}]settb=AVTB[inA];"
                    f"[{clip_labels[i]}]settb=AVTB[inB];"
                    f"[inA][inB]"
                    f"xfade=transition={xname}:offset={offset:.6f}:"
                    f"duration={d:.6f}[{out}]")
                current = out
                film = film + clip_durs[i] - d
                tl += clip_durs[i]
                continue
            # 无转场：concat（画布合成轨尺寸已一致）
            current = self._concat2(current, clip_labels[i], parts)
            film += clip_durs[i]
            tl += clip_durs[i]

        out_label = f"vtk{n}"
        parts.append(f"[{current}]null[{out_label}]")
        return out_label, film

    def _pad_black(self, label: str, pad_secs: float, parts: list[str],
                   fps_expr: str, at_start: bool = False) -> str:
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
        if at_start:
            parts.append(
                f"[{label}]tpad=start_mode=add:start_duration={pad_secs:.6f}"
                f",fps={fps_expr},setpts=PTS-STARTPTS[{out}]")
        else:
            parts.append(
                f"[{label}]tpad=stop_mode=add:stop_duration={pad_secs:.6f}"
                f",fps={fps_expr},setpts=PTS-STARTPTS[{out}]")
        return out

    def _concat2(self, a: str, b: str, parts: list[str]) -> str:
        """concat 两路视频为一个流（用于轨内无转场拼接）。"""
        out = f"cc{len(parts)}"
        parts.append(f"[{a}][{b}]concat=n=2:v=1:a=0[{out}]")
        return out

    def _build_clip_plain(self, clip: Clip, get_input, parts: list[str],
                          vseg: list[int]) -> str:
        """旧路径单片段：trim + 变速（+ 可选 eq 调色），尺寸保持源尺寸。"""
        vi = get_input(clip.asset_ref.source_path)
        seg = f"vseg{vseg[0]}"
        vseg[0] += 1
        speed = clip.speed
        # 定格帧（B06）：源窗口恒定为 freeze_at 处 1 帧，再 tpad 克隆补满时间线时长。
        if clip.freeze_at is not None:
            ss = float(clip.freeze_at.to_fraction())
            tl_dur = float(clip.duration.to_fraction())
            # 定格：截 freeze_at 处 1 帧（trim 短窗），tpad 克隆补满时间线时长。
            # setpts 先行归零起点，避免 clone 帧 PTS 错乱（实测 5s/150 帧正确）。
            # 注：帧内容全同由无损编码验证（损失编码量化会致 framemd5 不一致）。
            chain = (f"[{vi}:v]trim=start={ss:.6f}:duration=0.033,"
                     f"setpts=PTS-STARTPTS,tpad=stop_mode=clone:"
                     f"stop_duration={tl_dur:.6f}")
            col = clip.color_grade()
            if (col["brightness"] != 0.0 or col["contrast"] != 1.0
                    or col["saturation"] != 1.0):
                chain += (f",eq=brightness={col['brightness']:.6f}:"
                          f"contrast={col['contrast']:.6f}:"
                          f"saturation={col['saturation']:.6f}")
            if self._has_fx(clip):
                mid = f"vfx{vseg[0]}"
                vseg[0] += 1
                parts.append(f"{chain}[{mid}]")
                return self._apply_fx(clip, mid, parts, vseg)
            parts.append(f"{chain}[{seg}]")
            return seg
        src_dur = float((clip.duration * _abs_rat(speed)).to_fraction())
        ss = float(clip.source_start.to_fraction())
        speed_f = float(speed.to_fraction())
        if speed_f >= 0:
            speed_expr = f"setpts=PTS/{speed_f}"
        else:
            abs_f = float(_abs_rat(speed).to_fraction())
            speed_expr = (f"reverse,setpts=N/FRAME_RATE/TB,"
                          f"setpts=PTS/{abs_f}")
        chain = (f"[{vi}:v]trim=start={ss}:duration={src_dur},"
                 f"setpts=PTS-STARTPTS,{speed_expr}")
        # F18 调色（尺寸不变，inline）
        col = clip.color_grade()
        if (col["brightness"] != 0.0 or col["contrast"] != 1.0
                or col["saturation"] != 1.0):
            chain += (f",eq=brightness={col['brightness']:.6f}:"
                      f"contrast={col['contrast']:.6f}:"
                      f"saturation={col['saturation']:.6f}")
        # J01 效果栈：fx 画面滤镜按效果顺序应用（可能多流，走显式建图）
        if self._has_fx(clip):
            mid = f"vfx{vseg[0]}"
            vseg[0] += 1
            parts.append(f"{chain}[{mid}]")
            return self._apply_fx(clip, mid, parts, vseg)
        parts.append(f"{chain}[{seg}]")
        return seg

    def _scale_to_canvas(self, cur: str, W: int, H: int,
                         sx: float, sy: float,
                         parts: list[str], vseg: list[int]) -> str:
        """把片段流归一到画布尺寸。

        语义：`scale` 是**画布占比**（1.0 = 铺满画布）；transform 的用途是
        画中画与多轨合成，0<scale<1 即缩小成 PiP。

        必须**始终**插入 scale，不能因 scale==1.0 就跳过：源尺寸与画布尺寸不等时
        （如 320x240 素材放进 1920x1080 工程），跳过会让片段以**原生像素**贴在画布
        左上角——实测只占 3.7% 面积、其余全黑；而且与普通路径末尾的
        `scale=W:H`（铺满）行为不一致，会出现"给片段加一个转场，画面尺寸就变了"。
        """
        sw = max(1, round(W * sx))
        sh = max(1, round(H * sy))
        nxt = f"vs{vseg[0]}"
        vseg[0] += 1
        parts.append(f"[{cur}]scale={sw}:{sh}[{nxt}]")
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
        anim = _find_animation(clip)
        if anim is not None:
            return self._build_animated_clip(
                clip, seq, get_input, parts, vseg, fps_expr, color, W, H, anim)

        opacity_kfs = clip.keyframes.get("opacity")
        if opacity_kfs:
            return self._build_clip_opacity_keyframes(
                clip, seq, get_input, parts, vseg, fps_expr, color, W, H)

        # 常数透明度（来自 transform 的 opacity 参数；默认 1.0）
        tr = clip.transform()
        opacity = tr["opacity"]

        # 1) trim + 变速 + 调色（源尺寸）
        base = self._trim_speed_color(clip, get_input, parts, vseg, color)
        cur = base

        # 2) 归一到画布（必须无条件做，见 _scale_to_canvas 的说明）
        cur = self._scale_to_canvas(cur, W, H, tr["scale"], tr["scale"],
                                    parts, vseg)

        # 3) 旋转（度 -> 弧度）
        if tr["rotation"] != 0.0:
            rad = math.radians(tr["rotation"])
            nxt = f"vr{vseg[0]}"
            vseg[0] += 1
            parts.append(f"[{cur}]rotate=angle={rad:.6f}[{nxt}]")
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

    def _build_animated_clip(self, clip: Clip, seq: Sequence, get_input,
                            parts: list[str], vseg: list[int], fps_expr: str,
                            color: dict, W: int, H: int, anim: dict) -> str:
        """入场动画（1.5-A/B）：把动画效果渲染成时间插值的画布流。

        统一机制：把动画时长切成 N 个子段，每段按插值取中点常数
        (opacity/scale/position)，逐段渲染后 concat。fadeIn/zoomIn/slideIn
        都走同一条路径，得到真实的时间维运动，而非静态起始状态。

        缓动：easing 参数影响插值曲线（linear / ease-in / ease-out）。
        """
        eid = anim.get("effectId", "")
        params = anim.get("params", {}) or {}
        duration = float(params.get("duration", 1.0))
        easing = params.get("easing", "linear")
        dur_f = float(clip.duration.to_fraction())
        duration = min(max(duration, 0.05), dur_f)

        def _ease(t: float) -> float:
            if easing == "ease-in":
                return t * t
            if easing == "ease-out":
                return 1.0 - (1.0 - t) * (1.0 - t)
            return t  # linear

        def _state_at(t: float) -> dict:
            """返回片段局部时间 t 的合成状态。

            入场/出场动画：t 是**动画进度 p∈[0,1]**（已缓动）。
            循环动画：t 是**绝对秒**（用于正弦相位）。

            返回 opacity / scale / sx / sy / rot / x / y。
            sx/sy 为分轴缩放（翻转、遮罩显现用），缺省时渲染层回退到 scale。
            """
            p = _ease(t)
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
            elif eid == "cutvoke.anim.reveal":         # 遮罩显现（沿方向揭示）
                d = params.get("direction", "left")
                if d in ("up", "down"):
                    st["sx"], st["sy"] = 1.0, max(0.01, p)
                    st["y"] = int(H * (1.0 - p)) if d == "up" else 0
                else:
                    st["sx"], st["sy"] = max(0.01, p), 1.0
                    st["x"] = int(W * (1.0 - p)) if d == "right" else 0
            elif eid == "cutvoke.anim.fadeOut":
                st["opacity"] = 1.0 - p
            elif eid == "cutvoke.anim.zoomOut":
                to_scale = float(params.get("toScale", 0.8))
                st["scale"] = 1.0 - (1.0 - to_scale) * p
            elif eid in ("cutvoke.anim.breathe", "cutvoke.anim.float",
                         "cutvoke.anim.sway"):
                # 循环：t 为绝对秒；period 秒一个周期
                period = float(params.get("period", 2.0))
                amp = float(params.get("amplitude", 0.03))
                phase = (t / period) * 2.0 * math.pi if period > 0 else 0.0
                if eid == "cutvoke.anim.breathe":
                    st["scale"] = 1.0 + amp * math.sin(phase)
                elif eid == "cutvoke.anim.float":
                    # 轻微放大做余量，位移时四边不露黑
                    st["scale"] = 1.0 + amp
                    st["y"] = int(H * amp * math.sin(phase))
                else:  # sway
                    st["scale"] = 1.0 + amp
                    st["x"] = int(W * amp * math.sin(phase))
            elif eid.startswith("cutvoke.anim.combo"):   # 组合动画 / 组合运镜预设（E04/D04）
                # primary/secondary 各自按 p 计算基础状态，然后合成：
                # opacity/scale 相乘（两动画同时透明/缩放）、x/y/rot 相加（位移叠加）。
                # D04：预设效果（cutvoke.anim.comboXxx）复用同一合成逻辑，
                # 仅把 primary/secondary 在 spec 里固化为预设值，内核零新增分支。
                def _combo_base(sel: str, dist: float, fscale: float,
                                toScale: float = 0.8) -> dict:
                    s = {"opacity": 1.0, "scale": 1.0, "x": 0, "y": 0, "rot": 0.0}
                    if sel == "fadeIn":
                        s["opacity"] = p
                    elif sel == "zoomIn":
                        s["scale"] = fscale + (1.0 - fscale) * p
                    elif sel == "zoomOut":
                        s["scale"] = 1.0 + (toScale - 1.0) * p
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
                        s["opacity"] = p
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
                st["x"] = a["x"] + b["x"]
                st["y"] = a["y"] + b["y"]
                st["rot"] = a["rot"] + b["rot"]
            return st

        # 动画时序：入场在 [0,duration]、出场在 [dur-duration,dur]、循环贯穿整段
        OUT_ANIMS = ("cutvoke.anim.fadeOut", "cutvoke.anim.zoomOut")
        LOOP_ANIMS = ("cutvoke.anim.breathe", "cutvoke.anim.float",
                      "cutvoke.anim.sway")
        is_out = eid in OUT_ANIMS
        is_loop = eid in LOOP_ANIMS
        tr = clip.transform()
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

        if is_loop:
            # 循环动画：整段帧对齐切分，每段 6 帧（~0.2s @30fps）
            edges = _frame_edges(dur_f, max(1, int(round(dur_f / (frame_dur * 6)))))
            for a, b in zip(edges, edges[1:]):
                if b <= a:
                    continue
                boundaries.append((a, b, _state_at((a + b) / 2.0)))
        elif is_out:
            # 出场：前段静止，末段帧对齐插值
            anim_end = min(duration, dur_f)
            edges = _frame_edges(anim_end, max(2, min(20, int(anim_end / 0.1) + 1)))
            start = dur_f - edges[-1]
            if start > 1e-6:
                boundaries.append((0.0, start, dict(rest)))
            total = edges[-1]
            for a, b in zip(edges, edges[1:]):
                ma = a + (b - a) / 2.0
                p = ma / total if total > 0 else 1.0
                boundaries.append((start + a, start + b, _state_at(p)))
        else:
            # 入场：动画段在 [0,duration]，后段静止
            anim_end = min(duration, dur_f)
            edges = _frame_edges(anim_end, max(2, min(20, int(anim_end / 0.1) + 1)))
            total = edges[-1]
            for a, b in zip(edges, edges[1:]):
                ma = a + (b - a) / 2.0
                boundaries.append((a, b, _state_at(ma / total if total > 0 else 1.0)))
            if total < dur_f - 1e-6:
                boundaries.append((total, dur_f, dict(rest)))

        speed = clip.speed
        src_dur = float((clip.duration * _abs_rat(speed)).to_fraction())
        ss = float(clip.source_start.to_fraction())
        speed_f = float(speed.to_fraction())
        for (a, b, st) in boundaries:
            sub_local = b - a
            if sub_local <= 0:
                continue
            sub_ss = ss + (a / dur_f) * src_dur if dur_f > 0 else ss
            sub_src_dur = sub_local * (src_dur / dur_f) if dur_f > 0 else 0.0
            seg = f"vseg{vseg[0]}"; vseg[0] += 1
            if speed_f >= 0:
                speed_expr = f"setpts=PTS/{speed_f}"
            else:
                abs_f = float(_abs_rat(speed).to_fraction())
                speed_expr = (f"reverse,setpts=N/FRAME_RATE/TB,"
                              f"setpts=PTS/{abs_f}")
            chain = (f"[{get_input(clip.asset_ref.source_path)}:v]"
                     f"trim=start={sub_ss:.6f}:duration={sub_src_dur:.6f},"
                     f"setpts=PTS-STARTPTS,{speed_expr}")
            if (color["brightness"] != 0.0 or color["contrast"] != 1.0
                    or color["saturation"] != 1.0):
                chain += (f",eq=brightness={color['brightness']:.6f}:"
                          f"contrast={color['contrast']:.6f}:"
                          f"saturation={color['saturation']:.6f}")
            base = f"va{vseg[0]}"
            vseg[0] += 1
            parts.append(f"{chain}[{base}]")
            # J01 效果栈：动画子段同样应用 fx（顺序/旁路一致）
            cur = (self._apply_fx(clip, base, parts, vseg)
                   if self._has_fx(clip) else base)
            sc = float(st.get("scale", 1.0))
            sx = st.get("sx")
            sy = st.get("sy")
            sx = sc if sx is None else float(sx)
            sy = sc if sy is None else float(sy)
            cur = self._scale_to_canvas(cur, W, H, sx, sy, parts, vseg)
            rot = float(st.get("rot", 0.0))
            if abs(rot) > 1e-6:
                nxt = f"vr{vseg[0]}"; vseg[0] += 1
                parts.append(
                    f"[{cur}]rotate={math.radians(rot):.6f}:"
                    f"ow=iw:oh=ih:c=black[{nxt}]")
                cur = nxt
            if st["opacity"] < 1.0:
                nxt = f"va{vseg[0]}"; vseg[0] += 1
                parts.append(f"[{cur}]format=rgba,"
                             f"colorchannelmixer=aa={st['opacity']:.6f}[{nxt}]")
                cur = nxt
            sub_labels.append(
                self._composite_on_canvas(
                    cur, clip, seq, parts, vseg, fps_expr, W, H,
                    {"x": st["x"] + int(tr["position"]["x"]),
                     "y": st["y"] + int(tr["position"]["y"])},
                    sub_local))

        if len(sub_labels) == 1:
            return sub_labels[0]
        ins = "".join(f"[{s}]" for s in sub_labels)
        out = f"canim{vseg[0]}"; vseg[0] += 1
        parts.append(f"{ins}concat=n={len(sub_labels)}:v=1:a=0[{out}]")
        return out

    def _trim_speed_color(self, clip: Clip, get_input, parts: list[str],
                          vseg: list[int], color: dict) -> str:
        """生成 trim + 变速（+ eq 调色）的源尺寸分段，返回 label。"""
        vi = get_input(clip.asset_ref.source_path)
        seg = f"vseg{vseg[0]}"
        vseg[0] += 1
        speed = clip.speed
        ss = float(clip.source_start.to_fraction())
        src_dur = float((clip.duration * _abs_rat(speed)).to_fraction())
        speed_f = float(speed.to_fraction())
        if speed_f >= 0:
            speed_expr = f"setpts=PTS/{speed_f}"
        else:
            abs_f = float(_abs_rat(speed).to_fraction())
            speed_expr = (f"reverse,setpts=N/FRAME_RATE/TB,"
                          f"setpts=PTS/{abs_f}")
        chain = (f"[{vi}:v]trim=start={ss}:duration={src_dur},"
                 f"setpts=PTS-STARTPTS,{speed_expr}")
        if (color["brightness"] != 0.0 or color["contrast"] != 1.0
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

    def _apply_fx(self, clip: Clip, cur: str, parts: list[str],
                  vseg: list[int]) -> str:
        """按效果栈顺序把片段上的 fx 滤镜接到标签 cur 上，返回末标签。"""
        for i, e in enumerate(self._enabled_fx(clip)):
            key, params = self._fx_key_params(e)
            cur = self._fx_one(key, params, cur, parts, vseg, i)
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
        if key in _FX_MULTI:
            return self._fx_glow(params, cur, parts, vseg, idx)
        fn = _FX_STEPS.get(key)
        expr = fn(params) if fn is not None else key  # 表外键按字面滤镜表达式
        if not expr:
            return cur
        nxt = f"vfx{idx}_{vseg[0]}"
        vseg[0] += 1
        parts.append(f"[{cur}]{expr}[{nxt}]")
        return nxt

    def _fx_glow(self, params: dict, cur: str, parts: list[str],
                 vseg: list[int], idx: int) -> str:
        """发光：原图与模糊图以 screen 模式叠加（需要 split 多流建图）。"""
        intensity = float(params.get("intensity", 1.0))
        a = f"vga{idx}_{vseg[0]}"
        vseg[0] += 1
        b = f"vgb{idx}_{vseg[0]}"
        vseg[0] += 1
        blurred = f"vgc{idx}_{vseg[0]}"
        vseg[0] += 1
        out = f"vgo{idx}_{vseg[0]}"
        vseg[0] += 1
        parts.append(f"[{cur}]split=2[{a}][{b}]")
        parts.append(f"[{b}]gblur=sigma=12[{blurred}]")
        parts.append(f"[{a}][{blurred}]blend=all_mode=screen:"
                     f"all_opacity={max(0.05, min(1.0, intensity / 2.0)):.3f}[{out}]")
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
        # format=auto：跟随画布 alpha 格式，透明导出时不要把 alpha 压掉
        parts.append(
            f"[{bg}][{src_label}]overlay=shortest=0:eof_action=pass:"
            f"format=auto:x={x}:y={y}[{out}]")
        # 统一帧率，保证 xfade/concat 尺寸与 tb 一致
        fin = f"cf{vseg[0]}"
        vseg[0] += 1
        parts.append(f"[{out}]fps={fps_expr}[{fin}]")
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

        if float(speed.to_fraction()) < 0:
            # 倒放 + 透明度关键帧：当前退化为整体中点常数透明度
            op = kf_evaluate(kfs, dur / 2)
            base = self._trim_speed_color(clip, get_input, parts, vseg, color)
            cur = self._scale_to_canvas(base, W, H, tr["scale"], tr["scale"],
                                        parts, vseg)
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
            sub_ss = ss + float((a_t.to_fraction() / dur_f)) * src_dur
            sub_src_dur = sub_local * (src_dur / dur_f) if dur_f > 0 else 0.0
            # 子段常数透明度 = 中点求值
            mid = a_t + (b_t - a_t) * Rational.of(1, 2)
            op = float(kf_evaluate(kfs, mid))
            # 生成该子段的 trim 源窗
            seg = f"vseg{vseg[0]}"
            vseg[0] += 1
            speed_f = float(speed.to_fraction())
            if speed_f >= 0:
                speed_expr = f"setpts=PTS/{speed_f}"
            else:
                abs_f = float(_abs_rat(speed).to_fraction())
                speed_expr = (f"reverse,setpts=N/FRAME_RATE/TB,"
                              f"setpts=PTS/{abs_f}")
            chain = (f"[{get_input(clip.asset_ref.source_path)}:v]"
                     f"trim=start={sub_ss:.6f}:duration={sub_src_dur:.6f},"
                     f"setpts=PTS-STARTPTS,{speed_expr}")
            if (color["brightness"] != 0.0 or color["contrast"] != 1.0
                    or color["saturation"] != 1.0):
                chain += (f",eq=brightness={color['brightness']:.6f}:"
                          f"contrast={color['contrast']:.6f}:"
                          f"saturation={color['saturation']:.6f}")
            chain += f"[{seg}]"
            parts.append(chain)
            cur = self._scale_to_canvas(seg, W, H, tr["scale"], tr["scale"],
                                        parts, vseg)
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
    def probe_media(self, src: str,
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
            "-show_entries", "stream=codec_type,width,height,pix_fmt",
            "-of", "json", src,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
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
        return {
            "duration": duration,
            "width": int(v.get("width", 0)) if v else 0,
            "height": int(v.get("height", 0)) if v else 0,
            "has_video": v is not None,
            "has_audio": a is not None,
            "format": str(fmt.get("format_name", "")),
            # V03：静帧/透明导出要判 alpha 是否真的在（PNG 常被判成 rgba/rgb24）
            "pix_fmt": v.get("pix_fmt") if v else None,
        }

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
            self.ffmpeg, "-y",
            "-ss", f"{time:.4f}",
            "-i", src,
            "-frames:v", "1",
            "-vf", f"scale={width}:-2",
            "-f", "image2", out_png,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise RenderError(f"thumbnail timed out on {src!r}") from e
        if r.returncode != 0 or not os.path.isfile(out_png):
            raise RenderError(f"thumbnail failed on {src!r}: {r.stderr[-400:]}")
        return out_png

    def _has_audio_stream(self, src: str) -> bool:
        cmd = [
            self.ffprobe, "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "json", src,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
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
                    color_chain: str = "") -> None:
        preset = QUALITY_PRESETS.get(quality)
        if preset is None:
            raise RenderError(f"unknown quality preset: {quality!r} "
                              f"(available: {list(QUALITY_PRESETS)})")

        # O06 色彩管理：把滤镜链追加在 [outv] 之后，产出新的输出标签 [outvcm]。
        # 这样色彩管理是"导出级后处理"，完全不侵入既有效果/转场/字幕编译逻辑。
        out_label = "[outv]"
        if color_chain:
            graph = f"{graph};[outv]{color_chain}[outvcm]"
            out_label = "[outvcm]"

        cmd: list[str] = [self.ffmpeg, "-y"]
        for src in input_paths:
            if _is_image_src(src):
                # 静态图片：-loop 1 无限循环 + 显式帧率，使单帧源能作为
                # 有持续时间的片段参与时间线（1.5-A 图片素材，F03/F07）。
                fps_rat = seq.fps.to_fraction()
                cmd += ["-loop", "1", "-framerate",
                        f"{fps_rat.numerator}/{fps_rat.denominator}", "-i", src]
            else:
                cmd += ["-i", src]

        ext = os.path.splitext(out_path)[1].lower()
        if alpha:
            # V05：ProRes 4444（剪辑软件交接透明素材的通用口径）。
            # 容器白名单已在 render() 入口收敛，这里只可能有 .mov。
            cmd += ["-filter_complex", graph, "-map", "[outv]",
                    "-c:v", "prores_ks", "-profile:v", "4",
                    "-pix_fmt", "yuva444p10le"]
            if has_audio:
                cmd += ["-map", "[outa]", "-c:a", "pcm_s16le"]
            cmd += [out_path]
        else:
            cmd += [
                "-filter_complex", graph,
                "-map", out_label,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
            ]
            # V01 导出预设：给 video_bitrate_kbps 用 -b:v 目标码率，否则走 crf 画质档
            if video_bitrate_kbps:
                cmd += ["-b:v", f"{video_bitrate_kbps}k"]
            else:
                cmd += ["-crf", preset["crf"], "-preset", preset["preset"]]
            # 有音频才加音轨，保证「无音频工程导出无声视频」且不报错
            if has_audio:
                cmd += ["-map", "[outa]", "-c:a", "aac",
                        "-b:a", f"{audio_bitrate_kbps}k" if audio_bitrate_kbps else "192k"]
            cmd += [
                "-movflags", "+faststart",
                out_path,
            ]

        # 取消支持：用 Popen + 看门狗线程，在 cancel_event 置位时终止 ffmpeg
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                cwd=caption_cwd if caption_cwd else None)
        watcher = None
        if cancel_event is not None:
            def _watch() -> None:
                while proc.poll() is None:
                    if cancel_event.is_set():
                        try:
                            proc.terminate()
                        except OSError:
                            pass
                        return
                    cancel_event.wait(0.2)
            watcher = threading.Thread(target=_watch, daemon=True)
            watcher.start()

        stdout, stderr = proc.communicate()
        if watcher is not None:
            watcher.join(timeout=5)

        if cancel_event is not None and cancel_event.is_set():
            raise RenderCancelled("render cancelled before completion")
        if proc.returncode != 0:
            tail = (stderr or b"").decode("utf-8", "replace")[-1500:]
            raise RenderError(
                f"ffmpeg exited with code {proc.returncode}\n{tail}")

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
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
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
