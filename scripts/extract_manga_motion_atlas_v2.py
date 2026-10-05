"""Crop an ImageGen 4x4 manga-motion atlas into transparent sticker assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/manga-motion-atlas-v2-20260926.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_REPORT = ROOT / "docs/assets/manga-motion-atlas-v2-20260926.extraction.json"
DEFAULT_CONTACT = ROOT / "docs/assets/manga-motion-atlas-v2-20260926-contact.png"
LICENSE = "Project-internal original (AI-generated); not separately licensed for redistribution"

ITEMS = (
    ("manga_v2_bw_speed_burst", "黑白放射冲击线", ("漫画", "放射", "冲击")),
    ("manga_v2_coral_impact_crack", "珊瑚红排线裂纹", ("漫画", "裂纹", "冲击")),
    ("manga_v2_lime_spiral_motion", "青柠螺旋动线", ("漫画", "螺旋", "动势")),
    ("manga_v2_ink_smoke_wipe", "象牙白墨烟擦拭", ("漫画", "墨烟", "擦拭")),
    ("manga_v2_cobalt_accel_arcs", "钴蓝弧形加速线", ("漫画", "加速线", "钴蓝")),
    ("manga_v2_violet_star_shards", "紫罗兰星屑旋涡", ("漫画", "星屑", "旋涡")),
    ("manga_v2_teal_pulse_rings", "青绿双环脉冲", ("漫画", "脉冲", "光环")),
    ("manga_v2_magenta_afterimages", "洋红错位残影", ("漫画", "残影", "洋红")),
    ("manga_v2_amber_impact_cloud", "琥珀尘爆云团", ("漫画", "尘爆", "琥珀")),
    ("manga_v2_cyan_wind_tunnel", "冰青风洞旋线", ("漫画", "风洞", "冰青")),
    ("manga_v2_pearl_fracture_fan", "珍珠碎光扇束", ("漫画", "碎光", "放射")),
    ("manga_v2_lime_electric_zaps", "青柠电光折线", ("漫画", "电光", "折线")),
    ("manga_v2_violet_halftone_wave", "紫色半调浪花", ("漫画", "半调", "浪花")),
    ("manga_v2_vermilion_slashes", "朱红斩击弧线", ("漫画", "斩击", "朱红")),
    ("manga_v2_gold_spark_curve", "金色火花轨迹", ("漫画", "火花", "轨迹")),
    ("manga_v2_blue_coral_ink_burst", "蓝珊瑚水墨爆发", ("漫画", "水墨", "爆发")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contact_sheet(items: list[tuple[str, Image.Image]], target: Path) -> None:
    cell = 320
    label_height = 30
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
                if (xx // checker + yy // checker) % 2:
                    tile_draw.rectangle((xx, yy, xx + checker - 1, yy + checker - 1),
                                        fill=(225, 228, 232, 255))
                else:
                    tile_draw.rectangle((xx, yy, xx + checker - 1, yy + checker - 1),
                                        fill=(247, 248, 249, 255))
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
            "stem": stem, "name": name, "subcategory": "漫画动感",
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
