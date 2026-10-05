# 视频光效纹理贴纸批次（2026-09-24）

## 生成与裁切

用内置 Codex imagegen 一次生成 1254×1254 真透明 PNG，采用 4×4 排列，共 16 个光效/动势覆盖素材。原图保存在 [`docs/assets/video-overlay-texture-sheet-20260924.png`](assets/video-overlay-texture-sheet-20260924.png)，透明棋盘预览在 `output/preset_review/overlay-texture-atlas-20260924/atlas-checkerboard.jpg`。裁切脚本 [`scripts/extract_video_overlay_texture_sheet.py`](../scripts/extract_video_overlay_texture_sheet.py) 按比例逐格裁切，保留 alpha，将 313/314 像素单元补齐到方图，并记录原图 SHA-256、坐标、可见像素和输出哈希：[`docs/assets/video-overlay-texture-sheet-20260924.extraction.json`](assets/video-overlay-texture-sheet-20260924.extraction.json)。

这批图包含镜头光斑、双色漏光、散景、电弧、星轨、速度线、半调网点、动势弧、金色光束、扫描环、对焦角框、音波、火花轨迹、传送光环、赛博角标和星芒。各项均是真透明静态叠加 PNG，并作为独立贴纸素材接入时间线。

## 目录接入

16 项登记在 `src/cutvoke/core/builtin_stickers.json` 的“光效纹理”分类，沿用现有贴纸的预览、变换、保存、撤销与导出路径。资产随仓库按当前内置目录 MIT 许可声明。该批是可手动定位的静态覆盖素材，不含人物跟踪/抠像能力，也不作为已交付“人物特效”族的证据。

## 验收记录

`scripts/generate_sticker_previews.py --prefix cutvoke.sticker.texture_` 生成了 16 个两秒真实合成预览；`scripts/audit_sticker_library.py --prefix cutvoke.sticker.texture_` 逐项核对应用、变换、保存重开、撤销/重做、MP4 导出以及预览/导出同帧。可见像素为 659–4,094，变换生效像素为 1,057–4,946，预览/导出同帧平均 RGB 差最高 0.158。人工逐项查看原始透明图、浅底实际合成预览和深/浅底对照，观察与原图/预览哈希见 [`docs/OVERLAY_TEXTURE_VISUAL_REVIEW_20260924.json`](OVERLAY_TEXTURE_VISUAL_REVIEW_20260924.json)。16 项视觉与机器检查均通过。

审核拼图：[`原图裁切表`](../output/preset_review/overlay-texture-atlas-20260924/review/sticker-光效纹理.png)、[`实际合成预览`](../output/preset_review/overlay-texture-atlas-20260924/review/sticker-光效纹理-rendered.png)、[`深浅底可见性对照`](../output/preset_review/overlay-texture-atlas-20260924/review/dark-light-contrast.png)。

## 生图提示词

```text
Use case: stylized-concept
Asset type: transparent sticker atlas for a video editor
Primary request: exactly 16 unique, complete transparent overlay graphics in a square 4-column by 4-row atlas, centered one per cell with broad empty gutters. Reading order: amber cinematic lens flare; magenta-cyan film light leak ribbon; sparse pastel bokeh discs; cyan electric arc; violet magical orbit with gold sparks; white/cyan diagonal manga speed lines; blue-violet halftone patch fading transparent; hot-pink curved motion swoosh; golden sunbeam fan; cyan technical HUD partial rings; four lime focus brackets; teal-coral abstract waveform ribbon; amber spark trail; incomplete violet luminous portal ring; blue-cyan segmented frame-corner marks; pearl-white lens glint cluster.
Style/medium: polished clean motion-graphics, crisp vector-like shapes plus restrained soft luminous gradients, bold legible details.
Composition/framing: exact invisible 4x4 cell layout, one effect per cell, each within middle 62% of its cell, no crossing cell boundaries. True transparent alpha background.
Constraints: no visible grid, checkerboard, background color, labels, text, numbers, logo, watermark, frame, random extra objects, grain, or cropped art. No duplicate motifs.
```
