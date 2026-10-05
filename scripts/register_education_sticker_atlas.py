"""Register extracted ImageGen atlas cells in the built-in sticker catalog."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
STICKER_ROOT = ROOT / "src/cutvoke/assets/stickers"
ATLAS = ROOT / "docs/assets/education-science-sticker-atlas-20260925-v2.png"
REPORT = ROOT / "docs/assets/education-science-sticker-atlas-20260925-v2.extraction.json"
LICENSE = "Project-internal original (AI-generated); not separately licensed for redistribution"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def register() -> int:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if report.get("generator") != "OpenAI ImageGen" or report.get("layout") != "4x4":
        raise ValueError("unexpected atlas extraction report")
    atlas_sha = _sha(ATLAS)
    if report.get("sourceSha256") != atlas_sha:
        raise ValueError("atlas changed since extraction")

    existing = {item["stem"] for item in document.get("stickers", [])}
    pending = []
    for item in report.get("items", []):
        stem = item["stem"]
        if stem in existing:
            raise ValueError(f"sticker already registered: {stem}")
        asset = STICKER_ROOT / f"{stem}.png"
        if not asset.is_file() or _sha(asset) != item["sha256"]:
            raise ValueError(f"missing or changed extracted sticker: {stem}")
        row, column = item["row"] + 1, item["column"] + 1
        source = (
            f"OpenAI ImageGen atlas docs/assets/{ATLAS.name} "
            f"(SHA-256 {atlas_sha}), cell row {row} column {column}; "
            f"extracted PNG SHA-256 {item['sha256']} recorded in "
            f"docs/assets/{REPORT.name}"
        )
        pending.append({
            "stem": stem,
            "name": item["name"],
            "subcategory": item["subcategory"],
            "keywords": item["keywords"],
            "source": source,
            "license": LICENSE,
            "version": "1.0.0",
        })

    if len(pending) != 16:
        raise ValueError(f"expected 16 atlas cells, got {len(pending)}")
    document["stickers"].extend(pending)
    CATALOG.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    return len(pending)


if __name__ == "__main__":
    print(f"registered {register()} educational science stickers")
