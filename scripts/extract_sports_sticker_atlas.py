"""Crop the generated 4x4 sports sticker atlas into clean transparent assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ATLAS = ROOT / "docs/assets/sports-sticker-atlas-20260925.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_REPORT = ROOT / "docs/assets/sports-sticker-atlas-20260925.extraction.json"
CELL_COUNT = 4
OUTPUT_SIZE = 320
ART_SIZE = 256

STICKERS = (
    ("sports_basketball", "篮球", ("运动", "篮球", "球类")),
    ("sports_soccer_ball", "足球", ("运动", "足球", "球类")),
    ("sports_volleyball", "排球", ("运动", "排球", "球类")),
    ("sports_tennis_racket", "网球拍与网球", ("运动", "网球", "球拍")),
    ("sports_badminton", "羽毛球拍与羽毛球", ("运动", "羽毛球", "球拍")),
    ("sports_baseball", "棒球与球棒手套", ("运动", "棒球", "球棒")),
    ("sports_golf", "高尔夫球杆与球", ("运动", "高尔夫", "球杆")),
    ("sports_boxing_gloves", "拳击手套", ("运动", "拳击", "手套")),
    ("sports_trophy", "冠军奖杯", ("运动", "奖杯", "冠军")),
    ("sports_medal", "运动奖牌", ("运动", "奖牌", "荣誉")),
    ("sports_running_shoe", "跑鞋", ("运动", "跑步", "健身")),
    ("sports_dumbbell", "哑铃", ("运动", "力量训练", "健身")),
    ("sports_skateboard", "滑板", ("运动", "滑板", "极限运动")),
    ("sports_surfboard", "冲浪板与浪花", ("运动", "冲浪", "海浪")),
    ("sports_whistle", "裁判哨子", ("运动", "裁判", "哨子")),
    ("sports_stopwatch", "运动秒表", ("运动", "秒表", "计时")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _remove_small_islands(image: Image.Image, min_pixels: int = 32) -> tuple[Image.Image, int]:
    """Remove only detached alpha islands smaller than min_pixels."""
    pixels = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    alpha = pixels[:, :, 3]
    height, width = alpha.shape
    foreground = alpha.reshape(-1) >= 8
    visited = np.zeros(foreground.shape, dtype=np.bool_)
    removed = 0

    for seed_raw in np.flatnonzero(foreground):
        seed = int(seed_raw)
        if visited[seed]:
            continue
        visited[seed] = True
        stack = [seed]
        component: list[int] = []
        while stack:
            current = stack.pop()
            component.append(current)
            y, x = divmod(current, width)
            for dy in (-1, 0, 1):
                ny = y + dy
                if ny < 0 or ny >= height:
                    continue
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx = x + dx
                    if nx < 0 or nx >= width:
                        continue
                    neighbor = ny * width + nx
                    if foreground[neighbor] and not visited[neighbor]:
                        visited[neighbor] = True
                        stack.append(neighbor)
        if len(component) < min_pixels:
            indexes = np.asarray(component, dtype=np.int64)
            flat = pixels.reshape(-1, 4)
            flat[indexes] = 0
            removed += len(component)

    return Image.fromarray(pixels, "RGBA"), removed


def _contact_sheet(paths: list[Path], target: Path) -> None:
    tile = 180
    sheet = Image.new("RGB", (tile * CELL_COUNT, tile * CELL_COUNT), (34, 38, 46))
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(paths):
        row, col = divmod(index, CELL_COUNT)
        x, y = col * tile, row * tile
        background = Image.new("RGBA", (tile, tile), (246, 247, 249, 255))
        checker = ImageDraw.Draw(background)
        block = 18
        for cy in range(0, tile, block):
            for cx in range(0, tile, block):
                if (cx // block + cy // block) % 2:
                    checker.rectangle((cx, cy, cx + block - 1, cy + block - 1),
                                      fill=(222, 225, 230, 255))
        with Image.open(path) as source:
            artwork = source.convert("RGBA")
            artwork.thumbnail((tile - 24, tile - 36), Image.Resampling.LANCZOS)
            background.alpha_composite(artwork, ((tile - artwork.width) // 2,
                                                 (tile - artwork.height) // 2 - 4))
        sheet.paste(background.convert("RGB"), (x, y))
        draw.rectangle((x + 6, y + 6, x + 31, y + 25), fill=(25, 30, 38))
        draw.text((x + 12, y + 9), f"{index + 1:02d}", fill=(255, 255, 255))
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, optimize=True)


def extract(atlas: Path, output: Path, report: Path) -> list[dict]:
    source = Image.open(atlas).convert("RGBA")
    width, height = source.size
    if abs(width - height) > 1:
        raise ValueError(f"expected a square atlas, got {source.size}")
    if source.getchannel("A").getextrema() != (0, 255):
        raise ValueError("atlas must contain genuine transparent and opaque pixels")
    if len(STICKERS) != CELL_COUNT ** 2:
        raise ValueError("sticker list must match the 4x4 atlas")

    targets = [output / f"{stem}.png" for stem, _, _ in STICKERS]
    if any(target.exists() for target in targets):
        raise FileExistsError("refusing to overwrite: " + ", ".join(
            str(target) for target in targets if target.exists()))
    if report.exists():
        raise FileExistsError(f"refusing to overwrite {report}")

    output.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    for index, (stem, name, keywords) in enumerate(STICKERS):
        row, column = divmod(index, CELL_COUNT)
        x0, x1 = round(column * width / CELL_COUNT), round((column + 1) * width / CELL_COUNT)
        y0, y1 = round(row * height / CELL_COUNT), round((row + 1) * height / CELL_COUNT)
        cell = source.crop((x0, y0, x1, y1))
        # Atlas cells differ by one pixel; center-crop that rounding difference,
        # then remove only disconnected specks before normalizing to a shared size.
        side = min(cell.size)
        left, top = (cell.width - side) // 2, (cell.height - side) // 2
        cell = cell.crop((left, top, left + side, top + side))
        clean, removed_island_pixels = _remove_small_islands(cell)
        pixels = np.asarray(clean, dtype=np.uint8).copy()
        pixels[pixels[:, :, 3] < 8] = (0, 0, 0, 0)
        visible = pixels[:, :, 3] >= 16
        visible_count = int(visible.sum())
        if visible_count < 1000:
            raise ValueError(f"{stem} is nearly empty ({visible_count} visible pixels)")
        boundary = int(visible[:8, :].sum() + visible[-8:, :].sum()
                       + visible[:, :8].sum() + visible[:, -8:].sum())
        if boundary > max(12, round(visible_count * 0.002)):
            raise ValueError(f"{stem} artwork approaches its cell edge ({boundary} pixels)")

        art = clean.resize((ART_SIZE, ART_SIZE), Image.Resampling.LANCZOS)
        prepared = Image.new("RGBA", (OUTPUT_SIZE, OUTPUT_SIZE), (0, 0, 0, 0))
        prepared.alpha_composite(art, ((OUTPUT_SIZE - ART_SIZE) // 2,
                                       (OUTPUT_SIZE - ART_SIZE) // 2))
        target = output / f"{stem}.png"
        prepared.save(target, optimize=True)
        alpha = np.asarray(prepared.getchannel("A"), dtype=np.uint8)
        ys, xs = np.where(alpha >= 16)
        items.append({
            "index": index + 1,
            "stem": stem,
            "name": name,
            "subcategory": "运动",
            "keywords": list(keywords),
            "row": row,
            "column": column,
            "sourceCell": [x0, y0, x1, y1],
            "normalizationCrop": [x0 + left, y0 + top, x0 + left + side, y0 + top + side],
            "size": [OUTPUT_SIZE, OUTPUT_SIZE],
            "alphaBounds": [int(xs.min()), int(ys.min()), int(xs.max()) + 1,
                            int(ys.max()) + 1],
            "visiblePixels": int((alpha >= 16).sum()),
            "removedIsolatedPixels": removed_island_pixels,
            "alphaExtrema": [int(alpha.min()), int(alpha.max())],
            "sha256": _sha(target),
        })
    contact = report.with_name("sports-sticker-contact-sheet-20260925.png")
    _contact_sheet(targets, contact)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "schemaVersion": 1,
        "generator": "OpenAI ImageGen",
        "source": atlas.name,
        "sourceSha256": _sha(atlas),
        "sourceSize": [width, height],
        "layout": "4x4",
        "extraction": "proportional cell crop, center square normalization, remove detached alpha islands below 32 pixels, resize to 256px with 32px transparent padding",
        "contactSheet": contact.name,
        "items": items,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas", type=Path, default=DEFAULT_ATLAS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    items = extract(args.atlas, args.output, args.report)
    print(json.dumps({"extracted": len(items), "report": str(args.report),
                      "removedIsolatedPixels": sum(item["removedIsolatedPixels"]
                                                    for item in items)},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
