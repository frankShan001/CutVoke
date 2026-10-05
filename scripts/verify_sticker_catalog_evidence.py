"""Verify stored machine and visual evidence for every bundled sticker.

This validates the current files against their records. It does not rerender
stickers or replace visual review on real camera footage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cutvoke.core.builtin_stickers import PREVIEW_ROOT, load_builtin_stickers  # noqa: E402


MACHINE_CHECKS = {"apply", "edit", "saveReload", "undo", "export"}
ALL_CHECKS = MACHINE_CHECKS | {
    "cover", "motionPreview", "visualDistinct",
}
MAX_PREVIEW_EXPORT_MEAN_RGB_DIFFERENCE = 5.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _repo_file(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(REPO):
        raise ValueError(f"evidence path escapes repository: {path}")
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


def verify() -> dict:
    stickers = load_builtin_stickers()
    failures: list[dict] = []
    records: list[dict] = []
    by_subcategory: Counter[str] = Counter()
    max_difference = 0.0

    for sticker in stickers:
        sticker_id = sticker["stickerId"]
        stem = sticker_id.rsplit(".", 1)[-1]
        qa_path = _repo_file(PREVIEW_ROOT / f"{stem}.qa.json")
        audit_path = _repo_file(PREVIEW_ROOT / f"{stem}.audit.json")
        visual_path = _repo_file(PREVIEW_ROOT / f"{stem}.visual.json")
        source_path = _repo_file(Path(sticker["path"]))
        preview_path = _repo_file(Path(sticker["previewPath"]))

        try:
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            visual = json.loads(visual_path.read_text(encoding="utf-8"))
            source_sha = _sha256(source_path)
            preview_sha = _sha256(preview_path)
            audit_sha = _sha256(audit_path)
            export_name = audit.get("auditExport", "")
            if not isinstance(export_name, str) or Path(export_name).name != export_name:
                raise ValueError("invalid audit export filename")
            export_path = _repo_file(PREVIEW_ROOT / export_name)
            export_sha = _sha256(export_path)
            differences = [float(value) for value in
                           audit.get("previewExportMeanRgbDifferences", [])]
            if differences:
                max_difference = max(max_difference, *differences)

            if not sticker.get("qualified") or sticker.get("status") != "approved":
                failures.append({
                    "stickerId": sticker_id,
                    "check": "catalog-qualification",
                    "detail": "sticker is not currently qualified",
                })
            if not all(sticker.get("checks", {}).get(key) is True for key in ALL_CHECKS):
                failures.append({
                    "stickerId": sticker_id,
                    "check": "catalog-checks",
                    "detail": "one or more required catalog checks are false",
                })
            if (qa.get("stickerId") != sticker_id
                    or qa.get("sourceSha256") != source_sha
                    or qa.get("previewSha256") != preview_sha):
                failures.append({
                    "stickerId": sticker_id,
                    "check": "preview-record",
                    "detail": "preview QA identity or source hashes differ",
                })
            if (audit.get("stickerId") != sticker_id
                    or audit.get("sourceSha256") != source_sha
                    or audit.get("previewSha256") != preview_sha
                    or not MACHINE_CHECKS.issubset(set(audit.get("checks", [])))
                    or audit.get("auditExportSha256") != export_sha):
                failures.append({
                    "stickerId": sticker_id,
                    "check": "machine-audit",
                    "detail": "audit identity, checks, or export hash differs",
                })
            if (not differences
                    or max(differences) >= MAX_PREVIEW_EXPORT_MEAN_RGB_DIFFERENCE):
                failures.append({
                    "stickerId": sticker_id,
                    "check": "preview-export",
                    "detail": differences,
                })
            if (visual.get("decision") != "approved"
                    or visual.get("stickerId") != sticker_id
                    or visual.get("version") != sticker["version"]
                    or visual.get("effectId") != sticker["effectId"]
                    or visual.get("params") != sticker["params"]
                    or len(visual.get("observation", "")) < 8
                    or not visual.get("reviewer")
                    or visual.get("sourceSha256") != source_sha
                    or visual.get("previewSha256") != preview_sha
                    or visual.get("auditSha256") != audit_sha):
                failures.append({
                    "stickerId": sticker_id,
                    "check": "visual-approval",
                    "detail": "approval identity, observation, or hash binding differs",
                })

            by_subcategory[sticker["subcategory"]] += 1
            records.append({
                "stickerId": sticker_id,
                "subcategory": sticker["subcategory"],
                "kind": sticker["kind"],
                "version": sticker["version"],
                "sourceSha256": source_sha,
                "previewSha256": preview_sha,
                "auditSha256": audit_sha,
                "auditExportSha256": export_sha,
                "previewExportMeanRgbDifferences": differences,
                "visualReviewer": visual.get("reviewer"),
                "visualReviewedAt": visual.get("reviewedAt"),
            })
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            failures.append({
                "stickerId": sticker_id,
                "check": "evidence-read",
                "detail": str(error),
            })

    failed_ids = {item["stickerId"] for item in failures}
    return {
        "schemaVersion": 1,
        "catalog": "src/cutvoke/core/builtin_stickers.json",
        "stickerCount": len(stickers),
        "qualifiedCount": sum(bool(item.get("qualified")) for item in stickers),
        "verifiedCount": len(records) - len(failed_ids),
        "failureCount": len(failures),
        "maximumPreviewExportMeanRgbDifference": round(max_difference, 3),
        "maximumAllowedPreviewExportMeanRgbDifference": MAX_PREVIEW_EXPORT_MEAN_RGB_DIFFERENCE,
        "subcategoryCounts": dict(sorted(by_subcategory.items())),
        "failures": failures,
        "stickers": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path,
                        help="also save the complete verification report to this path")
    args = parser.parse_args()
    report = verify()
    print(json.dumps({
        key: report[key]
        for key in (
            "stickerCount", "qualifiedCount", "verifiedCount", "failureCount",
            "maximumPreviewExportMeanRgbDifference", "subcategoryCounts", "failures",
        )
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
