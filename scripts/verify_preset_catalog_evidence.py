"""Verify catalog flags against retained per-preset audit and visual evidence.

This checks evidence integrity and recorded observations. It does not rerun the
renderer, approve visual quality, or replace review on real camera footage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "src/cutvoke/core"
MANIFEST = CORE / "builtin_presets.json"
REQUIRED_MACHINE_CHECKS = {"apply", "edit", "saveReload", "undo", "export"}
REQUIRED_CATALOG_CHECKS = REQUIRED_MACHINE_CHECKS | {
    "cover", "motionPreview", "visualDistinct",
}
sys.path.insert(0, str(REPO / "src"))

from cutvoke.core.preset_catalog import PresetCatalog  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _asset_path(reference: str) -> Path:
    path = (CORE / reference).resolve()
    if not path.is_relative_to(REPO):
        raise ValueError(f"evidence path escapes repository: {reference}")
    return path


def verify() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    specs = {spec.id: spec for spec in PresetCatalog.builtin().all()}
    failures: list[dict] = []
    presets: list[dict] = []

    for preset in manifest["presets"]:
        preset_id = preset["presetId"]
        spec = specs.get(preset_id)
        if spec is None:
            failures.append({
                "presetId": preset_id,
                "check": "catalog-spec",
                "detail": "missing from PresetCatalog",
            })
            continue

        if preset.get("status") != "approved":
            failures.append({
                "presetId": preset_id,
                "check": "catalog-status",
                "detail": preset.get("status"),
            })
        for check in REQUIRED_CATALOG_CHECKS:
            if preset.get("checks", {}).get(check) is not True:
                failures.append({
                    "presetId": preset_id,
                    "check": check,
                    "detail": "catalog flag not true",
                })

        try:
            evidence = preset["evidence"]
            audit_path = _asset_path(evidence["apply"])
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            audit_sha = _sha256(audit_path)
            if (audit.get("presetId") != preset_id
                    or not REQUIRED_MACHINE_CHECKS.issubset(set(audit.get("checks", [])))):
                failures.append({
                    "presetId": preset_id,
                    "check": "audit-record",
                    "detail": "ID or required machine checks do not match",
                })
            preview_difference = float(audit["previewExportMeanRgbDifference"])
            if preview_difference >= 5:
                failures.append({
                    "presetId": preset_id,
                    "check": "preview-export",
                    "detail": preview_difference,
                })

            export_path = audit_path.parent / audit["auditExport"]
            if (not export_path.is_file()
                    or _sha256(export_path) != audit.get("auditExportSha256")
                    or audit.get("auditExportSha256") != audit.get("exportSha256")):
                failures.append({
                    "presetId": preset_id,
                    "check": "export-sha256",
                    "detail": "export file is missing or its recorded hash differs",
                })

            visual = json.loads(
                _asset_path(evidence["visualDistinct"]).read_text(encoding="utf-8"))
            cover_path = _asset_path(preset["cover"])
            motion_path = _asset_path(preset["motionPreview"])
            if (visual.get("decision") != "approved"
                    or visual.get("presetId") != preset_id
                    or visual.get("version") != spec.version
                    or visual.get("effectId") != spec.effect_id
                    # Visual approvals record normalized effective values from
                    # PresetCatalog, while the JSON manifest may contain only
                    # overrides and omit inherited defaults.
                    or visual.get("params") != spec.params
                    or visual.get("subcategory") != spec.subcategory
                    or not visual.get("reviewer")
                    or not visual.get("reviewedAt")
                    or len(visual.get("observation", "")) < 8
                    or len(visual.get("method", "")) < 8):
                failures.append({
                    "presetId": preset_id,
                    "check": "visual-approval",
                    "detail": "approval identity, params, subcategory, or review record differs",
                })
            if (_sha256(cover_path) != visual.get("coverSha256")
                    or _sha256(motion_path) != visual.get("motionPreviewSha256")
                    or audit_sha != visual.get("auditSha256")):
                failures.append({
                    "presetId": preset_id,
                    "check": "visual-evidence-hashes",
                    "detail": "cover, motion preview, or audit hash differs",
                })
            presets.append({
                "presetId": preset_id,
                "family": preset.get("family"),
                "subcategory": preset.get("subcategory"),
                "effectiveVersion": spec.version,
                "previewExportMeanRgbDifference": preview_difference,
                "auditSha256": audit_sha,
            })
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            failures.append({
                "presetId": preset_id,
                "check": "evidence-read",
                "detail": str(error),
            })

    failed_ids = {item["presetId"] for item in failures}
    return {
        "schemaVersion": 1,
        "manifest": "src/cutvoke/core/builtin_presets.json",
        "manifestSha256": _sha256(MANIFEST),
        "presetCount": len(manifest["presets"]),
        "checksPerPreset": [
            "apply", "edit", "saveReload", "undo", "export",
            "previewExportFrameMatch", "visualApprovalHashBinding",
            "visualReviewFieldsAndPresetParameters",
        ],
        "verifiedCount": len(presets) - len(failed_ids),
        "failureCount": len(failures),
        "failures": failures,
        "presets": presets,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path,
                        help="also save the complete verification report to this path")
    args = parser.parse_args()
    report = verify()
    print(json.dumps({
        key: report[key]
        for key in ("presetCount", "verifiedCount", "failureCount", "failures")
    }, ensure_ascii=False, indent=2))
    if args.json_out:
        output = args.json_out.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        print(f"report={output}")
    return 1 if report["failureCount"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
