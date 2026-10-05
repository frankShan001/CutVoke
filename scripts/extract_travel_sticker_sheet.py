"""Extract a generated 4x4 travel-vlog sticker atlas into transparent PNGs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/travel-vlog-sticker-sheet-20260925-v2.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_REPORT = ROOT / "docs/assets/travel-vlog-sticker-sheet-20260925-v2.extraction.json"
DEFAULT_CONTACT = ROOT / "docs/assets/travel-vlog-sticker-contact-sheet-20260925.png"
GRID = 4
OUTPUT_SIZE = 320
PADDING = 32

ITEMS = (
    ("travel_map_route", "旅行路线地图", ("旅行", "地图", "路线", "vlog")),
    ("travel_location_pin", "风景定位标", ("旅行", "定位", "地点", "风景")),
    ("travel_instant_camera", "复古拍立得", ("旅行", "相机", "拍立得", "记录")),
    ("travel_suitcase", "海盐行李箱", ("旅行", "行李", "出发", "手帐")),
    ("travel_mountain_sun", "山野日出", ("旅行", "山野", "日出", "风景")),
    ("travel_compass", "旅行罗盘", ("旅行", "罗盘", "方向", "路线")),
    ("travel_train", "山海列车", ("旅行", "列车", "交通", "沿途")),
    ("travel_boarding_pass", "空白登机票", ("旅行", "机票", "登机", "留白")),
    ("travel_airplane_route", "航线纸飞机", ("旅行", "飞机", "航线", "路线")),
    ("travel_postcard", "海边明信片", ("旅行", "明信片", "海边", "手帐")),
    ("travel_hiking_boot", "山行靴印", ("旅行", "徒步", "登山", "脚印")),
    ("travel_beach_umbrella", "海岛遮阳伞", ("旅行", "海岛", "海浪", "夏日")),
    ("travel_camcorder", "复古摄像机", ("旅行", "摄像机", "视频", "记录")),
    ("travel_globe_route", "环游地球仪", ("旅行", "地球", "环球", "航线")),
    ("travel_film_frame", "胶片取景框", ("旅行", "胶片", "取景框", "复古")),
    ("travel_seashell_leaf", "贝壳与棕榈", ("旅行", "贝壳", "海岛", "装饰")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contact_sheet(paths: list[Path], output: Path) -> None:
    tile = 180
    result = Image.new("RGB", (tile * GRID, tile * GRID), (229, 232, 236))
    draw = ImageDraw.Draw(result)
    for index, path in enumerate(paths):
        row, column = divmod(index, GRID)
        x, y = column * tile, row * tile
        background = Image.new("RGBA", (tile, tile), (242, 243, 244, 255))
        checker = ImageDraw.Draw(background)
        block = 18
        for cy in range(0, tile, block):
            for cx in range(0, tile, block):
                if (cx // block + cy // block) % 2:
                    checker.rectangle((cx, cy, cx + block - 1, cy + block - 1),
                                      fill=(218, 221, 225, 255))
        with Image.open(path) as source:
            source = source.convert("RGBA")
            source.thumbnail((tile - 28, tile - 28), Image.Resampling.LANCZOS)
            background.alpha_composite(source, ((tile - source.width) // 2,
                                                (tile - source.height) // 2))
        result.paste(background.convert("RGB"), (x, y))
        draw.rectangle((x + 6, y + 6, x + 31, y + 25), fill=(22, 27, 35))
        draw.text((x + 12, y + 9), f"{index + 1:02d}", fill=(255, 255, 255))
    output.parent.mkdir(parents=True, exist_ok=True)
    result.save(output, optimize=True)


def extract(sheet: Path, output: Path, report: Path,
            contact_sheet: Path = DEFAULT_CONTACT) -> list[dict]:
    source = Image.open(sheet).convert("RGBA")
    width, height = source.size
    if abs(width / height - 1) > 0.02:
        raise ValueError(f"expected a square atlas, got {source.size}")
    if source.getchannel("A").getextrema() != (0, 255):
        raise ValueError("source atlas must contain real transparent and opaque pixels")
    if len(ITEMS) != GRID * GRID:
        raise ValueError("atlas metadata must contain exactly 16 unique items")
    if len({item[0] for item in ITEMS}) != len(ITEMS):
        raise ValueError("sticker asset stems must be unique")

    targets = [output / f"{stem}.png" for stem, *_ in ITEMS]
    collisions = [path for path in targets if path.exists()]
    if collisions:
        raise FileExistsError("refusing to overwrite: " + ", ".join(map(str, collisions)))
    if report.exists():
        raise FileExistsError(f"refusing to overwrite {report}")

    output.mkdir(parents=True, exist_ok=True)
    records = []
    for index, (stem, name, keywords) in enumerate(ITEMS):
        row, column = divmod(index, GRID)
        x0, x1 = round(column * width / GRID), round((column + 1) * width / GRID)
        y0, y1 = round(row * height / GRID), round((row + 1) * height / GRID)
        tile = source.crop((x0, y0, x1, y1))
        pixels = np.asarray(tile, dtype=np.uint8).copy()
        pixels[pixels[:, :, 3] < 8] = (0, 0, 0, 0)
        visible = pixels[:, :, 3] >= 16
        visible_count = int(visible.sum())
        if visible_count < 5000:
            raise ValueError(f"{stem} is nearly empty ({visible_count} visible pixels)")
        edge_pixels = int(
            visible[:8, :].sum() + visible[-8:, :].sum()
            + visible[:, :8].sum() + visible[:, -8:].sum())
        pixels[:8, :, :] = pixels[-8:, :, :] = 0
        pixels[:, :8, :] = pixels[:, -8:, :] = 0

        # Preserve the full cell crop and its alpha; normalize its slightly
        # unequal edge cells onto a uniform canvas with transparent padding.
        artwork = Image.fromarray(pixels, "RGBA")
        artwork.thumbnail((OUTPUT_SIZE - 2 * PADDING, OUTPUT_SIZE - 2 * PADDING),
                          Image.Resampling.LANCZOS)
        prepared = Image.new("RGBA", (OUTPUT_SIZE, OUTPUT_SIZE), (0, 0, 0, 0))
        prepared.alpha_composite(artwork, ((OUTPUT_SIZE - artwork.width) // 2,
                                           (OUTPUT_SIZE - artwork.height) // 2))
        target = output / f"{stem}.png"
        prepared.save(target, optimize=True)
        alpha = np.asarray(prepared.getchannel("A"), dtype=np.uint8)
        visible_bounds = Image.fromarray((alpha >= 16).astype(np.uint8) * 255).getbbox()
        records.append({
            "index": index + 1,
            "stem": stem,
            "name": name,
            "subcategory": "旅行手帐",
            "keywords": list(keywords),
            "row": row,
            "column": column,
            "sourceCell": [x0, y0, x1, y1],
            "outputSize": [OUTPUT_SIZE, OUTPUT_SIZE],
            "visiblePixelsInSourceCell": visible_count,
            "sourceBoundaryPixelsWithin8px": edge_pixels,
            "visibleBounds": list(visible_bounds) if visible_bounds else None,
            "alphaExtrema": list(prepared.getchannel("A").getextrema()),
            "sha256": _sha(target),
        })

    _contact_sheet(targets, contact_sheet)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "schemaVersion": 1,
        "source": sheet.name,
        "sourceSha256": _sha(sheet),
        "sourceSize": [width, height],
        "layout": "4x4",
        "extraction": "proportional full-cell crops with alpha preserved, fit to 256px and centered in 320px transparent PNGs",
        "contactSheet": contact_sheet.name,
        "items": records,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--contact-sheet", type=Path, default=DEFAULT_CONTACT)
    args = parser.parse_args()
    records = extract(args.sheet, args.output, args.report, args.contact_sheet)
    print(json.dumps({"extracted": len(records), "report": str(args.report),
                      "contactSheet": str(args.contact_sheet)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
