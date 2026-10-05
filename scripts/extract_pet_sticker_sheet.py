"""Extract the generated 4x4 pet sticker atlas into transparent PNG assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/pet-sticker-sheet-20260925.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_REPORT = ROOT / "docs/assets/pet-sticker-sheet-20260925.extraction.json"
DEFAULT_CONTACT = ROOT / "docs/assets/pet-sticker-contact-sheet-20260925.png"
CATALOG_PATH = ROOT / "src/cutvoke/core/builtin_stickers.json"
GRID = 4
OUTPUT_SIZE = 320
PADDING = 32

ITEMS = (
    ("pet_tabby_cat", "橘猫坐坐", ("宠物", "橘猫", "猫咪", "萌宠")),
    ("pet_tuxedo_wave", "挥爪奶牛猫", ("宠物", "奶牛猫", "猫咪", "挥手")),
    ("pet_shiba", "奶油柴犬", ("宠物", "柴犬", "狗狗", "萌宠")),
    ("pet_corgi", "趴趴柯基", ("宠物", "柯基", "狗狗", "萌宠")),
    ("pet_sleepy_kitten", "打盹小猫", ("宠物", "小猫", "睡觉", "治愈")),
    ("pet_poodle", "卷毛贵宾犬", ("宠物", "贵宾犬", "狗狗", "卷毛")),
    ("pet_paw_trail", "爪印小路", ("宠物", "爪印", "脚印", "装饰")),
    ("pet_heart_bowl", "爱心食盆", ("宠物", "食盆", "爱心", "用品")),
    ("pet_mouse_toy", "逗猫小老鼠", ("宠物", "逗猫棒", "玩具", "猫咪")),
    ("pet_yarn_ball", "彩线毛线球", ("宠物", "毛线球", "玩具", "猫咪")),
    ("pet_fish_treat", "小鱼零食", ("宠物", "鱼形零食", "猫咪", "用品")),
    ("pet_paw_ball", "爪印网球", ("宠物", "网球", "狗狗", "玩具")),
    ("pet_bell_collar", "铃铛项圈", ("宠物", "项圈", "铃铛", "用品")),
    ("pet_carrier", "软垫航空箱", ("宠物", "航空箱", "出行", "用品")),
    ("pet_nose_greeting", "猫狗贴鼻问候", ("宠物", "猫狗", "伙伴", "问候")),
    ("pet_happy_dog", "吐舌开心狗", ("宠物", "狗狗", "开心", "爱心")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def register_catalog() -> int:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    existing = {item.get("stem") for item in catalog.get("stickers", [])}
    duplicates = sorted(stem for stem, *_ in ITEMS if stem in existing)
    if duplicates:
        raise ValueError(f"sticker catalog already contains: {duplicates}")
    if any(not (DEFAULT_OUTPUT / f"{stem}.png").is_file() for stem, *_ in ITEMS):
        raise FileNotFoundError("extract the pet sticker atlas before registering it")
    source = ("AI 生成图集 pet-sticker-sheet-20260925.png；CutVoke 原创资源，"
              "无第三方图像来源")
    catalog["stickers"].extend({
        "stem": stem,
        "name": name,
        "subcategory": "宠物日常",
        "keywords": list(keywords),
        "version": "1.0.0",
        "license": "MIT",
        "source": source,
        "defaultScale": 0.46,
    } for stem, name, keywords in ITEMS)
    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    return len(ITEMS)


def _contact_sheet(paths: list[Path], output: Path) -> None:
    tile = 180
    result = Image.new("RGB", (tile * GRID, tile * GRID), (225, 229, 234))
    draw = ImageDraw.Draw(result)
    for index, path in enumerate(paths):
        row, column = divmod(index, GRID)
        x, y = column * tile, row * tile
        background = Image.new("RGBA", (tile, tile), (242, 243, 245, 255))
        checker = ImageDraw.Draw(background)
        block = 18
        for cy in range(0, tile, block):
            for cx in range(0, tile, block):
                if (cx // block + cy // block) % 2:
                    checker.rectangle((cx, cy, cx + block - 1, cy + block - 1),
                                      fill=(218, 222, 227, 255))
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
    with Image.open(sheet) as opened:
        source = opened.convert("RGBA")
    width, height = source.size
    if abs(width - height) > 1:
        raise ValueError(f"expected a square 4x4 atlas, got {source.size}")
    if source.getchannel("A").getextrema() != (0, 255):
        raise ValueError("source atlas must contain real transparent and opaque pixels")
    if len(ITEMS) != GRID * GRID or len({item[0] for item in ITEMS}) != len(ITEMS):
        raise ValueError("atlas metadata must contain 16 unique sticker assets")

    targets = [output / f"{stem}.png" for stem, *_ in ITEMS]
    collisions = [path for path in targets if path.exists()]
    if collisions:
        raise FileExistsError("refusing to overwrite: " + ", ".join(map(str, collisions)))
    if report.exists() or contact_sheet.exists():
        raise FileExistsError("refusing to overwrite an extraction report or contact sheet")

    output.mkdir(parents=True, exist_ok=True)
    records = []
    for index, (stem, name, keywords) in enumerate(ITEMS):
        row, column = divmod(index, GRID)
        x0, x1 = round(column * width / GRID), round((column + 1) * width / GRID)
        y0, y1 = round(row * height / GRID), round((row + 1) * height / GRID)
        pixels = np.asarray(source.crop((x0, y0, x1, y1)), dtype=np.uint8).copy()
        pixels[pixels[:, :, 3] < 8] = (0, 0, 0, 0)
        visible = pixels[:, :, 3] >= 16
        visible_count = int(visible.sum())
        if visible_count < 5000:
            raise ValueError(f"{stem} is nearly empty ({visible_count} visible pixels)")
        boundary = int(visible[:8].sum() + visible[-8:].sum()
                       + visible[:, :8].sum() + visible[:, -8:].sum())
        if boundary > max(1200, int(visible_count * 0.03)):
            raise ValueError(f"{stem} artwork crosses its crop boundary ({boundary} pixels)")
        pixels[:8, :, :] = pixels[-8:, :, :] = 0
        pixels[:, :8, :] = pixels[:, -8:, :] = 0

        artwork = Image.fromarray(pixels, "RGBA")
        artwork.thumbnail((OUTPUT_SIZE - 2 * PADDING, OUTPUT_SIZE - 2 * PADDING),
                          Image.Resampling.LANCZOS)
        prepared = Image.new("RGBA", (OUTPUT_SIZE, OUTPUT_SIZE), (0, 0, 0, 0))
        prepared.alpha_composite(artwork, ((OUTPUT_SIZE - artwork.width) // 2,
                                           (OUTPUT_SIZE - artwork.height) // 2))
        target = output / f"{stem}.png"
        prepared.save(target, optimize=True)
        alpha = np.asarray(prepared.getchannel("A"), dtype=np.uint8)
        bounds = Image.fromarray((alpha >= 16).astype(np.uint8) * 255).getbbox()
        records.append({
            "index": index + 1,
            "stem": stem,
            "name": name,
            "subcategory": "宠物日常",
            "keywords": list(keywords),
            "row": row,
            "column": column,
            "sourceCell": [x0, y0, x1, y1],
            "sourceBoundaryPixelsWithin8px": boundary,
            "outputSize": [OUTPUT_SIZE, OUTPUT_SIZE],
            "visiblePixelsInSourceCell": visible_count,
            "visibleBounds": list(bounds) if bounds else None,
            "alphaExtrema": [int(alpha.min()), int(alpha.max())],
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
        "extraction": "proportional cell crops; remove 8px cell borders, fit artwork to 256px and center in 320px transparent PNGs",
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
    parser.add_argument("--register-catalog", action="store_true",
                        help="add the extracted assets to the built-in sticker catalog as candidates")
    args = parser.parse_args()
    records = extract(args.sheet, args.output, args.report, args.contact_sheet)
    registered = register_catalog() if args.register_catalog else 0
    print(json.dumps({"extracted": len(records), "report": str(args.report),
                      "contactSheet": str(args.contact_sheet),
                      "registeredCandidates": registered}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
