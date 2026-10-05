"""Versioned first-party sticker catalog backed by bundled transparent PNG files."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from .effects import default_registry
from .preset_catalog import CHECKS


STICKER_ROOT = Path(__file__).resolve().parent.parent / "assets" / "stickers"
CATALOG_PATH = Path(__file__).with_name("builtin_stickers.json")
PREVIEW_ROOT = STICKER_ROOT.parent / "sticker_previews"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _quality(item: dict[str, Any], preview_root: Path = PREVIEW_ROOT) -> None:
    """Treat a sticker as reviewed only while its artwork and evidence match."""
    stem = item["stickerId"].rsplit(".", 1)[-1]
    source = Path(item["path"])
    preview = Path(item["previewPath"])
    qa_path = preview_root / f"{stem}.qa.json"
    audit_path = preview_root / f"{stem}.audit.json"
    export_path = preview_root / f"{stem}.audit.mp4"
    review_path = preview_root / f"{stem}.visual.json"
    source_hash, preview_hash = _sha(source), _sha(preview)
    qa, audit, review = (_read_json(path) for path in (qa_path, audit_path, review_path))
    media_ok = bool(source_hash and preview_hash and
                    qa.get("schemaVersion") == 1 and
                    qa.get("stickerId") == item["stickerId"] and
                    qa.get("version") == item["version"] and
                    qa.get("kind") == item["kind"] and
                    qa.get("sourceSha256") == source_hash and
                    qa.get("previewSha256") == preview_hash and
                    qa.get("visiblePixels", 0) >= 100 and
                    (item["kind"] == "static" or qa.get("motionVisible") is True))
    export_hash = _sha(export_path)
    machine_checks = ("apply", "edit", "saveReload", "undo", "export")
    machine_ok = bool(media_ok and export_hash and
                      audit.get("schemaVersion") == 1 and
                      audit.get("stickerId") == item["stickerId"] and
                      audit.get("version") == item["version"] and
                      audit.get("sourceSha256") == source_hash and
                      audit.get("previewSha256") == preview_hash and
                      audit.get("auditExportSha256") == export_hash and
                      all(key in audit.get("checks", []) for key in machine_checks))
    review_ok = bool(machine_ok and
                     review.get("schemaVersion") == 1 and
                     review.get("decision") == "approved" and
                     review.get("stickerId") == item["stickerId"] and
                     review.get("version") == item["version"] and
                     review.get("effectId") == item["effectId"] and
                     review.get("params") == item["params"] and
                     review.get("sourceSha256") == source_hash and
                     review.get("previewSha256") == preview_hash and
                     review.get("auditSha256") == _sha(audit_path))
    checks = {key: False for key in CHECKS}
    checks["cover"] = checks["motionPreview"] = media_ok
    for key in machine_checks:
        checks[key] = machine_ok
    checks["visualDistinct"] = review_ok
    item["checks"] = checks
    item["status"] = "approved" if review_ok else "candidate"
    item["qualified"] = bool(item["license"] and all(checks.values()))
    item["evidence"] = {"media": qa_path.name, "audit": audit_path.name,
                        "visual": review_path.name}


def load_builtin_stickers(*, include_quality: bool = True,
                          package_root: Path | None = None) -> list[dict[str, Any]]:
    root = (package_root or Path(__file__).resolve().parent.parent).resolve()
    sticker_root = root / "assets" / "stickers"
    preview_root = root / "assets" / "sticker_previews"
    catalog_path = root / "core" / "builtin_stickers.json"
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != 1 or not isinstance(data.get("stickers"), list):
        raise ValueError("invalid built-in sticker catalog")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in data["stickers"]:
        stem = entry.get("stem")
        if (not isinstance(stem, str) or not stem.isascii() or
                not stem.replace("_", "").isalnum() or stem in seen):
            raise ValueError(f"invalid or duplicate sticker stem: {stem!r}")
        seen.add(stem)
        path = sticker_root / f"{stem}.png"
        entries.append({
            "stickerId": f"cutvoke.sticker.{stem}",
            "assetId": f"builtin_sticker_{stem}",
            "name": str(entry["name"]),
            "subcategory": str(entry["subcategory"]),
            "keywords": [str(word) for word in entry.get("keywords", [])],
            "version": str(entry.get("version", data["version"])),
            "license": str(entry.get("license", data["license"])),
            "source": str(entry.get("source", "")),
            **({"defaultScale": float(entry["defaultScale"])}
               if "defaultScale" in entry else {}),
            "kind": "static",
            "path": str(path),
            "effectId": "",
            "params": {},
            "previewPath": str(preview_root / f"{stem}.mp4"),
        })
    unlisted = {p.stem for p in sticker_root.glob("*.png")} - seen
    if unlisted:
        raise ValueError(f"unlisted built-in stickers: {sorted(unlisted)}")
    registry = default_registry()
    for variant in data.get("motionVariants", []):
        variant_id = variant.get("variantId")
        stem = variant.get("stem")
        if (not isinstance(variant_id, str) or not variant_id.isascii() or
                not variant_id.replace("_", "").isalnum() or
                variant_id in seen or stem not in seen):
            raise ValueError(f"invalid sticker motion variant: {variant_id!r}")
        seen.add(variant_id)
        effect_id = str(variant.get("effectId", ""))
        effect = registry.find(effect_id)
        if effect is None or effect.category != "animation" or "image" not in effect.applies_to:
            raise ValueError(f"invalid sticker animation: {effect_id!r}")
        params = registry.validate_params(effect_id, variant.get("params", {}))
        entries.append({
            "stickerId": f"cutvoke.sticker.{variant_id}",
            "assetId": f"builtin_sticker_{stem}",
            "name": str(variant["name"]),
            "subcategory": str(variant["subcategory"]),
            "keywords": [str(word) for word in variant.get("keywords", [])],
            "version": str(variant.get("version", data["version"])),
            "license": str(variant.get("license", data["license"])),
            "source": str(variant.get("source", "")),
            **({"defaultScale": float(variant["defaultScale"])}
               if "defaultScale" in variant else {}),
            "kind": "dynamic",
            "path": str(sticker_root / f"{stem}.png"),
            "effectId": effect_id,
            "params": params,
            "previewPath": str(preview_root / f"{variant_id}.mp4"),
        })
    if include_quality:
        for item in entries:
            _quality(item, preview_root)
    return entries
