"""Bind the completed visual review to the generated sticker artwork and media."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from extract_video_annotation_guide_atlas_20260927 import ITEMS, ROOT


STICKERS = ROOT / "src/cutvoke/assets/stickers"
PREVIEWS = ROOT / "src/cutvoke/assets/sticker_previews"
CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
REVIEW = ROOT / "docs/assets/video-annotation-guide-sticker-atlas-20260927-visual-review.json"
REVIEWER = "Codex"
METHOD = ("Reviewed each transparent PNG on light and dark contact-sheet backgrounds, then reviewed "
          "the actual editor-rendered MP4 contact sheet; checked silhouette, clipping, alpha edges, "
          "readability, and whether each item serves a clear guidance purpose.")
OBSERVATIONS = {
    "guide_pointer_arrow": "Curved cyan pointer reads clearly and keeps its full rounded tail and arrowhead.",
    "guide_measure_double_arrow": "Coral arrowheads point in both directions, making comparison or span clear.",
    "guide_emphasis_circle": "Open yellow loop leaves room for the target and remains separated from its cell edges.",
    "guide_selection_box": "Purple dashed selection frame and four handles remain legible over both test backgrounds.",
    "guide_focus_brackets": "Four mint corner brackets frame a subject without covering the center.",
    "guide_highlight_stroke": "Coral marker stroke has a clean tapered profile and remains readable at preview size.",
    "guide_dotted_route": "Cyan path nodes connect continuously and remain distinct when composited on video.",
    "guide_zoom_plus": "Blue magnifier silhouette and plus mark are both clear at the editor preview size.",
    "guide_spotlight": "Amber spotlight cone retains its circular source and base; the soft fill suits dark footage.",
    "guide_target_reticle": "Coral reticle is symmetric, centered, and clearly marks a focal point.",
    "guide_check_badge": "Green check and tilted badge are visually separable from the transparent background.",
    "guide_comment_bubble": "Purple bubble has a clean tail and three clear dots without generated lettering.",
    "guide_branch_nodes": "Blue connector joins three hollow nodes with no broken branch or clipped endpoint.",
    "guide_burst_highlight": "Yellow burst has a clear empty center and stays fully inside the extracted cell.",
    "guide_crop_rotate": "Cyan crop corners and rotate handle read as one complete editing guide mark.",
    "guide_split_compare": "Magenta and orange chevrons face opposite sides of a complete divider.",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply() -> int:
    report = json.loads((ROOT / "docs/assets/video-annotation-guide-sticker-atlas-20260927.extraction.json")
                        .read_text(encoding="utf-8"))
    if REVIEW.exists():
        raise FileExistsError(f"refusing to overwrite visual review: {REVIEW}")
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = {f"cutvoke.sticker.{item['stem']}": item for item in catalog["stickers"]}
    reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sidecars = []
    decisions = []
    for item in ITEMS:
        stem, name, _keywords = item
        sticker_id = f"cutvoke.sticker.{stem}"
        entry = entries[sticker_id]
        source = STICKERS / f"{stem}.png"
        preview = PREVIEWS / f"{stem}.mp4"
        qa_path = PREVIEWS / f"{stem}.qa.json"
        audit_path = PREVIEWS / f"{stem}.audit.json"
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        source_sha, preview_sha, audit_sha = _sha(source), _sha(preview), _sha(audit_path)
        if (qa.get("sourceSha256") != source_sha or qa.get("previewSha256") != preview_sha
                or audit.get("sourceSha256") != source_sha
                or audit.get("previewSha256") != preview_sha
                or not {"apply", "edit", "saveReload", "undo", "export"}
                .issubset(set(audit.get("checks", [])))):
            raise ValueError(f"machine evidence is incomplete or stale for {sticker_id}")
        differences = audit.get("previewExportMeanRgbDifferences", [])
        if (not differences or max(differences) >= 5.0 or audit.get("visiblePixels", 0) < 100
                or audit.get("editedPixels", 0) < 100):
            raise ValueError(f"sticker media QA is below the review threshold: {sticker_id}")
        sidecar = {
            "schemaVersion": 1,
            "decision": "approved",
            "stickerId": sticker_id,
            "version": entry["version"],
            "effectId": "",
            "params": {},
            "reviewer": REVIEWER,
            "reviewedAt": reviewed_at,
            "observation": OBSERVATIONS[stem],
            "method": METHOD,
            "sourceSha256": source_sha,
            "previewSha256": preview_sha,
            "auditSha256": audit_sha,
        }
        sidecars.append((PREVIEWS / f"{stem}.visual.json", sidecar))
        decisions.append({"stickerId": sticker_id, "name": name, **sidecar})
    for path, sidecar in sidecars:
        if path.exists():
            raise FileExistsError(f"refusing to overwrite visual approval: {path}")
    REVIEW.write_text(json.dumps({
        "schemaVersion": 1,
        "reviewer": REVIEWER,
        "reviewedAt": reviewed_at,
        "method": METHOD,
        "sourceSha256": report["sourceSha256"],
        "renderedContactSheet": "docs/assets/video-annotation-guide-sticker-atlas-20260927-rendered-review.png",
        "decisions": decisions,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for path, sidecar in sidecars:
        path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return len(sidecars)


if __name__ == "__main__":
    print(f"visually reviewed and hash-bound {apply()} video-guidance stickers")
