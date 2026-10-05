"""Extract a 4x4 generated digital-glitch atlas into transparent sticker PNGs.

The source atlas stays in docs/assets. Cropping is deterministic and preserves
the generated alpha; no artwork is repainted or synthesized by this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/digital-glitch-overlay-texture-sheet-20260925.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_MANIFEST = ROOT / "docs/assets/digital-glitch-overlay-texture-sheet-20260925.extraction.json"
DEFAULT_CONTACT = ROOT / "docs/assets/digital-glitch-overlay-texture-contact-sheet-20260925.png"
GRID = 4
OUTPUT_SIZE = 320
ARTWORK_SIZE = 288
SOURCE = (
    "Generated with OpenAI built-in image generation on 2026-09-25 as one "
    "4x4 transparent digital-glitch atlas; deterministically cropped by "
    "scripts/extract_digital_glitch_texture_sheet.py."
)

ITEMS = (
    ("glitch_horizontal_tear", "横向撕裂", ("数码故障", "横向", "色散", "信号干扰")),
    ("glitch_rgb_blocks", "RGB色块错位", ("数码故障", "RGB", "像素", "错位")),
    ("glitch_scan_bars", "扫描光条", ("数码故障", "扫描线", "光条", "赛博")),
    ("glitch_data_slices", "数据切片", ("数码故障", "切片", "数据", "横向")),
    ("glitch_frame_echo", "跳帧残影", ("数码故障", "跳帧", "残影", "色差")),
    ("glitch_pixel_dissolve", "像素溶解", ("数码故障", "像素", "溶解", "碎片")),
    ("glitch_channel_corner", "分色角芒", ("数码故障", "分色", "角落", "光效")),
    ("glitch_sync_roll", "纵向同步漂移", ("数码故障", "同步", "纵向", "扫描")),
    ("glitch_prism_shard", "棱镜碎片", ("数码故障", "棱镜", "碎片", "折射")),
    ("glitch_signal_break", "信号断裂", ("数码故障", "信号", "断裂", "色块")),
    ("glitch_wave_interrupt", "波形中断", ("数码故障", "波形", "中断", "干扰")),
    ("glitch_tracking_brackets", "霓虹追踪框", ("数码故障", "追踪框", "霓虹", "取景")),
    ("glitch_packet_streaks", "数据斜掠", ("数码故障", "数据", "斜线", "速度")),
    ("glitch_mosaic_tiles", "马赛克跳变", ("数码故障", "马赛克", "像素", "跳变")),
    ("glitch_double_image", "双影错帧", ("数码故障", "双影", "错帧", "残像")),
    ("glitch_chromatic_edges", "色差分离", ("数码故障", "色差", "边缘", "分色")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contact_sheet(paths: list[Path], names: tuple[tuple[str, str, tuple[str, ...]], ...],
                   output: Path) -> None:
    tile, label_h = 224, 34
    sheet = Image.new("RGB", (tile * GRID, (tile + label_h) * GRID), (34, 39, 48))
    draw = ImageDraw.Draw(sheet)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 15) if font_path.is_file() else ImageFont.load_default()
    for index, (path, item) in enumerate(zip(paths, names, strict=True)):
        row, col = divmod(index, GRID)
        x, y = col * tile, row * (tile + label_h)
        background = Image.new("RGBA", (tile, tile), (244, 245, 247, 255))
        checker = ImageDraw.Draw(background)
        block = 16
        for cy in range(0, tile, block):
            for cx in range(0, tile, block):
                if (cx // block + cy // block) % 2:
                    checker.rectangle((cx, cy, cx + block - 1, cy + block - 1),
                                      fill=(214, 218, 224, 255))
        with Image.open(path) as source:
            artwork = source.convert("RGBA")
            artwork.thumbnail((tile - 28, tile - 28), Image.Resampling.LANCZOS)
            background.alpha_composite(artwork, ((tile - artwork.width) // 2,
                                                 (tile - artwork.height) // 2))
        sheet.paste(background.convert("RGB"), (x, y))
        draw.text((x + 8, y + tile + 7), f"{index + 1:02d}  {item[1]}",
                  font=font, fill=(244, 246, 250))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, optimize=True)


def extract(sheet: Path, output: Path, manifest: Path,
            contact_sheet: Path = DEFAULT_CONTACT) -> list[dict]:
    source = Image.open(sheet).convert("RGBA")
    width, height = source.size
    if abs(width - height) > 1:
        raise ValueError(f"expected a square 4x4 atlas, got {source.size}")
    if len(ITEMS) != GRID * GRID or len({item[0] for item in ITEMS}) != len(ITEMS):
        raise ValueError("atlas metadata must contain 16 unique asset stems")
    alpha = np.asarray(source.getchannel("A"), dtype=np.uint8)
    if alpha.min() != 0 or alpha.max() < 250:
        raise ValueError("source atlas must contain genuine transparent and opaque pixels")

    targets = [output / f"{stem}.png" for stem, *_ in ITEMS]
    collisions = [path for path in targets if path.exists()]
    if collisions:
        raise FileExistsError("refusing to overwrite: " + ", ".join(map(str, collisions)))
    if manifest.exists():
        raise FileExistsError(f"refusing to overwrite {manifest}")
    if contact_sheet.exists():
        raise FileExistsError(f"refusing to overwrite {contact_sheet}")

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
        if visible_count < 1000:
            raise ValueError(f"{stem} is nearly empty ({visible_count} visible pixels)")
        boundary = int(visible[:8].sum() + visible[-8:].sum()
                       + visible[:, :8].sum() + visible[:, -8:].sum())
        if boundary > max(20, int(visible_count * 0.001)):
            raise ValueError(f"{stem} artwork reaches its 8px crop boundary ({boundary} pixels)")

        cell = Image.fromarray(pixels, "RGBA")
        cell.thumbnail((ARTWORK_SIZE, ARTWORK_SIZE), Image.Resampling.LANCZOS)
        prepared = Image.new("RGBA", (OUTPUT_SIZE, OUTPUT_SIZE), (0, 0, 0, 0))
        prepared.alpha_composite(cell, ((OUTPUT_SIZE - cell.width) // 2,
                                        (OUTPUT_SIZE - cell.height) // 2))
        target = output / f"{stem}.png"
        prepared.save(target, optimize=True)
        out_alpha = np.asarray(prepared.getchannel("A"), dtype=np.uint8)
        bbox = Image.fromarray((out_alpha >= 16).astype(np.uint8) * 255).getbbox()
        records.append({
            "index": index + 1,
            "stem": stem,
            "name": name,
            "subcategory": "数码故障",
            "keywords": list(keywords),
            "row": row,
            "column": column,
            "sourceCell": [x0, y0, x1, y1],
            "outputSize": [OUTPUT_SIZE, OUTPUT_SIZE],
            "visiblePixelsInSourceCell": visible_count,
            "sourceBoundaryPixelsWithin8px": boundary,
            "visibleBounds": list(bbox) if bbox else None,
            "alphaExtrema": list(prepared.getchannel("A").getextrema()),
            "sha256": _sha(target),
            "source": SOURCE,
            "license": "MIT",
        })

    _contact_sheet(targets, ITEMS, contact_sheet)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "schemaVersion": 1,
        "source": sheet.name,
        "sourceSha256": _sha(sheet),
        "sourceSize": [width, height],
        "layout": "4x4",
        "extraction": "proportional full-cell crops; alpha preserved; fit within 288px and centered on 320px transparent PNG canvases",
        "generationSource": SOURCE,
        "license": "MIT",
        "contactSheet": contact_sheet.name,
        "items": records,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--contact-sheet", type=Path, default=DEFAULT_CONTACT)
    args = parser.parse_args()
    rows = extract(args.sheet, args.output, args.manifest, args.contact_sheet)
    print(json.dumps({"extracted": len(rows), "manifest": str(args.manifest),
                      "contactSheet": str(args.contact_sheet)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
