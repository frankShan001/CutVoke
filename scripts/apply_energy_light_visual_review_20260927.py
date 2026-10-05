"""Bind visual decisions to energy-light artwork, preview, and edit evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
STICKERS = ROOT / "src/cutvoke/assets/stickers"
PREVIEWS = ROOT / "src/cutvoke/assets/sticker_previews"
EXTRACTION = ROOT / "docs/assets/energy-light-overlay-atlas-20260927.extraction.json"
RENDERED = ROOT / "docs/assets/energy-light-overlay-atlas-20260927-rendered-review.json"
REVIEW = ROOT / "docs/assets/energy-light-overlay-atlas-20260927-visual-review.json"
REVIEWER = "Codex"
METHOD = (
    "Reviewed all extracted RGBA overlays on dark and light checkerboard contacts and inspected "
    "the rendered player preview contact sheet. Checked silhouette clarity, alpha cleanup, "
    "edge safety, contrast, and use as a timeline compositing overlay."
)
OBSERVATIONS = {
    "energy_arc_cyan": "The branching cyan arc has a sharp readable silhouette with no detached edge flecks.",
    "energy_ribbon_violet": "The violet S ribbon remains continuous and distinct against both light and dark backgrounds.",
    "energy_trail_gold": "The short gold trail has a clear curved path and a compact bright head.",
    "energy_prism_flare_magenta": "The magenta four-point prism flare is centered with clean, even rays.",
    "energy_aurora_emerald": "The emerald aurora curl keeps an open center and a coherent luminous contour.",
    "energy_burst_cobalt": "The cobalt radial burst has evenly separated rays and a clear central point.",
    "energy_rainbow_arc": "The rainbow arc preserves its band separation and remains visible over both backgrounds.",
    "energy_halo_pearl": "The pearl halo remains a clean hollow ring with an unobstructed center.",
    "energy_orbs_multicolor": "The five colored orbs remain individually distinguishable with transparent space between them.",
    "energy_shards_crystal": "The crystal burst has separated shard tips and a balanced radial shape.",
    "energy_ribbons_dual": "The paired cyan and violet ribbons stay distinct without merging into a solid patch.",
    "energy_loop_golden": "The golden loop keeps a fine closed curve and a visible crossing point.",
    "energy_stars_falling": "The three gold star streaks remain separated and retain their pointed heads.",
    "energy_curtain_violet": "The violet-cyan light curtain reads as a compact vertical overlay with a clean lower edge.",
    "energy_wave_coral": "The coral pulse waveform has a crisp center spike and a transparent outer field.",
    "energy_zigzag_gold": "The gold zigzag has a clear continuous path with contained soft glow.",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply() -> int:
    extraction = json.loads(EXTRACTION.read_text(encoding="utf-8"))
    rendered = json.loads(RENDERED.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if REVIEW.exists():
        raise FileExistsError(f"refusing to overwrite visual review: {REVIEW}")
    if extraction.get("sourceSha256") != _sha(ROOT / extraction["source"]):
        raise ValueError("source atlas changed after extraction")
    rendered_by_stem = {item["stickerId"].rsplit(".", 1)[-1]: item
                        for item in rendered.get("items", [])}
    entries = {item["stem"]: item for item in catalog.get("stickers", [])}
    extracted_stems = {item["stem"] for item in extraction.get("items", [])}
    if (len(extraction.get("items", [])) != 16 or extracted_stems != set(OBSERVATIONS)
            or set(rendered_by_stem) != set(OBSERVATIONS)
            or not set(OBSERVATIONS).issubset(entries)):
        raise ValueError("visual review requires 16 new assets, renders, and catalog entries")
    if _sha(ROOT / rendered["contactSheet"]) != rendered.get("contactSheetSha256"):
        raise ValueError("rendered contact sheet changed after review")

    reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sidecars = []
    decisions = []
    for item in extraction["items"]:
        stem = item["stem"]
        if stem not in OBSERVATIONS:
            raise ValueError(f"missing visual observation: {stem}")
        catalog_item = next((entry for entry in catalog["stickers"]
                             if entry["stem"] == stem), None)
        if catalog_item is None:
            raise ValueError(f"sticker catalog is missing {stem}")
        sticker_id = f"cutvoke.sticker.{stem}"
        source = STICKERS / f"{stem}.png"
        preview = PREVIEWS / f"{stem}.mp4"
        qa_path = PREVIEWS / f"{stem}.qa.json"
        audit_path = PREVIEWS / f"{stem}.audit.json"
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        render_item = rendered_by_stem[stem]
        source_sha, preview_sha = _sha(source), _sha(preview)
        if (catalog_item.get("subcategory") != "能量光效"
                or catalog_item.get("version") != "1.0.0"
                or source_sha != item["sha256"]
                or render_item.get("sourceSha256") != source_sha
                or render_item.get("previewSha256") != preview_sha
                or qa.get("stickerId") != sticker_id
                or qa.get("version") != "1.0.0"
                or qa.get("sourceSha256") != source_sha
                or qa.get("previewSha256") != preview_sha
                or audit.get("stickerId") != sticker_id
                or audit.get("version") != "1.0.0"
                or audit.get("sourceSha256") != source_sha
                or audit.get("previewSha256") != preview_sha
                or not {"apply", "edit", "saveReload", "undo", "export"}
                .issubset(set(audit.get("checks", [])))):
            raise ValueError(f"media or edit/export evidence is stale or incomplete: {sticker_id}")
        differences = audit.get("previewExportMeanRgbDifferences", [])
        if (not differences or max(differences) >= 5.0
                or qa.get("visiblePixels", 0) < 100
                or audit.get("visiblePixels", 0) < 100
                or audit.get("editedPixels", 0) < 100):
            raise ValueError(f"machine review below threshold: {sticker_id}")
        sidecar = {
            "schemaVersion": 1,
            "decision": "approved",
            "stickerId": sticker_id,
            "version": "1.0.0",
            "effectId": "",
            "params": {},
            "reviewer": REVIEWER,
            "reviewedAt": reviewed_at,
            "observation": OBSERVATIONS[stem],
            "method": METHOD,
            "sourceSha256": source_sha,
            "previewSha256": preview_sha,
            "auditSha256": _sha(audit_path),
        }
        sidecar_path = PREVIEWS / f"{stem}.visual.json"
        sidecars.append((sidecar_path, sidecar))
        decisions.append({"name": catalog_item["name"], **sidecar})

    if any(path.exists() for path, _ in sidecars):
        raise FileExistsError("refusing to overwrite existing visual approval sidecars")
    REVIEW.write_text(json.dumps({
        "schemaVersion": 1,
        "reviewer": REVIEWER,
        "reviewedAt": reviewed_at,
        "method": METHOD,
        "sourceSha256": extraction["sourceSha256"],
        "darkContactSheet": extraction["contactSheets"][0],
        "lightContactSheet": extraction["contactSheets"][1],
        "renderedContactSheet": rendered["contactSheet"],
        "renderedContactSheetSha256": rendered["contactSheetSha256"],
        "decisions": decisions,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for path, sidecar in sidecars:
        path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return len(sidecars)


if __name__ == "__main__":
    print(f"visually reviewed and hash-bound {apply()} energy-light overlays")
