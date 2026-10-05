"""Recover the full existing gift artwork from its retained ImageGen atlas.

The bow extends above the fourth grid row. Cropping a fixed square cell clipped
it. This bounded extraction keeps the original illustration and transparent
pixels; it does not generate a replacement or add catalog entries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image, ImageDraw

from extract_legacy_icon_atlases import _remove_small_components

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/acceptance/product-completion-20261003/artwork"
ATLAS = ROOT / "docs/assets/basic-symbol-sticker-atlas-20260925.png"
TARGET = ROOT / "src/cutvoke/assets/stickers/gift.png"
CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
EXPECTED_ATLAS = "cad7244088163c8214c9f72847be0f8881823fa101f3621c638178bd1b1d791a"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    if sha(ATLAS) != EXPECTED_ATLAS:
        raise ValueError("source atlas changed; review its crop before extracting")
    OUT.mkdir(parents=True, exist_ok=True)
    crop_box = (0, 910, 320, 1254)
    with Image.open(ATLAS) as atlas:
        pixels = np.asarray(atlas.convert("RGBA").crop(crop_box)).copy()
    pixels[pixels[:, :, 3] < 8] = 0
    pixels, removed_components, removed_pixels = _remove_small_components(pixels)
    artwork = Image.fromarray(pixels, "RGBA")
    bounds = artwork.getchannel("A").point(lambda value: 255 if value >= 16 else 0).getbbox()
    if bounds is None or bounds[1] <= 3 or bounds[3] >= artwork.height - 3:
        raise ValueError("artwork touches extraction bounds; crop requires review")
    artwork = artwork.crop(bounds)
    artwork.thumbnail((284, 284), Image.Resampling.LANCZOS)
    corrected = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
    corrected.alpha_composite(artwork, ((320 - artwork.width) // 2, (320 - artwork.height) // 2))
    corrected_path = OUT / "gift-v2.png"
    corrected.save(corrected_path, optimize=True)
    backup = OUT / "gift-before.png"
    if not backup.exists():
        shutil.copy2(TARGET, backup)
    sheet = Image.new("RGB", (640, 355), (231, 234, 240))
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate((backup, corrected_path)):
        tile = Image.new("RGBA", (320, 320), (231, 234, 240, 255))
        with Image.open(path) as source:
            tile.alpha_composite(source.convert("RGBA"))
        sheet.paste(tile.convert("RGB"), (index * 320, 30))
        draw.text((index * 320 + 12, 10), "Previous crop" if index == 0 else "Recovered full bow",
                  fill=(20, 24, 31))
    sheet.save(OUT / "gift-before-after.png")
    report = {"schemaVersion": 1, "atlas": ATLAS.relative_to(ROOT).as_posix(),
              "atlasSha256": sha(ATLAS), "sourceCrop": list(crop_box),
              "visibleBoundsWithinCrop": list(bounds), "outputSize": [320, 320],
              "outputSha256": sha(corrected_path), "previousSha256": sha(backup),
              "removedSmallComponents": removed_components,
              "removedSmallComponentPixels": removed_pixels,
              "version": "1.1.1", "installed": args.install}
    (OUT / "gift-extraction.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)
                                              + "\n", encoding="utf-8")
    if args.install:
        shutil.copy2(corrected_path, TARGET)
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        provenance = (f"OpenAI ImageGen atlas {report['atlas']} (SHA-256 {EXPECTED_ATLAS}); "
                      "full gift bow recovered using crop [0,910,320,1254]; output SHA-256 "
                      f"{report['outputSha256']}; record: "
                      "output/acceptance/product-completion-20261003/artwork/gift-extraction.json")
        for item in data["stickers"]:
            if item["stem"] == "gift":
                item.update(version="1.1.1", source=provenance)
        for item in data.get("motionVariants", []):
            if item["stem"] == "gift":
                item.update(version="1.1.1", source=f"CutVoke {item['effectId']} over {provenance}")
        CATALOG.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
