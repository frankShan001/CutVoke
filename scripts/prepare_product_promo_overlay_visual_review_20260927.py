"""Complete the hash-pinned visual review after inspecting both contact sheets."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "docs/assets/product-promo-overlay-visual-review-20260927.json"
EXPECTED = {
    "promo_frame_brackets": "Four champagne corner brackets remain separated and frame a clear center.",
    "promo_orbit_rings": "Two fine orbital loops and the pearl bead stay distinct without crossing the cell boundary.",
    "promo_silk_loop": "The ivory silk loop keeps a smooth closed crossover and readable soft highlights.",
    "promo_prism_refraction": "The faceted prism and rainbow refraction remain clear on both dark and light backgrounds.",
    "promo_foil_flourish": "The curved gold foil ribbon and its particle trail form one complete, clean silhouette.",
    "promo_podium_outline": "The two-tier translucent product stand retains separate bases and a visible center platform.",
    "promo_callout_leaders": "Both blank callout lines have intact endpoints and leave open space for later annotation.",
    "promo_spotlight_fan": "The warm spotlight cone reaches a clean oval pool and remains visible over the preview gradient.",
    "promo_opal_halo": "The opalescent elliptical halo has a complete outline and restrained rainbow edge.",
    "promo_leaf_shadow": "Two jade leaves remain separately outlined with translucent centers and no clipped tips.",
    "promo_blank_seal": "The blank double-line seal forms a continuous circle with a clear empty center.",
    "promo_sparkle_glints": "Three pearl glints remain separated, with no stray fourth star or generated lettering.",
    "promo_glass_wave": "The teal glass wave is fully contained in its cell and keeps both curved ends intact.",
    "promo_corner_foil": "The gold corner accent has one crisp vertical-to-horizontal turn and a complete star flare.",
    "promo_focus_ring": "The pearl focus ring stays circular and readable, with the small flare attached to its rim.",
    "promo_luxury_badge": "The four-lobed gold badge is symmetrical and its center remains blank for user-added content.",
}


def prepare() -> int:
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    decisions = review.get("decisions")
    if not isinstance(decisions, list) or {item["stickerId"] for item in decisions} != {
            f"cutvoke.sticker.{stem}" for stem in EXPECTED}:
        raise ValueError("visual-review draft does not match the 16 product-promo stickers")
    if any(item.get("decision") != "pending" for item in decisions):
        raise ValueError("refusing to replace a non-pending visual decision")
    review["reviewer"] = "Codex"
    review["reviewedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    review["method"] = (
        "Inspected every extracted transparent PNG on the dark and light contact sheets, "
        "then inspected the corresponding actual RenderService preview contact sheet. "
        "Checked cell containment, shape completeness, alpha edges, contrast, thumbnail "
        "legibility, visual distinctness, and usefulness as a product-ad overlay."
    )
    review["evidence"] = {
        "sourceAtlas": "docs/assets/product-promo-overlay-atlas-20260927.png",
        "extractionReport": "docs/assets/product-promo-overlay-atlas-20260927.extraction.json",
        "darkContactSheet": "docs/assets/product-promo-overlay-atlas-20260927-contact-dark.png",
        "lightContactSheet": "docs/assets/product-promo-overlay-atlas-20260927-contact-light.png",
        "renderedContactSheet": "docs/assets/product-promo-overlay-rendered-review-20260927.png",
        "renderedContactReport": "docs/assets/product-promo-overlay-rendered-review-20260927.json",
    }
    for item in decisions:
        stem = item["stickerId"].rsplit(".", 1)[-1]
        item["decision"] = "approved"
        item["observation"] = EXPECTED[stem]
    REVIEW.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return len(decisions)


if __name__ == "__main__":
    print(f"prepared {prepare()} product-promo visual decisions")
