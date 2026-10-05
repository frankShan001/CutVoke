"""Extract a generated 4x4 visual-effects texture atlas as video backgrounds."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "docs/assets/cinematic-effect-texture-atlas-20260927.png"
OUTPUT = ROOT / "src/cutvoke/assets/backgrounds"
REPORT = ROOT / "docs/assets/cinematic-effect-texture-atlas-20260927.extraction.json"
CONTACT = ROOT / "docs/assets/cinematic-effect-texture-contact-sheet-20260927.png"
LICENSE = "Project-internal original (AI-generated); not separately licensed for redistribution"
OUTPUT_SIZE = (1920, 1080)
CELL_INSET = 0.035

ITEMS = (
    ("bgfx_amber_film_burn", "琥珀胶片烧光", ("胶片", "暖光", "烧光")),
    ("bgfx_cyan_scanline", "青色扫描线干扰", ("数码", "扫描线", "故障")),
    ("bgfx_magenta_chromatic", "洋红色差晕影", ("色差", "洋红", "光晕")),
    ("bgfx_violet_holographic", "紫色全息衍射", ("全息", "衍射", "紫色")),
    ("bgfx_gold_dust", "金色漂浮尘光", ("尘光", "金色", "氛围")),
    ("bgfx_blue_raindrops", "蓝调雨窗光斑", ("雨滴", "窗面", "散景")),
    ("bgfx_snow_crystals", "雪晶飘落", ("雪花", "冬日", "氛围")),
    ("bgfx_ember_sparks", "橙红余烬", ("余烬", "火星", "橙红")),
    ("bgfx_cream_paper", "奶油纸纤维", ("纸张", "纤维", "奶油色")),
    ("bgfx_graphite_scratches", "石墨刮痕", ("石墨", "刮痕", "深色")),
    ("bgfx_vintage_halftone", "复古印刷网点", ("复古", "网点", "印刷")),
    ("bgfx_linen_weave", "亚麻织纹", ("织物", "亚麻", "纤维")),
    ("bgfx_iridescent_caustics", "虹彩水纹折射", ("水纹", "虹彩", "折射")),
    ("bgfx_underwater_rays", "水下体积光束", ("水下", "光束", "青绿")),
    ("bgfx_rose_prism_flare", "玫瑰金棱镜光", ("棱镜", "光束", "玫瑰金")),
    ("bgfx_blue_electric_arcs", "蓝白电弧纹理", ("电弧", "蓝色", "能量")),
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _font() -> ImageFont.ImageFont:
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    return ImageFont.truetype(str(font_path), 19) if font_path.is_file() else ImageFont.load_default()


def extract() -> dict:
    with Image.open(ATLAS) as opened:
        atlas = opened.convert("RGB")
    width, height = atlas.size
    if abs(width - height) > 1 or width < 1200:
        raise ValueError(f"expected high-resolution square 4x4 atlas, got {atlas.size}")
    targets = [OUTPUT / f"{stem}.jpg" for stem, _, _ in ITEMS]
    if any(path.exists() for path in targets):
        raise FileExistsError("refusing to overwrite an existing generated background")
    if REPORT.exists() or CONTACT.exists():
        raise FileExistsError("refusing to overwrite existing extraction evidence")

    crops: list[Image.Image] = []
    records: list[dict] = []
    for index, (stem, name, keywords) in enumerate(ITEMS):
        row, column = divmod(index, 4)
        x0, x1 = round(column * width / 4), round((column + 1) * width / 4)
        y0, y1 = round(row * height / 4), round((row + 1) * height / 4)
        inset_x = round((x1 - x0) * CELL_INSET)
        inset_y = round((y1 - y0) * CELL_INSET)
        crop_box = (x0 + inset_x, y0 + inset_y, x1 - inset_x, y1 - inset_y)
        crop = atlas.crop(crop_box)
        background = ImageOps.fit(crop, OUTPUT_SIZE, method=Image.Resampling.LANCZOS,
                                  centering=(0.5, 0.5))
        crops.append(background)
        records.append({
            "stem": stem,
            "name": name,
            "keywords": list(keywords),
            "row": row,
            "column": column,
            "sourceCell": [x0, y0, x1, y1],
            "cropBox": list(crop_box),
            "output": f"assets/backgrounds/{stem}.jpg",
            "size": list(OUTPUT_SIZE),
        })

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for record, crop in zip(records, crops, strict=True):
        target = ROOT / "src/cutvoke" / record["output"]
        crop.save(target, format="JPEG", quality=94, subsampling=0,
                  optimize=True, progressive=True)
        record["sha256"] = _sha(target)
        record["sizeBytes"] = target.stat().st_size

    tile_w, tile_h, caption_h = 420, 236, 42
    contact = Image.new("RGB", (tile_w * 4, (tile_h + caption_h) * 4), "#202631")
    draw = ImageDraw.Draw(contact)
    font = _font()
    for index, (record, crop) in enumerate(zip(records, crops, strict=True)):
        row, column = divmod(index, 4)
        x, y = column * tile_w, row * (tile_h + caption_h)
        thumb = ImageOps.fit(crop, (tile_w - 8, tile_h - 8), method=Image.Resampling.LANCZOS)
        contact.paste(thumb, (x + 4, y + 4))
        draw.text((x + 10, y + tile_h + 5), record["name"], font=font, fill="#f5f3ed")
    contact.save(CONTACT, optimize=True)

    report = {
        "schemaVersion": 1,
        "generator": "OpenAI ImageGen",
        "source": "docs/assets/cinematic-effect-texture-atlas-20260927.png",
        "sourceSha256": _sha(ATLAS),
        "sourceSize": [width, height],
        "layout": "4x4 equal square cells",
        "cellInsetFraction": CELL_INSET,
        "outputSize": list(OUTPUT_SIZE),
        "format": "JPEG quality 94, 4:4:4 chroma",
        "license": LICENSE,
        "contactSheet": CONTACT.relative_to(ROOT).as_posix(),
        "items": records,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return report


if __name__ == "__main__":
    result = extract()
    print(json.dumps({
        "sourceSha256": result["sourceSha256"],
        "count": len(result["items"]),
        "contactSheet": result["contactSheet"],
        "items": result["items"],
    }, ensure_ascii=False, indent=2))
