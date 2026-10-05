"""Record explicit human visual decisions after machine preset audits.

The input pins the reviewed MP4 SHA-256 so regenerating media requires a new
review. This script records a decision; it does not decide artistic quality.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cutvoke.core.preset_catalog import CHECKS, PresetCatalog  # noqa: E402


CATALOG = REPO / "src/cutvoke/core/builtin_presets.json"
MACHINE_CHECKS = tuple(check for check in CHECKS if check != "visualDistinct")


def _path(spec, reference: str) -> Path:
    path = Path(reference)
    return path if path.is_absolute() else spec.asset_root / path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply(review_path: Path) -> int:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if review.get("schemaVersion") != 1 or not review.get("reviewer") or not review.get("reviewedAt"):
        raise ValueError("review needs schemaVersion, reviewer and reviewedAt")
    decisions = review.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise ValueError("review needs nonempty decisions")
    catalog = {spec.id: spec for spec in PresetCatalog.builtin().all()}
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = {entry["presetId"]: entry for entry in document["presets"]}
    prepared = []
    seen = set()
    for decision in decisions:
        preset_id = decision["presetId"]
        if preset_id in seen:
            raise ValueError(f"duplicate review: {preset_id}")
        seen.add(preset_id)
        spec = catalog[preset_id]
        if spec.status != "candidate" or decision.get("decision") != "approved":
            raise ValueError(f"only reviewed candidates may be approved: {preset_id}")
        if not decision.get("observation") or len(decision["observation"]) < 8:
            raise ValueError(f"missing visual observation: {preset_id}")
        if any(not spec.checks[key] or not spec._exists(spec.evidence[key])
               for key in MACHINE_CHECKS):
            raise ValueError(f"machine evidence incomplete: {preset_id}")
        current_sha = _sha(_path(spec, spec.motion_preview))
        if decision.get("motionPreviewSha256") != current_sha:
            raise ValueError(f"reviewed video changed: {preset_id}")
        stem = spec.id.removeprefix("cutvoke.preset.").replace(".", "-")
        output = REPO / "src/cutvoke/assets/preset_previews" / f"{stem}-v{spec.version.replace('.', '-')}.visual.json"
        evidence = {
            "schemaVersion": 1,
            "decision": "approved",
            "presetId": preset_id,
            "version": spec.version,
            "effectId": spec.effect_id,
            "params": spec.params,
            "subcategory": spec.subcategory,
            "reviewer": review["reviewer"],
            "reviewedAt": review["reviewedAt"],
            "observation": decision["observation"],
            "method": review.get("method", "manual motion preview review"),
            "coverSha256": _sha(_path(spec, spec.cover)),
            "motionPreviewSha256": current_sha,
            "auditSha256": _sha(_path(spec, spec.evidence["export"])),
        }
        prepared.append((entries[preset_id], output, evidence))
    for entry, output, evidence in prepared:
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        entry.setdefault("checks", {})["visualDistinct"] = True
        entry.setdefault("evidence", {})["visualDistinct"] = (
            "../assets/preset_previews/" + output.name)
        entry["status"] = "approved"
    CATALOG.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    qualified = {spec.id for spec in PresetCatalog.builtin().all() if spec.qualified}
    if not all(evidence["presetId"] in qualified for _, _, evidence in prepared):
        raise RuntimeError("reviewed presets did not pass qualified gate")
    return len(prepared)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path)
    print(f"approved {apply(parser.parse_args().review)} reviewed presets")
