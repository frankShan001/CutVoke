"""Extract a 4x4 ImageGen pixel-game effect atlas into transparent stickers."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/pixel-game-effect-atlas-20260926.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_REPORT = ROOT / "docs/assets/pixel-game-effect-atlas-20260926.extraction.json"
DEFAULT_CONTACT = ROOT / "docs/assets/pixel-game-effect-atlas-20260926-contact.png"
LICENSE = "Project-internal original (AI-generated); not separately licensed for redistribution"
SUBCATEGORY = "特效贴图"

ITEMS = (
    ("fxoverlay_pixel_impact_gold", "金色像素冲击", ("游戏", "像素", "冲击")),
    ("fxoverlay_pixel_frost_burst", "冰晶像素爆发", ("游戏", "像素", "冰晶")),
    ("fxoverlay_pixel_fireball", "橙色像素火球", ("游戏", "像素", "火焰")),
    ("fxoverlay_pixel_lightning", "蓝色像素电弧", ("游戏", "像素", "闪电")),
    ("fxoverlay_pixel_portal", "紫色像素传送门", ("游戏", "像素", "传送门")),
    ("fxoverlay_pixel_heal_swirl", "绿色像素治疗能量", ("游戏", "像素", "治疗")),
    ("fxoverlay_pixel_shockwave", "洋红像素冲击波", ("游戏", "像素", "冲击波")),
    ("fxoverlay_pixel_dash", "青色像素疾速拖尾", ("游戏", "像素", "速度")),
    ("fxoverlay_pixel_power_orb", "紫色像素能量球", ("游戏", "像素", "能量球")),
    ("fxoverlay_pixel_smoke", "紫黑像素烟团", ("游戏", "像素", "烟雾")),
    ("fxoverlay_pixel_arcade_coins", "街机金币叠影", ("游戏", "像素", "金币")),
    ("fxoverlay_pixel_toxic_bubbles", "青柠像素毒雾泡泡", ("游戏", "像素", "毒雾")),
    ("fxoverlay_pixel_shield", "冰蓝像素护盾", ("游戏", "像素", "护盾")),
    ("fxoverlay_pixel_debris", "橙色像素碎石爆发", ("游戏", "像素", "碎石")),
    ("fxoverlay_pixel_rainbow_sparks", "彩虹像素星光", ("游戏", "像素", "星光")),
    ("fxoverlay_pixel_critical_hit", "红白像素重击闪光", ("游戏", "像素", "重击")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contact_sheet(items: list[tuple[str, Image.Image]], target: Path) -> None:
    cell, label_height = 320, 30
    sheet = Image.new("RGB", (cell * 4, (cell + label_height) * 4), (28, 33, 43))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 13)
    except OSError:
        font = ImageFont.load_default()
    for index, (stem, image) in enumerate(items):
        col, row = index % 4, index // 4
        x, y = col * cell, row * (cell + label_height)
        tile = Image.new("RGBA", (cell, cell), (0, 0, 0, 255))
        tile_draw = ImageDraw.Draw(tile)
        checker = 20
        for yy in range(0, cell, checker):
            for xx in range(0, cell, checker):
                color = (225, 228, 232, 255) if (xx // checker + yy // checker) % 2 else (247, 248, 249, 255)
                tile_draw.rectangle((xx, yy, xx + checker - 1, yy + checker - 1), fill=color)
        art = image.copy()
        art.thumbnail((cell - 28, cell - 28), Image.Resampling.LANCZOS)
        tile.alpha_composite(art, ((cell - art.width) // 2, (cell - art.height) // 2))
        sheet.paste(tile.convert("RGB"), (x, y))
        draw.text((x + 8, y + cell + 5), f"{index + 1:02d}  {stem}",
                  fill=(234, 239, 246), font=font)
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, optimize=True)


def extract(sheet: Path, output: Path, report: Path, contact: Path) -> list[dict]:
    source = Image.open(sheet).convert("RGBA")
    width, height = source.size
    if abs(width - height) > 1:
        raise ValueError(f"expected a square 4x4 atlas, got {source.size}")
    alpha = np.asarray(source.getchannel("A"), dtype=np.uint8)
    if alpha.min() != 0 or alpha.max() != 255:
        raise ValueError("source must contain genuine transparent and opaque pixels")
    if report.exists() or contact.exists():
        raise FileExistsError("refusing to overwrite an existing report or contact sheet")

    crops: list[tuple[str, Image.Image, dict]] = []
    for index, (stem, name, keywords) in enumerate(ITEMS):
        row, col = divmod(index, 4)
        x0, x1 = round(col * width / 4), round((col + 1) * width / 4)
        y0, y1 = round(row * height / 4), round((row + 1) * height / 4)
        pixels = np.asarray(source.crop((x0, y0, x1, y1)), dtype=np.uint8).copy()
        pixels[pixels[:, :, 3] < 8] = (0, 0, 0, 0)
        visible = pixels[:, :, 3] >= 16
        visible_count = int(visible.sum())
        if visible_count < 1_000:
            raise ValueError(f"{stem} is nearly empty ({visible_count} visible pixels)")
        boundary = int(visible[:8].sum() + visible[-8:].sum()
                       + visible[:, :8].sum() + visible[:, -8:].sum())
        if boundary > max(200, int(visible_count * 0.025)):
            raise ValueError(f"{stem} crosses the cell safety gutter ({boundary} boundary pixels)")
        pixels[:8, :, :] = pixels[-8:, :, :] = 0
        pixels[:, :8, :] = pixels[:, -8:, :] = 0
        side = max(pixels.shape[:2])
        square = np.zeros((side, side, 4), dtype=np.uint8)
        top, left = (side - pixels.shape[0]) // 2, (side - pixels.shape[1]) // 2
        square[top:top + pixels.shape[0], left:left + pixels.shape[1]] = pixels
        result = Image.fromarray(square, "RGBA")
        visible_after = square[:, :, 3] >= 16
        ys, xs = np.where(visible_after)
        record = {
            "stem": stem, "name": name, "subcategory": SUBCATEGORY,
            "keywords": list(keywords), "row": row, "column": col,
            "sourceCell": [x0, y0, x1, y1],
            "visibleBounds": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
            "visiblePixels": int(visible_after.sum()),
            "trimmedBoundaryPixels": boundary,
            "alphaExtrema": [int(square[:, :, 3].min()), int(square[:, :, 3].max())],
            "size": [side, side],
        }
        crops.append((stem, result, record))

    output.mkdir(parents=True, exist_ok=True)
    targets = [output / f"{stem}.png" for stem, _, _ in crops]
    collisions = [str(path) for path in targets if path.exists()]
    if collisions:
        raise FileExistsError("refusing to overwrite: " + ", ".join(collisions))
    saved: list[tuple[str, Image.Image]] = []
    records = []
    for stem, image, record in crops:
        target = output / f"{stem}.png"
        image.save(target, optimize=True)
        record["sha256"] = _sha(target)
        records.append(record)
        saved.append((stem, image))

    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "schemaVersion": 1,
        "generator": "OpenAI ImageGen",
        "source": sheet.name,
        "sourceSha256": _sha(sheet),
        "layout": "4x4",
        "subcategory": SUBCATEGORY,
        "license": LICENSE,
        "items": records,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _contact_sheet(saved, contact)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--contact", type=Path, default=DEFAULT_CONTACT)
    args = parser.parse_args()
    records = extract(args.sheet, args.output, args.report, args.contact)
    print(json.dumps({"count": len(records), "items": records}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
