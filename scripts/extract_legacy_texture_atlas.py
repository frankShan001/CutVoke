"""Extract the 4x4 first-party cinematic texture atlas into sticker PNGs.

The generated atlas and this deterministic crop map are retained as provenance.
Existing artwork is backed up before an explicit replacement run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/cinematic-texture-atlas-20260925.png"
DEFAULT_OUTPUT = ROOT / "tmp/legacy-texture-review"
DEFAULT_REPORT = ROOT / "docs/assets/cinematic-texture-atlas-20260925.extraction.json"
DEFAULT_CONTACT = ROOT / "docs/assets/cinematic-texture-contact-sheet-20260925.png"
CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
BACKUP_ROOT = ROOT / "docs/assets/replaced-sticker-sources-20260925"
GRID = 4
OUTPUT_SIZE = 320
PADDING = 20
CROP_FRACTION = 0.94

ITEMS = (
    ("texture_atlas_dust_bokeh", "金尘散景", ("电影叠加", "金色", "散景", "光斑")),
    ("texture_atlas_edge_leak", "暖橙漏光", ("电影叠加", "暖光", "漏光", "边缘")),
    ("texture_atlas_embers", "微光余烬", ("电影叠加", "火花", "余烬", "光点")),
    ("texture_atlas_foil_flecks", "金银箔碎片", ("电影叠加", "金箔", "银箔", "碎片")),
    ("texture_atlas_frost_edge", "冰霜框边", ("电影叠加", "冰霜", "边框", "冬日")),
    ("texture_atlas_gate_weave", "青色放映门痕", ("电影叠加", "青色", "胶片", "划痕")),
    ("texture_atlas_leaf_shadows", "叶影漏光", ("电影叠加", "叶影", "植物", "光影")),
    ("texture_atlas_low_fog", "层叠低雾", ("电影叠加", "薄雾", "蓝色", "氛围")),
    ("texture_atlas_prism_arc", "虹彩棱镜弧", ("电影叠加", "棱镜", "虹彩", "弧光")),
    ("texture_atlas_rain_glass", "雨窗水滴", ("电影叠加", "雨滴", "玻璃", "水珠")),
    ("texture_atlas_smoke", "轻烟流线", ("电影叠加", "烟雾", "轻烟", "流动")),
    ("texture_atlas_snow_crystals", "散落雪晶", ("电影叠加", "雪花", "晶体", "冬日")),
    ("texture_atlas_torn_paper", "纤维撕纸边", ("电影叠加", "纸张", "撕边", "纤维")),
    ("texture_atlas_watercolor", "蓝珊瑚水彩", ("电影叠加", "水彩", "蓝色", "珊瑚")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contact_sheet(paths: list[Path], output: Path) -> None:
    tile = 200
    sheet = Image.new("RGB", (tile * 4, tile * 4), (237, 239, 243))
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(paths):
        row, column = divmod(index, 4)
        x, y = column * tile, row * tile
        background = Image.new("RGBA", (tile, tile), (240, 241, 244, 255))
        checker = ImageDraw.Draw(background)
        block = 20
        for cy in range(0, tile, block):
            for cx in range(0, tile, block):
                if (cx // block + cy // block) % 2:
                    checker.rectangle((cx, cy, cx + block - 1, cy + block - 1),
                                      fill=(216, 219, 225, 255))
        with Image.open(path) as source:
            source = source.convert("RGBA")
            source.thumbnail((tile - 24, tile - 24), Image.Resampling.LANCZOS)
            background.alpha_composite(source, ((tile - source.width) // 2,
                                                (tile - source.height) // 2))
        sheet.paste(background.convert("RGB"), (x, y))
        draw.rectangle((x + 5, y + 5, x + 34, y + 25), fill=(21, 26, 35))
        draw.text((x + 13, y + 8), f"{index + 1:02d}", fill=(255, 255, 255))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, optimize=True)


def extract(sheet: Path, output: Path, report: Path, contact: Path,
            *, replace_assets: bool = False, record_source: bool = False) -> list[dict]:
    source = Image.open(sheet).convert("RGBA")
    width, height = source.size
    if abs(width / height - 1) > 0.02:
        raise ValueError(f"expected a square atlas, got {source.size}")
    if source.getchannel("A").getextrema() != (0, 255):
        raise ValueError("source atlas must contain real transparent and opaque pixels")
    if len(ITEMS) > GRID * GRID:
        raise ValueError("atlas has fewer cells than named assets")

    cell_w, cell_h = width / GRID, height / GRID
    crop_fraction = CROP_FRACTION
    output.mkdir(parents=True, exist_ok=True)
    targets = [output / f"{stem}.png" for stem, *_ in ITEMS]
    existing = [path for path in targets if path.exists()]
    if existing and not replace_assets:
        raise FileExistsError("refusing to overwrite without --replace-assets: " +
                              ", ".join(map(str, existing)))
    if report.exists() and output.resolve() != DEFAULT_OUTPUT.resolve() and not replace_assets:
        raise FileExistsError(f"refusing to overwrite extraction record: {report}")

    records: list[dict] = []
    for index, (stem, name, keywords) in enumerate(ITEMS):
        row, column = divmod(index, GRID)
        x0, x1 = round(column * cell_w), round((column + 1) * cell_w)
        y0, y1 = round(row * cell_h), round((row + 1) * cell_h)
        side = min(x1 - x0, y1 - y0)
        crop_side = round(side * crop_fraction)
        left = x0 + ((x1 - x0) - crop_side) // 2
        top = y0 + ((y1 - y0) - crop_side) // 2
        crop_box = (left, top, left + crop_side, top + crop_side)
        pixels = np.asarray(source.crop(crop_box), dtype=np.uint8).copy()
        pixels[pixels[:, :, 3] < 8] = (0, 0, 0, 0)
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
        records.append({
            "index": index + 1,
            "stem": stem,
            "name": name,
            "subcategory": "电影叠加",
            "keywords": list(keywords),
            "row": row,
            "column": column,
            "sourceCell": [x0, y0, x1, y1],
            "cropBox": list(crop_box),
            "size": [OUTPUT_SIZE, OUTPUT_SIZE],
            "sourceVisiblePixels": visible_count,
            "alphaBounds": list(bounds) if bounds else None,
            "alphaExtrema": list(alpha.getextrema()),
            "sha256": _sha(target),
        })

    _contact_sheet(targets, contact)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "schemaVersion": 1,
        "source": sheet.name,
        "sourceSha256": _sha(sheet),
        "sourceSize": [width, height],
        "layout": "4x4; first 14 row-major cells extracted",
        "extraction": f"centered {crop_fraction:.0%} square crop per cell; transparent trim under alpha 8; fit to {OUTPUT_SIZE - 2 * PADDING}px and center on transparent {OUTPUT_SIZE}px canvas",
        "contactSheet": contact.name,
        "items": records,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if record_source:
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        by_stem = {item["stem"]: item for item in data["stickers"]}
        for record in records:
            item = by_stem.get(record["stem"])
            if item is None:
                raise ValueError(f"sticker catalog is missing {record['stem']}")
            item["version"] = "1.1.0"
            item["source"] = (
                f"OpenAI ImageGen atlas {sheet.relative_to(ROOT).as_posix()} "
                f"(SHA-256 {_sha(sheet)}), cell {record['index']} row-major; "
                f"crop and asset SHA-256 in {report.relative_to(ROOT).as_posix()}"
            )
        CATALOG.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--contact-sheet", type=Path, default=DEFAULT_CONTACT)
    parser.add_argument("--replace-assets", action="store_true",
                        help="backup and replace existing matching PNGs")
    parser.add_argument("--record-source", action="store_true",
                        help="write generated atlas provenance to the sticker catalog")
    args = parser.parse_args()
    records = extract(args.sheet, args.output, args.report, args.contact_sheet,
                      replace_assets=args.replace_assets, record_source=args.record_source)
    print(json.dumps({"extracted": len(records), "report": str(args.report),
                      "contactSheet": str(args.contact_sheet)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
