"""Register extracted ImageGen ink-and-gold overlays in the sticker library."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
STICKER_ROOT = ROOT / "src/cutvoke/assets/stickers"
ATLAS = ROOT / "docs/assets/east-asian-ink-motion-overlay-atlas-20260926.png"
REPORT = ROOT / "docs/assets/east-asian-ink-motion-overlay-atlas-20260926.extraction.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def register() -> int:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if (report.get("generator") != "OpenAI ImageGen" or report.get("layout") != "4x4"
            or len(report.get("items", [])) != 16):
        raise ValueError("unexpected East Asian ink atlas extraction report")
    atlas_sha = _sha(ATLAS)
    if report.get("sourceSha256") != atlas_sha:
        raise ValueError("atlas changed since extraction")

    existing = {item["stem"] for item in document.get("stickers", [])}
    pending = []
    for item in report["items"]:
        stem = item["stem"]
        if stem in existing:
            raise ValueError(f"sticker already registered: {stem}")
        asset = STICKER_ROOT / f"{stem}.png"
        if not asset.is_file() or _sha(asset) != item["sha256"]:
            raise ValueError(f"missing or changed extracted sticker: {stem}")
        source = (
            f"OpenAI ImageGen atlas docs/assets/{ATLAS.name} (SHA-256 {atlas_sha}), "
            f"cell row {item['row'] + 1} column {item['column'] + 1}; "
            f"extracted PNG SHA-256 {item['sha256']} recorded in docs/assets/{REPORT.name}"
        )
        pending.append({
            "stem": stem, "name": item["name"], "subcategory": item["subcategory"],
            "keywords": item["keywords"], "source": source,
            "license": report["license"], "defaultScale": 1.1, "version": "1.0.0",
        })

    document["stickers"].extend(pending)
    CATALOG.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    return len(pending)


if __name__ == "__main__":
    print(f"registered {register()} East Asian ink overlays")
