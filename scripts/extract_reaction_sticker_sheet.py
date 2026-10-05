"""Deterministically crop a 4x4 reaction-sticker atlas into transparent PNGs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image


NAMES = (
    "reaction_joy", "reaction_surprise", "reaction_laugh", "reaction_cool",
    "reaction_thumbsup", "reaction_applause", "reaction_point_right",
    "reaction_heart_hand", "reaction_heart", "reaction_crown",
    "reaction_megaphone", "reaction_camera", "reaction_coffee",
    "reaction_headphones", "reaction_check", "reaction_idea",
)
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/reaction-sticker-sheet-20260924.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_MANIFEST = ROOT / "docs/assets/reaction-sticker-sheet-20260924.extraction.json"


def extract(sheet: Path, output: Path, manifest: Path) -> list[dict]:
    source = Image.open(sheet).convert("RGBA")
    width, height = source.size
    if abs(width - height) > 1:
        raise ValueError(f"expected a square 4x4 atlas, got {source.size}")
    alpha = np.asarray(source.getchannel("A"), dtype=np.uint8)
    if alpha.min() != 0 or alpha.max() != 255:
        raise ValueError("source must contain genuine transparent and opaque pixels")

    targets = [output / f"{name}.png" for name in NAMES]
    if any(target.exists() for target in targets):
        existing = [str(target) for target in targets if target.exists()]
        raise FileExistsError("refusing to overwrite: " + ", ".join(existing))
    if manifest.exists():
        raise FileExistsError(f"refusing to overwrite {manifest}")

    output.mkdir(parents=True, exist_ok=True)
    records = []
    for index, stem in enumerate(NAMES):
        row, col = divmod(index, 4)
        x0, x1 = round(col * width / 4), round((col + 1) * width / 4)
        y0, y1 = round(row * height / 4), round((row + 1) * height / 4)
        artwork = source.crop((x0, y0, x1, y1))
        pixels = np.asarray(artwork, dtype=np.uint8).copy()
        pixels[pixels[:, :, 3] < 8] = (0, 0, 0, 0)
        visible = pixels[:, :, 3] >= 16
        visible_count = int(visible.sum())
        if visible_count < 1000:
            raise ValueError(f"{stem} is nearly empty ({visible_count} visible pixels)")
        boundary = int(
            visible[:8, :].sum() + visible[-8:, :].sum()
            + visible[:, :8].sum() + visible[:, -8:].sum()
        )
        if boundary > max(500, int(visible_count * 0.01)):
            raise ValueError(f"{stem} artwork crosses its crop boundary ({boundary} pixels)")
        # Remove a few detached rays/specs that landed in the safety gutter.
        # Larger boundary intrusions fail above instead of silently clipping art.
        pixels[:8, :, :] = pixels[-8:, :, :] = 0
        pixels[:, :8, :] = pixels[:, -8:, :] = 0
        visible = pixels[:, :, 3] >= 16
        visible_count = int(visible.sum())
        ys, xs = np.where(visible)
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]

        # The source is 1254x1254, so alternating cells are 313/314 pixels.
        # Pad the one-pixel-short cells to uniform square dimensions.
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
            "trimmedBoundaryPixels": boundary,
            "alphaExtrema": [int(square[:, :, 3].min()), int(square[:, :, 3].max())],
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        })

    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "source": sheet.name,
        "sourceSha256": hashlib.sha256(sheet.read_bytes()).hexdigest(),
        "layout": "4x4",
        "items": records,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    rows = extract(args.sheet, args.output, args.manifest)
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
