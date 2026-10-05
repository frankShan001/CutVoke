# Cinematic Optical Overlay Asset Batch (2026-09-26)

This batch adds sixteen transparent, static overlay stickers to the existing `镜头光效` library. They are selectable resources for a timeline overlay track, with provenance, preview media, edit/audit evidence, and offline-pack inclusion.

## Source and extraction

- Generated source atlas: `docs/assets/cinematic-optical-overlay-atlas-20260926.png`
- Atlas SHA-256: `c6a1edd2f82723fd433b961a3dc52b1722f5778f95884a2579d05f85e365ad7c`
- Actual returned image: 1254×1254 RGBA, arranged as a 4×4 sheet. Crop bounds are recorded per item in `docs/assets/cinematic-optical-overlay-atlas-20260926.extraction.json`.
- Transparent contact sheet: `docs/assets/cinematic-optical-overlay-contact-20260926.png`
- Source and crop procedure: `scripts/extract_cinematic_optical_overlay_atlas.py`
- Catalog registration: `scripts/register_cinematic_optical_atlas.py`
- Per-item manual visual decisions and source hashes: `docs/assets/cinematic-optical-overlay-visual-review-20260926.json`

The extractor divides the returned image into proportional 4×4 cells, preserves semi-transparent pixels, removes alpha specks below 8, and clears a four-pixel rim so light from a neighboring cell cannot leak into a crop. The crop report records source and output hashes, cell coordinates, visible-pixel counts, and any boundary pixels removed.

## ImageGen prompt

```text
Use case: stylized-concept
Asset type: a production sprite atlas of 16 cinematic optical overlay textures for a video editor, to be sliced into individual transparent PNG assets
Primary request: Create one perfectly square 1024x1024 image arranged as a precise 4-column by 4-row atlas of exactly 16 separate optical overlays. Every 256x256 cell contains one centered, self-contained overlay, scaled to fit with generous clear padding from all four cell edges. Maintain exact aligned row and column boundaries so the image can be cropped mechanically into equal 256x256 cells. The background outside each overlay must be truly transparent alpha, not a checkerboard or white. No panel backgrounds, no cell border lines, no gutters that are visible, no labels, no text, no watermark.
Subject and cell plan, in reading order: 1 warm amber anamorphic horizontal flare; 2 cool cyan anamorphic flare; 3 rose-magenta horizontal flare with subtle ghosts; 4 violet-blue cinematic streak with fine bokeh; 5 crisp four-point diffraction star; 6 elegant 8-point pearl diffraction star; 7 compact rainbow prism dispersion; 8 soft concentric lens ghost ring; 9 warm golden edge light leak sweeping in from the left; 10 cyan edge light leak sweeping in from the right; 11 rose-gold lower-corner light leak; 12 teal-blue upper-corner light leak; 13 sparse champagne bokeh glints; 14 restrained icy-blue bokeh glints; 15 subtle circular halation bloom; 16 elegant layered optical arc with a few tiny spectral glints.
Style/medium: premium cinematic compositing elements, refined photographic light behavior, soft luminous gradients with clean anti-aliased edges, visually distinct silhouettes and palettes, restrained detail, no photorealistic scene or lens body.
Color palette: warm amber, champagne, cyan, teal, rose, magenta, violet, pearl, restrained spectral rainbow.
Constraints: Preserve genuine transparency around each overlay so it can be composited over footage. Each tile must remain independent and must not overlap cell boundaries. Keep each element comfortably inside its cell. High visual separation between all 16 cells. No people, objects, camera hardware, film frames, typography, icons, dust/noise/grain, or background scenery.
```

## Registered items

| Set | Count | Resource IDs |
|---|---:|---|
| Amber, cyan, rose and violet horizontal flares | 4 | `cinematic_optical_flare_*` |
| Amber/pearl diffraction stars, rainbow prism, ghost ring | 4 | `cinematic_optical_star_*`, `cinematic_optical_prism_fan`, `cinematic_optical_ghost_ring` |
| Amber/cyan/rose/teal edge and corner light leaks | 4 | `cinematic_optical_leak_*` |
| Champagne/ice bokeh, peach halation, pearl optical arc | 4 | `cinematic_optical_bokeh_*`, `cinematic_optical_halation_peach`, `cinematic_optical_arc_pearl` |

The `镜头光效` category now contains 32 assets. These remain static sticker/overlay resources; they do not count as new effect operators or transition algorithms.

## Review and verification

- 16/16 source crops preserve alpha and passed the 320×180 composited-preview visibility check.
- 16/16 passed the sticker machine audit for apply, edit, save/reload, undo, and export; every item has an approved visual decision bound to its artwork, preview, and audit hashes.
- New Playwright coverage selects `镜头光效`, checks the approved card and source atlas, and inserts a sticker with a v1.25.0 resource reference.
- Full Python suite: 206 passed, 500 subtests passed.
- Full Web E2E run: TypeScript check and Vite build passed; Playwright was 57/58, with one unrelated crop-handle drag assertion timing out. That crop test passed in an immediate standalone rerun (1/1). The new optical-overlay E2E test passed in the full run.
- Resource-pack manifest check: 651 resources and 3,661 files verified.
- Offline resource ZIP: `output/acceptance/jy-r20-cinematic-optical-overlays-20260926/cutvoke-builtin-resources-v1.25.0.zip` (50,313,785 bytes), SHA-256 `9a498b0a6dead23623ce685bdb9ed4443b1e67dbd6798f28e2e749bdd0de523f`. A temporary installation reported v1.25.0, offline available, and no missing or invalid files.
- Coverage after registration: 189 qualified presets, 452 qualified stickers, 641 qualified content items, zero candidates, and zero required category-density gaps.

Review used generated artwork and a synthetic dark-to-light gradient. Real-footage visual quality remains part of the wider acceptance work.
