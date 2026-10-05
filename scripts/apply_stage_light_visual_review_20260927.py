"""Bind reviewed stage-light artwork to its current preview and export evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from extract_stage_light_overlay_atlas_20260927 import ROOT


CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
PREVIEWS = ROOT / "src/cutvoke/assets/sticker_previews"
EXTRACTION = ROOT / "docs/assets/concert-stage-light-atlas-20260927.extraction.json"
RENDERED = ROOT / "docs/assets/concert-stage-light-rendered-review-20260927.json"
REVIEW = ROOT / "docs/assets/concert-stage-light-visual-review-20260927.json"
REVIEWER = "Codex"
METHOD = (
    "Reviewed each cropped transparent PNG on dark and light checkerboard contacts, then reviewed "
    "the corresponding RenderService preview on the editor's dark-to-light canvas. Checked distinct "
    "shapes, cell spill, clipping, alpha edges, contrast, and whether each item is useful as a stage-light overlay."
)
OBSERVATIONS = {
    "stage_light_laser_fan_cyan": "Cyan and violet rays converge at one clean source and remain separated at the fan edge.",
    "stage_light_spotlight_pair_amber": "The two amber cones read clearly after removal of the detached purple shard.",
    "stage_light_moving_head_magenta": "The magenta elliptical sweep has a complete ring and a distinct beam source.",
    "stage_light_beam_columns_blue": "Four blue columns retain clear spacing and soft, even falloff.",
    "stage_light_prism_flare_rainbow": "The white flare core and rainbow arc are both visible without a background panel.",
    "stage_light_crossing_lasers": "Cyan and magenta beams cross cleanly and keep distinct color edges.",
    "stage_light_anamorphic_flare_pearl": "The pearl-white horizontal flare and small lens glints stay legible on the dark half.",
    "stage_light_starburst_ultraviolet": "The violet starburst is centered and its rays remain evenly spaced.",
    "stage_light_haze_ribbons_amber_rose": "The amber and rose ribbons remain separate, soft, and visible at preview size.",
    "stage_light_tunnel_cyan": "The cyan tunnel rings remain concentric and taper toward the center.",
    "stage_light_disco_facets_emerald_violet": "Emerald and violet reflected facets form a distinct clustered light pattern.",
    "stage_light_sweep_beams_red_blue": "The red and blue fans stay separated around the central crossing.",
    "stage_light_spotlight_bloom_gold": "The gold spotlight retains its source point and full downward cone.",
    "stage_light_curtain_magenta_cyan": "The magenta and cyan curtains remain distinct with a soft layered overlap.",
    "stage_light_beam_spotlight_coolwhite": "The cool-white cone and haze remain visible against the darker canvas area.",
    "stage_light_prism_beam_fan": "The rainbow beam fan has clean color bands and a clear shared origin.",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply() -> int:
    extraction = json.loads(EXTRACTION.read_text(encoding="utf-8"))
    rendered = json.loads(RENDERED.read_text(encoding="utf-8"))
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = {item["stem"]: item for item in document["stickers"]}
    rendered_by_stem = {
        item["stickerId"].rsplit(".", 1)[-1]: item for item in rendered["items"]
    }
    if REVIEW.exists():
        raise FileExistsError(f"refusing to overwrite visual review: {REVIEW}")
    if extraction.get("sourceSha256") != _sha(ROOT / extraction["source"]):
        raise ValueError("source atlas changed after extraction")

    reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sidecars = []
    decisions = []
    for item in extraction["items"]:
        stem = item["stem"]
        entry = entries[stem]
        sticker_id = f"cutvoke.sticker.{stem}"
        source = ROOT / "src/cutvoke/assets/stickers" / f"{stem}.png"
        preview = PREVIEWS / f"{stem}.mp4"
        qa_path = PREVIEWS / f"{stem}.qa.json"
        audit_path = PREVIEWS / f"{stem}.audit.json"
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        source_sha, preview_sha, audit_sha = _sha(source), _sha(preview), _sha(audit_path)
        render_item = rendered_by_stem[stem]
        if (entry.get("version") != "1.1.0" or entry.get("subcategory") != "舞台灯光"
                or source_sha != item["sha256"]
                or qa.get("stickerId") != sticker_id
                or qa.get("version") != entry["version"]
                or qa.get("sourceSha256") != source_sha
                or qa.get("previewSha256") != preview_sha
                or render_item.get("sourceSha256") != source_sha
                or render_item.get("previewSha256") != preview_sha
                or audit.get("stickerId") != sticker_id
                or audit.get("version") != entry["version"]
                or audit.get("sourceSha256") != source_sha
                or audit.get("previewSha256") != preview_sha
                or not {"apply", "edit", "saveReload", "undo", "export"}
                .issubset(set(audit.get("checks", [])))):
            raise ValueError(f"media or edit/export evidence is stale or incomplete: {sticker_id}")
        differences = audit.get("previewExportMeanRgbDifferences", [])
        if (not differences or max(differences) >= 5.0
                or audit.get("visiblePixels", 0) < 100
                or audit.get("editedPixels", 0) < 100):
            raise ValueError(f"machine review below threshold: {sticker_id}")
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
        decisions.append({"stickerId": sticker_id, "name": entry["name"], **sidecar})

    for path, _ in sidecars:
        if path.exists():
            raise FileExistsError(f"refusing to overwrite visual approval: {path}")
    REVIEW.write_text(json.dumps({
        "schemaVersion": 1,
        "reviewer": REVIEWER,
        "reviewedAt": reviewed_at,
        "method": METHOD,
        "sourceSha256": extraction["sourceSha256"],
        "darkContactSheet": extraction["contactSheets"][0],
        "lightContactSheet": extraction["contactSheets"][1],
        "renderedContactSheet": rendered["contactSheet"],
        "decisions": decisions,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for path, sidecar in sidecars:
        path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(sidecars)


if __name__ == "__main__":
    print(f"visually reviewed and hash-bound {apply()} stage-light textures")
