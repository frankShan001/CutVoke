"""Extract the 4x3 generated decor sticker sheet into transparent PNG assets.

Keep the source sheet in docs/assets so the batch can be reproduced.
This script is a deterministic crop; it does not generate or repaint artwork.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image


NAMES = (
    "decor_neon_stroke", "decor_paper_tape", "decor_sparkles", "decor_spiral",
    "decor_impact_burst", "decor_paint_splash", "decor_wavy_ribbon", "decor_confetti",
    "decor_torn_corner", "decor_rainbow", "decor_scribble_ring", "decor_three_dots",
)
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/decor-sticker-sheet-20260924.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"


def extract(sheet: Path, output: Path) -> list[dict]:
    source = Image.open(sheet).convert("RGBA")
    width, height = source.size
    if width % 4 or height % 3 or width // 4 != height // 3:
        raise ValueError(f"expected twelve square cells in a 4x3 sheet, got {source.size}")
    if source.getchannel("A").getextrema() != (0, 255):
        raise ValueError("source must have genuine transparent and opaque pixels")
    cell = width // 4
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for index, stem in enumerate(NAMES):
        row, col = divmod(index, 4)
        artwork = source.crop((col * cell, row * cell, (col + 1) * cell, (row + 1) * cell))
        pixels = np.asarray(artwork, dtype=np.uint8).copy()
        # Remove nearly invisible generator specks and zero hidden RGB to avoid
        # coloured fringes when FFmpeg scales the transparent asset.
        pixels[pixels[:, :, 3] < 8] = (0, 0, 0, 0)
        visible = pixels[:, :, 3] >= 16
        if int(visible.sum()) < 500:
            raise ValueError(f"{stem} is almost empty")
        boundary = (int(visible[:4, :].sum()) + int(visible[-4:, :].sum()) +
                    int(visible[:, :4].sum()) + int(visible[:, -4:].sum()))
        if boundary > 8:
            raise ValueError(f"{stem} touches a crop boundary")
        if boundary:
            pixels[:4, :] = pixels[-4:, :] = pixels[:, :4] = pixels[:, -4:] = 0
        target = output / f"{stem}.png"
        if target.exists():
            raise FileExistsError(f"will not overwrite {target}")
        Image.fromarray(pixels, "RGBA").save(target, optimize=True)
        records.append({
            "stem": stem, "row": row, "column": col,
            "sourceCell": [col * cell, row * cell, (col + 1) * cell, (row + 1) * cell],
            "size": [cell, cell], "visiblePixels": int(visible.sum()),
            "alphaExtrema": [int(pixels[:, :, 3].min()), int(pixels[:, :, 3].max())],
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        })
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    records = extract(args.sheet, args.output)
    print(json.dumps({"sheetSha256": hashlib.sha256(args.sheet.read_bytes()).hexdigest(),
                      "assets": records}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
