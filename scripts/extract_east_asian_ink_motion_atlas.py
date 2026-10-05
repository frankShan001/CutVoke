"""Extract transparent ink-and-gold motion overlays from an ImageGen atlas."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/east-asian-ink-motion-overlay-atlas-20260926.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_REPORT = ROOT / "docs/assets/east-asian-ink-motion-overlay-atlas-20260926.extraction.json"
DEFAULT_CONTACT = ROOT / "docs/assets/east-asian-ink-motion-overlay-contact-20260926.png"

ITEMS = (
    ("inkfx_black_splash", "浓墨飞溅", ("水墨", "墨点", "飞溅")),
    ("inkfx_vermilion_slash", "朱红飞白笔势", ("水墨", "朱红", "笔势")),
    ("inkfx_gold_burst", "金彩墨点爆发", ("水墨", "金彩", "爆发")),
    ("inkfx_turquoise_bloom", "青碧水墨晕染", ("水墨", "青碧", "晕染")),
    ("inkfx_dry_brush", "枯笔扫痕", ("水墨", "枯笔", "扫痕")),
    ("inkfx_ink_tendrils", "墨色回旋烟丝", ("水墨", "墨烟", "回旋")),
    ("inkfx_petal_arc", "粉樱花瓣弧线", ("水墨", "樱花", "花瓣")),
    ("inkfx_gold_ribbon", "金箔流线", ("水墨", "金箔", "流线")),
    ("inkfx_jade_ripple", "翡翠水纹环", ("水墨", "翡翠", "水纹")),
    ("inkfx_plum_blossom", "白梅金粉花簇", ("水墨", "白梅", "金粉")),
    ("inkfx_mica_trail", "暖金云母尘迹", ("水墨", "云母", "金尘")),
    ("inkfx_ink_drops", "悬浮浓墨滴", ("水墨", "墨滴", "飞溅")),
    ("inkfx_calligraphic_arc", "抽象书写弧线", ("水墨", "弧线", "抽象")),
    ("inkfx_vermilion_eddy", "朱金流体涡旋", ("水墨", "朱金", "涡旋")),
    ("inkfx_charcoal_mist", "炭墨轻雾", ("水墨", "炭墨", "薄雾")),
    ("inkfx_bronze_teal_ring", "青铜碧色流体环", ("水墨", "青铜", "流体环")),
)
SUBCATEGORY = "特效贴图"
LICENSE = "Project-internal original (AI-generated); not separately licensed for redistribution"
GENERATION_PROMPT = """Use case: stylized-concept.
Asset type: production-ready transparent overlay texture atlas for a desktop video editor.
Primary request: create one square 4 by 4 atlas containing sixteen visually distinct East Asian ink-and-gold motion overlay textures, one per equal-sized cell, in this exact row-major order: 1 expressive black ink splash with scattered droplets, 2 vermilion brush slash with fine pigment spray, 3 antique-gold ink burst with tiny foil flecks, 4 turquoise ink bloom with fluid edges, 5 dry graphite brush swipe with broken bristle tips, 6 deep-ink curling smoke tendrils, 7 pale pink petal fragments caught in a brush-motion arc, 8 thin gold-foil sweeping ribbon, 9 jade-and-cyan water ripple rings with pigment wisps, 10 white plum-blossom petals with restrained gold glints, 11 sparse warm-gold mica dust trail, 12 suspended black ink drops and tiny splashes, 13 abstract calligraphic curved brush arc with no letters or recognizable glyph, 14 vermilion and gold swirling pigment eddy, 15 soft charcoal wash mist with wispy edges, 16 bronze-and-teal liquid ring splash.
Scene/backdrop: truly transparent empty canvas; every cell contains only the overlay artwork and its very local soft glow.
Style/medium: polished hand-painted ink textures mixed with refined metallic pigment, suitable for compositing over live-action video; varied organic edges and graceful motion direction; each item must be clearly different from the others.
Composition/framing: exact square regular 4 by 4 grid, equal-sized cells, each texture centered, contained fully inside its own cell with generous transparent margin, useful as a standalone square overlay after cell cropping. Preserve a genuinely transparent RGBA background.
Lighting/mood: restrained cinematic highlights that remain visible over both dark and light footage.
Color palette: black ink, vermilion red, antique gold, jade, turquoise, pale plum pink, warm charcoal.
Materials/textures: flowing liquid ink, dry-brush fibers, suspended pigment, subtle metallic foil flecks.
Constraints: no cell backgrounds, no paper panels, no grid lines, no borders, no checkerboard, no frames, no labels, no lettering, no Chinese characters, no numbers, no watermark, no full-scene illustrations. Keep the 4x4 layout exact and uniform."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contact_sheet(items: list[tuple[str, Image.Image]], target: Path) -> None:
    cell, label_h, gutter = 256, 28, 8
    width = cell * 4 + gutter * 5
    height = (cell + label_h) * 4 + gutter * 5
    sheet = Image.new("RGB", (width, height), "#172536")
    draw = ImageDraw.Draw(sheet)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 13) if font_path.is_file() else ImageFont.load_default()
    for index, (stem, art) in enumerate(items):
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
        preview = art.copy()
        preview.thumbnail((cell - 18, cell - 18), Image.Resampling.LANCZOS)
        tile.alpha_composite(preview, ((cell - preview.width) // 2,
                                       (cell - preview.height) // 2))
        sheet.paste(tile.convert("RGB"), (x, y))
        draw.text((x + 3, y + cell + 5), f"{index + 1:02d}  {stem.removeprefix('inkfx_')}",
                  font=font, fill="#e7edf5")
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
        alpha = crop.getchannel("A")
        if alpha.getbbox() is None:
            raise ValueError(f"empty alpha cell at row {row + 1}, column {column + 1}")
        canvas = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
        canvas.alpha_composite(crop, ((320 - crop.width) // 2, (320 - crop.height) // 2))
        target = output / f"{stem}.png"
        canvas.save(target, optimize=True)
        contact_items.append((stem, canvas))

        alpha = canvas.getchannel("A")
        pixels = list(alpha.get_flattened_data())
        visible = sum(value >= 16 for value in pixels)
        if visible < 500:
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
            "alphaBounds": list(alpha.getbbox()),
            "visiblePixelsAlphaAtLeast16": visible,
            "visiblePixelsWithin8pxBoundary": sum(value >= 16 for value in edge.get_flattened_data()),
            "alphaExtrema": list(alpha.getextrema()), "sha256": _sha(target),
        })

    _contact_sheet(contact_items, contact)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "schemaVersion": 1, "generator": "OpenAI ImageGen",
        "source": sheet.name, "sourceSha256": _sha(sheet),
        "sourceSize": [width, height], "layout": "4x4",
        "generationPrompt": GENERATION_PROMPT,
        "extraction": "equal proportional cell crops; generated RGBA alpha preserved; centered on 320x320 transparent canvas",
        "license": LICENSE, "subcategory": SUBCATEGORY,
        "items": records, "contactSheet": contact.name,
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
