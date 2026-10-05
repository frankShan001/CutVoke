# Reaction Sticker Atlas Batch — 2026-09-24

## Source and extraction

Generated one transparent 1254×1254 PNG with Codex image generation. The artwork contains sixteen separate 3D reaction stickers in reading order: happy, surprised, laughing, cool, thumbs-up, applause, pointing right, hand-heart, heart, crown, megaphone, camera, coffee, headphones, completion badge, and idea bulb.

The preserved source is [`docs/assets/reaction-sticker-sheet-20260924.png`](assets/reaction-sticker-sheet-20260924.png). Its SHA-256 and every crop coordinate, output size, visible-pixel count, and PNG SHA-256 are recorded in [`docs/assets/reaction-sticker-sheet-20260924.extraction.json`](assets/reaction-sticker-sheet-20260924.extraction.json).

[`scripts/extract_reaction_sticker_sheet.py`](../scripts/extract_reaction_sticker_sheet.py) crops cells from left to right and top to bottom. Because the generated square is not divisible by four, it calculates each edge proportionally and pads the 313-pixel cells to square dimensions. It requires genuine alpha, rejects missing or boundary-crossing artwork, clears the transparent safety gutter and hidden RGB, and refuses to overwrite existing outputs.

## Library integration

Sixteen independent PNGs are registered in `src/cutvoke/core/builtin_stickers.json` under the **反应** subcategory and use the existing sticker timeline and editing path. The built-in catalog contains 70 stickers: 62 static and 8 animated. After this batch, 52 stickers are qualified and 18 remain candidates.

`scripts/generate_sticker_previews.py --prefix cutvoke.sticker.reaction_` produced a 2-second composited preview and visibility QA record for each item. All 16 exceeded the visibility threshold; `changedPixels=0` is expected because these items are static. The artwork and composites were visually inspected in [`docs/assets/reaction-sticker-contact-sheet-20260924.png`](assets/reaction-sticker-contact-sheet-20260924.png) and [`docs/assets/reaction-sticker-rendered-review-20260924.png`](assets/reaction-sticker-rendered-review-20260924.png). Then `scripts/audit_sticker_library.py --prefix cutvoke.sticker.reaction_` applied each sticker, changed its transform, saved and reopened the project, exercised undo/redo, exported a real MP4 and compared preview/export frames. All 16 passed; visible artwork ranged from 2,239 to 3,757 pixels and edit changes ranged from 3,344 to 4,742 pixels. Per-sticker visual observations and pinned hashes are in [`docs/REACTION_STICKER_VISUAL_REVIEW_20260924.json`](REACTION_STICKER_VISUAL_REVIEW_20260924.json), and `scripts/review_sticker_library.py apply` approved all 16 under the runtime qualification gate.

## Generation prompt

```text
Make a square transparent PNG atlas of exactly 16 separate reaction stickers, arranged in an exact 4-column by 4-row contact sheet. This is for deterministic computer cropping. Treat the canvas as 16 invisible square cells. In EVERY cell, draw ONE complete centered sticker no larger than the central 68% of that cell, leaving a wide uninterrupted transparent margin on all four sides. Nothing may cross or touch the exact 25%, 50%, or 75% row/column dividers; preserve completely empty transparent gutters around every cell. The grid itself must be invisible. Truly transparent alpha background, no checkerboard, no background color, no text, no letters, no numbers, no labels, no border, no watermark. All stickers are consistent premium 3D clay / glossy vinyl icons, bright friendly colors, smooth tactile highlights, clean silhouette, anatomically coherent simple shapes, no tiny details. Reading order: 1 delighted yellow smiling face, 2 amazed turquoise face, 3 laughing pink face with tears, 4 cool orange face with blue sunglasses; 5 thumbs-up hand; 6 two hands clapping; 7 hand pointing right; 8 hand making a heart gesture; 9 glossy red heart; 10 golden crown; 11 blue megaphone; 12 teal camera; 13 pink coffee cup with steam; 14 purple headphones; 15 green check inside a scalloped badge; 16 orange lightbulb. Keep all radiating rays, fingers, tears and steam inside the central 68% safe area. Each item must be isolated and crop-ready. No objects near the outer border.
```
