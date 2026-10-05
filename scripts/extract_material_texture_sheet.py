"""Extract the generated 4x4 material-texture atlas into transparent PNG tiles.

The atlas is retained in docs/assets; extraction uses a centered square crop
inside each cell and transparent padding so no neighbouring cell can bleed in.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/material-texture-sheet-20260925.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_REPORT = ROOT / "docs/assets/material-texture-sheet-20260925.extraction.json"
CELL_COUNT = 4
CROP_FRACTION = 0.82
OUTPUT_SIZE = 320
PADDING = 32

MATERIALS = (
    ("material_rice_paper", "手工米纸", ("材质", "纸张", "纤维", "手工")),
    ("material_vellum", "半透描图纸", ("材质", "纸张", "半透明", "描图")),
    ("material_kraft", "压纹牛皮纸", ("材质", "纸张", "牛皮纸", "压纹")),
    ("material_watercolor_paper", "水彩纸面", ("材质", "纸张", "水彩", "冷压")),
    ("material_linen", "天然亚麻", ("材质", "织物", "亚麻", "编织")),
    ("material_denim", "靛蓝丹宁", ("材质", "织物", "丹宁", "靛蓝")),
    ("material_velvet", "酒红绒布", ("材质", "织物", "天鹅绒", "酒红")),
    ("material_wool_felt", "浅色羊毛毡", ("材质", "织物", "羊毛", "毛毡")),
    ("material_cork", "天然软木", ("材质", "软木", "木质", "颗粒")),
    ("material_pebbled_leather", "深棕荔枝皮", ("材质", "皮革", "深棕", "荔枝纹")),
    ("material_walnut", "胡桃木皮", ("材质", "木纹", "胡桃木", "暖色")),
    ("material_terrazzo", "浅色水磨石", ("材质", "石材", "水磨石", "碎石")),
    ("material_aluminum", "拉丝铝", ("材质", "金属", "铝", "拉丝")),
    ("material_copper", "锤纹铜箔", ("材质", "金属", "铜", "锤纹")),
    ("material_holographic_foil", "虹彩镭射箔", ("材质", "金属", "虹彩", "镭射")),
    ("material_frosted_glass", "磨砂玻璃", ("材质", "玻璃", "磨砂", "半透明")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _transparent_clean(image: Image.Image) -> tuple[Image.Image, int]:
    pixels = bytearray(image.convert("RGBA").tobytes())
    visible = 0
    for offset in range(0, len(pixels), 4):
        alpha = pixels[offset + 3]
        if alpha < 8:
            pixels[offset:offset + 4] = b"\0\0\0\0"
        elif alpha >= 16:
            visible += 1
    return Image.frombytes("RGBA", image.size, bytes(pixels)), visible


def _contact_sheet(paths: list[Path], output: Path) -> None:
    tile = 180
    sheet = Image.new("RGB", (tile * CELL_COUNT, tile * CELL_COUNT), (31, 35, 43))
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(paths):
        row, col = divmod(index, CELL_COUNT)
        x, y = col * tile, row * tile
        background = Image.new("RGBA", (tile, tile), (238, 239, 242, 255))
        block = 18
        checker = ImageDraw.Draw(background)
        for cy in range(0, tile, block):
            for cx in range(0, tile, block):
                if (cx // block + cy // block) % 2:
                    checker.rectangle((cx, cy, cx + block - 1, cy + block - 1),
                                      fill=(214, 217, 222, 255))
        with Image.open(path) as source:
            source = source.convert("RGBA")
            source.thumbnail((tile - 28, tile - 28), Image.Resampling.LANCZOS)
            background.alpha_composite(source, ((tile - source.width) // 2,
                                                (tile - source.height) // 2))
        sheet.paste(background.convert("RGB"), (x, y))
        draw.rectangle((x + 6, y + 6, x + 31, y + 25), fill=(22, 27, 35))
        draw.text((x + 12, y + 9), f"{index + 1:02d}", fill=(255, 255, 255))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, optimize=True)


def extract(sheet: Path, output: Path, report: Path) -> list[dict]:
    source = Image.open(sheet).convert("RGBA")
    width, height = source.size
    if source.getchannel("A").getextrema() != (0, 255):
        raise ValueError("source atlas must contain real transparent and opaque pixels")
    cell_w, cell_h = width / CELL_COUNT, height / CELL_COUNT
    assets: list[dict] = []
    targets: list[Path] = []
    output.mkdir(parents=True, exist_ok=True)

    for index, (stem, name, keywords) in enumerate(MATERIALS):
        row, col = divmod(index, CELL_COUNT)
        x0, x1 = round(col * cell_w), round((col + 1) * cell_w)
        y0, y1 = round(row * cell_h), round((row + 1) * cell_h)
        side = min(x1 - x0, y1 - y0)
        crop_side = round(side * CROP_FRACTION)
        left = x0 + ((x1 - x0) - crop_side) // 2
        top = y0 + ((y1 - y0) - crop_side) // 2
        box = (left, top, left + crop_side, top + crop_side)
        cropped, visible = _transparent_clean(source.crop(box))
        if visible < crop_side * crop_side * 0.25:
            raise ValueError(f"{stem} central crop is too empty: {visible} pixels")
        target = output / f"{stem}.png"
        if target.exists():
            raise FileExistsError(f"will not overwrite existing asset: {target}")
        artwork_size = OUTPUT_SIZE - 2 * PADDING
        artwork = cropped.resize((artwork_size, artwork_size), Image.Resampling.LANCZOS)
        prepared = Image.new("RGBA", (OUTPUT_SIZE, OUTPUT_SIZE), (0, 0, 0, 0))
        prepared.alpha_composite(artwork, (PADDING, PADDING))
        prepared.save(target, optimize=True)
        alpha = prepared.getchannel("A")
        bounds = alpha.point(lambda value: 255 if value >= 16 else 0).getbbox()
        assets.append({
            "index": index + 1,
            "stem": stem,
            "name": name,
            "subcategory": "材质纹理",
            "keywords": list(keywords),
            "row": row,
            "column": col,
            "sourceCell": [x0, y0, x1, y1],
            "cropBox": list(box),
            "size": [OUTPUT_SIZE, OUTPUT_SIZE],
            "alphaBounds": list(bounds) if bounds else None,
            "visiblePixels": visible,
            "alphaExtrema": list(alpha.getextrema()),
            "sha256": _sha(target),
        })
        targets.append(target)

    _contact_sheet(targets, report.with_name("material-texture-contact-sheet-20260925.png"))
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "schemaVersion": 1,
        "source": sheet.name,
        "sourceSha256": _sha(sheet),
        "sourceSize": [width, height],
        "layout": "4x4",
        "extraction": "centered square crop at 82% of each proportional cell; 32px transparent padding",
        "contactSheet": "material-texture-contact-sheet-20260925.png",
        "items": assets,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return assets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    print(json.dumps({"extracted": len(extract(args.sheet, args.output, args.report)),
                      "report": str(args.report)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
