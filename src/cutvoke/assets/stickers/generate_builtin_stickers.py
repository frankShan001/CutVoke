"""CutVoke 内置贴纸资产生成器（J07 素材内容扩展）。

用 PIL 生成一批真实可用、透明背景、风格统一（描边 + 几何形）的贴纸 PNG，
落到本目录（src/cutvoke/assets/stickers/）。首次服务启动时会经
core/builtin_assets.py 的 ensure_builtin_stickers() 目录扫描登记进素材账本
（与用户素材共存同表、幂等、不互删）。

设计约束：
- 尺寸 256×256，RGBA，透明背景（alpha 通道存在）。
- 统一风格：实色填充 + 深色描边，几何/箭头/符号为主，便于叠加在视频上。
- 仅生成「新增」贴纸；已存在的 7 个贴纸（star/rounded_blue/arrow_red/
  circle_green/check_green/exclaim_yellow/bolt_orange）保持不动，不重复生成。

运行方式（在仓库根目录）：
    PYTHONPATH=src python src/cutvoke/assets/stickers/generate_builtin_stickers.py
"""

from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))

# 工作分辨率（超采样后缩小，得到平滑边缘）
SS = 1024
OUT = 256

OUTLINE = (30, 30, 45, 235)
WHITE = (255, 255, 255, 255)


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # 旧版 PIL 不支持 size 参数
        return ImageFont.load_default()


def _new_layer() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _ow() -> int:
    return max(2, int(SS * 0.03))  # 描边宽度


# ---------------------------------------------------------------------------
# 个体贴纸绘制（在 SS 分辨率下绘制）
# ---------------------------------------------------------------------------

def _draw_arrow_up(d: ImageDraw.ImageDraw, color: tuple):
    cx = SS / 2
    sw = SS * 0.20
    d.rounded_rectangle([cx - sw / 2, SS * 0.30, cx + sw / 2, SS * 0.74],
                        radius=sw / 2, fill=color)
    hw, tip, base = SS * 0.40, SS * 0.16, SS * 0.46
    d.polygon([(cx, tip), (cx - hw, base), (cx + hw, base)], fill=color)


def draw_arrow(angle: int, color: tuple) -> Image.Image:
    img, d = _new_layer()
    _draw_arrow_up(d, color)
    if angle:
        img = img.rotate(angle, resample=Image.BICUBIC)
    return img


def draw_dot(color: tuple) -> Image.Image:
    img, d = _new_layer()
    d.ellipse([SS * 0.22, SS * 0.22, SS * 0.78, SS * 0.78],
              fill=color, outline=OUTLINE, width=_ow())
    return img


def draw_cross(color: tuple) -> Image.Image:
    img, d = _new_layer()
    w = SS * 0.16
    d.line([SS * 0.26, SS * 0.26, SS * 0.74, SS * 0.74],
           fill=color, width=int(w), joint="curve")
    d.line([SS * 0.74, SS * 0.26, SS * 0.26, SS * 0.74],
           fill=color, width=int(w), joint="curve")
    return img


def draw_plus(color: tuple) -> Image.Image:
    img, d = _new_layer()
    w = SS * 0.16
    d.line([SS * 0.5, SS * 0.2, SS * 0.5, SS * 0.8],
           fill=color, width=int(w), joint="curve")
    d.line([SS * 0.2, SS * 0.5, SS * 0.8, SS * 0.5],
           fill=color, width=int(w), joint="curve")
    return img


def draw_minus(color: tuple) -> Image.Image:
    img, d = _new_layer()
    w = SS * 0.16
    d.line([SS * 0.22, SS * 0.5, SS * 0.78, SS * 0.5],
           fill=color, width=int(w), joint="curve")
    return img


def draw_badge_text(ch: str, color: tuple) -> Image.Image:
    img, d = _new_layer()
    d.ellipse([SS * 0.14, SS * 0.14, SS * 0.86, SS * 0.86],
              fill=color, outline=OUTLINE, width=_ow())
    d.text((SS / 2, SS / 2), ch, font=_font(int(SS * 0.5)),
           fill=WHITE, anchor="mm")
    return img


def draw_speech_bubble(color: tuple) -> Image.Image:
    img, d = _new_layer()
    d.rounded_rectangle([SS * 0.16, SS * 0.18, SS * 0.84, SS * 0.66],
                        radius=SS * 0.12, fill=color,
                        outline=OUTLINE, width=_ow())
    d.polygon([(SS * 0.34, SS * 0.62), (SS * 0.52, SS * 0.62),
               (SS * 0.34, SS * 0.82)], fill=color)
    return img


def draw_tag(color: tuple) -> Image.Image:
    img, d = _new_layer()
    w = _ow()
    d.polygon([(SS * 0.32, SS * 0.30), (SS * 0.84, SS * 0.18),
               (SS * 0.84, SS * 0.82), (SS * 0.32, SS * 0.70)],
              fill=color, outline=OUTLINE, width=w)
    # 左侧圆孔（用深色+透明感表现）
    d.ellipse([SS * 0.42, SS * 0.42, SS * 0.52, SS * 0.52],
              fill=(255, 255, 255, 255))
    return img


def draw_timer(color: tuple) -> Image.Image:
    img, d = _new_layer()
    w = _ow()
    d.ellipse([SS * 0.20, SS * 0.26, SS * 0.80, SS * 0.86],
              fill=color, outline=OUTLINE, width=w)
    d.rectangle([SS * 0.44, SS * 0.12, SS * 0.56, SS * 0.28], fill=color)
    d.line([SS * 0.5, SS * 0.42, SS * 0.5, SS * 0.62],
           fill=WHITE, width=int(w * 0.7))
    d.line([SS * 0.5, SS * 0.5, SS * 0.63, SS * 0.58],
           fill=WHITE, width=int(w * 0.7))
    return img


def draw_music_note(color: tuple) -> Image.Image:
    img, d = _new_layer()
    w = _ow()
    d.ellipse([SS * 0.28, SS * 0.58, SS * 0.50, SS * 0.80], fill=color)
    d.line([SS * 0.49, SS * 0.62, SS * 0.49, SS * 0.20],
           fill=color, width=int(w * 0.9))
    d.polygon([(SS * 0.49, SS * 0.20), (SS * 0.66, SS * 0.26),
               (SS * 0.49, SS * 0.40)], fill=color)
    return img


def draw_magnifier(color: tuple) -> Image.Image:
    img, d = _new_layer()
    w = _ow()
    d.ellipse([SS * 0.18, SS * 0.18, SS * 0.62, SS * 0.62],
              outline=color, width=w)
    d.line([SS * 0.56, SS * 0.56, SS * 0.84, SS * 0.84],
           fill=color, width=int(w * 1.1))
    return img


def draw_gift(color: tuple) -> Image.Image:
    img, d = _new_layer()
    w = _ow()
    lid = (min(color[0] + 30, 255), min(color[1] + 30, 255),
           min(color[2] + 30, 255), 255)
    d.rectangle([SS * 0.22, SS * 0.46, SS * 0.78, SS * 0.84],
                fill=color, outline=OUTLINE, width=w)
    d.rectangle([SS * 0.20, SS * 0.36, SS * 0.80, SS * 0.50],
                fill=lid, outline=OUTLINE, width=w)
    d.line([SS * 0.5, SS * 0.36, SS * 0.5, SS * 0.84],
           fill=WHITE, width=int(w * 0.7))
    d.ellipse([SS * 0.34, SS * 0.22, SS * 0.50, SS * 0.40], fill=lid)
    d.ellipse([SS * 0.50, SS * 0.22, SS * 0.66, SS * 0.40], fill=lid)
    return img


def draw_flame(color: tuple) -> Image.Image:
    img, d = _new_layer()
    d.polygon([(SS * 0.5, SS * 0.12), (SS * 0.68, SS * 0.42),
               (SS * 0.60, SS * 0.66), (SS * 0.72, SS * 0.82),
               (SS * 0.28, SS * 0.82), (SS * 0.40, SS * 0.66),
               (SS * 0.32, SS * 0.42)], fill=color)
    d.polygon([(SS * 0.5, SS * 0.40), (SS * 0.60, SS * 0.58),
               (SS * 0.50, SS * 0.80), (SS * 0.40, SS * 0.58)],
              fill=(255, 214, 120, 255))
    return img


def draw_heart(color: tuple) -> Image.Image:
    img, d = _new_layer()
    pts = []
    for i in range(0, 360, 3):
        t = math.radians(i)
        x = 16 * math.sin(t) ** 3
        y = (13 * math.cos(t) - 5 * math.cos(2 * t)
             - 2 * math.cos(3 * t) - math.cos(4 * t))
        px = SS * 0.5 + x / 16 * SS * 0.42
        py = SS * 0.5 - y / 16 * SS * 0.42 + SS * 0.06
        pts.append((px, py))
    d.polygon(pts, fill=color, outline=OUTLINE, width=_ow())
    return img


def draw_clock(color: tuple) -> Image.Image:
    img, d = _new_layer()
    w = _ow()
    d.ellipse([SS * 0.16, SS * 0.16, SS * 0.84, SS * 0.84],
              fill=color, outline=OUTLINE, width=w)
    d.ellipse([SS * 0.46, SS * 0.46, SS * 0.54, SS * 0.54], fill=WHITE)
    d.line([SS * 0.5, SS * 0.5, SS * 0.5, SS * 0.30],
           fill=WHITE, width=int(w * 0.7))
    d.line([SS * 0.5, SS * 0.5, SS * 0.66, SS * 0.5],
           fill=WHITE, width=int(w * 0.7))
    return img


def draw_warning(color: tuple) -> Image.Image:
    img, d = _new_layer()
    w = _ow()
    d.polygon([(SS * 0.5, SS * 0.16), (SS * 0.86, SS * 0.82),
               (SS * 0.14, SS * 0.82)], fill=color,
              outline=OUTLINE, width=w)
    d.line([SS * 0.5, SS * 0.34, SS * 0.5, SS * 0.60],
           fill=WHITE, width=int(w * 0.6))
    d.ellipse([SS * 0.46, SS * 0.64, SS * 0.54, SS * 0.72], fill=WHITE)
    return img


def draw_shield(color: tuple) -> Image.Image:
    img, d = _new_layer()
    w = _ow()
    d.polygon([(SS * 0.5, SS * 0.14), (SS * 0.82, SS * 0.26),
               (SS * 0.82, SS * 0.56), (SS * 0.5, SS * 0.88),
               (SS * 0.18, SS * 0.56), (SS * 0.18, SS * 0.26)],
              fill=color, outline=OUTLINE, width=w)
    return img


def draw_sun(color: tuple) -> Image.Image:
    img, d = _new_layer()
    w = _ow()
    d.ellipse([SS * 0.34, SS * 0.34, SS * 0.66, SS * 0.66], fill=color)
    cx = cy = SS / 2
    for a in range(0, 360, 30):
        rad = math.radians(a)
        r0, r1 = SS * 0.36, SS * 0.46
        d.line([cx + r0 * math.cos(rad), cy + r0 * math.sin(rad),
                cx + r1 * math.cos(rad), cy + r1 * math.sin(rad)],
               fill=color, width=int(w * 0.7))
    return img


def draw_moon(color: tuple) -> Image.Image:
    mask = Image.new("L", (SS, SS), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([SS * 0.18, SS * 0.18, SS * 0.82, SS * 0.82], fill=255)
    md.ellipse([SS * 0.44, SS * 0.10, SS * 0.94, SS * 0.60], fill=0)
    colored = Image.new("RGBA", (SS, SS), color + (255,))
    out = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    out.paste(colored, (0, 0), mask)
    return out


def draw_location(color: tuple) -> Image.Image:
    img, d = _new_layer()
    w = _ow()
    d.ellipse([SS * 0.28, SS * 0.16, SS * 0.72, SS * 0.60],
              fill=color, outline=OUTLINE, width=w)
    d.polygon([(SS * 0.28, SS * 0.48), (SS * 0.72, SS * 0.48),
               (SS * 0.5, SS * 0.88)], fill=color, outline=OUTLINE, width=w)
    d.ellipse([SS * 0.40, SS * 0.28, SS * 0.60, SS * 0.48], fill=WHITE)
    return img


def draw_star_burst(color: tuple) -> Image.Image:
    img, d = _new_layer()
    pts = [(SS * 0.5, SS * 0.10), (SS * 0.58, SS * 0.42),
           (SS * 0.90, SS * 0.5), (SS * 0.58, SS * 0.58),
           (SS * 0.5, SS * 0.90), (SS * 0.42, SS * 0.58),
           (SS * 0.10, SS * 0.5), (SS * 0.42, SS * 0.42)]
    d.polygon(pts, fill=color, outline=OUTLINE, width=_ow())
    return img


# ---------------------------------------------------------------------------
# 资产清单：file_stem -> (绘制器名, 颜色)
# 仅新增贴纸；不覆盖已存在的 7 个。
# ---------------------------------------------------------------------------

RED = (230, 70, 70)
BLUE = (60, 130, 230)
GREEN = (60, 190, 120)
YELLOW = (240, 200, 60)
ORANGE = (240, 140, 50)
PURPLE = (160, 90, 220)
TEAL = (50, 190, 190)
PINK = (240, 110, 170)
DARK = (70, 80, 100)
INDIGO = (90, 110, 210)

NEW_STICKERS: list[tuple[str, str, tuple]] = [
    ("arrow_up", "arrow_up", BLUE),
    ("arrow_down", "arrow_down", BLUE),
    ("arrow_left", "arrow_left", BLUE),
    ("arrow_right", "arrow_right", BLUE),
    ("arrow_ne", "arrow_ne", BLUE),
    ("arrow_nw", "arrow_nw", BLUE),
    ("cross", "cross", RED),
    ("plus", "plus", GREEN),
    ("minus", "minus", ORANGE),
    ("question", "question", PURPLE),
    ("dot", "dot", PINK),
    ("number_bubble", "number_bubble", TEAL),
    ("speech_bubble", "speech_bubble", BLUE),
    ("tag", "tag", ORANGE),
    ("timer", "timer", INDIGO),
    ("music_note", "music_note", PURPLE),
    ("magnifier", "magnifier", TEAL),
    ("gift", "gift", PINK),
    ("flame", "flame", ORANGE),
    ("heart", "heart", RED),
    ("clock", "clock", DARK),
    ("warning", "warning", YELLOW),
    ("shield", "shield", GREEN),
    ("sun", "sun", YELLOW),
    ("moon", "moon", INDIGO),
    ("location", "location", RED),
    ("star_burst", "star_burst", YELLOW),
]

_DRAWERS = {
    "arrow_up": lambda c: draw_arrow(0, c),
    "arrow_down": lambda c: draw_arrow(180, c),
    "arrow_left": lambda c: draw_arrow(90, c),
    "arrow_right": lambda c: draw_arrow(-90, c),
    "arrow_ne": lambda c: draw_arrow(-45, c),
    "arrow_nw": lambda c: draw_arrow(45, c),
    "cross": draw_cross,
    "plus": draw_plus,
    "minus": draw_minus,
    "question": lambda c: draw_badge_text("?", c),
    "dot": draw_dot,
    "number_bubble": lambda c: draw_badge_text("1", c),
    "speech_bubble": draw_speech_bubble,
    "tag": draw_tag,
    "timer": draw_timer,
    "music_note": draw_music_note,
    "magnifier": draw_magnifier,
    "gift": draw_gift,
    "flame": draw_flame,
    "heart": draw_heart,
    "clock": draw_clock,
    "warning": draw_warning,
    "shield": draw_shield,
    "sun": draw_sun,
    "moon": draw_moon,
    "location": draw_location,
    "star_burst": draw_star_burst,
}


def generate_all() -> list[str]:
    out_paths: list[str] = []
    for stem, _kind, color in NEW_STICKERS:
        if stem not in _DRAWERS:
            raise RuntimeError(f"no drawer for {stem}")
        layer = _DRAWERS[stem](color)
        layer = layer.resize((OUT, OUT), Image.LANCZOS)
        # 确保是 RGBA（有 alpha 通道）
        if layer.mode != "RGBA":
            layer = layer.convert("RGBA")
        path = os.path.join(HERE, f"{stem}.png")
        layer.save(path, "PNG")
        out_paths.append(path)
    return out_paths


if __name__ == "__main__":
    paths = generate_all()
    print(f"生成 {len(paths)} 个新贴纸 → {HERE}")
    for p in paths:
        print("  ", os.path.basename(p))
