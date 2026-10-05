"""Crop and audit the 4x4 ImageGen product-promo overlay atlas."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "docs/assets/product-promo-overlay-atlas-20260927.png"
OUTPUT = ROOT / "src/cutvoke/assets/stickers"
REPORT = ROOT / "docs/assets/product-promo-overlay-atlas-20260927.extraction.json"
CONTACT_DARK = ROOT / "docs/assets/product-promo-overlay-atlas-20260927-contact-dark.png"
CONTACT_LIGHT = ROOT / "docs/assets/product-promo-overlay-atlas-20260927-contact-light.png"
LICENSE = "Project-internal original (AI-generated); not separately licensed for redistribution"

ITEMS = (
    ("promo_frame_brackets", "香槟金圆角构图角", ("产品广告", "取景", "构图", "香槟金")),
    ("promo_orbit_rings", "珍珠轨道环", ("产品广告", "轨道", "珍珠", "装饰")),
    ("promo_silk_loop", "奶油金丝绸环", ("产品广告", "丝绸", "飘带", "柔光")),
    ("promo_prism_refraction", "虹彩棱镜折射", ("产品广告", "棱镜", "虹彩", "折射")),
    ("promo_foil_flourish", "金箔弧形飘带", ("产品广告", "金箔", "飘带", "弧光")),
    ("promo_podium_outline", "阶梯产品展示台", ("产品广告", "展示台", "阶梯", "金属")),
    ("promo_callout_leaders", "留白标注引线", ("产品广告", "标注", "引线", "说明")),
    ("promo_spotlight_fan", "奶油金放射柔光", ("产品广告", "聚光", "放射", "柔光")),
    ("promo_opal_halo", "欧泊玻璃光环", ("产品广告", "欧泊", "玻璃", "光环")),
    ("promo_leaf_shadow", "青瓷叶片柔影", ("产品广告", "叶影", "自然", "青瓷")),
    ("promo_blank_seal", "空白双线圆章", ("产品广告", "圆章", "边框", "留白")),
    ("promo_sparkle_glints", "三枚珍珠星芒", ("产品广告", "星芒", "闪光", "珍珠")),
    ("promo_glass_wave", "青瓷流动玻璃带", ("产品广告", "玻璃", "飘带", "流动")),
    ("promo_corner_foil", "金箔角花", ("产品广告", "角花", "金箔", "装饰")),
    ("promo_focus_ring", "珍珠对焦光圈", ("产品广告", "对焦", "光圈", "珍珠")),
    ("promo_luxury_badge", "空白鎏金徽章", ("产品广告", "徽章", "鎏金", "留白")),
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
    if any(path.exists() for path in (REPORT, CONTACT_DARK, CONTACT_LIGHT)):
        raise FileExistsError("refusing to overwrite extraction evidence")

    # Validate every cell before creating any crop so a bad atlas never leaves
    # a partly written batch in the built-in asset directory.
    for index, (stem, _name, _keywords) in enumerate(ITEMS):
        row, column = divmod(index, 4)
        x0, x1 = round(column * width / 4), round((column + 1) * width / 4)
        y0, y1 = round(row * height / 4), round((row + 1) * height / 4)
        pixels = np.asarray(source.crop((x0, y0, x1, y1)), dtype=np.uint8)
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

    OUTPUT.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    crops: list[Image.Image] = []
    for index, (stem, name, keywords) in enumerate(ITEMS):
        row, column = divmod(index, 4)
        x0, x1 = round(column * width / 4), round((column + 1) * width / 4)
        y0, y1 = round(row * height / 4), round((row + 1) * height / 4)
        pixels = np.asarray(source.crop((x0, y0, x1, y1)), dtype=np.uint8).copy()
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
            "subcategory": "产品广告",
            "keywords": list(keywords),
            "row": row,
            "column": column,
            "nominalGridCell": [x0, y0, x1, y1],
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
        "subcategory": "产品广告",
        "license": LICENSE,
        "alphaThreshold": 8,
        "safetyGutterPixels": 8,
        "items": records,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")

    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 17) if font_path.exists() else ImageFont.load_default()
    for contact_path, tile_color in (
            (CONTACT_DARK, "#202735"), (CONTACT_LIGHT, "#f1eee8")):
        canvas = Image.new("RGB", (4 * 360, 4 * 390), "#e8e9ed")
        draw = ImageDraw.Draw(canvas)
        for index, ((_, name, _), item) in enumerate(zip(ITEMS, crops, strict=True)):
            row, column = divmod(index, 4)
            x, y = column * 360, row * 390
            draw.rectangle((x + 8, y + 8, x + 352, y + 352), fill=tile_color)
            canvas.paste(item, (x + 20, y + 20), item)
            draw.text((x + 12, y + 360), name, font=font, fill="#20242a")
        canvas.save(contact_path, optimize=True)
    return records


if __name__ == "__main__":
    result = extract()
    print(json.dumps({"sourceSha256": _sha(ATLAS), "items": result},
                     ensure_ascii=False, indent=2))
