# 运动贴图批次（2026-09-25）

为补齐贴纸库里缺少的运动主题，新增一套 4×4 透明图集，并将 16 个单体贴图接入内置资源目录。

## 来源与裁切

- 图集：`docs/assets/sports-sticker-atlas-20260925.png`
- 生成方式：Codex 内置 ImageGen；先生成完整图集，再要求各格主体缩小并增加透明留白。
- 来源 SHA-256、每格坐标、裁切文件 SHA-256 和透明像素信息：`docs/assets/sports-sticker-atlas-20260925.extraction.json`
- 裁切脚本：`scripts/extract_sports_sticker_atlas.py`
- 预览联系表：`docs/assets/sports-sticker-contact-sheet-20260925.png`
- 单体文件：`src/cutvoke/assets/stickers/sports_*.png`；每张 320×320 RGBA，透明边距 32 px。
- 图集中发现并移除了 72 个与主体断开的微小 alpha 杂点；裁切报告记录了数量。

## 内容

篮球、足球、排球、网球拍与网球、羽毛球拍与羽毛球、棒球与球棒手套、高尔夫球杆与球、拳击手套、冠军奖杯、运动奖牌、跑鞋、哑铃、滑板、冲浪板与浪花、裁判哨子、运动秒表。

逐项视觉决定记录在 `docs/assets/sports-sticker-visual-review-20260925.json`。复核同时查看了棋盘透明底贴图和实际视频画布预览。

## 图像生成提示

首轮提示：

> Create exactly one square transparent atlas with a strict 4x4 grid, reading row-major. Each cell contains one isolated sports sticker: orange basketball; classic black-white soccer ball; blue-yellow volleyball; tennis racket with tennis ball; badminton racket with shuttlecock; baseball bat with ball and glove; golf club with ball and tee; red boxing gloves; gold champion trophy; blue-gold medal and ribbon; white running shoe with coral accents; compact dumbbell; colorful skateboard; turquoise surfboard with a small curling wave; silver whistle with short red lanyard; red-white analog stopwatch. Polished cute premium 3D collectible illustration, smooth studio shading, crisp silhouette, refined rounded forms, consistent style. Each sticker has a thin white die-cut outline. Wide transparent gutters, centered with generous margins, uniform scale, genuine transparency outside the stickers. No grid lines, tile backgrounds, overlap, humans, text, numbers, brands, logos, watermark, grain, dirt, or speckles.

编辑提示：

> Change only sticker scale and placement: reduce each of the sixteen existing sticker groups to about 72% of its current size and center it within its same original 4x4 cell, leaving broad transparent safety margins. Preserve the exact objects, colors, materials, style, outlines, grouping, row-major order, and atlas canvas positions. Preserve genuine transparency. Do not add, remove, replace, repeat, or redraw objects; avoid edge clipping, inter-cell leaks, speckles, and stray marks.

## 验收

- 16 张均生成合成预览；视觉内容无重复、无可见串格，缩略图下仍可识别。
- `scripts/audit_sticker_library.py --prefix cutvoke.sticker.sports_`：16/16 通过贴图应用、变换编辑、保存重载、撤销重做、淡入淡出、预览/导出一致性和视频导出检查。
- 16/16 具备哈希绑定的独立视觉复核记录，并通过 `review_sticker_library.py apply`。
- 内置资源包提升为 v1.14.0；重建后包含 474 个资源和 2,673 个校验文件（其余回归验证见本次验收结果）。
