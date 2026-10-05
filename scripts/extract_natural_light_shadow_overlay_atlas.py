"""Crop and audit the 4x4 ImageGen natural-light overlay atlas."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "docs/assets/natural-light-shadow-overlay-atlas-20260927.png"
OUTPUT = ROOT / "src/cutvoke/assets/stickers"
REPORT = ROOT / "docs/assets/natural-light-shadow-overlay-atlas-20260927.extraction.json"
CONTACT_DARK = ROOT / "docs/assets/natural-light-shadow-overlay-atlas-20260927-contact-dark.png"
CONTACT_LIGHT = ROOT / "docs/assets/natural-light-shadow-overlay-atlas-20260927-contact-light.png"
LICENSE = "Project-internal original (AI-generated); not separately licensed for redistribution"

ITEMS = (
    ("natural_light_window_beams", "金色窗光光束", ("自然光影", "窗光", "光束", "实拍")),
    ("natural_light_foliage_dapple", "树叶斑驳光影", ("自然光影", "树影", "斑驳", "实拍")),
    ("natural_light_pool_caustics", "泳池水纹焦散", ("自然光影", "水纹", "焦散", "实拍")),
    ("natural_light_candle_bloom", "暖烛柔光", ("自然光影", "暖光", "烛光", "实拍")),
    ("natural_light_sunrise_cloud_glow", "日出云隙辉光", ("自然光影", "日出", "云隙光", "实拍")),
    ("natural_light_window_blind_bands", "百叶窗光带", ("自然光影", "窗光", "条纹", "实拍")),
    ("natural_light_moonlit_water", "月光水面微闪", ("自然光影", "月光", "水面", "实拍")),
    ("natural_light_peach_haze", "桃色柔雾", ("自然光影", "柔雾", "桃色", "实拍")),
    ("natural_light_forest_rays", "青绿森林体积光", ("自然光影", "森林", "体积光", "实拍")),
    ("natural_light_snow_glints", "雪面反光星点", ("自然光影", "雪景", "反光", "实拍")),
    ("natural_light_prism_arc", "柔彩棱镜折射弧", ("自然光影", "棱镜", "折射", "实拍")),
    ("natural_light_storm_flash", "冷蓝风暴闪光", ("自然光影", "风暴", "冷蓝", "实拍")),
    ("natural_light_golden_dust_beam", "黄金时刻尘光", ("自然光影", "金色时刻", "尘光", "实拍")),
    ("natural_light_cloud_shadow_wisps", "流云阴影薄雾", ("自然光影", "流云", "薄雾", "实拍")),
    ("natural_light_aurora_ribbons", "极光柔带", ("自然光影", "极光", "柔光", "实拍")),
    ("natural_light_firelight_reflection", "暖橙反射火光", ("自然光影", "火光", "暖橙", "实拍")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _render_contact_sheets() -> None:
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 17) if font_path.exists() else ImageFont.load_default()
    for contact_path, tile_color in (
            (CONTACT_DARK, "#263246"),
            (CONTACT_LIGHT, "#f1eee8")):
        canvas = Image.new("RGB", (4 * 560, 4 * 590), "#e8e9ed")
        draw = ImageDraw.Draw(canvas)
        for index, (stem, name, _) in enumerate(ITEMS):
            row, column = divmod(index, 4)
            x, y = column * 560, row * 590
            draw.rectangle((x + 12, y + 12, x + 548, y + 548), fill=tile_color)
            item = Image.open(OUTPUT / f"{stem}.png").convert("RGBA")
            canvas.paste(item, (x + 24, y + 24), item)
            draw.text((x + 18, y + 554), name, font=font, fill="#20242a")
        canvas.save(contact_path, optimize=True)


def extract() -> list[dict]:
    source = Image.open(ATLAS).convert("RGBA")
    width, height = source.size
    if abs(width - height) > 1 or width < 1200:
        raise ValueError(f"expected a high-resolution square atlas, got {source.size}")
    alpha = np.asarray(source.getchannel("A"), dtype=np.uint8)
    if alpha.min() != 0 or alpha.max() < 240:
        raise ValueError("atlas must contain genuine transparent and opaque pixels")

    targets = [OUTPUT / f"{stem}.png" for stem, _, _ in ITEMS]
    if any(path.exists() for path in targets):
        raise FileExistsError("refusing to overwrite existing sticker assets")
    if any(path.exists() for path in (REPORT, CONTACT_DARK, CONTACT_LIGHT)):
        raise FileExistsError("refusing to overwrite existing extraction evidence")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    records = []
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
        if boundary > max(160, int(visible_count * 0.02)):
            raise ValueError(f"{stem} crosses its crop boundary ({boundary} visible pixels)")
        pixels[:8, :, :] = pixels[-8:, :, :] = 0
        pixels[:, :8, :] = pixels[:, -8:, :] = 0

        cell = Image.fromarray(pixels, "RGBA")
        side = max(cell.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(cell, ((side - cell.width) // 2, (side - cell.height) // 2))
        cell = square.resize((512, 512), Image.Resampling.LANCZOS)
        target = OUTPUT / f"{stem}.png"
        cell.save(target, optimize=True)
        crops.append(cell)
        final_alpha = np.asarray(cell.getchannel("A"), dtype=np.uint8)
        final_visible = final_alpha >= 16
        ys, xs = np.where(final_visible)
        records.append({
            "stem": stem,
            "name": name,
            "subcategory": "自然光影",
            "keywords": list(keywords),
            "row": row,
            "column": column,
            "sourceCell": [x0, y0, x1, y1],
            "visibleBounds": [int(xs.min()), int(ys.min()), int(xs.max()) + 1,
                              int(ys.max()) + 1],
            "size": [512, 512],
            "visiblePixels": int(final_visible.sum()),
            "trimmedBoundaryPixels": boundary,
            "alphaExtrema": [int(final_alpha.min()), int(final_alpha.max())],
            "sha256": _sha(target),
        })

    report = {
        "generator": "OpenAI ImageGen",
        "source": ATLAS.name,
        "sourceSha256": _sha(ATLAS),
        "sourceSize": [width, height],
        "layout": "4x4 equal square cells",
        "subcategory": "自然光影",
        "license": LICENSE,
        "alphaThreshold": 8,
        "safetyGutterPixels": 8,
        "items": records,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")

    _render_contact_sheets()
    return records


if __name__ == "__main__":
    result = extract()
    print(json.dumps({"sourceSha256": _sha(ATLAS), "items": result},
                     ensure_ascii=False, indent=2))
