# 2026-09-24 装饰贴纸批量制作

本批使用内置图像生成工具一次生成 4 × 3 透明底贴图表，再按固定 362 × 362 网格裁切为 12 张独立 PNG。未输入第三方图片。原始表保存在 [`assets/decor-sticker-sheet-20260924.png`](assets/decor-sticker-sheet-20260924.png)，SHA-256 为 `db4769be4361d894ec86c969cb2a8ce9799b2650e31f147b7b5eb5e9b78c29fa`。

## 生成配置

- 模式：`stylized-concept`；一张 RGBA PNG，1448 × 1086，真实透明通道。
- 提示词：

> Use case: stylized-concept. Asset type: a production sprite sheet for independently cropped transparent stickers in a browser video editor. Create ONE large PNG image with a genuinely transparent background and EXACTLY twelve separate illustrated sticker assets in a strict 4-column by 3-row grid. Each asset stays entirely inside its own equal-size cell with generous clear transparent gutters; no item touches another cell. Consistent polished contemporary editorial cut-paper and soft 3D decal style, crisp silhouettes, clean anti-aliased edges, vivid but controlled coral, teal, sky blue, butter yellow, violet palette. Row 1 left to right: curved neon brush stroke, folded paper tape strip, small iridescent sparkle cluster, hand-drawn spiral doodle. Row 2: comic impact burst, pastel paint splash, wavy underline ribbon, tiny confetti cascade. Row 3: torn paper corner, soft rainbow arc, scribbled circular highlight ring, floating three-dot trail. Every cell contains exactly one fully visible isolated graphic, centered with 15% transparent padding around it. Transparent alpha only outside the graphics, no checkerboard, no white matte, no background color. No letters, numbers, captions, logos, watermarks, shadows outside the cells, or separator lines. High-resolution, suitable for extracting twelve square transparent PNG stickers.

## 裁切与接入

运行 `python scripts/extract_sticker_sheet.py` 可从原始表提取贴图；脚本拒绝覆盖已有 PNG，以免无意中使审核哈希失效。网格从左到右、从上到下依次是：

| 行 | 贴图文件名 |
| --- | --- |
| 1 | `decor_neon_stroke.png`、`decor_paper_tape.png`、`decor_sparkles.png`、`decor_spiral.png` |
| 2 | `decor_impact_burst.png`、`decor_paint_splash.png`、`decor_wavy_ribbon.png`、`decor_confetti.png` |
| 3 | `decor_torn_corner.png`、`decor_rainbow.png`、`decor_scribble_ring.png`、`decor_three_dots.png` |

各 PNG 位于 `src/cutvoke/assets/stickers/`；`builtin_stickers.json` 将它们加入“装饰”分类。每张图有透明边界、独立素材 ID、悬停播放的真实合成预览和可编辑贴纸轨入口。

## 验收边界

12 张贴图的 Alpha、裁切边界、可见像素均通过脚本检查；每项 2 秒合成样片、机器审计和逐项视觉观察记录在 `src/cutvoke/assets/sticker_previews/` 与 [`DECOR_STICKER_VISUAL_REVIEW_20260924.json`](DECOR_STICKER_VISUAL_REVIEW_20260924.json)。审核记录固定源图和样片哈希，源图变化会使“合格”状态失效。Web 资源库确认显示“装饰 12”，能添加到独立贴纸轨。复杂实拍背景的逐项成片观感仍属于 JY-R10 后续验收。
