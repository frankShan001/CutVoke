"""Crop the generated 4x4 educational sticker atlas into transparent PNG assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ATLAS = ROOT / "docs/assets/education-science-sticker-atlas-20260925-v2.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_REPORT = ROOT / "docs/assets/education-science-sticker-atlas-20260925-v2.extraction.json"
CELL_COUNT = 4
OUTPUT_SIZE = 320
ART_SIZE = 256
SUBCATEGORY = "学习科普"

STICKERS = (
    ("learn_open_book", "打开的书", ("学习", "阅读", "知识")),
    ("learn_book_stack", "书本叠放", ("学习", "书籍", "阅读")),
    ("learn_stationery_cup", "文具笔筒", ("学习", "文具", "笔筒")),
    ("learn_pencil", "木质铅笔", ("学习", "铅笔", "书写")),
    ("learn_globe", "地球仪", ("科普", "地理", "地球")),
    ("learn_microscope", "显微镜", ("科普", "实验", "观察")),
    ("learn_flask", "蓝色实验烧瓶", ("科普", "化学", "实验")),
    ("learn_telescope", "天文望远镜", ("科普", "天文", "观测")),
    ("learn_dna", "DNA 双螺旋", ("科普", "生物", "基因")),
    ("learn_atom", "原子模型", ("科普", "物理", "原子")),
    ("learn_brain", "脑部模型", ("科普", "大脑", "神经")),
    ("learn_calculator", "计算器", ("学习", "数学", "计算")),
    ("learn_geometry_set", "圆规与直尺", ("学习", "几何", "绘图")),
    ("learn_graduation_cap", "学士帽", ("学习", "毕业", "教育")),
    ("learn_idea_bulb", "发光灯泡", ("学习", "灵感", "点子")),
    ("learn_robot", "阅读机器人", ("科普", "机器人", "阅读")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _remove_small_islands(image: Image.Image, min_pixels: int = 32,
                          keep_only_largest: bool = False) -> tuple[Image.Image, int]:
    """Remove detached specks while preserving intentional nearby icon details."""
    pixels = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    alpha = pixels[:, :, 3]
    height, width = alpha.shape
    foreground = alpha.reshape(-1) >= 8
    visited = np.zeros(foreground.shape, dtype=np.bool_)
    flat = pixels.reshape(-1, 4)
    components: list[tuple[list[int], tuple[int, int, int, int]]] = []

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
        indexes = np.asarray(component, dtype=np.int64)
        ys, xs = np.divmod(indexes, width)
        component_bounds = (int(xs.min()), int(ys.min()),
                            int(xs.max()) + 1, int(ys.max()) + 1)
        components.append((component, component_bounds))

    if not components:
        return Image.fromarray(pixels, "RGBA"), 0
    primary, _ = max(components, key=lambda value: len(value[0]))
    primary_size = len(primary)
    primary_mask = np.zeros((height, width), dtype=np.uint8)
    primary_mask.reshape(-1)[np.asarray(primary, dtype=np.int64)] = 255
    nearby_primary = np.asarray(
        Image.fromarray(primary_mask, "L").filter(ImageFilter.MaxFilter(97))
    ) > 0
    removed = 0
    for component, bounds in components:
        component_indexes = np.asarray(component, dtype=np.int64)
        small_remote_component = (len(component) < primary_size * 0.05
                                  and not bool(nearby_primary.reshape(-1)[component_indexes].any()))
        discard = (len(component) < min_pixels or small_remote_component
                   or (keep_only_largest and component is not primary))
        if discard:
            flat[component_indexes] = 0
            removed += len(component)

    return Image.fromarray(pixels, "RGBA"), removed


def _contact_sheet(paths: list[Path], target: Path) -> None:
    tile = 256
    sheet = Image.new("RGB", (tile * CELL_COUNT, tile * CELL_COUNT), (34, 38, 46))
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(paths):
        row, col = divmod(index, CELL_COUNT)
        x, y = col * tile, row * tile
        background = Image.new("RGBA", (tile, tile), (246, 247, 249, 255))
        checker = ImageDraw.Draw(background)
        block = 32
        for cy in range(0, tile, block):
            for cx in range(0, tile, block):
                if (cx // block + cy // block) % 2:
                    checker.rectangle((cx, cy, cx + block - 1, cy + block - 1),
                                      fill=(222, 225, 230, 255))
        with Image.open(path) as source:
            artwork = source.convert("RGBA")
            artwork.thumbnail((tile - 28, tile - 40), Image.Resampling.LANCZOS)
            background.alpha_composite(artwork, ((tile - artwork.width) // 2,
                                                 (tile - artwork.height) // 2 - 4))
        sheet.paste(background.convert("RGB"), (x, y))
        draw.rectangle((x + 8, y + 8, x + 40, y + 32), fill=(25, 30, 38))
        draw.text((x + 14, y + 11), f"{index + 1:02d}", fill=(255, 255, 255))
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
        # These generated single-object cells include detached edge fragments;
        # their intended components overlap and remain connected in the art.
        clean, removed_island_pixels = _remove_small_islands(
            cell, keep_only_largest=index in {2, 3, 8})
        pixels = np.asarray(clean, dtype=np.uint8).copy()
        pixels[pixels[:, :, 3] < 8] = (0, 0, 0, 0)
        visible = pixels[:, :, 3] >= 16
        visible_count = int(visible.sum())
        if visible_count < 1000:
            raise ValueError(f"{stem} is nearly empty ({visible_count} visible pixels)")
        ys_raw, xs_raw = np.where(visible)
        bounds = (int(xs_raw.min()), int(ys_raw.min()),
                  int(xs_raw.max()) + 1, int(ys_raw.max()) + 1)
        # Crop around the visible artwork, with an inner safety margin. Some
        # generated icons use nearly the full cell, so a raw full-cell resize
        # would make them much larger than compact icons in the same library.
        art_width, art_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        side = min(min(cell.size), max(art_width, art_height) + 2 * max(
            12, round(max(art_width, art_height) * 0.06)))
        center_x, center_y = (bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2
        left = min(max(0, center_x - side // 2), cell.width - side)
        top = min(max(0, center_y - side // 2), cell.height - side)
        crop_bounds = (left, top, left + side, top + side)
        clean = clean.crop(crop_bounds)
        pixels = np.asarray(clean, dtype=np.uint8).copy()
        pixels[pixels[:, :, 3] < 8] = (0, 0, 0, 0)
        clean = Image.fromarray(pixels, "RGBA")

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
            "subcategory": SUBCATEGORY,
            "keywords": list(keywords),
            "row": row,
            "column": column,
            "sourceCell": [x0, y0, x1, y1],
            "artBoundsInCell": list(bounds),
            "normalizationCrop": [x0 + left, y0 + top, x0 + left + side, y0 + top + side],
            "size": [OUTPUT_SIZE, OUTPUT_SIZE],
            "alphaBounds": [int(xs.min()), int(ys.min()), int(xs.max()) + 1,
                            int(ys.max()) + 1],
            "visiblePixels": int((alpha >= 16).sum()),
            "removedIsolatedPixels": removed_island_pixels,
            "alphaExtrema": [int(alpha.min()), int(alpha.max())],
            "sha256": _sha(target),
        })
    contact = report.with_name("education-science-sticker-contact-sheet-20260925-v2.png")
    _contact_sheet(targets, contact)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "schemaVersion": 1,
        "generator": "OpenAI ImageGen",
        "source": atlas.name,
        "sourceSha256": _sha(atlas),
        "sourceSize": [width, height],
        "layout": "4x4",
        "extraction": "proportional cell crop, center square normalization, remove detached alpha islands below 32 pixels and small remote specks; keep only the connected primary artwork in single-object pencil, stationery and DNA cells; resize to 256px with 32px transparent padding",
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
