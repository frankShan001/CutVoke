"""Extract three first-party icon atlases into transparent sticker PNGs.

The retained source atlases, cell coordinates, and resulting hashes make these
assets reproducible and traceable. Existing images are backed up on an explicit
replacement run.
"""

from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
STICKER_ROOT = ROOT / "src/cutvoke/assets/stickers"
CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
BACKUP_ROOT = ROOT / "docs/assets/replaced-sticker-sources-20260925"
DEFAULT_OUTPUT = ROOT / "tmp/legacy-icon-review"
DEFAULT_REPORT = ROOT / "docs/assets/legacy-icon-atlases-20260925.extraction.json"
DEFAULT_CONTACT = ROOT / "docs/assets/legacy-icon-atlases-contact-sheet-20260925.png"
GRID = 4
OUTPUT_SIZE = 320
PADDING = 18
CROP_FRACTION = 0.94
MIN_COMPONENT_PIXELS = 80

ATLASES = (
    {
        "file": "basic-navigation-sticker-atlas-20260925.png",
        "items": (
            ("arrow_down", "向下箭头", ("方向", "向下")),
            ("arrow_left", "向左箭头", ("方向", "向左")),
            ("arrow_ne", "右上箭头", ("方向", "右上")),
            ("arrow_nw", "左上箭头", ("方向", "左上")),
            ("arrow_red", "红色箭头", ("方向", "红色")),
            ("arrow_right", "向右箭头", ("方向", "向右")),
            ("arrow_up", "向上箭头", ("方向", "向上")),
            ("cross", "叉号", ("标记", "叉号")),
            ("location", "位置标记", ("位置", "地点")),
            ("magnifier", "放大镜", ("搜索", "放大镜")),
            ("minus", "减号", ("符号", "减号")),
            ("plus", "加号", ("符号", "加号")),
            ("check_green", "绿色对勾", ("标记", "正确")),
            ("circle_green", "绿色圆点", ("标记", "绿色")),
            ("dot", "圆点", ("标记", "圆点")),
            ("shield", "盾牌", ("标记", "盾牌")),
        ),
    },
    {
        "file": "basic-symbol-sticker-atlas-20260925.png",
        "items": (
            ("bolt_orange", "橙色闪电", ("闪电", "速度")),
            ("clock", "时钟", ("时间", "时钟")),
            ("exclaim_yellow", "黄色感叹号", ("提示", "感叹号")),
            ("flame", "火焰", ("火焰", "热度")),
            ("heart", "爱心", ("爱心", "情绪")),
            ("moon", "月亮", ("月亮", "夜晚")),
            ("question", "问号", ("提示", "问号")),
            ("star", "星星", ("星星", "氛围")),
            ("star_burst", "爆闪星", ("星星", "闪光")),
            ("sun", "太阳", ("太阳", "白天")),
            ("timer", "计时器", ("时间", "计时")),
            ("warning", "警告标识", ("提示", "警告")),
            ("gift", "礼物", ("礼物", "庆祝")),
            ("music_note", "音符", ("音乐", "音符")),
            ("number_bubble", "数字气泡", ("数字", "气泡")),
            ("rounded_blue", "蓝色圆角牌", ("标记", "蓝色")),
        ),
    },
    {
        "file": "basic-label-sticker-atlas-20260925.png",
        "items": (
            ("speech_bubble", "对话气泡", ("气泡", "对话")),
            ("tag", "标签牌", ("标记", "标签")),
        ),
    },
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _remove_small_components(pixels: np.ndarray) -> tuple[np.ndarray, int, int]:
    """Remove isolated generator specks while preserving the main icon parts."""
    visible = pixels[:, :, 3] >= 24
    height, width = visible.shape
    seen = bytearray(height * width)
    removed_pixels = 0
    removed_components = 0
    for y in range(height):
        for x in range(width):
            start = y * width + x
            if not visible[y, x] or seen[start]:
                continue
            seen[start] = 1
            queue = deque([start])
            component = [start]
            while queue:
                index = queue.popleft()
                cy, cx = divmod(index, width)
                for ny in range(max(0, cy - 1), min(height, cy + 2)):
                    for nx in range(max(0, cx - 1), min(width, cx + 2)):
                        neighbor = ny * width + nx
                        if visible[ny, nx] and not seen[neighbor]:
                            seen[neighbor] = 1
                            queue.append(neighbor)
                            component.append(neighbor)
            if len(component) < MIN_COMPONENT_PIXELS:
                removed_components += 1
                removed_pixels += len(component)
                ys, xs = np.divmod(np.asarray(component), width)
                pixels[ys, xs, :] = 0
    return pixels, removed_components, removed_pixels


def _contact_sheet(paths: list[tuple[str, Path]], output: Path) -> None:
    columns, tile, label_h = 5, 180, 28
    rows = (len(paths) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * tile, rows * (tile + label_h)), (237, 239, 243))
    draw = ImageDraw.Draw(sheet)
    for index, (stem, path) in enumerate(paths):
        row, column = divmod(index, columns)
        x, y = column * tile, row * (tile + label_h)
        background = Image.new("RGBA", (tile, tile), (240, 241, 244, 255))
        checker = ImageDraw.Draw(background)
        block = 18
        for cy in range(0, tile, block):
            for cx in range(0, tile, block):
                if (cx // block + cy // block) % 2:
                    checker.rectangle((cx, cy, cx + block - 1, cy + block - 1),
                                      fill=(216, 219, 225, 255))
        with Image.open(path) as source:
            source = source.convert("RGBA")
            source.thumbnail((tile - 20, tile - 20), Image.Resampling.LANCZOS)
            background.alpha_composite(source, ((tile - source.width) // 2,
                                                (tile - source.height) // 2))
        sheet.paste(background.convert("RGB"), (x, y))
        draw.text((x + 5, y + tile + 5), stem, fill=(18, 23, 31))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, optimize=True)


def extract(output: Path, report: Path, contact: Path, *, replace_assets: bool = False,
            record_source: bool = False) -> list[dict]:
    flat_items = [(atlas, index, item) for atlas in ATLASES
                  for index, item in enumerate(atlas["items"])]
    stems = [item[2][0] for item in flat_items]
    if len(set(stems)) != len(stems):
        raise ValueError("icon atlas maps contain duplicate sticker stems")
    targets = [output / f"{stem}.png" for stem in stems]
    existing = [path for path in targets if path.exists()]
    if existing and not replace_assets:
        raise FileExistsError("refusing to overwrite without --replace-assets: " +
                              ", ".join(map(str, existing)))
    if report.exists() and output.resolve() != DEFAULT_OUTPUT.resolve() and not replace_assets:
        raise FileExistsError(f"refusing to overwrite extraction record: {report}")

    records: list[dict] = []
    contact_paths: list[tuple[str, Path]] = []
    atlas_records = []
    output.mkdir(parents=True, exist_ok=True)
    for atlas in ATLASES:
        sheet_path = ROOT / "docs/assets" / atlas["file"]
        if not sheet_path.is_file():
            raise FileNotFoundError(sheet_path)
        sheet = Image.open(sheet_path).convert("RGBA")
        width, height = sheet.size
        if abs(width / height - 1) > 0.02:
            raise ValueError(f"expected square atlas {sheet_path.name}, got {sheet.size}")
        if sheet.getchannel("A").getextrema() != (0, 255):
            raise ValueError(f"atlas has no real alpha transparency: {sheet_path.name}")
        atlas_record = {"source": sheet_path.relative_to(ROOT).as_posix(),
                        "sourceSha256": _sha(sheet_path), "sourceSize": [width, height],
                        "items": []}
        cell_w, cell_h = width / GRID, height / GRID
        for index, (stem, name, keywords) in enumerate(atlas["items"]):
            row, column = divmod(index, GRID)
            x0, x1 = round(column * cell_w), round((column + 1) * cell_w)
            y0, y1 = round(row * cell_h), round((row + 1) * cell_h)
            side = min(x1 - x0, y1 - y0)
            crop_side = round(side * CROP_FRACTION)
            left, top = x0 + (x1 - x0 - crop_side) // 2, y0 + (y1 - y0 - crop_side) // 2
            crop_box = (left, top, left + crop_side, top + crop_side)
            pixels = np.asarray(sheet.crop(crop_box), dtype=np.uint8).copy()
            pixels[pixels[:, :, 3] < 8] = 0
            pixels, removed_components, removed_pixels = _remove_small_components(pixels)
            visible = pixels[:, :, 3] >= 16
            visible_count = int(visible.sum())
            if visible_count < 400:
                raise ValueError(f"{stem} is nearly empty ({visible_count} visible pixels)")
            pixels[:3, :, :] = pixels[-3:, :, :] = 0
            pixels[:, :3, :] = pixels[:, -3:, :] = 0
            artwork = Image.fromarray(pixels, "RGBA")
            artwork.thumbnail((OUTPUT_SIZE - 2 * PADDING, OUTPUT_SIZE - 2 * PADDING),
                              Image.Resampling.LANCZOS)
            prepared = Image.new("RGBA", (OUTPUT_SIZE, OUTPUT_SIZE), (0, 0, 0, 0))
            prepared.alpha_composite(artwork, ((OUTPUT_SIZE - artwork.width) // 2,
                                               (OUTPUT_SIZE - artwork.height) // 2))
            target = output / f"{stem}.png"
            if target.exists() and replace_assets:
                backup = BACKUP_ROOT / target.name
                backup.parent.mkdir(parents=True, exist_ok=True)
                if not backup.exists():
                    shutil.copy2(target, backup)
            prepared.save(target, optimize=True)
            alpha = prepared.getchannel("A")
            bounds = alpha.point(lambda value: 255 if value >= 16 else 0).getbbox()
            record = {
                "stem": stem, "name": name, "keywords": list(keywords),
                "row": row, "column": column,
                "sourceCell": [x0, y0, x1, y1], "cropBox": list(crop_box),
                "size": [OUTPUT_SIZE, OUTPUT_SIZE],
                "visiblePixelsAfterSpeckRemoval": visible_count,
                "removedSmallComponents": removed_components,
                "removedSmallComponentPixels": removed_pixels,
                "alphaBounds": list(bounds) if bounds else None,
                "alphaExtrema": list(alpha.getextrema()),
                "sha256": _sha(target),
            }
            records.append(record)
            atlas_record["items"].append(record)
            contact_paths.append((stem, target))
        atlas_records.append(atlas_record)

    _contact_sheet(contact_paths, contact)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "schemaVersion": 1,
        "generator": "OpenAI ImageGen",
        "layout": "4x4 equal square cells; atlas 3 uses its first two cells",
        "extraction": f"centered {CROP_FRACTION:.0%} square cell crop, removes connected visible components under {MIN_COMPONENT_PIXELS}px, and centers on transparent {OUTPUT_SIZE}px PNG with {PADDING}px padding",
        "contactSheet": contact.relative_to(ROOT).as_posix(),
        "atlases": atlas_records,
        "itemCount": len(records),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if record_source:
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        by_stem = {item["stem"]: item for item in data["stickers"]}
        source_by_stem = {}
        for atlas in atlas_records:
            for record in atlas["items"]:
                source = (
                    f"OpenAI ImageGen atlas {atlas['source']} "
                    f"(SHA-256 {atlas['sourceSha256']}), cell row {record['row'] + 1} "
                    f"column {record['column'] + 1}; crop and output SHA-256 in "
                    f"{report.relative_to(ROOT).as_posix()}"
                )
                item = by_stem.get(record["stem"])
                if item is None:
                    raise ValueError(f"sticker catalog is missing {record['stem']}")
                item["version"] = "1.1.0"
                item["source"] = source
                source_by_stem[record["stem"]] = source
        for variant in data.get("motionVariants", []):
            base_source = source_by_stem.get(variant["stem"])
            if base_source:
                variant["version"] = "1.1.0"
                variant["source"] = (
                    f"CutVoke motion variant using {variant['effectId']} over "
                    f"assets/stickers/{variant['stem']}.png; base artwork provenance: "
                    f"{base_source}; animation parameters are recorded in "
                    "src/cutvoke/core/builtin_stickers.json"
                )
        CATALOG.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--contact-sheet", type=Path, default=DEFAULT_CONTACT)
    parser.add_argument("--replace-assets", action="store_true",
                        help="backup and replace existing matching PNGs")
    parser.add_argument("--record-source", action="store_true",
                        help="write atlas provenance for icons and derived variants")
    args = parser.parse_args()
    records = extract(args.output, args.report, args.contact_sheet,
                      replace_assets=args.replace_assets, record_source=args.record_source)
    print(json.dumps({"extracted": len(records), "report": str(args.report),
                      "contactSheet": str(args.contact_sheet)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
