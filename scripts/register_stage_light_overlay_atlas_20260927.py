"""Register the generated stage-light overlay textures in the built-in sticker catalog."""

from __future__ import annotations

import hashlib
import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
STICKER_ROOT = ROOT / "src/cutvoke/assets/stickers"
ATLAS = ROOT / "docs/assets/concert-stage-light-atlas-20260927.png"
REPORT = ROOT / "docs/assets/concert-stage-light-atlas-20260927.extraction.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def register(*, update: bool = False) -> int:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if (report.get("generator") != "OpenAI ImageGen"
            or report.get("layout") != "4x4 equal square cells"
            or report.get("subcategory") != "舞台灯光"
            or len(report.get("items", [])) != 16):
        raise ValueError("unexpected stage-light atlas extraction report")
    atlas_sha = _sha(ATLAS)
    if report.get("sourceSha256") != atlas_sha:
        raise ValueError("atlas changed since extraction")

    catalog_entries = document.get("stickers", [])
    existing = {item["stem"]: item for item in catalog_entries}
    stems = [item["stem"] for item in report["items"]]
    if len(set(stems)) != 16:
        raise ValueError("duplicate sticker stems in extraction report")
    collisions = existing.keys() & set(stems)
    if collisions and not update:
        raise ValueError("sticker stems already registered: " + ", ".join(sorted(collisions)))

    pending = []
    for item in report["items"]:
        asset = STICKER_ROOT / f"{item['stem']}.png"
        if not asset.is_file() or _sha(asset) != item["sha256"]:
            raise ValueError(f"missing or changed extracted sticker: {item['stem']}")
        if item["stem"] in existing:
            prior = existing[item["stem"]]
            if (f"OpenAI ImageGen atlas {ATLAS.relative_to(ROOT).as_posix()} "
                    f"(SHA-256 {atlas_sha})") not in prior.get("source", ""):
                raise ValueError(f"refusing to update unrelated sticker: {item['stem']}")
        source = (
            f"OpenAI ImageGen atlas {ATLAS.relative_to(ROOT).as_posix()} "
            f"(SHA-256 {atlas_sha}), cell row {item['row'] + 1} column {item['column'] + 1}; "
            f"extracted PNG SHA-256 {item['sha256']} recorded in "
            f"{REPORT.relative_to(ROOT).as_posix()}"
        )
        pending.append({
            "stem": item["stem"],
            "name": item["name"],
            "subcategory": item["subcategory"],
            "keywords": item["keywords"],
            "source": source,
            "license": report["license"],
            "defaultScale": 1.35,
            "version": "1.1.0",
        })

    if collisions:
        replacements = {item["stem"]: item for item in pending}
        document["stickers"] = [replacements.get(item["stem"], item)
                                 for item in catalog_entries]
    else:
        document["stickers"].extend(pending)
    CATALOG.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(pending)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true",
                        help="update only entries already sourced from this exact atlas SHA-256")
    args = parser.parse_args()
    print(f"registered or updated {register(update=args.update)} stage-light overlay textures")
