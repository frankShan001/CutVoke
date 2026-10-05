"""Extract and review the 4x4 ImageGen cinematic optical overlay atlas.

The original generated atlas remains in docs/assets. Cropping is deterministic;
this script does not invent, repaint, or relabel the source artwork.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


ITEMS = (
    ("cinematic_optical_flare_amber", "琥珀宽银幕光斑", ("镜头", "暖光", "宽银幕")),
    ("cinematic_optical_flare_cyan", "青色宽银幕光斑", ("镜头", "冷光", "宽银幕")),
    ("cinematic_optical_flare_rose", "玫瑰色镜头光斑", ("镜头", "粉色", "柔光")),
    ("cinematic_optical_flare_violet", "紫蓝镜头光带", ("镜头", "紫色", "光带")),
    ("cinematic_optical_star_amber", "琥珀四芒星耀斑", ("镜头", "星芒", "金色")),
    ("cinematic_optical_star_pearl", "珍珠八芒星耀斑", ("镜头", "星芒", "珍珠白")),
    ("cinematic_optical_prism_fan", "彩虹棱镜散射", ("镜头", "棱镜", "彩虹")),
    ("cinematic_optical_ghost_ring", "虹彩镜头鬼影环", ("镜头", "鬼影", "虹彩")),
    ("cinematic_optical_leak_amber", "琥珀左侧漏光", ("电影", "漏光", "琥珀")),
    ("cinematic_optical_leak_cyan", "青蓝右侧漏光", ("电影", "漏光", "青蓝")),
    ("cinematic_optical_leak_rose", "玫瑰下沿漏光", ("电影", "漏光", "玫瑰")),
    ("cinematic_optical_leak_teal", "蓝青上角漏光", ("电影", "漏光", "蓝青")),
    ("cinematic_optical_bokeh_gold", "香槟金散景光点", ("镜头", "散景", "香槟金")),
    ("cinematic_optical_bokeh_ice", "冰蓝散景光点", ("镜头", "散景", "冰蓝")),
    ("cinematic_optical_halation_peach", "蜜桃柔光晕", ("电影", "光晕", "蜜桃")),
    ("cinematic_optical_arc_pearl", "珍珠色弧形光轨", ("镜头", "弧光", "光轨")),
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/cinematic-optical-overlay-atlas-20260926.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_REPORT = ROOT / "docs/assets/cinematic-optical-overlay-atlas-20260926.extraction.json"
DEFAULT_CONTACT = ROOT / "docs/assets/cinematic-optical-overlay-contact-20260926.png"
LICENSE = "Project-internal original (AI-generated); not separately licensed for redistribution"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _crop_boundary_count(alpha: np.ndarray, width: int, height: int) -> int:
    band = 4
    edge = np.zeros((height, width), dtype=bool)
    edge[:band, :] = edge[-band:, :] = True
    edge[:, :band] = edge[:, -band:] = True
    return int(((alpha >= 16) & edge).sum())


def extract(sheet: Path, output: Path, contact: Path) -> dict:
    source = Image.open(sheet).convert("RGBA")
    width, height = source.size
    if width != height or width < 1024:
        raise ValueError(f"expected a square atlas at least 1024px wide, got {source.size}")
    alpha_extrema = source.getchannel("A").getextrema()
    if alpha_extrema[0] != 0 or alpha_extrema[1] < 128:
        raise ValueError(f"source needs genuine transparent alpha, got {alpha_extrema}")

    output.mkdir(parents=True, exist_ok=True)
    targets = [output / f"{stem}.png" for stem, _, _ in ITEMS]
    existing = [path for path in targets if path.exists()]
    if existing:
        raise FileExistsError(f"will not overwrite existing assets: {existing[:3]}")

    records = []
    crops: list[Image.Image] = []
    for index, (stem, name, keywords) in enumerate(ITEMS):
        row, column = divmod(index, 4)
        left, top = round(column * width / 4), round(row * height / 4)
        right, bottom = round((column + 1) * width / 4), round((row + 1) * height / 4)
        crop = source.crop((left, top, right, bottom))
        pixels = np.asarray(crop, dtype=np.uint8).copy()
        before = _crop_boundary_count(pixels[:, :, 3], crop.width, crop.height)
        # Eliminate low-alpha generator specks and hidden RGB that can create
        # colored fringes during scale/composite. A four-pixel transparent rim
        # prevents neighboring cells from bleeding into the extracted PNG.
        pixels[pixels[:, :, 3] < 8] = (0, 0, 0, 0)
        boundary_count = _crop_boundary_count(pixels[:, :, 3], crop.width, crop.height)
        if boundary_count > 128:
            raise ValueError(f"{stem} has too much artwork touching a crop boundary: {boundary_count}")
        pixels[:4, :] = pixels[-4:, :] = pixels[:, :4] = pixels[:, -4:] = 0
        visible = pixels[:, :, 3] >= 16
        visible_pixels = int(visible.sum())
        if visible_pixels < 500:
            raise ValueError(f"{stem} is almost empty after boundary cleanup")
        artwork = Image.fromarray(pixels, "RGBA")
        artwork.save(targets[index], optimize=True)
        crops.append(artwork)
        records.append({
            "stem": stem,
            "name": name,
            "subcategory": "镜头光效",
            "keywords": list(keywords),
            "row": row,
            "column": column,
            "sourceCell": [left, top, right, bottom],
            "size": [crop.width, crop.height],
            "visiblePixels": visible_pixels,
            "sourceBoundaryPixels": before,
            "removedBoundaryPixels": boundary_count,
            "alphaExtrema": list(artwork.getchannel("A").getextrema()),
            "sha256": _sha(targets[index]),
        })

    cell_w, cell_h = 360, 360
    margin, label_h = 10, 24
    sheet_review = Image.new("RGB", (4 * cell_w, 4 * (cell_h + label_h)), (27, 30, 38))
    draw = ImageDraw.Draw(sheet_review)
    for index, artwork in enumerate(crops):
        row, column = divmod(index, 4)
        preview = artwork.copy()
        preview.thumbnail((cell_w - 2 * margin, cell_h - 2 * margin), Image.Resampling.LANCZOS)
        x = column * cell_w + (cell_w - preview.width) // 2
        y = row * (cell_h + label_h) + (cell_h - preview.height) // 2
        sheet_review.paste(preview, (x, y), preview)
        draw.text((column * cell_w + margin, row * (cell_h + label_h) + cell_h),
                  records[index]["stem"], fill=(233, 235, 241))
    contact.parent.mkdir(parents=True, exist_ok=True)
    sheet_review.save(contact, optimize=True)

    report = {
        "schemaVersion": 1,
        "generator": "OpenAI ImageGen",
        "layout": "4x4",
        "sourceImageSize": [width, height],
        "sourceSha256": _sha(sheet),
        "license": LICENSE,
        "cropCleanup": "zero alpha below 8; clear 4px cell rim; preserve semi-transparent pixels",
        "contactSheet": contact.relative_to(ROOT).as_posix(),
        "items": records,
    }
    DEFAULT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--contact", type=Path, default=DEFAULT_CONTACT)
    args = parser.parse_args()
    report = extract(args.sheet, args.output, args.contact)
    print(json.dumps({"sourceSha256": report["sourceSha256"],
                      "count": len(report["items"]),
                      "contactSheet": report["contactSheet"],
                      "items": [{"stem": item["stem"],
                                 "visiblePixels": item["visiblePixels"],
                                 "removedBoundaryPixels": item["removedBoundaryPixels"],
                                 "sha256": item["sha256"]}
                                for item in report["items"]]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
