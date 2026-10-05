"""Extract a generated 4x4 celebration sticker atlas into square PNG assets."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "docs/assets/celebration-party-sticker-atlas-20260927.png"
OUTPUT = ROOT / "src/cutvoke/assets/stickers"
REPORT = ROOT / "docs/assets/celebration-party-sticker-atlas-20260927.extraction.json"
CONTACT = ROOT / "docs/assets/celebration-party-sticker-atlas-20260927-contact.png"

ITEMS = (
    ("party_balloon_bouquet", "派对气球束", ["庆祝", "派对", "气球", "生日"]),
    ("party_confetti_popper", "彩纸礼花筒", ["庆祝", "派对", "礼花", "彩纸"]),
    ("party_candle_cake", "蜡烛庆祝蛋糕", ["庆祝", "生日", "蛋糕", "蜡烛"]),
    ("party_gift_box", "彩带礼物盒", ["庆祝", "礼物", "派对", "礼盒"]),
    ("party_cone_hat", "条纹派对帽", ["庆祝", "派对", "帽子", "生日"]),
    ("party_curled_streamer", "卷曲庆祝彩带", ["庆祝", "派对", "彩带", "装饰"]),
    ("party_starburst", "星芒庆祝闪光", ["庆祝", "闪光", "星芒", "装饰"]),
    ("party_juice_cheers", "碰杯果汁杯", ["庆祝", "碰杯", "果汁", "派对"]),
    ("party_rosette_badge", "粉色庆祝绶带", ["庆祝", "绶带", "奖章", "派对"]),
    ("party_paper_crown", "彩钻派对皇冠", ["庆祝", "皇冠", "派对", "生日"]),
    ("party_cupcake", "奶油庆祝纸杯蛋糕", ["庆祝", "纸杯蛋糕", "甜点", "生日"]),
    ("party_horn", "派对吹龙", ["庆祝", "派对", "吹龙", "彩带"]),
    ("party_fireworks", "彩色烟花绽放", ["庆祝", "烟花", "派对", "节日"]),
    ("party_bunting_garland", "三角旗派对拉旗", ["庆祝", "派对", "拉旗", "装饰"]),
    ("party_confetti_cluster", "彩纸碎片", ["庆祝", "彩纸", "派对", "装饰"]),
    ("party_sparklers", "双支庆祝仙女棒", ["庆祝", "仙女棒", "派对", "闪光"]),
)

LICENSE = "Project-internal original (AI-generated); not separately licensed for redistribution"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract() -> list[dict]:
    source = Image.open(ATLAS).convert("RGBA")
    width, height = source.size
    if width != height or width < 4:
        raise ValueError(f"expected a square 4x4 atlas, got {source.size}")
    if source.getchannel("A").getextrema() != (0, 255):
        raise ValueError("source must have genuine transparent and opaque pixels")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    cell_size = math.ceil(width / 4)
    contact = Image.new("RGBA", (4 * cell_size, 4 * cell_size), (45, 49, 58, 255))
    records = []
    for index, (stem, name, keywords) in enumerate(ITEMS):
        row, col = divmod(index, 4)
        x0, x1 = round(col * width / 4), round((col + 1) * width / 4)
        y0, y1 = round(row * height / 4), round((row + 1) * height / 4)
        artwork = source.crop((x0, y0, x1, y1))
        pixels = np.asarray(artwork, dtype=np.uint8).copy()
        if pixels.shape[0] > cell_size or pixels.shape[1] > cell_size:
            raise ValueError(f"cell {row + 1},{col + 1} exceeds the square output: {pixels.shape}")

        # Remove tiny disconnected generator specks while retaining the detached
        # confetti/sparkle groups used by the intended artwork.
        labels_count, labels, stats, _ = cv2.connectedComponentsWithStats(
            (pixels[:, :, 3] >= 8).astype(np.uint8), 8
        )
        removed_components = 0
        for label in range(1, labels_count):
            if int(stats[label, cv2.CC_STAT_AREA]) < 40:
                pixels[labels == label] = (0, 0, 0, 0)
                removed_components += 1
        pixels[pixels[:, :, 3] < 8] = (0, 0, 0, 0)

        visible = pixels[:, :, 3] >= 16
        if int(visible.sum()) < 5000:
            raise ValueError(f"{stem} is almost empty after extraction")
        boundary_pixels = int(
            visible[:4, :].sum() + visible[-4:, :].sum()
            + visible[:, :4].sum() + visible[:, -4:].sum()
        )
        if boundary_pixels > 500:
            raise ValueError(f"{stem} appears clipped or crosses its cell boundary")

        target = OUTPUT / f"{stem}.png"
        if target.exists():
            raise FileExistsError(f"will not overwrite {target}")
        square = np.zeros((cell_size, cell_size, 4), dtype=np.uint8)
        square[:pixels.shape[0], :pixels.shape[1]] = pixels
        Image.fromarray(square, "RGBA").save(target, optimize=True)
        contact.alpha_composite(Image.fromarray(square, "RGBA"), (col * cell_size, row * cell_size))
        records.append({
            "stem": stem,
            "name": name,
            "subcategory": "庆祝派对",
            "keywords": keywords,
            "row": row,
            "column": col,
            "sourceCell": [x0, y0, x1, y1],
            "sourceCellSize": [x1 - x0, y1 - y0],
            "outputSize": [cell_size, cell_size],
            "visiblePixels": int(visible.sum()),
            "removedTinyComponents": removed_components,
            "boundaryPixelsInFourPixelMargin": boundary_pixels,
            "alphaExtrema": [int(pixels[:, :, 3].min()), int(pixels[:, :, 3].max())],
            "sha256": _sha(target),
        })

    contact.convert("RGB").save(CONTACT, optimize=True)
    return records


def main() -> None:
    items = extract()
    report = {
        "schemaVersion": 1,
        "generator": "OpenAI ImageGen",
        "layout": "4x4 round-proportioned square cells",
        "subcategory": "庆祝派对",
        "source": "docs/assets/celebration-party-sticker-atlas-20260927.png",
        "sourceSha256": _sha(ATLAS),
        "license": LICENSE,
        "items": items,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"sourceSha256": report["sourceSha256"], "assetCount": len(items),
                      "report": str(REPORT.relative_to(ROOT)),
                      "contact": str(CONTACT.relative_to(ROOT))}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
