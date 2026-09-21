"""CutVoke 内置背景图资产生成器（J07 素材内容扩展）。

用 PIL 生成一批真实可用的背景画面，落到本目录
（src/cutvoke/assets/backgrounds/）。首次服务启动时会经
core/builtin_assets.py 的 ensure_builtin_backgrounds() 目录扫描登记进素材账本
（与用户素材共存同表、幂等、不互删）。

设计约束：
- 尺寸 1920×1080（≥ 任务要求的最小尺寸），PNG。
- 风格：纯色 / 渐变 / 网格 / 纸张纹理 / 暗角 / 散景 / 棋盘，适合做视频背景或画布底。
- 全部为真实像素内容，非占位空文件（经 PIL 验证尺寸）。

运行方式（在仓库根目录）：
    PYTHONPATH=src python src/cutvoke/assets/backgrounds/generate_builtin_backgrounds.py
"""

from __future__ import annotations

import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))

W, H = 1920, 1080


def _solid(name: str, color: tuple[int, int, int]) -> str:
    img = Image.new("RGB", (W, H), color)
    path = os.path.join(HERE, name)
    img.save(path, "PNG")
    return path


def _v_gradient(name: str, top: tuple, bottom: tuple) -> str:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        col = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (W, y)], fill=col)
    path = os.path.join(HERE, name)
    img.save(path, "PNG")
    return path


def _grid_dark(name: str) -> str:
    img = Image.new("RGB", (W, H), (24, 26, 34))
    d = ImageDraw.Draw(img)
    step = 80
    for x in range(0, W, step):
        d.line([(x, 0), (x, H)], fill=(60, 66, 86), width=1)
    for y in range(0, H, step):
        d.line([(0, y), (W, y)], fill=(60, 66, 86), width=1)
    path = os.path.join(HERE, name)
    img.save(path, "PNG")
    return path


def _paper(name: str) -> str:
    img = Image.new("RGB", (W, H), (245, 242, 235))
    d = ImageDraw.Draw(img)
    random.seed(3)
    for _ in range(5000):
        x = random.randint(0, W)
        y = random.randint(0, H)
        g = random.randint(205, 236)
        d.point((x, y), (g, g, g))
    for _ in range(400):
        x = random.randint(0, W)
        y = random.randint(0, H)
        ln = random.randint(4, 22)
        d.line([(x, y), (x + ln, y + random.randint(-3, 3))],
               fill=(226, 221, 211), width=1)
    path = os.path.join(HERE, name)
    img.save(path, "PNG")
    return path


def _vignette(name: str) -> str:
    img = Image.new("RGB", (W, H))
    px = img.load()
    cx, cy = W / 2, H / 2
    maxd = math.hypot(cx, cy)
    for y in range(H):
        for x in range(W):
            d = math.hypot(x - cx, y - cy) / maxd
            v = 1 - 0.82 * d * d
            px[x, y] = (int(40 * v), int(44 * v), int(62 * v))
    path = os.path.join(HERE, name)
    img.save(path, "PNG")
    return path


def _bokeh(name: str) -> str:
    base = Image.new("RGB", (W, H), (18, 22, 38))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    random.seed(7)
    colors = [(255, 180, 120), (120, 200, 255), (255, 120, 180),
              (180, 255, 160), (255, 230, 120), (180, 160, 255)]
    for _ in range(46):
        x = random.randint(0, W)
        y = random.randint(0, H)
        r = random.randint(40, 170)
        c = random.choice(colors) + (random.randint(45, 95),)
        ld.ellipse([x - r, y - r, x + r, y + r], fill=c)
    base.paste(layer, (0, 0), layer)
    base = base.filter(ImageFilter.GaussianBlur(30))
    path = os.path.join(HERE, name)
    base.save(path, "PNG")
    return path


def _checker(name: str) -> str:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    sq = 120
    for y in range(0, H, sq):
        for x in range(0, W, sq):
            col = (230, 230, 236) if ((x // sq + y // sq) % 2 == 0) else (176, 182, 200)
            d.rectangle([x, y, x + sq, y + sq], fill=col)
    path = os.path.join(HERE, name)
    img.save(path, "PNG")
    return path


# (文件名, 生成器)
BACKGROUNDS: list[tuple[str, callable]] = [
    ("bg_solid_black.png", lambda: _solid("bg_solid_black.png", (12, 12, 14))),
    ("bg_solid_white.png", lambda: _solid("bg_solid_white.png", (244, 244, 246))),
    ("bg_gradient_blue.png",
     lambda: _v_gradient("bg_gradient_blue.png", (20, 40, 90), (90, 150, 220))),
    ("bg_gradient_sunset.png",
     lambda: _v_gradient("bg_gradient_sunset.png", (255, 150, 90), (120, 60, 130))),
    ("bg_gradient_teal.png",
     lambda: _v_gradient("bg_gradient_teal.png", (10, 60, 70), (40, 170, 160))),
    ("bg_grid_dark.png", lambda: _grid_dark("bg_grid_dark.png")),
    ("bg_paper.png", lambda: _paper("bg_paper.png")),
    ("bg_vignette.png", lambda: _vignette("bg_vignette.png")),
    ("bg_bokeh.png", lambda: _bokeh("bg_bokeh.png")),
    ("bg_checker.png", lambda: _checker("bg_checker.png")),
]


def generate_all() -> list[str]:
    out: list[str] = []
    for name, fn in BACKGROUNDS:
        out.append(fn())
    return out


if __name__ == "__main__":
    paths = generate_all()
    print(f"生成 {len(paths)} 张背景图 → {HERE}")
    for p in paths:
        im = Image.open(p)
        print(f"  {os.path.basename(p):24s} {im.size[0]}x{im.size[1]} mode={im.mode}")
