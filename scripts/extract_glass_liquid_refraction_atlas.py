"""Extract a 4x4 transparent ImageGen sheet into video overlay texture stickers."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/glass-liquid-refraction-atlas-20260927.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_REPORT = ROOT / "docs/assets/glass-liquid-refraction-atlas-20260927.extraction.json"
DEFAULT_CONTACT = ROOT / "docs/assets/glass-liquid-refraction-atlas-20260927-contact.png"
GRID = 4
OUTPUT_SIZE = 512
LICENSE = "Project-internal original (AI-generated); not separately licensed for redistribution"

ITEMS = (
    ("fxoverlay_refraction_caustics_turquoise", "青碧水纹焦散", ("水纹", "焦散", "青色")),
    ("fxoverlay_refraction_liquidglass_violet", "紫晶液态玻璃", ("液态玻璃", "紫色", "折射")),
    ("fxoverlay_refraction_oilslick_iridescent", "虹彩油膜折光", ("油膜", "虹彩", "折光")),
    ("fxoverlay_refraction_icefracture_cyan", "冰晶裂纹", ("冰晶", "裂纹", "冷色")),
    ("fxoverlay_refraction_molten_amber", "琥珀熔璃纹路", ("熔璃", "琥珀", "金色")),
    ("fxoverlay_refraction_pearl_sheen", "珍珠母贝虹光", ("珍珠", "虹光", "柔光")),
    ("fxoverlay_refraction_prism_shards", "棱镜晶体碎光", ("棱镜", "晶体", "碎光")),
    ("fxoverlay_refraction_glass_ripples", "透明玻璃涟漪", ("玻璃", "涟漪", "圆环")),
    ("fxoverlay_refraction_underwater_blue", "深海蓝光焦散", ("深海", "焦散", "蓝色")),
    ("fxoverlay_refraction_holo_foil", "青紫全息箔褶", ("全息", "箔片", "青紫")),
    ("fxoverlay_refraction_heat_haze", "橙色热浪折射", ("热浪", "折射", "橙色")),
    ("fxoverlay_refraction_emerald_geode", "祖母绿晶簇闪光", ("祖母绿", "晶簇", "矿物")),
    ("fxoverlay_refraction_brushed_silver", "银色流动拉丝", ("银色", "金属", "拉丝")),
    ("fxoverlay_refraction_soapfilm_bubbles", "肥皂膜虹彩泡泡", ("泡泡", "虹彩", "透明")),
    ("fxoverlay_refraction_liquid_chrome_rose", "玫瑰金液态铬花", ("液态铬", "玫瑰金", "金属")),
    ("fxoverlay_refraction_rainbow_caustics", "彩虹边缘焦散光", ("彩虹", "边缘光", "焦散")),
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _edge_pixels(alpha: np.ndarray, band: int = 8) -> int:
    edge = np.zeros(alpha.shape, dtype=bool)
    edge[:band, :] = edge[-band:, :] = True
    edge[:, :band] = edge[:, -band:] = True
    return int(((alpha >= 16) & edge).sum())


def _contact_sheet(crops: list[Image.Image], output: Path) -> None:
    tile_w, preview_size, label_h, margin = 520, 240, 26, 10
    cell_h = preview_size + 2 * margin + label_h
    sheet = Image.new("RGB", (tile_w * GRID, cell_h * GRID), (30, 33, 42))
    draw = ImageDraw.Draw(sheet)
    backgrounds = ((19, 29, 49), (224, 231, 239))
    for index, (artwork, (stem, _, _)) in enumerate(zip(crops, ITEMS, strict=True)):
        row, column = divmod(index, GRID)
        x0, y0 = column * tile_w, row * cell_h
        artwork = artwork.resize((preview_size, preview_size), Image.Resampling.LANCZOS)
        for side, background in enumerate(backgrounds):
            base = Image.new("RGBA", artwork.size, (*background, 255))
            base.alpha_composite(artwork)
            sheet.paste(base.convert("RGB"), (x0 + margin + side * preview_size, y0 + margin))
        draw.text((x0 + margin, y0 + preview_size + margin), stem, fill=(239, 241, 246))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, optimize=True)


def extract(sheet: Path, output: Path, report_path: Path, contact_path: Path) -> dict:
    source = Image.open(sheet).convert("RGBA")
    width, height = source.size
    if width != height or width < 1024:
        raise ValueError(f"expected a square 4x4 atlas at least 1024px wide, got {source.size}")
    alpha_extrema = source.getchannel("A").getextrema()
    if alpha_extrema[0] != 0 or alpha_extrema[1] < 128:
        raise ValueError(f"source must have real transparency, got alpha range {alpha_extrema}")

    output.mkdir(parents=True, exist_ok=True)
    targets = [output / f"{stem}.png" for stem, _, _ in ITEMS]
    existing = [path for path in targets if path.exists()]
    if existing:
        raise FileExistsError("refusing to overwrite existing sticker assets: " + ", ".join(map(str, existing[:4])))
    if report_path.exists() or contact_path.exists():
        raise FileExistsError("refusing to overwrite the extraction report or contact sheet")

    cell_w, cell_h = width / GRID, height / GRID
    records: list[dict] = []
    crops: list[Image.Image] = []
    for index, (stem, name, keywords) in enumerate(ITEMS):
        row, column = divmod(index, GRID)
        left, right = round(column * cell_w), round((column + 1) * cell_w)
        top, bottom = round(row * cell_h), round((row + 1) * cell_h)
        pixels = np.asarray(source.crop((left, top, right, bottom)), dtype=np.uint8).copy()
        before = int((pixels[:, :, 3] >= 16).sum())
        boundary_pixels = _edge_pixels(pixels[:, :, 3])
        if before < 1000:
            raise ValueError(f"{stem} is nearly empty ({before} visible pixels)")
        if boundary_pixels > max(300, int(before * 0.01)):
            raise ValueError(f"{stem} artwork crosses its crop boundary ({boundary_pixels}/{before})")

        # Clear near-transparent generator noise and a narrow cell rim to avoid
        # neighboring-cell bleed; preserve all useful semi-transparent pixels.
        pixels[pixels[:, :, 3] < 8] = (0, 0, 0, 0)
        pixels[:8, :, :] = pixels[-8:, :, :] = 0
        pixels[:, :8, :] = pixels[:, -8:, :] = 0
        artwork = Image.fromarray(pixels, "RGBA")
        artwork = artwork.resize((OUTPUT_SIZE, OUTPUT_SIZE), Image.Resampling.LANCZOS)
        target = targets[index]
        artwork.save(target, optimize=True)
        crops.append(artwork)
        records.append({
            "stem": stem,
            "name": name,
            "subcategory": "特效贴图",
            "keywords": list(keywords),
            "row": row,
            "column": column,
            "sourceCell": [left, top, right, bottom],
            "outputSize": [OUTPUT_SIZE, OUTPUT_SIZE],
            "visiblePixelsBeforeCleanup": before,
            "sourceBoundaryPixels": boundary_pixels,
            "alphaExtrema": list(artwork.getchannel("A").getextrema()),
            "sha256": _sha(target),
        })

    _contact_sheet(crops, contact_path)
    report = {
        "schemaVersion": 1,
        "generator": "OpenAI ImageGen",
        "layout": "4x4 equal square cells",
        "source": sheet.relative_to(ROOT).as_posix(),
        "sourceImageSize": [width, height],
        "sourceSha256": _sha(sheet),
        "license": LICENSE,
        "cropCleanup": "alpha below 8 set transparent; clear 8px cell rim; preserve semi-transparent pixels; resize to 512x512",
        "contactSheet": contact_path.relative_to(ROOT).as_posix(),
        "items": records,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--contact", type=Path, default=DEFAULT_CONTACT)
    args = parser.parse_args()
    report = extract(args.sheet, args.output, args.report, args.contact)
    print(json.dumps({"sourceSha256": report["sourceSha256"], "count": len(report["items"]),
                      "contactSheet": report["contactSheet"],
                      "items": [{"stem": item["stem"], "visiblePixels": item["visiblePixelsBeforeCleanup"],
                                 "boundaryPixels": item["sourceBoundaryPixels"], "sha256": item["sha256"]}
                                for item in report["items"]]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
