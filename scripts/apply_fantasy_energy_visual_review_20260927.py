"""Bind visual review decisions to fantasy-energy sticker and render evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from extract_fantasy_energy_overlay_atlas_20260927 import ITEMS, ROOT


CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
STICKERS = ROOT / "src/cutvoke/assets/stickers"
PREVIEWS = ROOT / "src/cutvoke/assets/sticker_previews"
EXTRACTION = ROOT / "docs/assets/fantasy-energy-overlay-atlas-20260927.extraction.json"
RENDERED = ROOT / "docs/assets/fantasy-energy-overlay-atlas-20260927-rendered-review.json"
REVIEW = ROOT / "docs/assets/fantasy-energy-overlay-atlas-20260927-visual-review.json"
REVIEWER = "Codex"
METHOD = (
    "Reviewed each extracted RGBA PNG on dark and light contact sheets, then checked the actual "
    "RenderService preview contact sheet. Examined distinct silhouettes, cell-edge clipping, "
    "alpha fringes, visibility on both tones, and usefulness as a compositing overlay."
)
OBSERVATIONS = {
    "fxoverlay_fantasy_amber_arc": "The crescent keeps a clean open center and a visible amber spark trail on both backgrounds.",
    "fxoverlay_fantasy_violet_portal": "The violet shard ring stays circular, with a clear center and separated outer fragments.",
    "fxoverlay_fantasy_cyan_electric": "The cyan bolt silhouette remains distinct from its soft electric glow and has no clipped endpoints.",
    "fxoverlay_fantasy_gold_pulse": "The concentric gold pulse remains centered and its thin rings stay visible over dark footage.",
    "fxoverlay_fantasy_aqua_ribbon": "The aqua glass ribbon has a continuous S curve and detached bubbles remain inside the crop.",
    "fxoverlay_fantasy_blue_shockwave": "The icy ring has a readable hollow center, with crystal fragments separated around its perimeter.",
    "fxoverlay_fantasy_indigo_smoke": "The indigo smoke swirl has a coherent S shape; its soft dark edge is strongest over lighter footage.",
    "fxoverlay_fantasy_magenta_spiral": "The magenta particle spiral reads from its bright center through the full outer curve.",
    "fxoverlay_fantasy_lens_glow": "The horizontal lens flare keeps a bright core and broad soft rays without an opaque panel.",
    "fxoverlay_fantasy_pearl_glints": "The pearl glints keep one dominant central star and smaller separated highlights.",
    "fxoverlay_fantasy_cyan_shards": "The cyan crystal burst has clear shard edges and a balanced radial silhouette.",
    "fxoverlay_fantasy_crimson_afterimage": "The crimson curved streak and trailing fragments remain visually separate from the transparent canvas.",
    "fxoverlay_fantasy_aqua_caustic": "The aqua refractive wave keeps a continuous bright edge and visible droplets around its rim.",
    "fxoverlay_fantasy_gold_comet": "The gold comet trail has a clean S curve and a distinct bright head with sparse particles.",
    "fxoverlay_fantasy_violet_flame": "The violet spectral flame retains its upright plume and a bright inner energy contour.",
    "fxoverlay_fantasy_white_speedlines": "The white speedline burst has a clean open center and even rays; it reads best on dark footage.",
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
    entries = {item["stem"]: item for item in catalog["stickers"]}
    rendered_by_stem = {
        item["stickerId"].rsplit(".", 1)[-1]: item for item in rendered["items"]
    }
    if len(extraction.get("items", [])) != len(ITEMS) or len(rendered_by_stem) != len(ITEMS):
        raise ValueError("visual review requires all 16 atlas cells and rendered previews")

    reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sidecars = []
    decisions = []
    for item in extraction["items"]:
        stem = item["stem"]
        if stem not in OBSERVATIONS:
            raise ValueError(f"missing visual observation: {stem}")
        entry = entries[stem]
        sticker_id = f"cutvoke.sticker.{stem}"
        source, preview = STICKERS / f"{stem}.png", PREVIEWS / f"{stem}.mp4"
        qa_path, audit_path = PREVIEWS / f"{stem}.qa.json", PREVIEWS / f"{stem}.audit.json"
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        source_sha, preview_sha = _sha(source), _sha(preview)
        render_item = rendered_by_stem[stem]
        if (entry.get("version") != "1.0.0" or entry.get("subcategory") != "特效贴图"
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
            "auditSha256": _sha(audit_path),
        }
        sidecars.append((PREVIEWS / f"{stem}.visual.json", sidecar))
        decisions.append({"stickerId": sticker_id, "name": entry["name"], **sidecar})

    if any(path.exists() for path, _ in sidecars):
        raise FileExistsError("refusing to overwrite existing visual approval sidecars")
    review = {
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
    }
    REVIEW.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    for path, sidecar in sidecars:
        path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return len(sidecars)


if __name__ == "__main__":
    print(f"visually reviewed and hash-bound {apply()} fantasy-energy overlays")
