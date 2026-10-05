# 玻璃与液态折射贴图批次（2026-09-27）

使用一次 OpenAI ImageGen 生成 4×4 RGBA 图集，按固定网格裁切为 16 张 512×512 独立透明 PNG，登记到现有“贴纸 / 特效贴图”分类。内容包括水纹焦散、液态玻璃、虹彩油膜、冰晶裂纹、熔璃纹路、珍珠母贝、棱镜晶体、玻璃涟漪、全息箔、热浪、矿物晶簇、金属拉丝、肥皂膜泡泡、液态铬和边缘焦散。

- 原始图集：[`docs/assets/glass-liquid-refraction-atlas-20260927.png`](assets/glass-liquid-refraction-atlas-20260927.png)
- 深浅背景接触表：[`docs/assets/glass-liquid-refraction-atlas-20260927-contact.png`](assets/glass-liquid-refraction-atlas-20260927-contact.png)
- 实际导出帧复核表：[`docs/assets/glass-liquid-refraction-rendered-review-20260927.png`](assets/glass-liquid-refraction-rendered-review-20260927.png)
- 网格坐标、逐项边界/Alpha、来源与 SHA-256：[`docs/assets/glass-liquid-refraction-atlas-20260927.extraction.json`](assets/glass-liquid-refraction-atlas-20260927.extraction.json)
- 哈希绑定逐项视觉决定：[`docs/assets/glass-liquid-refraction-visual-review-20260927.json`](assets/glass-liquid-refraction-visual-review-20260927.json)
- 可复跑裁切：[`scripts/extract_glass_liquid_refraction_atlas.py`](../scripts/extract_glass_liquid_refraction_atlas.py)
- 来源校验与分类登记：[`scripts/register_glass_liquid_refraction_atlas.py`](../scripts/register_glass_liquid_refraction_atlas.py)
- 裁切后 PNG：`src/cutvoke/assets/stickers/fxoverlay_refraction_*.png`

## 图集生成提示词

```text
Create one square, clean 4 by 4 atlas of exactly 16 distinct premium video-editor overlay texture stickers. Output the complete atlas only. The cells are an exact regular grid in reading order, with equal square cell sizes, no lines, no gutters drawn, no labels. Each cell contains one isolated full-bleed-leaning texture sample with generous transparent padding, independently crop-safe, no overlap across cell boundaries. Real transparent RGBA background, not a checkerboard, not black, not white. High-resolution polished visual effects, subtle alpha gradients and translucency, preserve useful detail at 512px crop size. In reading order: 1 turquoise water caustic light ripples; 2 violet liquid glass refraction ribbons; 3 iridescent oil-slick film membrane; 4 delicate branching ice crystal fractures; 5 amber molten-glass veins; 6 opalescent pearl sheen arcs; 7 prismatic fractured crystal shards; 8 layered clear-glass ripple rings; 9 deep blue underwater light caustics; 10 holographic foil folds in cyan-magenta; 11 soft heat-haze distortion ribbons in warm orange; 12 emerald mineral geode glints; 13 silver brushed-metal flowing grain; 14 tiny translucent soap-film bubbles and spectral rims; 15 rose-gold liquid chrome splash; 16 luminous refracted rainbow edge caustics. Every item must be visibly different from the others and useful as a transparent overlay on live-action video. No text, no numerals, no symbols, no logos, no frame, no mockup, no background scene, no shadows beyond each cell's own transparent art.
```

## 裁切与验收边界

图集为 1254×1254 RGBA，Alpha 范围 0–255。裁切使用等分坐标（允许 313/314 像素格宽差），将 Alpha 小于 8 的像素置透明，清除每格 8px 边缘并保留有效半透明像素。16 格均有足量可见像素，输出保持 512×512 透明画布；逐项坐标、边界像素和哈希见 extraction JSON。接触表同时展示深蓝与浅灰背景；浅色和珍珠质感在明亮画面上对比偏弱，使用时应按素材亮度选择。

16/16 贴图通过预览生成和插入、变换编辑、保存重开、撤销/重做、淡入淡出、预览/导出一致性机器审计。透明贴图和实际导出帧经 Codex 视觉复核，逐项决定绑定源图、预览和机器审计哈希。素材是本项目内部原创 AI 生成贴图，不单独授权再分发。以上检查不替代独立用户体验测试，也不证明复杂真实摄影素材下的商业观感。
