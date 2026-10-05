"""Register the extracted ImageGen energy-light overlays in the sticker catalog."""

from __future__ import annotations

import hashlib
import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
STICKER_ROOT = ROOT / "src/cutvoke/assets/stickers"
ATLAS = ROOT / "docs/assets/energy-light-overlay-atlas-20260927.png"
REPORT = ROOT / "docs/assets/energy-light-overlay-atlas-20260927.extraction.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def register(*, update: bool = False) -> int:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if (report.get("generator") != "OpenAI ImageGen"
            or report.get("subcategory") != "能量光效"
            or len(report.get("items", [])) != 16):
        raise ValueError("unexpected energy-light atlas extraction report")
    atlas_sha = _sha(ATLAS)
    if report.get("sourceSha256") != atlas_sha:
        raise ValueError("atlas changed since extraction")

    entries = document.get("stickers", [])
    existing = {item["stem"]: item for item in entries}
    stems = {item["stem"] for item in report["items"]}
    collisions = set(existing) & stems
    if collisions and not update:
        raise ValueError("sticker stems already registered: " + ", ".join(sorted(collisions)))
    pending = []
    for item in report["items"]:
        stem = item["stem"]
        asset = STICKER_ROOT / f"{stem}.png"
        if not asset.is_file() or _sha(asset) != item["sha256"]:
            raise ValueError(f"missing or changed extracted sticker: {stem}")
        if stem in existing:
            expected_source = f"OpenAI ImageGen atlas docs/assets/{ATLAS.name} (SHA-256 {atlas_sha})"
            if expected_source not in existing[stem].get("source", ""):
                raise ValueError(f"refusing to update unrelated sticker: {stem}")
        pending.append({
            "stem": stem,
            "name": item["name"],
            "subcategory": item["subcategory"],
            "keywords": item["keywords"],
            "source": (
                f"OpenAI ImageGen atlas docs/assets/{ATLAS.name} "
                f"(SHA-256 {atlas_sha}), cell row {item['row'] + 1} "
                f"column {item['column'] + 1}; extracted PNG SHA-256 "
                f"{item['sha256']} recorded in docs/assets/{REPORT.name}"
            ),
            "license": report["license"],
            "defaultScale": 0.92,
            "version": "1.0.0",
        })

    if len({item["stem"] for item in pending}) != 16:
        raise ValueError("atlas report contains duplicate sticker stems")
    if collisions:
        replacements = {item["stem"]: item for item in pending}
        document["stickers"] = [replacements.get(item["stem"], item)
                                 for item in entries]
    else:
        document["stickers"].extend(pending)
    CATALOG.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    return len(pending)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true",
                        help="update only entries already sourced from this exact atlas hash")
    args = parser.parse_args()
    print(f"registered or updated {register(update=args.update)} energy-light overlays")
