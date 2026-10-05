"""Register the person-focused ImageGen overlay atlas in the sticker catalog."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
STICKER_ROOT = ROOT / "src/cutvoke/assets/stickers"
ATLAS = ROOT / "docs/assets/person-fx-overlay-atlas-20260925.png"
REPORT = ROOT / "docs/assets/person-fx-overlay-atlas-20260925.extraction.json"
LICENSE = "Project-internal original (AI-generated); not separately licensed for redistribution"
SUBCATEGORY = "人物氛围"

ITEMS = (
    ("personfx_aura_amber", "琥珀环形气场", ("人物", "气场", "暖光")),
    ("personfx_halo_cyan", "青色霓虹光环", ("人物", "光环", "霓虹")),
    ("personfx_bokeh_halo", "柔彩散景光环", ("人物", "散景", "柔光")),
    ("personfx_arc_electric", "青紫电弧光环", ("人物", "电弧", "能量")),
    ("personfx_orbit_motes", "金色星点轨迹", ("人物", "星点", "轨迹")),
    ("personfx_manga_rays", "黑白漫画射线", ("人物", "漫画", "射线")),
    ("personfx_energy_crescent", "珍珠弧光", ("人物", "弧光", "珍珠光")),
    ("personfx_pulse_rings", "青色脉冲光圈", ("人物", "脉冲", "光圈")),
    ("personfx_scan_bands", "全息扫描光带", ("人物", "扫描", "全息")),
    ("personfx_prismatic_ribbons", "棱镜彩虹光弧", ("人物", "棱镜", "彩虹")),
    ("personfx_orbit_constellation", "环绕光点", ("人物", "环绕", "光点")),
    ("personfx_spotlight_violet", "紫色聚光", ("人物", "聚光", "紫色")),
    ("personfx_chromatic_afterimage", "青紫色残影", ("人物", "残影", "色差")),
    ("personfx_energy_ribbons", "青绿色能量飘带", ("人物", "能量", "飘带")),
    ("personfx_shockwave_gold", "金色冲击波", ("人物", "冲击波", "金色")),
    ("personfx_pixel_shards", "霓虹像素碎片", ("人物", "像素", "霓虹")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def register() -> int:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if report.get("layout") != "4x4" or len(report.get("items", [])) != len(ITEMS):
        raise ValueError("unexpected person FX atlas extraction report")
    atlas_sha = _sha(ATLAS)
    if report.get("sourceSha256") != atlas_sha:
        raise ValueError("atlas changed since extraction")

    extracted = {item["stem"]: item for item in report["items"]}
    existing = {item["stem"] for item in document.get("stickers", [])}
    pending = []
    for stem, name, keywords in ITEMS:
        item = extracted.get(stem)
        if item is None or stem in existing:
            raise ValueError(f"missing extraction record or duplicate sticker: {stem}")
        asset = STICKER_ROOT / f"{stem}.png"
        if not asset.is_file() or _sha(asset) != item["sha256"]:
            raise ValueError(f"missing or changed extracted sticker: {stem}")
        source = (
            f"OpenAI ImageGen atlas docs/assets/{ATLAS.name} "
            f"(SHA-256 {atlas_sha}), cell row {item['row'] + 1} "
            f"column {item['column'] + 1}; extracted PNG SHA-256 "
            f"{item['sha256']} recorded in docs/assets/{REPORT.name}"
        )
        pending.append({
            "stem": stem,
            "name": name,
            "subcategory": SUBCATEGORY,
            "keywords": list(keywords),
            "source": source,
            "license": LICENSE,
            "version": "1.0.0",
        })

    document["stickers"].extend(pending)
    CATALOG.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    return len(pending)


if __name__ == "__main__":
    print(f"registered {register()} person FX overlays")
