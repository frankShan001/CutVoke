"""Crop a transparent overlay-texture atlas into sticker-library PNGs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image


NAMES = (
    "texture_lens_flare_amber", "texture_light_leak_duotone",
    "texture_bokeh_pastel", "texture_arc_electric",
    "texture_orbit_magic", "texture_speedlines_white",
    "texture_halftone_blue", "texture_swoosh_pink",
    "texture_sunbeam_gold", "texture_hud_scan_cyan",
    "texture_focus_brackets_lime", "texture_waveform_teal_coral",
    "texture_spark_trail_amber", "texture_portal_violet",
    "texture_corner_grid_cyan", "texture_glint_pearl",
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/video-overlay-texture-sheet-20260924.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_MANIFEST = ROOT / "docs/assets/video-overlay-texture-sheet-20260924.extraction.json"


def extract(
    sheet: Path,
    output: Path,
    manifest: Path,
    names: tuple[str, ...] = NAMES,
    rows: int = 4,
    columns: int = 4,
    inset_px: int = 0,
) -> list[dict]:
    source = Image.open(sheet).convert("RGBA")
    original_width, original_height = source.size
    if rows < 1 or columns < 1:
        raise ValueError("rows and columns must be positive")
    if inset_px < 0:
        raise ValueError("inset_px must be non-negative")
    cell_aspect = (original_width / columns) / (original_height / rows)
    if abs(cell_aspect - 1) > 0.04:
        raise ValueError(
            f"expected approximately square {rows}x{columns} atlas cells, got {source.size}")
    cell_side = min(original_width / columns, original_height / rows)
    crop_width = round(cell_side * columns)
    crop_height = round(cell_side * rows)
    crop_left = (original_width - crop_width) // 2
    crop_top = (original_height - crop_height) // 2
    source = source.crop((crop_left, crop_top, crop_left + crop_width, crop_top + crop_height))
    width, height = source.size
    if (len(names) != rows * columns or len(set(names)) != len(names) or
            any(not name.isascii() or not name.replace("_", "").isalnum()
                for name in names)):
        raise ValueError(
            f"names must contain {rows * columns} unique ASCII alphanumeric/underscore stems")
    alpha = np.asarray(source.getchannel("A"), dtype=np.uint8)
    if alpha.min() != 0 or alpha.max() < 250:
        raise ValueError("source must contain genuine transparent and near-opaque pixels")

    targets = [output / f"{name}.png" for name in names]
    if any(target.exists() for target in targets):
        existing = [str(target) for target in targets if target.exists()]
        raise FileExistsError("refusing to overwrite: " + ", ".join(existing))
    if manifest.exists():
        raise FileExistsError(f"refusing to overwrite {manifest}")

    output.mkdir(parents=True, exist_ok=True)
    records = []
    for index, stem in enumerate(names):
        row, col = divmod(index, columns)
        x0 = round(col * width / columns) + inset_px
        x1 = round((col + 1) * width / columns) - inset_px
        y0 = round(row * height / rows) + inset_px
        y1 = round((row + 1) * height / rows) - inset_px
        if x1 <= x0 or y1 <= y0:
            raise ValueError("inset_px is too large for the selected atlas grid")
        artwork = source.crop((x0, y0, x1, y1))
        pixels = np.asarray(artwork, dtype=np.uint8).copy()
        pixels[pixels[:, :, 3] < 8] = (0, 0, 0, 0)
        visible = pixels[:, :, 3] >= 16
        visible_count = int(visible.sum())
        if visible_count < 1000:
            raise ValueError(f"{stem} is nearly empty ({visible_count} visible pixels)")
        boundary = int(
            visible[:12, :].sum() + visible[-12:, :].sum()
            + visible[:, :12].sum() + visible[:, -12:].sum()
        )
        ys, xs = np.where(visible)
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]

        # Pad rounding differences to a uniform square without resampling or
        # changing the generated artwork.
        side = max(pixels.shape[0], pixels.shape[1])
        square = np.zeros((side, side, 4), dtype=np.uint8)
        top = (side - pixels.shape[0]) // 2
        left = (side - pixels.shape[1]) // 2
        square[top:top + pixels.shape[0], left:left + pixels.shape[1]] = pixels
        target = output / f"{stem}.png"
        Image.fromarray(square, "RGBA").save(target, optimize=True)
        records.append({
            "stem": stem,
            "row": row,
            "column": col,
            "sourceCell": [x0, y0, x1, y1],
            "visibleBounds": bbox,
            "size": [side, side],
            "visiblePixels": visible_count,
            "visiblePixelsWithin12pxBoundary": boundary,
            "alphaExtrema": [int(square[:, :, 3].min()), int(square[:, :, 3].max())],
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        })

    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "source": sheet.name,
        "sourceSha256": hashlib.sha256(sheet.read_bytes()).hexdigest(),
        "sourceSize": [original_width, original_height],
        "normalizationCrop": [crop_left, crop_top, crop_left + crop_width,
                             crop_top + crop_height],
        "layout": f"{rows}x{columns}",
        "edgeInsetPx": inset_px,
        "extraction": "proportional cell crops with edge inset, centered square padding, alpha preserved",
        "items": records,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--rows", type=int, default=4)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--inset", type=int, default=0,
                        help="crop this many source pixels inward from each cell edge")
    parser.add_argument("--names", nargs="+",
                        help="output stems in row-major order; defaults to the built-in 4x4 names")
    args = parser.parse_args()
    names = tuple(args.names) if args.names else NAMES
    if args.rows * args.columns != len(names):
        parser.error(f"the selected {args.rows}x{args.columns} layout needs {args.rows * args.columns} names")
    records = extract(args.sheet, args.output, args.manifest, names,
                      args.rows, args.columns, args.inset)
    print(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
