"""Crop the ImageGen background-material atlas into reproducible editor assets."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "docs/assets/video-background-material-atlas-20260927.png"
ASSET_DIR = ROOT / "src/cutvoke/assets/backgrounds"
REPORT = ROOT / "docs/assets/video-background-material-atlas-20260927.extraction.json"
CONTACT = ROOT / "docs/assets/video-background-material-atlas-20260927-contact.png"
GRID = 4
EDGE_INSET = 12
OUTPUT_SIZE = (1920, 1080)
JPEG_QUALITY = 94

ITEMS = [
    ("bgmat_cotton_ivory", "米白棉纸"),
    ("bgmat_watercolor_blue", "浅蓝水彩纸"),
    ("bgmat_handmade_rose", "玫瑰手工纸"),
    ("bgmat_linen_sage", "鼠尾草亚麻"),
    ("bgmat_velvet_navy", "深蓝丝绒"),
    ("bgmat_concrete_charcoal", "炭灰水泥"),
    ("bgmat_marble_teal", "青绿云石"),
    ("bgmat_wood_walnut", "胡桃木纹"),
    ("bgmat_terrazzo_cream", "奶油水磨石"),
    ("bgmat_vellum_lavender", "淡紫描图纸"),
    ("bgmat_plaster_peach", "蜜桃灰泥"),
    ("bgmat_frosted_mint", "薄荷磨砂玻璃"),
    ("bgmat_denim_indigo", "靛蓝牛仔布"),
    ("bgmat_canvas_sand", "沙色画布"),
    ("bgmat_satin_emerald", "翡翠缎面"),
    ("bgmat_slate_bluegray", "蓝灰板岩"),
]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_or_verify(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f"refusing to overwrite changed asset: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def extract() -> dict:
    if not ATLAS.is_file():
        raise FileNotFoundError(ATLAS)
    if len(ITEMS) != GRID * GRID:
        raise ValueError("atlas item count does not match grid")

    atlas_bytes = ATLAS.read_bytes()
    atlas = Image.open(io.BytesIO(atlas_bytes)).convert("RGB")
    width, height = atlas.size
    if width != height:
        raise ValueError(f"expected square atlas, got {atlas.size}")

    contact_cell = (360, 203)
    contact = Image.new("RGB", (GRID * contact_cell[0] + 5 * 12,
                                 GRID * contact_cell[1] + 5 * 12), (27, 30, 38))
    rows = []
    for index, (stem, name) in enumerate(ITEMS):
        row, column = divmod(index, GRID)
        left = round(column * width / GRID)
        top = round(row * height / GRID)
        right = round((column + 1) * width / GRID)
        bottom = round((row + 1) * height / GRID)
        source_cell = (left, top, right, bottom)
        crop_box = (left + EDGE_INSET, top + EDGE_INSET,
                    right - EDGE_INSET, bottom - EDGE_INSET)
        tile = atlas.crop(crop_box)
        final = ImageOps.fit(tile, OUTPUT_SIZE, method=Image.Resampling.LANCZOS,
                             centering=(0.5, 0.5))
        buffer = io.BytesIO()
        final.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True,
                   progressive=True, subsampling=0)
        encoded = buffer.getvalue()
        output = ASSET_DIR / f"{stem}.jpg"
        _write_or_verify(output, encoded)

        thumbnail = final.resize(contact_cell, Image.Resampling.LANCZOS)
        x = 12 + column * (contact_cell[0] + 12)
        y = 12 + row * (contact_cell[1] + 12)
        contact.paste(thumbnail, (x, y))
        rows.append({
            "index": index + 1,
            "stem": stem,
            "name": name,
            "row": row,
            "column": column,
            "sourceCell": list(source_cell),
            "cropBox": list(crop_box),
            "interiorSize": list(tile.size),
            "size": list(OUTPUT_SIZE),
            "format": f"JPEG quality {JPEG_QUALITY}, 4:4:4 chroma",
            "sizeBytes": len(encoded),
            "sha256": _sha256(encoded),
        })

    contact_buffer = io.BytesIO()
    contact.save(contact_buffer, format="PNG", optimize=True)
    contact_bytes = contact_buffer.getvalue()
    _write_or_verify(CONTACT, contact_bytes)
    report = {
        "schemaVersion": 1,
        "generator": "OpenAI ImageGen",
        "source": ATLAS.name,
        "sourceSha256": _sha256(atlas_bytes),
        "sourceSize": [width, height],
        "layout": "4x4 equal square cells",
        "extraction": (f"centered cell crops with {EDGE_INSET}px inset to remove dark grid; "
                       f"resized to {OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]} by Lanczos fit"),
        "contactSheet": CONTACT.name,
        "license": "Project-internal original (AI-generated); not separately licensed for redistribution",
        "items": rows,
    }
    report_bytes = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _write_or_verify(REPORT, report_bytes)
    return report


if __name__ == "__main__":
    result = extract()
    print(f"extracted {len(result['items'])} background materials")
    for item in result["items"]:
        print(f"  {item['stem']}: {item['size'][0]}x{item['size'][1]} "
              f"{item['sizeBytes']} bytes sha256={item['sha256']}")
