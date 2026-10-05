"""Crop a 4x4 transparent ImageGen texture atlas into sticker PNGs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/vintage-film-print-texture-atlas-20260926.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_REPORT = ROOT / "docs/assets/vintage-film-print-texture-atlas-20260926.extraction.json"
DEFAULT_CONTACT = ROOT / "docs/assets/vintage-film-print-texture-contact-20260926.png"

ITEMS = (
    ("printfx_film_perforation_strip", "35 毫米胶片齿孔", ("复古", "胶片", "齿孔")),
    ("printfx_film_dust_specks", "胶片浮尘颗粒", ("复古", "胶片", "浮尘")),
    ("printfx_film_scratch_cluster", "胶片纵向划痕", ("复古", "胶片", "划痕")),
    ("printfx_ink_roller_grain", "油墨滚筒颗粒", ("印刷", "油墨", "颗粒")),
    ("printfx_halftone_gradient", "渐隐半调网点", ("印刷", "网点", "半调")),
    ("printfx_comic_halftone_burst", "漫画半调爆发", ("漫画", "网点", "爆发")),
    ("printfx_photocopy_tear_edge", "复印纸撕边", ("拼贴", "纸张", "撕边")),
    ("printfx_masking_tape_pair", "复古纸胶带", ("拼贴", "纸胶带", "手账")),
    ("printfx_curled_paper_corner", "卷起的纸角", ("拼贴", "纸张", "卷角")),
    ("printfx_creased_paper_sheet", "折痕纸张", ("纸张", "折痕", "复古")),
    ("printfx_toner_smudge", "复印机碳粉污迹", ("印刷", "复印", "污迹")),
    ("printfx_vintage_label_frame", "复古空白标签框", ("拼贴", "标签", "相框")),
    ("printfx_screenprint_splatter", "丝网印刷飞溅", ("印刷", "油墨", "飞溅")),
    ("printfx_analog_scanline_sweep", "模拟扫描纹理", ("模拟", "扫描线", "复古")),
    ("printfx_registration_marks", "四色套印标记", ("印刷", "套印", "十字线")),
    ("printfx_film_frame_corners", "胶片取景角标", ("胶片", "取景框", "角标")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contact_sheet(items: list[tuple[str, Image.Image]], target: Path) -> None:
    cell = 256
    sheet = Image.new("RGB", (cell * 4, cell * 4), (25, 29, 38))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 12)
    except OSError:
        font = ImageFont.load_default()
    for index, (stem, rgba) in enumerate(items):
        x, y = (index % 4) * cell, (index // 4) * cell
        tile = Image.new("RGBA", (cell, cell), (35, 42, 54, 255))
        tile_draw = ImageDraw.Draw(tile)
        checker = 16
        for yy in range(0, cell, checker):
            for xx in range(0, cell, checker):
                if (xx // checker + yy // checker) % 2:
                    tile_draw.rectangle((xx, yy, xx + checker - 1, yy + checker - 1),
                                        fill=(51, 59, 72, 255))
        art = rgba.copy()
        art.thumbnail((cell - 16, cell - 36), Image.Resampling.LANCZOS)
        tile.alpha_composite(art, ((cell - art.width) // 2, (cell - 28 - art.height) // 2))
        sheet.paste(tile.convert("RGB"), (x, y))
        draw.text((x + 7, y + cell - 18), f"{index + 1:02d}  {stem.removeprefix('printfx_')}",
                  fill=(235, 239, 246), font=font)
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, optimize=True)


def extract(sheet: Path, output: Path, report: Path, contact: Path) -> list[dict]:
    source = Image.open(sheet).convert("RGBA")
    width, height = source.size
    if width != height:
        raise ValueError(f"expected square 4x4 atlas, got {source.size}")
    if report.exists():
        raise FileExistsError(f"refusing to overwrite {report}")
    targets = [output / f"{stem}.png" for stem, *_ in ITEMS]
    collisions = [str(path) for path in targets if path.exists()]
    if collisions:
        raise FileExistsError("refusing to overwrite: " + ", ".join(collisions))

    output.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    contact_items: list[tuple[str, Image.Image]] = []
    for index, (stem, name, keywords) in enumerate(ITEMS):
        row, column = divmod(index, 4)
        x0, x1 = round(column * width / 4), round((column + 1) * width / 4)
        y0, y1 = round(row * height / 4), round((row + 1) * height / 4)
        cell = source.crop((x0, y0, x1, y1))
        rgba = np.asarray(cell, dtype=np.uint8)
        alpha = rgba[:, :, 3]
        visible = alpha >= 16
        visible_count = int(visible.sum())
        if visible_count < 100:
            raise ValueError(f"{stem} is nearly empty ({visible_count} visible pixels)")
        ys, xs = np.where(visible)
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
        boundary = int(visible[:8].sum() + visible[-8:].sum()
                       + visible[:, :8].sum() + visible[:, -8:].sum())

        side = max(cell.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.alpha_composite(cell, ((side - cell.width) // 2, (side - cell.height) // 2))
        target = output / f"{stem}.png"
        square.save(target, optimize=True)
        contact_items.append((stem, square))
        records.append({
            "stem": stem, "name": name, "subcategory": "电影叠加",
            "keywords": list(keywords), "row": row, "column": column,
            "cell": [x0, y0, x1, y1], "visibleBounds": bbox,
            "visiblePixels": visible_count,
            "visiblePixelsWithin8pxBoundary": boundary,
            "size": [side, side], "sha256": _sha(target),
        })

    _contact_sheet(contact_items, contact)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "generator": "OpenAI ImageGen", "source": sheet.name,
        "sourceSha256": _sha(sheet), "sourceSize": [width, height],
        "layout": "4x4", "alpha": "preserved from the generated RGBA atlas",
        "extraction": "equal proportional 4x4 crops; transparent pixels preserved; centered square RGBA PNG",
        "license": "Project-internal original AI-generated assets; not separately licensed for redistribution",
        "items": records, "contactSheet": contact.name,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--contact", type=Path, default=DEFAULT_CONTACT)
    args = parser.parse_args()
    records = extract(args.sheet, args.output, args.report, args.contact)
    print(json.dumps({"count": len(records), "report": str(args.report),
                      "contact": str(args.contact), "items": records},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
