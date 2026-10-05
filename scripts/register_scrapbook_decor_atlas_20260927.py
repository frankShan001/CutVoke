"""Register extracted ImageGen scrapbook-decor stickers in the built-in catalog."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
STICKER_ROOT = ROOT / "src/cutvoke/assets/stickers"
ATLAS = ROOT / "docs/assets/scrapbook-decor-atlas-20260927-v2.png"
REPORT = ROOT / "docs/assets/scrapbook-decor-atlas-20260927-v2.extraction.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def register() -> int:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if (report.get("generator") != "OpenAI ImageGen"
            or report.get("layout") != "4x4 equal square cells"
            or report.get("subcategory") != "装饰"
            or len(report.get("items", [])) != 16):
        raise ValueError("unexpected scrapbook-decor extraction report")
    atlas_sha = _sha(ATLAS)
    if report.get("sourceSha256") != atlas_sha:
        raise ValueError("atlas changed after extraction")

    existing = {item["stem"] for item in document.get("stickers", [])}
    stems = [item["stem"] for item in report["items"]]
    if len(set(stems)) != 16 or existing.intersection(stems):
        raise ValueError("duplicate sticker stems in catalog or extraction report")

    pending = []
    for item in report["items"]:
        asset = STICKER_ROOT / f"{item['stem']}.png"
        if not asset.is_file() or _sha(asset) != item["sha256"]:
            raise ValueError(f"missing or changed extracted sticker: {item['stem']}")
        source = (
            f"OpenAI ImageGen atlas docs/assets/{ATLAS.name} "
            f"(SHA-256 {atlas_sha}), cell row {item['row'] + 1} "
            f"column {item['column'] + 1}; extracted PNG SHA-256 "
            f"{item['sha256']} recorded in docs/assets/{REPORT.name}"
        )
        pending.append({
            "stem": item["stem"],
            "name": item["name"],
            "subcategory": item["subcategory"],
            "keywords": item["keywords"],
            "source": source,
            "license": report["license"],
            "defaultScale": 0.28,
            "version": "1.0.0",
        })

    document["stickers"].extend(pending)
    CATALOG.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    return len(pending)


if __name__ == "__main__":
    print(f"registered {register()} scrapbook-decor stickers")
