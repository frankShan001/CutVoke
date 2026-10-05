"""Extract the 4x4 generated stage-light atlas into transparent overlay PNGs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "docs/assets/concert-stage-light-atlas-20260927.png"
OUTPUT = ROOT / "src/cutvoke/assets/stickers"
REPORT = ROOT / "docs/assets/concert-stage-light-atlas-20260927.extraction.json"
CONTACT_DARK = ROOT / "docs/assets/concert-stage-light-atlas-20260927-contact-dark.png"
CONTACT_LIGHT = ROOT / "docs/assets/concert-stage-light-atlas-20260927-contact-light.png"
GRID = 4
OUTPUT_SIZE = 512
PADDING = 14
ITEMS = (
    ("stage_light_laser_fan_cyan", "青紫激光扇", ("舞台灯光", "激光", "青色", "紫色")),
    ("stage_light_spotlight_pair_amber", "琥珀双聚光灯", ("舞台灯光", "聚光灯", "琥珀色")),
    ("stage_light_moving_head_magenta", "洋红环形摇头灯", ("舞台灯光", "摇头灯", "洋红")),
    ("stage_light_beam_columns_blue", "蓝色体积光柱", ("舞台灯光", "光柱", "蓝色")),
    ("stage_light_prism_flare_rainbow", "彩虹棱镜光晕", ("舞台灯光", "棱镜", "彩虹", "镜头光效")),
    ("stage_light_crossing_lasers", "青粉交叉激光", ("舞台灯光", "激光", "交叉光束")),
    ("stage_light_anamorphic_flare_pearl", "珍珠白变形宽银幕光", ("舞台灯光", "宽银幕光", "珍珠白")),
    ("stage_light_starburst_ultraviolet", "紫外星芒光束", ("舞台灯光", "星芒", "紫色")),
    ("stage_light_haze_ribbons_amber_rose", "琥珀玫瑰雾光丝带", ("舞台灯光", "雾光", "暖色")),
    ("stage_light_tunnel_cyan", "青色光环隧道", ("舞台灯光", "光环", "青色")),
    ("stage_light_disco_facets_emerald_violet", "翡翠紫色镜面光斑", ("舞台灯光", "镜面反射", "翡翠色")),
    ("stage_light_sweep_beams_red_blue", "红蓝横扫光束", ("舞台灯光", "横扫", "红色", "蓝色")),
    ("stage_light_spotlight_bloom_gold", "金色聚光灯晕", ("舞台灯光", "聚光灯", "金色")),
    ("stage_light_curtain_magenta_cyan", "洋红青色叠层光幕", ("舞台灯光", "光幕", "洋红", "青色")),
    ("stage_light_beam_spotlight_coolwhite", "冷白舞台追光", ("舞台灯光", "追光", "冷白")),
    ("stage_light_prism_beam_fan", "彩色棱镜扇形光束", ("舞台灯光", "棱镜", "彩色光束")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contact_sheet(paths: list[Path], target: Path, background: tuple[int, int, int]) -> None:
    tile = 220
    footer = 24
    sheet = Image.new("RGB", (tile * GRID, (tile + footer) * GRID), background)
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(paths):
        row, column = divmod(index, GRID)
        x, y = column * tile, row * (tile + footer)
        bg = Image.new("RGBA", (tile, tile), (*background, 255))
        checker = ImageDraw.Draw(bg)
        block = 22
        other = tuple(max(0, min(255, channel + (18 if sum(background) < 380 else -16)))
                      for channel in background)
        for cy in range(0, tile, block):
            for cx in range(0, tile, block):
                if (cx // block + cy // block) % 2:
                    checker.rectangle((cx, cy, cx + block - 1, cy + block - 1), fill=(*other, 255))
        with Image.open(path) as source:
            artwork = source.convert("RGBA")
            artwork.thumbnail((tile - 12, tile - 12), Image.Resampling.LANCZOS)
            bg.alpha_composite(artwork, ((tile - artwork.width) // 2, (tile - artwork.height) // 2))
        sheet.paste(bg.convert("RGB"), (x, y))
        draw.rectangle((x, y + tile, x + tile - 1, y + tile + footer - 1), fill=(24, 28, 34))
        draw.text((x + 8, y + tile + 5), f"{index + 1:02d}  {ITEMS[index][0]}", fill=(242, 244, 248))
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, optimize=True)


def extract(*, replace_existing: bool = False) -> list[dict]:
    if len(ITEMS) != GRID * GRID:
        raise ValueError("item list must match the 4x4 atlas")
    if not ATLAS.is_file():
        raise FileNotFoundError(ATLAS)
    source = Image.open(ATLAS).convert("RGBA")
    width, height = source.size
    if abs(width - height) > 1:
        raise ValueError(f"expected a square atlas, got {source.size}")
    alpha_range = source.getchannel("A").getextrema()
    if alpha_range[0] != 0:
        raise ValueError(f"atlas has no genuine transparent pixels: {alpha_range}")
    targets = [OUTPUT / f"{stem}.png" for stem, _, _ in ITEMS]
    existing = [path for path in [*targets, REPORT, CONTACT_DARK, CONTACT_LIGHT] if path.exists()]
    if existing and not replace_existing:
        raise FileExistsError("refusing to overwrite: " + ", ".join(str(p) for p in existing))
    if replace_existing and REPORT.exists():
        old_report = json.loads(REPORT.read_text(encoding="utf-8"))
        if old_report.get("sourceSha256") != _sha(ATLAS):
            raise ValueError("refusing to replace extraction artifacts from a different source atlas")
        old_items = {item["stem"]: item for item in old_report.get("items", [])}
        for stem, _, _ in ITEMS:
            target = OUTPUT / f"{stem}.png"
            if target.exists() and (stem not in old_items or _sha(target) != old_items[stem].get("sha256")):
                raise ValueError(f"refusing to replace locally modified extracted artwork: {stem}")
        old_contacts = old_report.get("contactSheetSha256", {})
        for contact in (CONTACT_DARK, CONTACT_LIGHT):
            if contact.exists() and old_contacts and old_contacts.get(contact.name) != _sha(contact):
                raise ValueError(f"refusing to replace locally modified contact sheet: {contact.name}")
    elif replace_existing and existing:
        raise ValueError("refusing to replace outputs without a matching extraction report")

    cell_w, cell_h = width / GRID, height / GRID
    records: list[dict] = []
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for index, (stem, name, keywords) in enumerate(ITEMS):
        row, column = divmod(index, GRID)
        x0, x1 = round(column * cell_w), round((column + 1) * cell_w)
        y0, y1 = round(row * cell_h), round((row + 1) * cell_h)
        cell = source.crop((x0, y0, x1, y1))
        side = min(cell.size)
        left, top = (cell.width - side) // 2, (cell.height - side) // 2
        cell = cell.crop((left, top, left + side, top + side))
        px = np.asarray(cell, dtype=np.uint8).copy()
        px[px[:, :, 3] < 8] = 0
        removed_remnant_pixels = 0
        if index == 1:
            # This cell contains one detached violet shard from the neighboring
            # laser illustration; keep the amber spotlight connected artwork.
            component_count, labels, stats, _ = cv2.connectedComponentsWithStats(
                (px[:, :, 3] >= 16).astype(np.uint8), connectivity=8)
            for component in range(1, component_count):
                if stats[component, cv2.CC_STAT_AREA] < 2500:
                    component_mask = (labels == component).astype(np.uint8)
                    component_mask = cv2.dilate(component_mask, np.ones((15, 15), np.uint8)) > 0
                    removed_remnant_pixels += int(component_mask.sum())
                    px[component_mask] = 0
        # Clear the atlas cell boundary so neighboring glows cannot form seams
        # when these 16 tiles are reused at different sizes.
        px[:2, :, :] = px[-2:, :, :] = 0
        px[:, :2, :] = px[:, -2:, :] = 0
        visible = px[:, :, 3] >= 16
        visible_pixels = int(visible.sum())
        if visible_pixels < 400:
            raise ValueError(f"{stem} is nearly empty ({visible_pixels} visible pixels)")
        art = Image.fromarray(px, "RGBA")
        art.thumbnail((OUTPUT_SIZE - 2 * PADDING, OUTPUT_SIZE - 2 * PADDING), Image.Resampling.LANCZOS)
        output = Image.new("RGBA", (OUTPUT_SIZE, OUTPUT_SIZE), (0, 0, 0, 0))
        output.alpha_composite(art, ((OUTPUT_SIZE - art.width) // 2, (OUTPUT_SIZE - art.height) // 2))
        target = OUTPUT / f"{stem}.png"
        output.save(target, optimize=True)
        a = np.asarray(output.getchannel("A"), dtype=np.uint8)
        ys, xs = np.where(a >= 16)
        records.append({
            "index": index + 1,
            "stem": stem,
            "name": name,
            "subcategory": "舞台灯光",
            "keywords": list(keywords),
            "row": row,
            "column": column,
            "sourceCell": [x0, y0, x1, y1],
            "normalizationCrop": [x0 + left, y0 + top, x0 + left + side, y0 + top + side],
            "size": [OUTPUT_SIZE, OUTPUT_SIZE],
            "visiblePixels": int((a >= 16).sum()),
            "removedDetachedRemnantPixels": removed_remnant_pixels,
            "alphaBounds": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
            "alphaExtrema": [int(a.min()), int(a.max())],
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
        "layout": "4x4 equal square cells",
        "subcategory": "舞台灯光",
        "license": "Project-internal original (AI-generated); not separately licensed for redistribution",
        "extraction": "proportional cell crop, alpha below 8 cleared, 2px cell edge cleared, centered on transparent 512px PNG with 14px padding",
        "contactSheets": [CONTACT_DARK.relative_to(ROOT).as_posix(), CONTACT_LIGHT.relative_to(ROOT).as_posix()],
        "contactSheetSha256": {CONTACT_DARK.name: _sha(CONTACT_DARK),
                               CONTACT_LIGHT.name: _sha(CONTACT_LIGHT)},
        "items": records,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace-existing", action="store_true",
                        help="replace extraction outputs only when their report uses this same source atlas")
    args = parser.parse_args()
    records = extract(replace_existing=args.replace_existing)
    print(json.dumps({"extracted": len(records), "report": REPORT.relative_to(ROOT).as_posix()},
                     ensure_ascii=False, indent=2))
