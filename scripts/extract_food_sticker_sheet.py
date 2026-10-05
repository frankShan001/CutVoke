"""Extract the generated 4x4 transparent food sticker atlas into PNG assets.

The atlas stays under docs/assets as reproducible source material. Cropping is
deterministic and does not repaint or synthesize the generated artwork.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image


NAMES = (
    "food_bubble_tea", "food_strawberry_cake", "food_croissant", "food_matcha_latte",
    "food_ramen", "food_onigiri", "food_dumplings", "food_sushi",
    "food_taiyaki", "food_soft_serve", "food_egg_tart", "food_tanghulu",
    "food_peach", "food_lemon_tea", "food_burger_fries", "food_donut",
)
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/food-drink-sticker-sheet-20260925-v2.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
GRID_SIZE = 4
# The generated green-striped straw sits beside the adjacent soft-serve cone.
# Its rounded cap starts below this source row; trim the one-pixel white outline
# contact while keeping the complete straw cap in the extracted sticker.
CROP_TOP_OVERRIDES = {"food_lemon_tea": 928}
# The neighboring soft-serve art crosses the bottom cell edge; retain its tip
# through source row 926 and omit the first pixels of the next sticker.
CROP_BOTTOM_OVERRIDES = {"food_soft_serve": 927}


def _connected_components(mask: np.ndarray) -> list[dict]:
    """Label alpha-connected artwork with scanline runs (no OpenCV dependency)."""
    parents: list[int] = []
    ranks: list[int] = []
    rows: list[list[tuple[int, int, int]]] = []

    def find(node: int) -> int:
        while parents[node] != node:
            parents[node] = parents[parents[node]]
            node = parents[node]
        return node

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a == b:
            return
        if ranks[a] < ranks[b]:
            a, b = b, a
        parents[b] = a
        if ranks[a] == ranks[b]:
            ranks[a] += 1

    previous: list[tuple[int, int, int]] = []
    for y in range(mask.shape[0]):
        active = np.flatnonzero(mask[y])
        if not active.size:
            rows.append([])
            previous = []
            continue
        split_points = np.flatnonzero(np.diff(active) > 1) + 1
        runs: list[tuple[int, int, int]] = []
        groups = np.split(active, split_points)
        for group in groups:
            start, end = int(group[0]), int(group[-1]) + 1
            node = len(parents)
            parents.append(node)
            ranks.append(0)
            runs.append((start, end, node))
        old_index = 0
        for start, end, node in runs:
            while old_index < len(previous) and previous[old_index][1] < start:
                old_index += 1
            scan = old_index
            while scan < len(previous) and previous[scan][0] <= end:
                union(node, previous[scan][2])
                scan += 1
        rows.append(runs)
        previous = runs

    found: dict[int, dict] = {}
    for y, runs in enumerate(rows):
        for start, end, node in runs:
            root = find(node)
            record = found.setdefault(root, {
                "left": start, "top": y, "right": end, "bottom": y + 1,
                "area": 0, "sumX": 0.0, "sumY": 0.0,
            })
            count = end - start
            record["left"] = min(record["left"], start)
            record["top"] = min(record["top"], y)
            record["right"] = max(record["right"], end)
            record["bottom"] = max(record["bottom"], y + 1)
            record["area"] += count
            record["sumX"] += (start + end - 1) * count / 2
            record["sumY"] += y * count
    for record in found.values():
        record["centerX"] = record.pop("sumX") / record["area"]
        record["centerY"] = record.pop("sumY") / record["area"]
    return list(found.values())


def extract(sheet: Path, output: Path) -> list[dict]:
    source = Image.open(sheet).convert("RGBA")
    width, height = source.size
    if abs(width - height) > 1 or min(width, height) < GRID_SIZE * 128:
        raise ValueError(f"expected a square 4x4 atlas with useful cell resolution, got {source.size}")
    if source.getchannel("A").getextrema() != (0, 255):
        raise ValueError("source must have genuine transparent and opaque pixels")

    cell_width, cell_height = width / GRID_SIZE, height / GRID_SIZE
    groups: list[list[dict]] = [[] for _ in NAMES]
    for component in _connected_components(np.asarray(source.getchannel("A")) >= 16):
        if component["area"] < 8:
            continue
        col = min(GRID_SIZE - 1, max(0, int(component["centerX"] / cell_width)))
        row = min(GRID_SIZE - 1, max(0, int(component["centerY"] / cell_height)))
        groups[row * GRID_SIZE + col].append(component)

    cell_size = math.ceil(max(cell_width, cell_height))
    prepared: list[tuple[str, int, int, Image.Image, int,
                         tuple[int, int], tuple[int, int, int, int]]] = []
    for index, stem in enumerate(NAMES):
        row, col = divmod(index, GRID_SIZE)
        components = groups[index]
        if not components:
            raise ValueError(f"{stem} has no connected artwork near its atlas cell")
        main = max(components, key=lambda item: item["area"])
        expected_x = (col + 0.5) * cell_width
        expected_y = (row + 0.5) * cell_height
        center_offset = math.hypot((main["centerX"] - expected_x) / cell_width,
                                   (main["centerY"] - expected_y) / cell_height)
        if main["area"] < 500 or center_offset > 0.48:
            raise ValueError(f"{stem} main artwork is missing or misplaced: {main}")
        related = [item for item in components
                   if math.hypot((item["centerX"] - main["centerX"]) / cell_width,
                                 (item["centerY"] - main["centerY"]) / cell_height) <= 0.68]
        left = max(0, min(item["left"] for item in related) - 7)
        top = max(0, min(item["top"] for item in related) - 7)
        right = min(width, max(item["right"] for item in related) + 7)
        bottom = min(height, max(item["bottom"] for item in related) + 7)
        top = max(top, CROP_TOP_OVERRIDES.get(stem, top))
        bottom = min(bottom, CROP_BOTTOM_OVERRIDES.get(stem, bottom))
        artwork = source.crop((left, top, right, bottom))
        scale = min(1.0, (cell_size - 12) / max(artwork.width, artwork.height))
        if scale < 1.0:
            artwork = artwork.resize((round(artwork.width * scale),
                                      round(artwork.height * scale)), Image.Resampling.LANCZOS)
        cell = Image.new("RGBA", (cell_size, cell_size), (0, 0, 0, 0))
        cell.alpha_composite(artwork, ((cell_size - artwork.width) // 2,
                                      (cell_size - artwork.height) // 2))
        pixels = np.asarray(cell, dtype=np.uint8).copy()
        pixels[pixels[:, :, 3] < 8] = (0, 0, 0, 0)
        visible = pixels[:, :, 3] >= 16
        visible_count = int(visible.sum())
        if visible_count < 500:
            raise ValueError(f"{stem} is almost empty")
        edge_count = [
            int(visible[:4, :].sum()), int(visible[-4:, :].sum()),
            int(visible[:, :4].sum()), int(visible[:, -4:].sum()),
        ]
        if sum(edge_count) > 0:
            raise ValueError(f"{stem} touches output boundary after padding: {edge_count}")
        prepared.append((stem, row, col, Image.fromarray(pixels, "RGBA"),
                         visible_count, (right - left, bottom - top),
                         (left, top, right, bottom)))

    output.mkdir(parents=True, exist_ok=True)
    existing = [output / f"{stem}.png" for stem in NAMES if (output / f"{stem}.png").exists()]
    if existing:
        raise FileExistsError(f"will not overwrite existing assets: {existing}")

    records = []
    for stem, row, col, artwork, visible_count, crop_size, crop_bounds in prepared:
        target = output / f"{stem}.png"
        artwork.save(target, optimize=True)
        pixels = np.asarray(artwork, dtype=np.uint8)
        records.append({
            "stem": stem, "row": row, "column": col,
            "size": [cell_size, cell_size], "cropBounds": list(crop_bounds),
            "cropSize": list(crop_size),
            "visiblePixels": visible_count,
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
