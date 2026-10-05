"""Register extracted product-ad overlays in the built-in sticker catalog."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
STICKER_ROOT = ROOT / "src/cutvoke/assets/stickers"
ATLAS = ROOT / "docs/assets/product-promo-overlay-atlas-20260927.png"
REPORT = ROOT / "docs/assets/product-promo-overlay-atlas-20260927.extraction.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def register() -> int:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if (report.get("generator") != "OpenAI ImageGen"
            or report.get("subcategory") != "产品广告"
            or len(report.get("items", [])) != 16):
        raise ValueError("unexpected product-promo atlas extraction report")
    atlas_sha = _sha(ATLAS)
    if report.get("sourceSha256") != atlas_sha:
        raise ValueError("atlas changed since extraction")

    existing = {item["stem"] for item in document.get("stickers", [])}
    by_stem = {item["stem"]: item for item in report["items"]}
    if len(by_stem) != 16:
        raise ValueError("atlas extraction report has duplicate stems")
    pending = []
    for item in report["items"]:
        stem = item["stem"]
        if stem in existing:
            raise ValueError(f"sticker already registered: {stem}")
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
            "name": item["name"],
            "subcategory": item["subcategory"],
            "keywords": item["keywords"],
            "source": source,
            "license": report["license"],
            "defaultScale": 0.86,
            "version": "1.0.0",
        })

    document["stickers"].extend(pending)
    CATALOG.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    return len(pending)


if __name__ == "__main__":
    print(f"registered {register()} product-promo overlay stickers")
