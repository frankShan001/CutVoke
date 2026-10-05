"""Extract an ImageGen energy-light atlas into individually padded RGBA overlays."""

from __future__ import annotations

import hashlib
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "docs/assets/energy-light-overlay-atlas-20260927.png"
OUTPUT = ROOT / "src/cutvoke/assets/stickers"
REPORT = ROOT / "docs/assets/energy-light-overlay-atlas-20260927.extraction.json"
CONTACT_DARK = ROOT / "docs/assets/energy-light-overlay-atlas-20260927-contact-dark.png"
CONTACT_LIGHT = ROOT / "docs/assets/energy-light-overlay-atlas-20260927-contact-light.png"
GRID = 4
OUTPUT_SIZE = 512
PADDING = 18
MIN_ALPHA = 8
SUBCATEGORY = "能量光效"
LICENSE = "Project-internal original (AI-generated); not separately licensed for redistribution"

ITEMS = (
    ("energy_arc_cyan", "青色电弧", ("能量", "电弧", "青色")),
    ("energy_ribbon_violet", "紫色等离子飘带", ("能量", "紫色", "飘带")),
    ("energy_trail_gold", "香槟金火花轨迹", ("能量", "金色", "火花")),
    ("energy_prism_flare_magenta", "洋红棱镜星芒", ("能量", "棱镜", "洋红")),
    ("energy_aurora_emerald", "翡翠极光弧", ("能量", "极光", "绿色")),
    ("energy_burst_cobalt", "钴蓝放射光束", ("能量", "放射", "蓝色")),
    ("energy_rainbow_arc", "彩虹折射弧", ("能量", "彩虹", "折射")),
    ("energy_halo_pearl", "珍珠光环", ("能量", "光环", "珍珠白")),
    ("energy_orbs_multicolor", "多彩能量光球", ("能量", "光球", "多彩")),
    ("energy_shards_crystal", "水晶碎片爆发", ("能量", "水晶", "碎片")),
    ("energy_ribbons_dual", "青紫双层能量带", ("能量", "飘带", "青紫")),
    ("energy_loop_golden", "金色光迹环", ("能量", "金色", "光迹")),
    ("energy_stars_falling", "星点垂落", ("能量", "星点", "垂落")),
    ("energy_curtain_violet", "紫青光幕", ("能量", "光幕", "紫青")),
    ("energy_wave_coral", "珊瑚色脉冲波形", ("能量", "脉冲", "珊瑚色")),
    ("energy_zigzag_gold", "金色电光折线", ("能量", "电光", "金色")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contact_sheet(paths: list[Path], target: Path, background: tuple[int, int, int]) -> None:
    tile, footer = 220, 28
    sheet = Image.new("RGB", (tile * GRID, (tile + footer) * GRID), background)
    draw = ImageDraw.Draw(sheet)
    other = tuple(max(0, min(255, value + (18 if sum(background) < 380 else -16)))
                  for value in background)
    for index, path in enumerate(paths):
        row, column = divmod(index, GRID)
        x, y = column * tile, row * (tile + footer)
        composite = Image.new("RGBA", (tile, tile), (*background, 255))
        checker = ImageDraw.Draw(composite)
        block = 22
        for cy in range(0, tile, block):
            for cx in range(0, tile, block):
                if (cx // block + cy // block) % 2:
                    checker.rectangle((cx, cy, cx + block - 1, cy + block - 1),
                                      fill=(*other, 255))
        with Image.open(path) as source:
            artwork = source.convert("RGBA")
            artwork.thumbnail((tile - 12, tile - 12), Image.Resampling.LANCZOS)
            composite.alpha_composite(artwork, ((tile - artwork.width) // 2,
                                                 (tile - artwork.height) // 2))
        sheet.paste(composite.convert("RGB"), (x, y))
        draw.rectangle((x, y + tile, x + tile - 1, y + tile + footer - 1), fill=(24, 28, 34))
        draw.text((x + 7, y + tile + 7), f"{index + 1:02d}  {ITEMS[index][0]}",
                  fill=(242, 244, 248))
    sheet.save(target, optimize=True)


def extract(*, replace_existing: bool = False) -> list[dict]:
    if len(ITEMS) != GRID * GRID:
        raise ValueError("item list must match the 4x4 atlas")
    if not ATLAS.is_file():
        raise FileNotFoundError(ATLAS)
    source = Image.open(ATLAS).convert("RGBA")
    width, height = source.size
    if source.getchannel("A").getextrema()[0] != 0:
        raise ValueError("atlas has no fully transparent background pixels")

    targets = [OUTPUT / f"{stem}.png" for stem, _, _ in ITEMS]
    existing = [path for path in [*targets, REPORT, CONTACT_DARK, CONTACT_LIGHT] if path.exists()]
    if existing and not replace_existing:
        raise FileExistsError("refusing to overwrite: " + ", ".join(str(path) for path in existing))
    if replace_existing and REPORT.exists():
        old_report = json.loads(REPORT.read_text(encoding="utf-8"))
        if old_report.get("sourceSha256") != _sha(ATLAS):
            raise ValueError("refusing to replace extraction output from a different source atlas")
        old_items = {item["stem"]: item for item in old_report.get("items", [])}
        for (stem, _name, _keywords), target in zip(ITEMS, targets, strict=True):
            if target.exists() and (stem not in old_items or
                                    _sha(target) != old_items[stem].get("sha256")):
                raise ValueError(f"refusing to replace locally changed artwork: {stem}")
        old_contacts = old_report.get("contactSheetSha256", {})
        for contact in (CONTACT_DARK, CONTACT_LIGHT):
            if contact.exists() and old_contacts.get(contact.name) != _sha(contact):
                raise ValueError(f"refusing to replace locally changed contact sheet: {contact.name}")
    elif replace_existing and existing:
        raise ValueError("refusing to replace outputs without a matching extraction report")

    records: list[dict] = []
    OUTPUT.mkdir(parents=True, exist_ok=True)
    cell_w, cell_h = width / GRID, height / GRID
    for index, (stem, name, keywords) in enumerate(ITEMS):
        row, column = divmod(index, GRID)
        x0, x1 = round(column * cell_w), round((column + 1) * cell_w)
        y0, y1 = round(row * cell_h), round((row + 1) * cell_h)
        pixels = np.asarray(source.crop((x0, y0, x1, y1)), dtype=np.uint8).copy()
        before = pixels[:, :, 3]
        removed_low_alpha = int((before < MIN_ALPHA).sum())
        pixels[before < MIN_ALPHA] = 0
        # Keep a transparent cell seam so a weak atlas edge can never leak into an overlay.
        pixels[:3, :, :] = pixels[-3:, :, :] = 0
        pixels[:, :3, :] = pixels[:, -3:, :] = 0
        visible = pixels[:, :, 3] >= 16
        visible_pixels = int(visible.sum())
        if visible_pixels < 400:
            raise ValueError(f"{stem} is nearly empty ({visible_pixels} visible pixels)")

        alpha_bounds = Image.fromarray(pixels[:, :, 3]).point(
            lambda value: 255 if value >= MIN_ALPHA else 0).getbbox()
        if alpha_bounds is None:
            raise ValueError(f"{stem} has no visible artwork")
        left, top, right, bottom = alpha_bounds
        content_padding = 2
        crop_box = (max(0, left - content_padding), max(0, top - content_padding),
                    min(pixels.shape[1], right + content_padding),
                    min(pixels.shape[0], bottom + content_padding))
        pixels = pixels[crop_box[1]:crop_box[3], crop_box[0]:crop_box[2]]
        artwork = Image.fromarray(pixels)
        artwork.thumbnail((OUTPUT_SIZE - 2 * PADDING, OUTPUT_SIZE - 2 * PADDING),
                          Image.Resampling.LANCZOS)
        output = Image.new("RGBA", (OUTPUT_SIZE, OUTPUT_SIZE), (0, 0, 0, 0))
        output.alpha_composite(artwork, ((OUTPUT_SIZE - artwork.width) // 2,
                                         (OUTPUT_SIZE - artwork.height) // 2))
        target = OUTPUT / f"{stem}.png"
        output.save(target, optimize=True)
        alpha = np.asarray(output.getchannel("A"), dtype=np.uint8)
        border = np.concatenate((alpha[:PADDING, :].ravel(), alpha[-PADDING:, :].ravel(),
                                 alpha[:, :PADDING].ravel(), alpha[:, -PADDING:].ravel()))
        if int(border.max()) != 0:
            raise ValueError(f"{stem} has visible alpha inside the transparent safety padding")
        ys, xs = np.where(alpha >= 16)
        records.append({
            "index": index + 1,
            "stem": stem,
            "name": name,
            "subcategory": SUBCATEGORY,
            "keywords": list(keywords),
            "row": row,
            "column": column,
            "sourceCell": [x0, y0, x1, y1],
            "contentCropWithinCell": list(crop_box),
            "sourceSize": [width, height],
            "size": [OUTPUT_SIZE, OUTPUT_SIZE],
            "visiblePixels": visible_pixels,
            "removedLowAlphaPixels": removed_low_alpha,
            "alphaBounds": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
            "alphaExtrema": [int(alpha.min()), int(alpha.max())],
            "sha256": _sha(target),
        })

    _contact_sheet(targets, CONTACT_DARK, (25, 30, 42))
    _contact_sheet(targets, CONTACT_LIGHT, (232, 235, 240))
    report = {
        "schemaVersion": 1,
        "generator": "OpenAI ImageGen",
        "source": ATLAS.relative_to(ROOT).as_posix(),
        "sourceSha256": _sha(ATLAS),
        "sourceSize": [width, height],
        "layout": "4x4 proportional cells; each source cell normalized to a square transparent PNG",
        "subcategory": SUBCATEGORY,
        "license": LICENSE,
        "extraction": f"proportional cell crop; alpha below {MIN_ALPHA} cleared; 3px cell edge cleared; content cropped to its visible bounds with {content_padding}px source margin, then centered on {OUTPUT_SIZE}px transparent PNG with {PADDING}px guaranteed safety padding",
        "contactSheets": [CONTACT_DARK.relative_to(ROOT).as_posix(),
                          CONTACT_LIGHT.relative_to(ROOT).as_posix()],
        "contactSheetSha256": {CONTACT_DARK.name: _sha(CONTACT_DARK),
                               CONTACT_LIGHT.name: _sha(CONTACT_LIGHT)},
        "items": records,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace-existing", action="store_true",
                        help="replace outputs only when their hashes match this source atlas report")
    args = parser.parse_args()
    result = extract(replace_existing=args.replace_existing)
    print(json.dumps({"extracted": len(result), "report": REPORT.relative_to(ROOT).as_posix()},
                     ensure_ascii=False, indent=2))
