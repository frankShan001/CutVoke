"""Extract transparent weather and atmosphere overlays from an ImageGen atlas."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/weather-atmosphere-overlay-atlas-20260926.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_REPORT = ROOT / "docs/assets/weather-atmosphere-overlay-atlas-20260926.extraction.json"
DEFAULT_CONTACT = ROOT / "docs/assets/weather-atmosphere-overlay-contact-20260926.png"

ITEMS = (
    ("weatherfx_light_rain", "细雨斜线", ("天气", "细雨", "雨丝")),
    ("weatherfx_heavy_rain", "密集雨幕", ("天气", "暴雨", "雨幕")),
    ("weatherfx_lens_raindrops", "镜头水滴", ("天气", "水滴", "镜头")),
    ("weatherfx_soft_snow", "柔和飘雪", ("天气", "飘雪", "冬日")),
    ("weatherfx_snowflakes", "晶亮雪花", ("天气", "雪花", "冰晶")),
    ("weatherfx_ground_fog", "低空薄雾", ("氛围", "薄雾", "地面")),
    ("weatherfx_sunrays", "体积阳光", ("氛围", "阳光", "光束")),
    ("weatherfx_window_lightleak", "暖色窗光漏光", ("氛围", "窗光", "漏光")),
    ("weatherfx_dust_motes", "漂浮尘埃", ("氛围", "尘埃", "微粒")),
    ("weatherfx_cherry_petals", "樱花花瓣", ("天气", "花瓣", "春日")),
    ("weatherfx_autumn_leaves", "秋叶飘落", ("天气", "秋叶", "落叶")),
    ("weatherfx_fireflies", "萤火微光", ("氛围", "萤火", "微光")),
    ("weatherfx_soap_bubbles", "彩虹泡泡", ("氛围", "泡泡", "虹彩")),
    ("weatherfx_embers", "橙色余烬", ("氛围", "余烬", "火星")),
    ("weatherfx_wind_seeds", "风中草籽", ("天气", "风", "草籽")),
)
SUBCATEGORY = "特效贴图"
LICENSE = "Project-internal original (AI-generated); not separately licensed for redistribution"
GENERATION_PROMPT = """Use case: production-ready video editor overlay texture atlas.
Asset type: one square 4 by 4 atlas of sixteen independent transparent RGBA overlay textures for compositing over live-action footage.
Primary request: create exactly sixteen distinct weather and atmosphere effects, one per equal-sized grid cell, in this fixed row-major order: 1 fine diagonal rain streaks, 2 dense rain streaks, 3 scattered large lens raindrops, 4 soft falling snow, 5 tiny sparkling snowflakes, 6 low drifting ground fog, 7 soft volumetric sun rays, 8 warm window light leak, 9 floating dust motes, 10 pink cherry blossom petals, 11 golden autumn leaves, 12 glowing fireflies, 13 tiny soap bubbles, 14 orange embers, 15 wind-swept pale grass seeds, 16 blue-green underwater suspended particles.
Style/medium: polished realistic compositing assets, restrained cinematic colors, detailed but clean edges.
Composition/framing: exact square 4x4 grid, identical cells, generous empty transparent padding inside every cell, each effect centered within its own cell and fully contained; no effect may cross a cell boundary; uniform visual density and useful alpha falloff.
Lighting/mood: varied to match each named weather effect, visually distinct and readable when composited over varied footage.
Materials/textures: genuinely transparent background (alpha channel), each cell contains only the named floating overlay elements and their faint local glow; suitable for normal/additive compositing, no solid plates.
Constraints: no visible grid lines, no borders, no cell backgrounds, no checkerboard, no shadows outside the effects, no labels, no symbols, no text, no numbers, no watermark. Maintain the exact 4x4 regular layout."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contact_sheet(items: list[tuple[str, Image.Image]], rejected: dict[int, str], target: Path) -> None:
    cell, label_h, gutter = 256, 30, 8
    width = cell * 4 + gutter * 5
    height = (cell + label_h) * 4 + gutter * 5
    sheet = Image.new("RGB", (width, height), "#172536")
    draw = ImageDraw.Draw(sheet)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 13) if font_path.is_file() else ImageFont.load_default()
    for index in range(16):
        x = gutter + (index % 4) * (cell + gutter)
        y = gutter + (index // 4) * (cell + label_h + gutter)
        tile = Image.new("RGBA", (cell, cell), (42, 50, 62, 255))
        tile_draw = ImageDraw.Draw(tile)
        checker = 16
        for yy in range(0, cell, checker):
            for xx in range(0, cell, checker):
                if (xx // checker + yy // checker) % 2:
                    tile_draw.rectangle((xx, yy, xx + checker - 1, yy + checker - 1),
                                        fill=(82, 91, 104, 255))
        if index in rejected:
            tile_draw.rectangle((0, 0, cell - 1, cell - 1), fill=(94, 42, 47, 255))
            tile_draw.text((16, 105), "REJECTED", font=font, fill=(255, 226, 220, 255))
            tile_draw.text((16, 132), rejected[index][:30], font=font, fill=(255, 226, 220, 255))
            label = f"{index + 1:02d}  excluded"
        else:
            stem, art = items[index]
            art = art.copy()
            art.thumbnail((cell - 18, cell - 18), Image.Resampling.LANCZOS)
            tile.alpha_composite(art, ((cell - art.width) // 2, (cell - art.height) // 2))
            label = f"{index + 1:02d}  {stem.removeprefix('weatherfx_')}"
        sheet.paste(tile.convert("RGB"), (x, y))
        draw.text((x + 2, y + cell + 5), label, font=font, fill="#e7edf5")
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, optimize=True)


def extract(sheet: Path, output: Path, report: Path, contact: Path) -> list[dict]:
    source = Image.open(sheet).convert("RGBA")
    width, height = source.size
    if width != height or width < 1024:
        raise ValueError(f"expected a square high-resolution 4x4 atlas, got {source.size}")
    if report.exists() or contact.exists():
        raise FileExistsError("refusing to overwrite existing extraction report or contact sheet")
    targets = [output / f"{stem}.png" for stem, *_ in ITEMS]
    collisions = [str(path) for path in targets if path.exists()]
    if collisions:
        raise FileExistsError("refusing to overwrite: " + ", ".join(collisions))

    output.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    contact_items: list[tuple[str, Image.Image]] = []
    for index, (stem, name, keywords) in enumerate(ITEMS):
        row, column = divmod(index, 4)
        x0, x1 = round(column * width / 4), round((column + 1) * width / 4)
        y0, y1 = round(row * height / 4), round((row + 1) * height / 4)
        crop = source.crop((x0, y0, x1, y1))
        if crop.getchannel("A").getbbox() is None:
            raise ValueError(f"empty alpha cell at row {row + 1}, column {column + 1}")
        canvas = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
        canvas.alpha_composite(crop, ((320 - crop.width) // 2, (320 - crop.height) // 2))
        target = output / f"{stem}.png"
        canvas.save(target, optimize=True)
        contact_items.append((stem, canvas))
        alpha = canvas.getchannel("A")
        bounds = alpha.getbbox()
        pixels = list(alpha.get_flattened_data())
        visible = sum(value >= 16 for value in pixels)
        if visible < 100:
            raise ValueError(f"{stem} has too few visible pixels ({visible})")
        edge = Image.new("L", alpha.size, 0)
        edge.paste(alpha.crop((0, 0, 320, 8)), (0, 0))
        edge.paste(alpha.crop((0, 312, 320, 320)), (0, 312))
        edge.paste(alpha.crop((0, 0, 8, 320)), (0, 0))
        edge.paste(alpha.crop((312, 0, 320, 320)), (312, 0))
        records.append({
            "stem": stem, "name": name, "subcategory": SUBCATEGORY,
            "keywords": list(keywords), "row": row, "column": column,
            "cell": [x0, y0, x1, y1], "size": [320, 320],
            "alphaBounds": list(bounds), "visiblePixelsAlphaAtLeast16": visible,
            "visiblePixelsWithin8pxBoundary": sum(
                value >= 16 for value in edge.get_flattened_data()),
            "alphaExtrema": list(alpha.getextrema()), "sha256": _sha(target),
        })

    rejected = {
        15: "broad opaque teal panel; unsuitable over live footage",
    }
    _contact_sheet(contact_items, rejected, contact)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "schemaVersion": 1, "generator": "OpenAI ImageGen",
        "source": sheet.name, "sourceSha256": _sha(sheet),
        "sourceSize": [width, height], "layout": "4x4",
        "generationPrompt": GENERATION_PROMPT,
        "extraction": "equal proportional cell crops; original generated RGBA alpha preserved; centered on 320x320 transparent canvas",
        "license": LICENSE, "subcategory": SUBCATEGORY,
        "items": records,
        "rejectedCells": [{"row": 3, "column": 3, "reason": rejected[15]}],
        "contactSheet": contact.name,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--contact", type=Path, default=DEFAULT_CONTACT)
    args = parser.parse_args()
    records = extract(args.sheet, args.output, args.report, args.contact)
    print(json.dumps({"count": len(records), "report": str(args.report),
                      "contact": str(args.contact), "items": records},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
