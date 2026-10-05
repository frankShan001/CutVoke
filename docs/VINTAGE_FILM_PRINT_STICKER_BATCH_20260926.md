# 复古胶片与印刷纹理贴图批次

日期：2026-09-26。使用内置 ImageGen 一次生成 4×4 透明图集，裁切为 16 张可单独放入时间线的静态贴图。它们覆盖胶片、印刷网点、复印纸和拼贴纸张等视觉元素。

## 图集和可复跑文件

- 原始 RGBA 图集：[vintage-film-print-texture-atlas-20260926.png](assets/vintage-film-print-texture-atlas-20260926.png)，1254×1254 px，透明 Alpha 范围为 0–255。
- 透明裁切预览：[vintage-film-print-texture-contact-20260926.png](assets/vintage-film-print-texture-contact-20260926.png)。
- 16 项实际画面合成复核图：[vintage-film-print-texture-composite-review-20260926.png](assets/vintage-film-print-texture-composite-review-20260926.png)。
- 每格坐标、像素范围、资产 SHA-256 与来源哈希：[裁切报告](assets/vintage-film-print-texture-atlas-20260926.extraction.json)。
- 提取与登记：[extract_vintage_print_texture_atlas.py](../scripts/extract_vintage_print_texture_atlas.py)、[register_vintage_print_texture_atlas.py](../scripts/register_vintage_print_texture_atlas.py)。
- 贴图 PNG：`src/cutvoke/assets/stickers/printfx_*.png`；真实合成预览和机器审计位于 `src/cutvoke/assets/sticker_previews/printfx_*`。

裁切器按比例计算 4×4 等分边界，保留原图透明度，并将每格置于透明正方形中；没有把整张图铺进每个资源。报告记录每张裁切的 PNG 哈希，登记脚本在来源图或裁切 PNG 哈希不匹配时拒绝登记。

## 16 项内容

| 格 | 贴图 | 格 | 贴图 |
|---|---|---|---|
| 1 | 35 毫米胶片齿孔 | 9 | 卷起的纸角 |
| 2 | 胶片浮尘颗粒 | 10 | 折痕纸张 |
| 3 | 胶片纵向划痕 | 11 | 复印机碳粉污迹 |
| 4 | 油墨滚筒颗粒 | 12 | 复古空白标签框 |
| 5 | 渐隐半调网点 | 13 | 丝网印刷飞溅 |
| 6 | 漫画半调爆发 | 14 | 模拟扫描纹理 |
| 7 | 复印纸撕边 | 15 | 四色套印标记 |
| 8 | 复古纸胶带 | 16 | 胶片取景角标 |

新项目归入现有“电影叠加”类，类目总量从 14 增至 30；默认缩放为 0.92。关键词保留胶片、印刷、纸张、复古、拼贴等检索词。

## 验收状态

16 项均已生成 MP4 合成预览；16/16 通过插入、变换编辑、保存重载、撤销/重做、入出场动画、预览和导出机器审计。透明裁切表与 16 项真实合成预览完成逐项视觉检查，并以来源图、预览和机器审计哈希写入 `output/acceptance/vintage-film-print-texture-20260926/visual-review.json`；16/16 记录为 `Codex visual QA` approved。

审批后“电影叠加”分类为 30 项，覆盖统计为 189 项合格预设、468 项合格贴纸、657 项合格内容；候选数为 0，`effect_coverage.py` 的开放/必需类别密度缺口为 0。资源包升级为 v1.27.0，共 667 项资源、3,757 个文件。ZIP 位于 `output/acceptance/vintage-film-print-texture-20260926/cutvoke-builtin-resources-v1.27.0.zip`，大小 52,149,795 字节，SHA-256 为 `FB938E53C041EF0CE1726411A9126E4BC28AFB41669E8D4AB1326804F17298A3`；完整 ZIP 校验、临时目录离线安装和启用均通过，缺失/哈希无效文件为 0。

本批视觉审批逐项绑定透明 PNG、合成预览和机器审计哈希；资源打包器把 `.visual.json` 纳入 v1.27.0 离线包，离线安装重新校验全部清单文件及资源目录。

所有生成素材标记为项目内 AI 原创，未单独授予再分发许可。不要将该标记误认为外部素材授权。

## ImageGen 提示词

```text
Use case: illustration-story
Asset type: a production-ready 4x4 atlas of 16 video-editor overlay stickers, to be cropped into individual transparent PNG assets.
Primary request: create one square 4-by-4 sprite sheet of distinct analog-film and print-texture overlays for a modern video editing sticker library. Each of the 16 equal square cells contains exactly one separate, centered, clearly recognizable overlay, with generous transparent padding. Read left to right, top to bottom: 1) vertical 35mm film perforation strip; 2) scattered fine film dust; 3) long fine film scratches; 4) soft ink roller grain; 5) halftone dot fade; 6) comic halftone burst; 7) torn photocopy-paper edge; 8) ripped masking-tape strips; 9) curled paper corner; 10) old paper creases; 11) photocopier toner smudge; 12) vintage paper label frame with a blank center; 13) screen-print ink splatter; 14) diagonal analog scanline sweep; 15) subtle chromatic registration offset marks; 16) analog film frame corners.
Style/medium: refined analog printmaking, editorial film-lab textures, tactile but clean, richly detailed and usable as decorative video overlays.
Composition/framing: strict geometric 4x4 equal-cell grid; each design stays fully inside its own cell and never touches neighboring cells; consistent 12 percent inset margin; isolated overlay shapes, no backgrounds inside cells, no cell borders, no gutters, no overlaps.
Lighting/mood: flat graphic artwork, even illumination, no cast shadows.
Color palette: mostly charcoal, warm ivory, muted amber, faded cyan and subdued vermilion, restrained colors suitable for layering over live-action footage.
Constraints: genuinely transparent canvas and transparent negative space within each cell; 16 unique cells; no words, no letters, no numerals, no logos, no watermark; no checkerboard pattern; no mockup; no realistic objects or scenes.
```
