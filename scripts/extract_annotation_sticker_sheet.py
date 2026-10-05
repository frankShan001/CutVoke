"""Crop a generated 4x4 annotation atlas into independent transparent PNG stickers."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/annotation-doodle-sticker-sheet-20260925.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_REPORT = ROOT / "docs/assets/annotation-doodle-sticker-sheet-20260925.extraction.json"
DEFAULT_CONTACT = ROOT / "docs/assets/annotation-doodle-sticker-contact-sheet-20260925.png"
DEFAULT_CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
DEFAULT_PREVIEW_OUTPUT = ROOT / "src/cutvoke/assets/sticker_previews"
GRID = 4
OUTPUT_SIZE = 320
EDGE_GUTTER = 8

ITEMS = (
    ("marker_doodle_circle", "手绘圈选", ("标注", "圈选", "重点", "手绘")),
    ("marker_doodle_underline", "弧线强调", ("标注", "下划线", "重点", "手绘")),
    ("marker_doodle_double_arrow", "双向箭头", ("标注", "方向", "比较", "手绘")),
    ("marker_doodle_curl_arrow", "弯折箭头", ("标注", "方向", "指向", "手绘")),
    ("marker_doodle_burst", "漫画强调爆炸", ("标注", "漫画", "强调", "手绘")),
    ("marker_doodle_sparkle_cluster", "星芒组合", ("标注", "星光", "装饰", "手绘")),
    ("marker_doodle_dotted_orbit", "虚线环绕星芒", ("标注", "虚线", "星光", "手绘")),
    ("marker_doodle_motion_rays", "动感短线", ("标注", "动感", "强调", "手绘")),
    ("marker_doodle_corner_brackets", "四角取景框", ("标注", "取景框", "定位", "手绘")),
    ("marker_doodle_round_frame", "圆角重点框", ("标注", "边框", "重点", "手绘")),
    ("marker_doodle_dashed_oval", "虚线椭圆圈", ("标注", "圈选", "虚线", "手绘")),
    ("marker_doodle_zigzag", "折线强调", ("标注", "折线", "重点", "手绘")),
    ("marker_doodle_location_pin", "空白定位图钉", ("标注", "位置", "地图", "手绘")),
    ("marker_doodle_sunburst", "放射光芒", ("标注", "光芒", "强调", "手绘")),
    ("marker_doodle_ribbon_flag", "空白飘带旗标", ("标注", "旗标", "标签", "手绘")),
    ("marker_doodle_confetti", "几何彩纸礼花", ("标注", "彩纸", "庆祝", "手绘")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def register_assets(catalog_path: Path = DEFAULT_CATALOG) -> int:
    """Register extracted assets as unreviewed candidates in the built-in library."""
    document = json.loads(catalog_path.read_text(encoding="utf-8"))
    stickers = document.get("stickers")
    if not isinstance(stickers, list):
        raise ValueError("built-in sticker catalog has no sticker list")
    existing = {item.get("stem"): item for item in stickers}
    additions = []
    for stem, name, keywords in ITEMS:
        path = DEFAULT_OUTPUT / f"{stem}.png"
        if not path.is_file():
            raise FileNotFoundError(f"extracted sticker is missing: {path}")
        entry = {
            "stem": stem,
            "name": name,
            "subcategory": "标记",
            "keywords": list(keywords),
            "version": "1.0.0",
            "license": "MIT",
            "source": "AI生成图集 annotation-doodle-sticker-sheet-20260925.png；CutVoke 原创素材，无第三方图像来源",
        }
        if stem in existing:
            if existing[stem] != entry:
                raise ValueError(f"catalog entry conflicts with generated sticker: {stem}")
        else:
            additions.append(entry)
    if additions:
        stickers.extend(additions)
        catalog_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    return len(additions)


def prepare_pending_evidence() -> int:
    """Write explicit pending records so candidate assets can ship with provenance."""
    written = 0
    for stem, _name, _keywords in ITEMS:
        source_path = DEFAULT_OUTPUT / f"{stem}.png"
        preview_path = DEFAULT_PREVIEW_OUTPUT / f"{stem}.mp4"
        audit_path = DEFAULT_PREVIEW_OUTPUT / f"{stem}.audit.json"
        for path in (source_path, preview_path, audit_path):
            if not path.is_file():
                raise FileNotFoundError(f"sticker review evidence is missing: {path}")
        record = {
            "schemaVersion": 1,
            "decision": "pending",
            "stickerId": f"cutvoke.sticker.{stem}",
            "version": "1.0.0",
            "effectId": "",
            "params": {},
            "reviewer": "",
            "reviewedAt": "",
            "observation": "",
            "method": "Awaiting visual review of the transparent artwork and composited preview",
            "sourceSha256": _sha(source_path),
            "previewSha256": _sha(preview_path),
            "auditSha256": _sha(audit_path),
        }
        target = DEFAULT_PREVIEW_OUTPUT / f"{stem}.visual.json"
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if existing != record:
                raise FileExistsError(f"refusing to replace existing review record: {target}")
        else:
            target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
            written += 1
    return written


def _contact_sheet(paths: list[Path], output: Path) -> None:
    tile = 180
    sheet = Image.new("RGB", (tile * GRID, tile * GRID), (31, 35, 43))
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(paths):
        row, column = divmod(index, GRID)
        x, y = column * tile, row * tile
        background = Image.new("RGBA", (tile, tile), (240, 241, 244, 255))
        checker = ImageDraw.Draw(background)
        block = 18
        for cy in range(0, tile, block):
            for cx in range(0, tile, block):
                if (cx // block + cy // block) % 2:
                    checker.rectangle((cx, cy, cx + block - 1, cy + block - 1),
                                      fill=(216, 220, 226, 255))
        with Image.open(path) as source:
            artwork = source.convert("RGBA")
            artwork.thumbnail((tile - 28, tile - 28), Image.Resampling.LANCZOS)
            background.alpha_composite(artwork, ((tile - artwork.width) // 2,
                                                  (tile - artwork.height) // 2))
        sheet.paste(background.convert("RGB"), (x, y))
        draw.rectangle((x + 6, y + 6, x + 31, y + 25), fill=(22, 27, 35))
        draw.text((x + 12, y + 9), f"{index + 1:02d}", fill=(255, 255, 255))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, optimize=True)


def extract(sheet: Path, output: Path, report: Path,
            contact_sheet: Path = DEFAULT_CONTACT) -> list[dict]:
    source = Image.open(sheet).convert("RGBA")
    width, height = source.size
    if abs(width - height) > 1:
        raise ValueError(f"expected a square atlas, got {source.size}")
    if source.getchannel("A").getextrema() != (0, 255):
        raise ValueError("source atlas must contain genuine transparent and opaque pixels")
    if len(ITEMS) != GRID * GRID or len({item[0] for item in ITEMS}) != len(ITEMS):
        raise ValueError("atlas metadata must contain exactly 16 unique items")

    targets = [output / f"{stem}.png" for stem, *_ in ITEMS]
    collisions = [path for path in targets if path.exists()]
    if collisions:
        raise FileExistsError("refusing to overwrite: " + ", ".join(map(str, collisions)))
    if report.exists() or contact_sheet.exists():
        raise FileExistsError("refusing to overwrite an existing extraction report or contact sheet")

    output.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
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
        gutter = min(EDGE_GUTTER, pixels.shape[0] // 16, pixels.shape[1] // 16)
        edge_pixels = int(
            visible[:gutter, :].sum() + visible[-gutter:, :].sum()
            + visible[:, :gutter].sum() + visible[:, -gutter:].sum())
        if edge_pixels > max(100, int(visible_count * 0.01)):
            raise ValueError(f"{stem} artwork crosses the crop boundary ({edge_pixels} pixels)")
        pixels[:gutter, :, :] = pixels[-gutter:, :, :] = 0
        pixels[:, :gutter, :] = pixels[:, -gutter:, :] = 0

        artwork = Image.fromarray(pixels, "RGBA")
        square_side = max(artwork.size)
        square = Image.new("RGBA", (square_side, square_side), (0, 0, 0, 0))
        square.alpha_composite(artwork, ((square_side - artwork.width) // 2,
                                         (square_side - artwork.height) // 2))
        prepared = square.resize((OUTPUT_SIZE, OUTPUT_SIZE), Image.Resampling.LANCZOS)
        target = output / f"{stem}.png"
        prepared.save(target, optimize=True)
        alpha = np.asarray(prepared.getchannel("A"), dtype=np.uint8)
        visible_bounds = Image.fromarray((alpha >= 16).astype(np.uint8) * 255).getbbox()
        records.append({
            "index": index + 1,
            "stem": stem,
            "name": name,
            "subcategory": "标记",
            "keywords": list(keywords),
            "row": row,
            "column": column,
            "sourceCell": [x0, y0, x1, y1],
            "outputSize": [OUTPUT_SIZE, OUTPUT_SIZE],
            "visiblePixelsInSourceCell": visible_count,
            "sourceBoundaryPixelsWithin8px": edge_pixels,
            "visibleBounds": list(visible_bounds) if visible_bounds else None,
            "alphaExtrema": list(prepared.getchannel("A").getextrema()),
            "sha256": _sha(target),
        })

    _contact_sheet(targets, contact_sheet)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "schemaVersion": 1,
        "source": sheet.name,
        "sourceSha256": _sha(sheet),
        "sourceSize": [width, height],
        "layout": "4x4",
        "extraction": "proportional transparent RGBA cell crops, 8px boundary safety gutter, normalized to 320px square PNGs",
        "contactSheet": contact_sheet.name,
        "items": records,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--contact-sheet", type=Path, default=DEFAULT_CONTACT)
    parser.add_argument("--register-only", action="store_true",
                        help="register existing extracted PNGs without cropping again")
    parser.add_argument("--prepare-pending-evidence", action="store_true",
                        help="write hash-pinned review records after preview and audit generation")
    args = parser.parse_args()
    if args.prepare_pending_evidence:
        written = prepare_pending_evidence()
        print(json.dumps({"pendingReviewRecordsWritten": written,
                          "directory": str(DEFAULT_PREVIEW_OUTPUT)},
                         ensure_ascii=False, indent=2))
    elif args.register_only:
        added = register_assets()
        print(json.dumps({"registered": added, "catalog": str(DEFAULT_CATALOG)},
                         ensure_ascii=False, indent=2))
    else:
        records = extract(args.sheet, args.output, args.report, args.contact_sheet)
        added = register_assets()
        print(json.dumps({"extracted": len(records), "registered": added,
                          "report": str(args.report),
                          "contactSheet": str(args.contact_sheet)},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
