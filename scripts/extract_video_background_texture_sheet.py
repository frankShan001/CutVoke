"""Extract a generated 4x4 video-background texture atlas into sticker tiles.

The original atlas stays under docs/assets for review. Each texture is cropped
inside its cell, centered on a transparent 320px canvas, and accompanied by a
hash-pinned extraction report and contact sheet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/video-background-texture-sheet-20260925.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_REPORT = ROOT / "docs/assets/video-background-texture-sheet-20260925.extraction.json"
DEFAULT_CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
CELL_COUNT = 4
CROP_FRACTION = 0.82
OUTPUT_SIZE = 320
PADDING = 32
DEFAULT_SCALE = 2.22

PROMPT = (
    "One square 4x4 atlas of 16 restrained video background textures with exact "
    "magenta gutters: underwater blue caustics, champagne brushed glass, rose "
    "satin, sage leaf shadows, emerald velvet, lilac pearl, terracotta plaster, "
    "charcoal gold geometry, icy frosted glass, coral teal flow, peach watercolor, "
    "plum silk, seafoam refraction, cream terrazzo, twilight clouds, amber glass. "
    "No text, logos, objects, or noisy artifacts. Generated with OpenAI built-in "
    "image generation for the CutVoke project on 2026-09-25."
)

TEXTURES = (
    ("texture_bg_underwater_caustics", "深海焦散", ("背景纹理", "深海", "水光", "蓝色")),
    ("texture_bg_champagne_glass", "香槟拉丝玻璃", ("背景纹理", "玻璃", "香槟金", "细腻")),
    ("texture_bg_rose_satin", "柔玫缎面", ("背景纹理", "缎面", "玫瑰粉", "柔光")),
    ("texture_bg_sage_leaf_shadows", "鼠尾草叶影", ("背景纹理", "叶影", "鼠尾草绿", "自然")),
    ("texture_bg_emerald_velvet", "深翡翠绒面", ("背景纹理", "天鹅绒", "翡翠绿", "质感")),
    ("texture_bg_lilac_pearl", "淡紫珠光", ("背景纹理", "珠光", "淡紫", "虹彩")),
    ("texture_bg_terracotta_plaster", "赤陶灰泥", ("背景纹理", "赤陶", "灰泥", "暖色")),
    ("texture_bg_charcoal_gold", "炭黑金线", ("背景纹理", "炭黑", "金线", "几何")),
    ("texture_bg_ice_frosted_glass", "冰蓝磨砂玻璃", ("背景纹理", "冰蓝", "磨砂玻璃", "冷调")),
    ("texture_bg_coral_teal_flow", "珊瑚青流体", ("背景纹理", "珊瑚色", "青色", "流体")),
    ("texture_bg_peach_watercolor", "桃粉水彩", ("背景纹理", "桃粉", "水彩", "柔和")),
    ("texture_bg_plum_silk", "深梅丝绸", ("背景纹理", "丝绸", "梅紫", "暗调")),
    ("texture_bg_seafoam_refraction", "海沫折射", ("背景纹理", "海沫绿", "折射", "水光")),
    ("texture_bg_cream_terrazzo", "奶油水磨石", ("背景纹理", "奶油色", "水磨石", "浅色")),
    ("texture_bg_twilight_clouds", "暮色云层", ("背景纹理", "暮色", "云层", "蓝紫")),
    ("texture_bg_amber_glass", "蜜糖琥珀玻璃", ("背景纹理", "琥珀", "玻璃", "金色")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contact_sheet(paths: list[Path], output: Path) -> None:
    tile = 180
    sheet = Image.new("RGB", (tile * CELL_COUNT, tile * CELL_COUNT), (29, 34, 43))
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(paths):
        row, col = divmod(index, CELL_COUNT)
        x, y = col * tile, row * tile
        background = Image.new("RGBA", (tile, tile), (232, 235, 240, 255))
        block = 18
        checker = ImageDraw.Draw(background)
        for cy in range(0, tile, block):
            for cx in range(0, tile, block):
                if (cx // block + cy // block) % 2:
                    checker.rectangle((cx, cy, cx + block - 1, cy + block - 1),
                                      fill=(205, 210, 218, 255))
        with Image.open(path) as source:
            source = source.convert("RGBA")
            source.thumbnail((tile - 26, tile - 26), Image.Resampling.LANCZOS)
            background.alpha_composite(source, ((tile - source.width) // 2,
                                                (tile - source.height) // 2))
        sheet.paste(background.convert("RGB"), (x, y))
        draw.rectangle((x + 5, y + 5, x + 34, y + 25), fill=(22, 27, 35))
        draw.text((x + 12, y + 9), f"{index + 1:02d}", fill=(255, 255, 255))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, optimize=True)


def extract(sheet: Path, output: Path, report: Path) -> list[dict]:
    source = Image.open(sheet).convert("RGBA")
    width, height = source.size
    if width < CELL_COUNT * 64 or height < CELL_COUNT * 64:
        raise ValueError("source atlas is too small for a 4x4 extraction")
    cell_w, cell_h = width / CELL_COUNT, height / CELL_COUNT
    output.mkdir(parents=True, exist_ok=True)
    targets = [output / f"{stem}.png" for stem, _name, _keywords in TEXTURES]
    existing = [path.name for path in targets if path.exists()]
    if existing:
        raise FileExistsError(f"will not overwrite existing assets: {existing}")

    assets: list[dict] = []
    for index, (stem, name, keywords) in enumerate(TEXTURES):
        row, col = divmod(index, CELL_COUNT)
        x0, x1 = round(col * cell_w), round((col + 1) * cell_w)
        y0, y1 = round(row * cell_h), round((row + 1) * cell_h)
        side = min(x1 - x0, y1 - y0)
        crop_side = round(side * CROP_FRACTION)
        left = x0 + ((x1 - x0) - crop_side) // 2
        top = y0 + ((y1 - y0) - crop_side) // 2
        box = (left, top, left + crop_side, top + crop_side)
        artwork = source.crop(box).resize(
            (OUTPUT_SIZE - 2 * PADDING, OUTPUT_SIZE - 2 * PADDING),
            Image.Resampling.LANCZOS)
        prepared = Image.new("RGBA", (OUTPUT_SIZE, OUTPUT_SIZE), (0, 0, 0, 0))
        prepared.alpha_composite(artwork, (PADDING, PADDING))
        target = output / f"{stem}.png"
        prepared.save(target, optimize=True)
        alpha = prepared.getchannel("A")
        bounds = alpha.point(lambda value: 255 if value >= 16 else 0).getbbox()
        assets.append({
            "index": index + 1,
            "stem": stem,
            "name": name,
            "subcategory": "背景纹理",
            "keywords": list(keywords),
            "row": row,
            "column": col,
            "sourceCell": [x0, y0, x1, y1],
            "cropBox": list(box),
            "size": [OUTPUT_SIZE, OUTPUT_SIZE],
            "defaultScale": DEFAULT_SCALE,
            "alphaBounds": list(bounds) if bounds else None,
            "alphaExtrema": list(alpha.getextrema()),
            "sha256": _sha(target),
        })

    _contact_sheet(targets, report.with_name("video-background-texture-contact-sheet-20260925.png"))
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "schemaVersion": 1,
        "source": sheet.name,
        "sourceSha256": _sha(sheet),
        "sourceSize": [width, height],
        "sourceGenerator": "OpenAI built-in image generation",
        "generatedAt": "2026-09-25",
        "prompt": PROMPT,
        "layout": "4x4",
        "extraction": "centered square crop at 82% of each proportional cell; 32px transparent padding",
        "contactSheet": "video-background-texture-contact-sheet-20260925.png",
        "items": assets,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return assets


def register_catalog(report: Path, catalog_path: Path) -> int:
    extraction = json.loads(report.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    entries = catalog.get("stickers")
    if not isinstance(entries, list):
        raise ValueError("built-in sticker catalog has no stickers list")
    items = extraction.get("items")
    if not isinstance(items, list) or len(items) != len(TEXTURES):
        raise ValueError("texture extraction report does not contain the expected 16 items")
    existing = {item.get("stem") for item in entries if isinstance(item, dict)}
    new_stems = {item.get("stem") for item in items if isinstance(item, dict)}
    if existing & new_stems:
        raise ValueError(f"texture entries already exist in catalog: {sorted(existing & new_stems)}")
    source = (f"OpenAI built-in image generation; generated 2026-09-25 from "
              f"docs/assets/{extraction['source']}; no third-party source")
    entries.extend({
        "stem": item["stem"],
        "name": item["name"],
        "subcategory": item["subcategory"],
        "keywords": item["keywords"],
        "version": "1.0.0",
        "license": "AI-generated asset; no external source",
        "source": source,
        "defaultScale": float(item.get("defaultScale", DEFAULT_SCALE)),
    } for item in items)
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    return len(items)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--register-catalog", action="store_true",
                        help="also add extracted assets to builtin_stickers.json")
    parser.add_argument("--register-only", action="store_true",
                        help="register an existing extraction report without cropping again")
    args = parser.parse_args()
    extracted = [] if args.register_only else extract(args.sheet, args.output, args.report)
    registered = register_catalog(args.report, DEFAULT_CATALOG) if args.register_catalog or args.register_only else 0
    print(json.dumps({"extracted": len(extracted), "registered": registered,
                      "report": str(args.report)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
