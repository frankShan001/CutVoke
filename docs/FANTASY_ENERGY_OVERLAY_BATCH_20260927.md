# 奇幻能量与镜头动势贴图（2026-09-27）

本批用 OpenAI ImageGen 一次生成 4×4 图集，再按格坐标裁出 16 张透明 PNG，加入内置贴纸库的“特效贴图”分类。该分类由 79 项增至 95 项。图集里包含熔光弧、传送门碎片、电弧、脉冲环、玻璃光带、冲击波、烟雾旋涡、星尘、镜头辉光、晶体碎片、残影、水波折射和放射线。

## 来源与生成

- 原图：`docs/assets/fantasy-energy-overlay-atlas-20260927.png`
- 原图尺寸：1254×1254，RGBA；单元格使用按 4×4 均分并四舍五入后的边界坐标。
- 提示词：

```text
A square 4x4 sprite atlas, exactly 16 standalone video-compositing overlay effects in row-major cells, no labels, no borders, ample cell margins, perfectly flat pure chroma key green #00FF00 everywhere in background and absolutely no green in art. Premium crisp luminous fantasy and cinematic motion artwork: 1 amber molten energy crescent arc, 2 violet portal shard halo, 3 cyan electric zigzag arc, 4 gold radial pulse rings, 5 turquoise glass ribbon curl, 6 blue-white circular shockwave, 7 indigo smoke ribbon swirl, 8 magenta particle spiral, 9 thin warm lens flare streak, 10 pearl diamond glint cluster, 11 cyan polygonal energy shards, 12 crimson chromatic afterimage swoosh, 13 aqua refractive caustic wave, 14 golden stardust comet trail, 15 violet spectral flame plume, 16 white kinetic speedline burst. Each cell art isolated and centered, no cell crossings. Background perfectly uniform, no shadow, gradient, texture, grid, divider, extra decoration. Use a unified polished transparent-glass and luminous-particle art direction. High contrast; clean edges suitable for removing green to make transparent PNG overlays for a real video editor.
```

生成结果本身为 RGBA 透明底，提取时保留原 Alpha；没有进行绿幕抠像。原图视觉复核后记录发现，浅色画面上白色放射线和淡金辉光对比偏低。

## 裁切与证据

- 裁切脚本：`scripts/extract_fantasy_energy_overlay_atlas_20260927.py`
- 注册脚本：`scripts/register_fantasy_energy_overlay_atlas_20260927.py`
- 提取报告记录逐项行列、像素坐标、可见范围、透明像素统计和 PNG SHA-256：`docs/assets/fantasy-energy-overlay-atlas-20260927.extraction.json`
- 深色、浅色合成预览：`docs/assets/fantasy-energy-overlay-atlas-20260927-contact-dark.png`、`docs/assets/fantasy-energy-overlay-atlas-20260927-contact-light.png`
- 编辑器实际渲染预览：`docs/assets/fantasy-energy-overlay-atlas-20260927-rendered-review.png`
- 机器审计覆盖插入、位置/比例编辑、保存重开、撤销/重做、淡入淡出、预览和导出；审批侧车绑定贴图、预览和审计的当前哈希。
- 视觉审批：`docs/assets/fantasy-energy-overlay-atlas-20260927-visual-review.json`

素材许可字段标注为项目内部 AI 原创素材，未单独授权再分发。

## 浏览器与离线包

Playwright 用例检查“特效贴图”分类的 95/580 计数、资源来源、卡片预览、添加到独立叠加轨、v1.34.0 资源引用和刷新后的工程恢复。

本批生成的离线资源包：`output/acceptance/jy-fantasy-energy-20260927/cutvoke-builtin-resources-v1.34.0.zip`。包内 779 项资源、4,429 个文件，72,111,718 字节；SHA-256 为 `46a960e139cd85bcdf8192161a9df71979b56272a44b3a928931c52deebad34c`。
