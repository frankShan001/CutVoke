"""Build labeled contact sheets for human review of built-in preset covers.

The output is review material, not an approval or visual-distinctness check.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cutvoke.core.effects import default_registry  # noqa: E402
from cutvoke.core.preset_catalog import PresetCatalog  # noqa: E402


def make_sheets(family: str, output: Path) -> list[Path]:
    presets = [item for item in PresetCatalog.builtin(default_registry()).all()
               if item.family == family and item.status != "retired"]
    if not presets:
        raise ValueError(f"no presets in family {family!r}")
    by_category: dict[str, list] = {}
    for item in presets:
        by_category.setdefault(item.subcategory, []).append(item)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 16) if font_path.is_file() else ImageFont.load_default()
    small = ImageFont.truetype(str(font_path), 12) if font_path.is_file() else ImageFont.load_default()
    cell_w, cell_h, columns = 344, 236, 4
    output.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for category, items in by_category.items():
        rows = (len(items) + columns - 1) // columns
        sheet = Image.new("RGB", (columns * cell_w, rows * cell_h), "#18202d")
        draw = ImageDraw.Draw(sheet)
        for index, item in enumerate(items):
            x, y = (index % columns) * cell_w, (index // columns) * cell_h
            cover = Path(item.cover)
            if not cover.is_absolute():
                cover = item.asset_root / cover
            with Image.open(cover) as image:
                tile = image.convert("RGB")
                tile.thumbnail((320, 180))
                sheet.paste(tile, (x + 12, y + 8))
            draw.text((x + 12, y + 194), item.name, font=font, fill="#ffffff")
            draw.text((x + 12, y + 216), item.id.removeprefix("cutvoke.preset."),
                      font=small, fill="#b6c6d9")
        name = f"{family}-{category}.png"
        destination = output / name
        sheet.save(destination)
        written.append(destination)
    return written


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("family", choices=("fx", "transition", "animation", "filter",
                                            "text", "personFx"))
    parser.add_argument("--output", type=Path, default=REPO / "output/preset_review")
    args = parser.parse_args()
    for result in make_sheets(args.family, args.output):
        print(result)
