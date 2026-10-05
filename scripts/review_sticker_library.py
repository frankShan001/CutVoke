"""Prepare and apply hash-pinned manual visual decisions for bundled stickers.

Neither a rendered sample nor a machine audit decides visual quality. The
reviewer must inspect artwork and its composited preview and write a specific
observation before `apply` can mark an item qualified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from cutvoke.core.builtin_stickers import PREVIEW_ROOT, load_builtin_stickers


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(path: Path, ids: list[str]) -> int:
    catalog = {item["stickerId"]: item for item in load_builtin_stickers()}
    selected = ids or list(catalog)
    unknown = set(selected) - catalog.keys()
    if unknown or len(selected) != len(set(selected)):
        raise ValueError(f"unknown or duplicate sticker ids: {sorted(unknown)}")
    decisions = []
    for sticker_id in selected:
        item = catalog[sticker_id]
        decisions.append({
            "stickerId": sticker_id, "decision": "pending",
            "sourceSha256": _sha(Path(item["path"])),
            "previewSha256": _sha(Path(item["previewPath"])),
            "observation": "",
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schemaVersion": 1, "reviewer": "", "reviewedAt": "",
        "method": "Manual comparison of transparent artwork and actual composited preview",
        "decisions": decisions,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(decisions)


def apply(path: Path) -> int:
    review = json.loads(path.read_text(encoding="utf-8"))
    if (review.get("schemaVersion") != 1 or not review.get("reviewer") or
            not review.get("reviewedAt") or not review.get("method")):
        raise ValueError("review requires schemaVersion, reviewer, reviewedAt and method")
    decisions = review.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise ValueError("review requires nonempty decisions")
    catalog = {item["stickerId"]: item for item in load_builtin_stickers()}
    prepared = []
    seen = set()
    for decision in decisions:
        sticker_id = decision["stickerId"]
        if sticker_id in seen or sticker_id not in catalog:
            raise ValueError(f"duplicate or unknown sticker: {sticker_id}")
        seen.add(sticker_id)
        item = catalog[sticker_id]
        if item["status"] != "candidate" or decision.get("decision") != "approved":
            raise ValueError(f"only reviewed candidates may be approved: {sticker_id}")
        if len(decision.get("observation", "")) < 8:
            raise ValueError(f"specific visual observation required: {sticker_id}")
        if not all(item["checks"][key] for key in
                   ("cover", "motionPreview", "apply", "edit", "saveReload", "undo", "export")):
            raise ValueError(f"machine evidence incomplete: {sticker_id}")
        source_sha = _sha(Path(item["path"]))
        preview_sha = _sha(Path(item["previewPath"]))
        if (decision.get("sourceSha256") != source_sha or
                decision.get("previewSha256") != preview_sha):
            raise ValueError(f"reviewed media changed: {sticker_id}")
        stem = sticker_id.rsplit(".", 1)[-1]
        audit = PREVIEW_ROOT / f"{stem}.audit.json"
        evidence = {
            "schemaVersion": 1, "decision": "approved", "stickerId": sticker_id,
            "version": item["version"], "effectId": item["effectId"],
            "params": item["params"], "reviewer": review["reviewer"],
            "reviewedAt": review["reviewedAt"], "method": review["method"],
            "observation": decision["observation"],
            "sourceSha256": source_sha, "previewSha256": preview_sha,
            "auditSha256": _sha(audit),
        }
        prepared.append((PREVIEW_ROOT / f"{stem}.visual.json", evidence))
    for output, evidence in prepared:
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    qualified = {item["stickerId"] for item in load_builtin_stickers() if item["qualified"]}
    if not seen <= qualified:
        raise RuntimeError("one or more approved stickers failed the qualified gate")
    return len(prepared)


def refresh_audit_bindings(ids: list[str]) -> int:
    """Rebind unchanged human artwork reviews to new machine audit evidence.

    This is only valid when the approved sticker image and composited preview
    remain byte-for-byte identical. The new audit must include the current
    fade-in/fade-out checks before its hash can replace the old audit hash.
    """
    catalog = {item["stickerId"]: item for item in load_builtin_stickers()}
    selected = ids or list(catalog)
    if len(selected) != len(set(selected)) or set(selected) - catalog.keys():
        raise ValueError(f"unknown or duplicate sticker ids: {sorted(set(selected) - catalog.keys())}")
    prepared = []
    for sticker_id in selected:
        item = catalog[sticker_id]
        stem = sticker_id.rsplit(".", 1)[-1]
        visual_path = PREVIEW_ROOT / f"{stem}.visual.json"
        audit_path = PREVIEW_ROOT / f"{stem}.audit.json"
        if not visual_path.is_file():
            continue
        review = json.loads(visual_path.read_text(encoding="utf-8"))
        if review.get("decision") != "approved":
            continue
        if (review.get("schemaVersion") != 1 or review.get("stickerId") != sticker_id or
                review.get("version") != item["version"] or
                review.get("effectId") != item["effectId"] or
                review.get("params") != item["params"]):
            raise ValueError(f"visual approval identity changed: {sticker_id}")
        if (review.get("sourceSha256") != _sha(Path(item["path"])) or
                review.get("previewSha256") != _sha(Path(item["previewPath"]))):
            raise ValueError(f"approved artwork or preview changed: {sticker_id}")
        if not audit_path.is_file():
            raise ValueError(f"machine audit missing: {sticker_id}")
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        checks = set(audit.get("checks", []))
        fade_comparison = audit.get("fadeVisibilityComparison", {})
        entry = fade_comparison.get("entry", {})
        exit_ = fade_comparison.get("exit", {})
        fades_verified = (
            entry.get("withFade", 0) < entry.get("withoutFade", 0) and
            exit_.get("withFade", 0) < exit_.get("withoutFade", 0))
        if not fade_comparison and item["kind"] == "static":
            # Static artwork is time-invariant, so the original absolute
            # entry/steady and steady/exit comparison remains valid evidence.
            visibility = audit.get("animationVisibilityPixels", {})
            fades_verified = (
                visibility.get("entry", 0) < visibility.get("steady_entry", 0) and
                visibility.get("exit", 0) < visibility.get("steady_exit", 0))
        if (audit.get("schemaVersion") != 1 or audit.get("stickerId") != sticker_id or
                audit.get("version") != item["version"] or
                audit.get("sourceSha256") != review["sourceSha256"] or
                audit.get("previewSha256") != review["previewSha256"] or
                not {"apply", "edit", "saveReload", "undo", "export"} <= checks or
                not {"cutvoke.anim.fadeIn", "cutvoke.anim.fadeOut"} <=
                    set(audit.get("animationIds", [])) or
                audit.get("animationDurationSeconds") != 0.4 or
                not fades_verified):
            raise ValueError(f"complete fade animation audit required: {sticker_id}")
        export_name = audit.get("auditExport")
        if not isinstance(export_name, str) or Path(export_name).name != export_name:
            raise ValueError(f"invalid audit export path: {sticker_id}")
        export_path = PREVIEW_ROOT / export_name
        if (not export_path.is_file() or
                audit.get("auditExportSha256") != _sha(export_path)):
            raise ValueError(f"audit export hash mismatch: {sticker_id}")
        updated = dict(review)
        updated["auditSha256"] = _sha(audit_path)
        prepared.append((visual_path, updated))
    for visual_path, review in prepared:
        visual_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    qualified = {item["stickerId"] for item in load_builtin_stickers() if item["qualified"]}
    refreshed_ids = {json.loads(path.read_text(encoding="utf-8"))["stickerId"]
                     for path, _ in prepared}
    if not refreshed_ids <= qualified:
        raise RuntimeError("refreshed visual approvals failed the qualified gate")
    return len(prepared)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    draft = sub.add_parser("prepare")
    draft.add_argument("output", type=Path)
    draft.add_argument("--sticker-id", action="append", default=[])
    approve = sub.add_parser("apply")
    approve.add_argument("review", type=Path)
    refresh = sub.add_parser("refresh-audits")
    refresh.add_argument("--sticker-id", action="append", default=[])
    args = parser.parse_args()
    if args.command == "prepare":
        print(f"prepared {prepare(args.output, args.sticker_id)} pending decisions")
    elif args.command == "apply":
        print(f"approved {apply(args.review)} reviewed stickers")
    else:
        print(f"refreshed {refresh_audit_bindings(args.sticker_id)} existing visual approvals")
