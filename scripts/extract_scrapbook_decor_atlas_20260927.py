"""Crop and audit the 4x4 ImageGen scrapbook-decor atlas."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "docs/assets/scrapbook-decor-atlas-20260927-v2.png"
OUTPUT = ROOT / "src/cutvoke/assets/stickers"
REPORT = ROOT / "docs/assets/scrapbook-decor-atlas-20260927-v2.extraction.json"
CONTACT_DARK = ROOT / "docs/assets/scrapbook-decor-atlas-20260927-v2-contact-dark.png"
CONTACT_LIGHT = ROOT / "docs/assets/scrapbook-decor-atlas-20260927-v2-contact-light.png"
LICENSE = "Project-internal original (AI-generated); not separately licensed for redistribution"

ITEMS = (
    ("scrapbook_corner_sprig", "鼠尾草折角纸片", ("手帐", "植物", "折角", "纸艺")),
    ("scrapbook_crossed_washi", "交叉和纸胶带", ("手帐", "胶带", "拼贴", "纸艺")),
    ("scrapbook_coral_torn_corner", "珊瑚色撕纸框角", ("手帐", "撕纸", "边角", "拼贴")),
    ("scrapbook_blue_vellum_ribbon", "天蓝描图纸飘带", ("手帐", "飘带", "透明纸", "装饰")),
    ("scrapbook_kraft_stitched_label", "牛皮纸缝线空白标签", ("手帐", "标签", "牛皮纸", "空白")),
    ("scrapbook_lavender_curl", "薰衣草色卷曲纸带", ("手帐", "飘带", "纸艺", "薰衣草")),
    ("scrapbook_layered_flower", "珊瑚奶油叠层纸花", ("手帐", "花朵", "拼贴", "装饰")),
    ("scrapbook_yellow_stars", "奶油黄剪纸星星组", ("手帐", "星星", "剪纸", "装饰")),
    ("scrapbook_sage_leaf", "鼠尾草叶片贴纸", ("手帐", "植物", "叶片", "装饰")),
    ("scrapbook_scalloped_edge", "奶油色扇形纸边", ("手帐", "纸边", "边框", "装饰")),
    ("scrapbook_charcoal_loops", "炭黑手绘双圈", ("手帐", "涂鸦", "圈选", "标记")),
    ("scrapbook_blue_paperclip", "天蓝回形针与珊瑚珠", ("手帐", "回形针", "文具", "装饰")),
    ("scrapbook_kraft_torn_patch", "牛皮纸撕边空白贴片", ("手帐", "牛皮纸", "撕纸", "空白")),
    ("scrapbook_matte_confetti", "哑光箔纸屑组合", ("手帐", "纸屑", "箔片", "装饰")),
    ("scrapbook_double_bookmark", "薰衣草奶油双层书签", ("手帐", "书签", "拼贴", "装饰")),
    ("scrapbook_coral_bow", "珊瑚色纸艺蝴蝶结", ("手帐", "蝴蝶结", "纸艺", "装饰")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract() -> list[dict]:
    source = Image.open(ATLAS).convert("RGBA")
    width, height = source.size
    if abs(width - height) > 1 or width < 1200:
        raise ValueError(f"expected a high-resolution square atlas, got {source.size}")
    alpha = np.asarray(source.getchannel("A"), dtype=np.uint8)
    if alpha.min() != 0 or alpha.max() != 255:
        raise ValueError("atlas must contain genuine transparent and opaque pixels")

    targets = [OUTPUT / f"{stem}.png" for stem, _, _ in ITEMS]
    if any(path.exists() for path in targets):
        raise FileExistsError("refusing to overwrite existing sticker assets")
    if REPORT.exists() or CONTACT_DARK.exists() or CONTACT_LIGHT.exists():
        raise FileExistsError("refusing to overwrite existing extraction evidence")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    records = []
    crops: list[Image.Image] = []
    for index, (stem, name, keywords) in enumerate(ITEMS):
        row, column = divmod(index, 4)
        x0, x1 = round(column * width / 4), round((column + 1) * width / 4)
        y0, y1 = round(row * height / 4), round((row + 1) * height / 4)
        # The generated coin/confetti and bookmark extend 20px into their
        # otherwise empty upper gutter. Recover that complete art; adjacent
        # cells are clear in this area and the measured safety band remains empty.
        extra_top = 20 if index in (13, 14) else 0
        crop_y0 = max(0, y0 - extra_top)
        pixels = np.asarray(source.crop((x0, crop_y0, x1, y1)), dtype=np.uint8).copy()
        pixels[pixels[:, :, 3] < 8] = (0, 0, 0, 0)
        visible = pixels[:, :, 3] >= 16
        visible_count = int(visible.sum())
        if visible_count < 1000:
            raise ValueError(f"{stem} is nearly empty ({visible_count} visible pixels)")
        boundary = int(
            visible[:8, :].sum() + visible[-8:, :].sum()
            + visible[:, :8].sum() + visible[:, -8:].sum()
        )
        if boundary > max(120, int(visible_count * 0.01)):
            raise ValueError(f"{stem} crosses its crop boundary ({boundary} visible pixels)")
        pixels[:8, :, :] = pixels[-8:, :, :] = 0
        pixels[:, :8, :] = pixels[:, -8:, :] = 0

        # Normalize the 313/314px grid cells to the library's common 320px size
        # without stretching the two slightly taller recovered cells.
        cell = Image.fromarray(pixels, "RGBA")
        side = max(cell.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(cell, ((side - cell.width) // 2, (side - cell.height) // 2))
        cell = square.resize((320, 320), Image.Resampling.LANCZOS)
        target = OUTPUT / f"{stem}.png"
        cell.save(target, optimize=True)
        crops.append(cell)
        final_alpha = np.asarray(cell.getchannel("A"), dtype=np.uint8)
        final_visible = final_alpha >= 16
        ys, xs = np.where(final_visible)
        records.append({
            "stem": stem,
            "name": name,
            "subcategory": "装饰",
            "keywords": list(keywords),
            "row": row,
            "column": column,
            "nominalGridCell": [x0, y0, x1, y1],
            "sourceCell": [x0, crop_y0, x1, y1],
            "extraTopRecoveredPixels": extra_top,
            "visibleBounds": [int(xs.min()), int(ys.min()), int(xs.max()) + 1,
                              int(ys.max()) + 1],
            "size": [320, 320],
            "visiblePixels": int(final_visible.sum()),
            "trimmedBoundaryPixels": boundary,
            "alphaExtrema": [int(final_alpha.min()), int(final_alpha.max())],
            "sha256": _sha(target),
        })

    report = {
        "generator": "OpenAI ImageGen",
        "source": ATLAS.name,
        "sourceSha256": _sha(ATLAS),
        "layout": "4x4 equal square cells",
        "subcategory": "装饰",
        "license": LICENSE,
        "alphaThreshold": 8,
        "safetyGutterPixels": 8,
        "items": records,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")

    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 17) if font_path.exists() else ImageFont.load_default()
    for contact_path, tile_color, label_color in (
            (CONTACT_DARK, "#263246", "#20242a"),
            (CONTACT_LIGHT, "#f1eee8", "#20242a")):
        canvas = Image.new("RGB", (4 * 360, 4 * 390), "#e8e9ed")
        draw = ImageDraw.Draw(canvas)
        for index, ((_, name, _), item) in enumerate(zip(ITEMS, crops, strict=True)):
            row, column = divmod(index, 4)
            x, y = column * 360, row * 390
            draw.rectangle((x + 8, y + 8, x + 352, y + 352), fill=tile_color)
            canvas.paste(item, (x + 20, y + 20), item)
            draw.text((x + 12, y + 360), name, font=font, fill=label_color)
        canvas.save(contact_path, optimize=True)
    return records


if __name__ == "__main__":
    result = extract()
    print(json.dumps({"sourceSha256": _sha(ATLAS), "items": result},
                     ensure_ascii=False, indent=2))
