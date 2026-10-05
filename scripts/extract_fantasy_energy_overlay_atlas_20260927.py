"""Extract the 4x4 ImageGen fantasy-energy overlay atlas into transparent stickers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "docs/assets/fantasy-energy-overlay-atlas-20260927.png"
OUTPUT = ROOT / "src/cutvoke/assets/stickers"
REPORT = ROOT / "docs/assets/fantasy-energy-overlay-atlas-20260927.extraction.json"
CONTACT_DARK = ROOT / "docs/assets/fantasy-energy-overlay-atlas-20260927-contact-dark.png"
CONTACT_LIGHT = ROOT / "docs/assets/fantasy-energy-overlay-atlas-20260927-contact-light.png"
LICENSE = "Project-internal original (AI-generated); not separately licensed for redistribution"
SUBCATEGORY = "特效贴图"

ITEMS = (
    ("fxoverlay_fantasy_amber_arc", "琥珀熔光弧", ("幻想", "能量", "暖光")),
    ("fxoverlay_fantasy_violet_portal", "紫晶传送门碎片", ("幻想", "传送门", "紫色")),
    ("fxoverlay_fantasy_cyan_electric", "青蓝能量电弧", ("幻想", "电弧", "青蓝")),
    ("fxoverlay_fantasy_gold_pulse", "金色脉冲光环", ("幻想", "脉冲", "金色")),
    ("fxoverlay_fantasy_aqua_ribbon", "青碧玻璃光带", ("幻想", "光带", "水色")),
    ("fxoverlay_fantasy_blue_shockwave", "冰蓝冲击波", ("幻想", "冲击波", "冰蓝")),
    ("fxoverlay_fantasy_indigo_smoke", "靛蓝烟雾旋涡", ("幻想", "烟雾", "旋涡")),
    ("fxoverlay_fantasy_magenta_spiral", "洋红星尘旋涡", ("幻想", "星尘", "洋红")),
    ("fxoverlay_fantasy_lens_glow", "青金镜头辉光", ("幻想", "镜头", "辉光")),
    ("fxoverlay_fantasy_pearl_glints", "珍珠菱形闪光", ("幻想", "菱形", "闪光")),
    ("fxoverlay_fantasy_cyan_shards", "冰青多边碎片", ("幻想", "碎片", "冰青")),
    ("fxoverlay_fantasy_crimson_afterimage", "绯红色差残影", ("幻想", "残影", "绯红")),
    ("fxoverlay_fantasy_aqua_caustic", "水青折射波纹", ("幻想", "折射", "波纹")),
    ("fxoverlay_fantasy_gold_comet", "金色彗星星尘", ("幻想", "彗星", "星尘")),
    ("fxoverlay_fantasy_violet_flame", "紫色幽光火焰", ("幻想", "火焰", "紫色")),
    ("fxoverlay_fantasy_white_speedlines", "白色动感放射线", ("幻想", "速度", "放射线")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contact_sheet(items: list[tuple[str, str, Image.Image]], target: Path,
                   background: tuple[int, int, int]) -> None:
    cell, footer = 320, 34
    sheet = Image.new("RGB", (cell * 4, (cell + footer) * 4), background)
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("msyh.ttc", 13)
    except OSError:
        font = ImageFont.load_default()
    for index, (stem, name, image) in enumerate(items):
        row, column = divmod(index, 4)
        x, y = column * cell, row * (cell + footer)
        tile = Image.new("RGBA", (cell, cell), (*background, 255))
        art = image.copy()
        art.thumbnail((cell - 24, cell - 24), Image.Resampling.LANCZOS)
        tile.alpha_composite(art, ((cell - art.width) // 2, (cell - art.height) // 2))
        sheet.paste(tile.convert("RGB"), (x, y))
        draw.rectangle((x, y + cell, x + cell - 1, y + cell + footer - 1),
                       fill=background)
        text_color = (248, 248, 248) if sum(background) < 400 else (34, 42, 52)
        draw.text((x + 7, y + cell + 9), f"{index + 1:02d}  {name} · {stem}",
                  fill=text_color, font=font)
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, optimize=True)


def extract() -> list[dict]:
    if REPORT.exists() or CONTACT_DARK.exists() or CONTACT_LIGHT.exists():
        raise FileExistsError("refusing to overwrite existing fantasy-energy extraction artifacts")
    source = Image.open(ATLAS).convert("RGBA")
    width, height = source.size
    if abs(width - height) > 1:
        raise ValueError(f"expected a square 4x4 atlas, got {source.size}")
    alpha = np.asarray(source.getchannel("A"), dtype=np.uint8)
    if alpha.min() != 0 or alpha.max() != 255:
        raise ValueError("source must contain genuine transparent and opaque pixels")

    crops: list[tuple[str, str, Image.Image, dict]] = []
    for index, (stem, name, keywords) in enumerate(ITEMS):
        row, column = divmod(index, 4)
        x0, x1 = round(column * width / 4), round((column + 1) * width / 4)
        y0, y1 = round(row * height / 4), round((row + 1) * height / 4)
        pixels = np.asarray(source.crop((x0, y0, x1, y1)), dtype=np.uint8).copy()
        pixels[pixels[:, :, 3] < 8] = (0, 0, 0, 0)
        visible = pixels[:, :, 3] >= 24
        visible_count = int(visible.sum())
        if visible_count < 1_000:
            raise ValueError(f"{stem} is nearly empty ({visible_count} visible pixels)")
        boundary = int(visible[:8].sum() + visible[-8:].sum()
                       + visible[:, :8].sum() + visible[:, -8:].sum())
        if boundary > max(200, int(visible_count * 0.04)):
            raise ValueError(f"{stem} crosses the cell safety gutter ({boundary} boundary pixels)")
        pixels[:8, :, :] = pixels[-8:, :, :] = 0
        pixels[:, :8, :] = pixels[:, -8:, :] = 0
        side = max(pixels.shape[:2])
        square = np.zeros((side, side, 4), dtype=np.uint8)
        top, left = (side - pixels.shape[0]) // 2, (side - pixels.shape[1]) // 2
        square[top:top + pixels.shape[0], left:left + pixels.shape[1]] = pixels
        result = Image.fromarray(square, "RGBA")
        ys, xs = np.where(square[:, :, 3] >= 24)
        record = {
            "stem": stem, "name": name, "subcategory": SUBCATEGORY,
            "keywords": list(keywords), "row": row, "column": column,
            "sourceCell": [x0, y0, x1, y1],
            "visibleBounds": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
            "visiblePixels": int((square[:, :, 3] >= 24).sum()),
            "trimmedBoundaryPixels": boundary,
            "alphaExtrema": [int(square[:, :, 3].min()), int(square[:, :, 3].max())],
            "size": [side, side],
        }
        crops.append((stem, name, result, record))

    targets = [OUTPUT / f"{stem}.png" for stem, _, _, _ in crops]
    collisions = [str(path) for path in targets if path.exists()]
    if collisions:
        raise FileExistsError("refusing to overwrite: " + ", ".join(collisions))
    saved: list[tuple[str, str, Image.Image]] = []
    records = []
    for stem, name, image, record in crops:
        target = OUTPUT / f"{stem}.png"
        image.save(target, optimize=True)
        record["sha256"] = _sha(target)
        records.append(record)
        saved.append((stem, name, image))

    _contact_sheet(saved, CONTACT_DARK, (28, 34, 46))
    _contact_sheet(saved, CONTACT_LIGHT, (240, 237, 229))
    extraction = {
        "schemaVersion": 1,
        "generator": "OpenAI ImageGen",
        "source": ATLAS.relative_to(ROOT).as_posix(),
        "sourceSha256": _sha(ATLAS),
        "layout": "4x4",
        "subcategory": SUBCATEGORY,
        "license": LICENSE,
        "contactSheets": [CONTACT_DARK.relative_to(ROOT).as_posix(),
                          CONTACT_LIGHT.relative_to(ROOT).as_posix()],
        "generationPrompt": "A square 4x4 sprite atlas, exactly 16 standalone video-compositing overlay effects in row-major cells, no labels, no borders, ample cell margins, perfectly flat pure chroma key green #00FF00 everywhere in background and absolutely no green in art. Premium crisp luminous fantasy and cinematic motion artwork: 1 amber molten energy crescent arc, 2 violet portal shard halo, 3 cyan electric zigzag arc, 4 gold radial pulse rings, 5 turquoise glass ribbon curl, 6 blue-white circular shockwave, 7 indigo smoke ribbon swirl, 8 magenta particle spiral, 9 thin warm lens flare streak, 10 pearl diamond glint cluster, 11 cyan polygonal energy shards, 12 crimson chromatic afterimage swoosh, 13 aqua refractive caustic wave, 14 golden stardust comet trail, 15 violet spectral flame plume, 16 white kinetic speedline burst. Each cell art isolated and centered, no cell crossings. Background perfectly uniform, no shadow, gradient, texture, grid, divider, extra decoration. Use a unified polished transparent-glass and luminous-particle art direction. High contrast; clean edges suitable for removing green to make transparent PNG overlays for a real video editor.",
        "items": records,
    }
    REPORT.write_text(json.dumps(extraction, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return records


if __name__ == "__main__":
    print(json.dumps({"count": len(records := extract()), "items": records},
                     ensure_ascii=False, indent=2))
