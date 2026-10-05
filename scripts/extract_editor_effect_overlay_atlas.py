"""Extract a chroma-keyed ImageGen VFX atlas into transparent sticker PNGs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/editor-effect-overlay-atlas-20260925.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_REPORT = ROOT / "docs/assets/editor-effect-overlay-atlas-20260925.extraction.json"
DEFAULT_CONTACT = ROOT / "docs/assets/editor-effect-overlay-contact-20260925.png"

ITEMS = (
    ("fxoverlay_cyan_aura_ring", "青色气场光环", "特效贴图", ("气场", "光环", "青色")),
    ("fxoverlay_violet_energy_halo", "紫色能量光环", "特效贴图", ("能量", "光环", "紫色")),
    ("fxoverlay_gold_sunburst", "金色放射星芒", "特效贴图", ("放射", "星芒", "金色")),
    ("fxoverlay_blue_lens_flare", "蓝白镜头星芒", "特效贴图", ("镜头", "星芒", "蓝白")),
    ("fxoverlay_magenta_sparkles", "桃紫闪烁星群", "特效贴图", ("星群", "闪烁", "桃紫")),
    ("fxoverlay_gold_bokeh", "金色散景粒子", "特效贴图", ("散景", "光点", "金色")),
    ("fxoverlay_teal_orbit_motes", "青绿环绕光点", "特效贴图", ("环绕", "光点", "青绿")),
    ("fxoverlay_pink_heart_trail", "粉色爱心光迹", "特效贴图", ("爱心", "光迹", "粉色")),
    ("fxoverlay_ice_speed_streaks", "冰蓝速度光线", "特效贴图", ("速度", "光线", "冰蓝")),
    ("fxoverlay_gold_swoosh", "金色弧形拖尾", "特效贴图", ("弧形", "拖尾", "金色")),
    ("fxoverlay_violet_magic_ribbon", "紫色魔法飘带", "特效贴图", ("魔法", "飘带", "紫色")),
    ("fxoverlay_cyan_electric_arc", "青色电弧", "特效贴图", ("电弧", "能量", "青色")),
    ("fxoverlay_pale_smoke_wisps", "浅色烟雾", "特效贴图", ("烟雾", "薄雾", "浅色")),
    ("fxoverlay_golden_dust_burst", "金色尘光爆发", "特效贴图", ("尘光", "爆发", "金色")),
    ("fxoverlay_blue_crystal_burst", "冰蓝水晶碎片", "特效贴图", ("水晶", "碎片", "冰蓝")),
    ("fxoverlay_rainbow_confetti", "彩虹纸屑爆发", "特效贴图", ("纸屑", "彩虹", "庆祝")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _key_green(image: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """Estimate foreground alpha by distance from the pure green key color.

    Generated glows include antialiased pixels blended with the #00FF00 key.
    The distance matte preserves cyan, blue, gold, white and purple effects,
    then removes green spill from the recovered foreground RGB.
    """
    rgba = np.asarray(image.convert("RGBA"), dtype=np.float32)
    rgb = rgba[:, :, :3]
    source_alpha = rgba[:, :, 3] / 255.0
    distance = np.sqrt(
        rgb[:, :, 0] ** 2 + (255.0 - rgb[:, :, 1]) ** 2 + rgb[:, :, 2] ** 2
    )
    key_alpha = np.clip(distance / (255.0 * np.sqrt(2.0)), 0.0, 1.0)
    # Keep the exact chroma background fully clear, including compression noise.
    key_alpha[distance < 10.0] = 0.0
    alpha = key_alpha * source_alpha
    foreground = np.zeros_like(rgb)
    visible = key_alpha > 1e-4
    matte_bg = np.zeros_like(rgb)
    matte_bg[:, :, 1] = 255.0
    foreground[visible] = (
        rgb[visible] - matte_bg[visible] * (1.0 - key_alpha[visible, None])
    ) / key_alpha[visible, None]
    foreground = np.clip(foreground, 0, 255)
    result = np.dstack((foreground, alpha * 255.0)).round().astype(np.uint8)
    result[result[:, :, 3] < 5] = 0
    return result, alpha


def _contact_sheet(items: list[tuple[str, Image.Image]], target: Path) -> None:
    cell = 256
    sheet = Image.new("RGB", (cell * 4, cell * 4), (23, 28, 38))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 13)
    except OSError:
        font = ImageFont.load_default()
    for index, (stem, rgba) in enumerate(items):
        x, y = (index % 4) * cell, (index // 4) * cell
        tile = Image.new("RGBA", (cell, cell), (35, 42, 54, 255))
        tile_draw = ImageDraw.Draw(tile)
        checker = 16
        for yy in range(0, cell, checker):
            for xx in range(0, cell, checker):
                if (xx // checker + yy // checker) % 2:
                    tile_draw.rectangle((xx, yy, xx + checker - 1, yy + checker - 1),
                                        fill=(51, 59, 72, 255))
        art = rgba.copy()
        art.thumbnail((cell - 22, cell - 44), Image.Resampling.LANCZOS)
        tile.alpha_composite(art, ((cell - art.width) // 2, (cell - 36 - art.height) // 2))
        sheet.paste(tile.convert("RGB"), (x, y))
        draw.text((x + 8, y + cell - 19), f"{index + 1:02d}  {stem.removeprefix('fxoverlay_')}",
                  fill=(230, 236, 244), font=font)
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, optimize=True)


def extract(sheet: Path, output: Path, report: Path, contact: Path) -> list[dict]:
    source = Image.open(sheet).convert("RGBA")
    width, height = source.size
    if abs((width / 4) - (height / 4)) > 1:
        raise ValueError(f"expected square 4x4 atlas, got {source.size}")
    if report.exists():
        raise FileExistsError(f"refusing to overwrite {report}")
    targets = [output / f"{stem}.png" for stem, *_ in ITEMS]
    collisions = [str(path) for path in targets if path.exists()]
    if collisions:
        raise FileExistsError("refusing to overwrite: " + ", ".join(collisions))

    output.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    contact_items: list[tuple[str, Image.Image]] = []
    for index, (stem, name, subcategory, keywords) in enumerate(ITEMS):
        row, column = divmod(index, 4)
        x0, x1 = round(column * width / 4), round((column + 1) * width / 4)
        y0, y1 = round(row * height / 4), round((row + 1) * height / 4)
        cell_image = source.crop((x0, y0, x1, y1))
        pixels, alpha = _key_green(cell_image)
        visible = pixels[:, :, 3] >= 16
        visible_count = int(visible.sum())
        if visible_count < 500:
            raise ValueError(f"{stem} is nearly empty after keying ({visible_count} pixels)")
        ys, xs = np.where(visible)
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
        boundary = int(visible[:12].sum() + visible[-12:].sum()
                       + visible[:, :12].sum() + visible[:, -12:].sum())
        side = max(pixels.shape[:2])
        square = np.zeros((side, side, 4), dtype=np.uint8)
        top = (side - pixels.shape[0]) // 2
        left = (side - pixels.shape[1]) // 2
        square[top:top + pixels.shape[0], left:left + pixels.shape[1]] = pixels
        target = output / f"{stem}.png"
        result = Image.fromarray(square, "RGBA")
        result.save(target, optimize=True)
        contact_items.append((stem, result))
        records.append({
            "stem": stem, "name": name, "subcategory": subcategory,
            "keywords": list(keywords), "row": row, "column": column,
            "cell": [x0, y0, x1, y1], "visibleBounds": bbox,
            "visiblePixels": visible_count,
            "visiblePixelsWithin12pxBoundary": boundary,
            "alphaExtrema": [int(square[:, :, 3].min()), int(square[:, :, 3].max())],
            "size": [side, side], "sha256": _sha(target),
            "meanKeyedAlpha": round(float(alpha.mean()), 6),
        })
    _contact_sheet(contact_items, contact)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "generator": "OpenAI ImageGen", "source": sheet.name,
        "sourceSha256": _sha(sheet), "sourceSize": [width, height],
        "layout": "4x4", "keyColor": "#00FF00",
        "extraction": "equal proportional 4x4 crops, green-screen distance matte, despill, centered square RGBA PNG",
        "items": records, "contactSheet": contact.name,
        "license": "Project-internal original AI-generated assets; not separately licensed for redistribution",
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
