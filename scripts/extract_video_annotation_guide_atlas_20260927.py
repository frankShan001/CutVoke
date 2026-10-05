"""Extract and register the 4x4 ImageGen video-guidance sticker atlas."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "docs/assets/video-annotation-guide-sticker-atlas-20260927.png"
REPORT = ROOT / "docs/assets/video-annotation-guide-sticker-atlas-20260927.extraction.json"
CONTACT = ROOT / "docs/assets/video-annotation-guide-sticker-atlas-20260927-contact.png"
STICKERS = ROOT / "src/cutvoke/assets/stickers"
CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
GRID = 4
OUTPUT_SIZE = 512
EDGE_GUTTER = 8
LICENSE = "Project-internal original (AI-generated); not separately licensed for redistribution"

ITEMS = (
    ("guide_pointer_arrow", "青色弧线指引箭头", ("标注", "指向", "箭头", "青色")),
    ("guide_measure_double_arrow", "珊瑚双向测量箭头", ("标注", "测量", "比较", "双向")),
    ("guide_emphasis_circle", "黄色手绘重点圈", ("标注", "圈选", "重点", "手绘")),
    ("guide_selection_box", "紫色虚线选区框", ("标注", "选区", "虚线框", "紫色")),
    ("guide_focus_brackets", "薄荷绿焦点取景框", ("标注", "焦点", "取景框", "薄荷绿")),
    ("guide_highlight_stroke", "珊瑚色荧光强调线", ("标注", "强调", "划线", "珊瑚色")),
    ("guide_dotted_route", "青色节点引导路径", ("标注", "路径", "节点", "青色")),
    ("guide_zoom_plus", "蓝色放大镜加号", ("标注", "放大", "查看", "蓝色")),
    ("guide_spotlight", "琥珀色聚焦光束", ("标注", "聚焦", "光束", "琥珀色")),
    ("guide_target_reticle", "珊瑚色目标准星", ("标注", "目标", "准星", "珊瑚色")),
    ("guide_check_badge", "绿色完成勾选徽章", ("标注", "完成", "勾选", "绿色")),
    ("guide_comment_bubble", "紫色三点提示气泡", ("标注", "提示", "对话气泡", "紫色")),
    ("guide_branch_nodes", "蓝色分支连接节点", ("标注", "连接", "分支", "蓝色")),
    ("guide_burst_highlight", "黄色放射重点强调", ("标注", "重点", "放射", "黄色")),
    ("guide_crop_rotate", "青色裁切旋转框", ("标注", "裁切", "旋转", "青色")),
    ("guide_split_compare", "双色左右对比标记", ("标注", "对比", "分屏", "双色")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contact_sheet(assets: list[tuple[str, Path]]) -> None:
    tile = 176
    label_height = 28
    panel_width = tile * 2
    sheet = Image.new("RGB", (panel_width * GRID, (tile + label_height) * GRID), "#303744")
    draw = ImageDraw.Draw(sheet)
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font = ImageFont.truetype(str(font_path), 11) if font_path.is_file() else ImageFont.load_default()
    backgrounds = (("#f0f2f5", 0), ("#202838", tile))
    for index, (stem, path) in enumerate(assets):
        row, column = divmod(index, GRID)
        x = column * panel_width
        y = row * (tile + label_height)
        with Image.open(path) as source:
            artwork = source.convert("RGBA")
            artwork.thumbnail((tile - 24, tile - 24), Image.Resampling.LANCZOS)
            for color, offset in backgrounds:
                panel = Image.new("RGBA", (tile, tile), color)
                panel.alpha_composite(artwork, ((tile - artwork.width) // 2,
                                                (tile - artwork.height) // 2))
                sheet.paste(panel.convert("RGB"), (x + offset, y))
        draw.rectangle((x, y + tile, x + panel_width - 1, y + tile + label_height - 1),
                       fill="#303744")
        draw.text((x + 5, y + tile + 7), f"{index + 1:02d}  {stem}", font=font, fill="white")
    CONTACT.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(CONTACT, optimize=True)


def extract() -> int:
    with Image.open(ATLAS) as opened:
        source = opened.convert("RGBA")
    width, height = source.size
    if width != height or source.getchannel("A").getextrema() != (0, 255):
        raise ValueError(f"expected a square RGBA atlas with real transparency, got {source.size}")
    if len(ITEMS) != GRID * GRID or len({item[0] for item in ITEMS}) != len(ITEMS):
        raise ValueError("atlas metadata must contain exactly 16 unique sticker stems")
    if REPORT.exists() or CONTACT.exists():
        raise FileExistsError("refusing to overwrite an existing extraction report or contact sheet")
    collisions = [STICKERS / f"{stem}.png" for stem, *_ in ITEMS
                  if (STICKERS / f"{stem}.png").exists()]
    if collisions:
        raise FileExistsError("refusing to overwrite sticker assets: " +
                              ", ".join(map(str, collisions)))

    outputs: list[tuple[str, Image.Image]] = []
    records: list[dict] = []
    for index, (stem, name, keywords) in enumerate(ITEMS):
        row, column = divmod(index, GRID)
        x0, x1 = round(column * width / GRID), round((column + 1) * width / GRID)
        y0, y1 = round(row * height / GRID), round((row + 1) * height / GRID)
        pixels = np.asarray(source.crop((x0, y0, x1, y1)), dtype=np.uint8).copy()
        pixels[pixels[:, :, 3] < 8] = 0
        visible = pixels[:, :, 3] >= 16
        visible_count = int(visible.sum())
        if visible_count < 1_000:
            raise ValueError(f"{stem} is nearly empty ({visible_count} visible pixels)")
        edge_pixels = int(
            visible[:EDGE_GUTTER, :].sum() + visible[-EDGE_GUTTER:, :].sum()
            + visible[:, :EDGE_GUTTER].sum() + visible[:, -EDGE_GUTTER:].sum())
        if edge_pixels > max(80, int(visible_count * 0.01)):
            raise ValueError(f"{stem} touches a cell boundary ({edge_pixels} visible edge pixels)")
        pixels[:EDGE_GUTTER, :, :] = pixels[-EDGE_GUTTER:, :, :] = 0
        pixels[:, :EDGE_GUTTER, :] = pixels[:, -EDGE_GUTTER:, :] = 0
        image = Image.fromarray(pixels, "RGBA").resize(
            (OUTPUT_SIZE, OUTPUT_SIZE), Image.Resampling.LANCZOS)
        alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
        bounds = Image.fromarray((alpha >= 16).astype(np.uint8) * 255).getbbox()
        if image.getchannel("A").getextrema() != (0, 255) or bounds is None:
            raise ValueError(f"{stem} lost transparent or visible pixels during crop")
        outputs.append((stem, image))
        records.append({
            "index": index + 1,
            "stem": stem,
            "name": name,
            "subcategory": "指引",
            "keywords": list(keywords),
            "row": row,
            "column": column,
            "sourceCell": [x0, y0, x1, y1],
            "edgeGutterPixels": EDGE_GUTTER,
            "visiblePixelsInSourceCell": visible_count,
            "visiblePixelsWithinSourceEdgeGutter": edge_pixels,
            "outputSize": [OUTPUT_SIZE, OUTPUT_SIZE],
            "visibleBounds": list(bounds),
            "alphaExtrema": list(image.getchannel("A").getextrema()),
        })

    STICKERS.mkdir(parents=True, exist_ok=True)
    for (stem, image), record in zip(outputs, records, strict=True):
        target = STICKERS / f"{stem}.png"
        image.save(target, optimize=True)
        record["sha256"] = _sha(target)
    _contact_sheet([(stem, STICKERS / f"{stem}.png") for stem, *_ in ITEMS])
    report = {
        "schemaVersion": 1,
        "generator": "OpenAI ImageGen",
        "source": ATLAS.relative_to(ROOT).as_posix(),
        "sourceSha256": _sha(ATLAS),
        "sourceSize": [width, height],
        "layout": "4x4 equal square RGBA cells in reading order",
        "extraction": "equal-cell crop with an 8px safety gutter, resized to transparent 512x512 PNG",
        "subcategory": "指引",
        "license": LICENSE,
        "contactSheet": CONTACT.relative_to(ROOT).as_posix(),
        "items": records,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(records)


def register() -> int:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if (report.get("generator") != "OpenAI ImageGen" or report.get("subcategory") != "指引"
            or len(report.get("items", [])) != 16):
        raise ValueError("unexpected video-guidance atlas extraction report")
    if report.get("sourceSha256") != _sha(ATLAS):
        raise ValueError("atlas changed since extraction")
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    if not isinstance(document.get("stickers"), list):
        raise ValueError("built-in sticker catalog has no sticker list")
    existing = {item["stem"]: item for item in document["stickers"]}
    additions = []
    for item in report["items"]:
        stem = item["stem"]
        asset = STICKERS / f"{stem}.png"
        if not asset.is_file() or _sha(asset) != item["sha256"]:
            raise ValueError(f"missing or changed extracted sticker: {stem}")
        source = (
            f"OpenAI ImageGen atlas {report['source']} (SHA-256 {report['sourceSha256']}), "
            f"cell row {item['row'] + 1} column {item['column'] + 1}; extracted PNG SHA-256 "
            f"{item['sha256']} recorded in {REPORT.relative_to(ROOT).as_posix()}"
        )
        expected = {
            "stem": stem,
            "name": item["name"],
            "subcategory": item["subcategory"],
            "keywords": item["keywords"],
            "source": source,
            "license": LICENSE,
            "version": "1.0.0",
        }
        current = existing.get(stem)
        if current is not None:
            if current != expected:
                raise ValueError(f"catalog entry conflicts with generated sticker: {stem}")
        else:
            additions.append(expected)
    if additions:
        document["stickers"].extend(additions)
        CATALOG.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return len(additions)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--extract-only", action="store_true")
    mode.add_argument("--register-only", action="store_true")
    mode.add_argument("--contact-only", action="store_true")
    args = parser.parse_args()
    if args.contact_only:
        _contact_sheet([(stem, STICKERS / f"{stem}.png") for stem, *_ in ITEMS])
        extracted = registered = 0
    else:
        extracted = 0 if args.register_only else extract()
        registered = 0 if args.extract_only else register()
    print(json.dumps({"extracted": extracted, "registered": registered,
                      "report": str(REPORT), "contactSheet": str(CONTACT)},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
