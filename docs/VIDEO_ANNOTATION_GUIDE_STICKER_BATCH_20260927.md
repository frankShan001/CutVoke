# 视频标注与指引贴纸图集（2026-09-27）

使用内置 ImageGen 一次生成一张 4×4 透明贴纸图集，再裁成 16 张独立 PNG，接入贴纸库“指引”分类。

## 图集与提示词

- 原图：[video-annotation-guide-sticker-atlas-20260927.png](assets/video-annotation-guide-sticker-atlas-20260927.png)，1254×1254 RGBA，SHA-256：`fe4d7804e0e16fad2ddb7caabcc1ba02f297ab5e2732ad3db290f948e5867255`。
- 浅/深底裁切接触表：[video-annotation-guide-sticker-atlas-20260927-contact.png](assets/video-annotation-guide-sticker-atlas-20260927-contact.png)。
- 编辑器实际预览接触表：[video-annotation-guide-sticker-atlas-20260927-rendered-review.png](assets/video-annotation-guide-sticker-atlas-20260927-rendered-review.png)。
- 每格坐标、透明范围及裁切文件 SHA-256：[提取报告](assets/video-annotation-guide-sticker-atlas-20260927.extraction.json)。逐项视觉判断与素材、预览和机器审计哈希：[视觉复核记录](assets/video-annotation-guide-sticker-atlas-20260927-visual-review.json)。

使用的生成提示词：

```text
Create one production-ready 4×4 atlas of 16 distinct video-editing annotation stickers. Square 2048×2048 canvas with a genuinely transparent RGBA background. Each of the 16 equal square cells contains exactly one centered sticker, arranged row by row in this order; keep every sticker fully inside its own cell with generous transparent padding. No cell borders, no grid lines, no text, letters, numbers, logos, watermark, or background. Consistent polished editorial sticker style: crisp hand-drawn vector forms with subtle dimensional shading, confident outlines, bright saturated color accents and clear silhouettes that remain readable over both light and dark footage. Make all 16 clearly different: 1) sweeping curved cyan pointer arrow with a small circular tail; 2) coral double-ended measurement arrow; 3) broken yellow hand-drawn emphasis circle; 4) violet dashed rounded-rectangle selection frame with four corner handles; 5) mint four-corner focus brackets; 6) peach highlighter underline stroke with tapered ends; 7) turquoise dotted route with three round nodes and one gentle bend; 8) cobalt magnifying glass with a small plus symbol inside the lens; 9) amber spotlight cone with a crisp circular focal point; 10) coral-and-cream target reticle; 11) lime checkmark inside a tilted outlined badge; 12) violet speech balloon containing only three dots; 13) blue branching connector line joining three hollow nodes; 14) yellow comic burst highlight with an empty center; 15) cyan crop-frame corners with a tiny rotate handle; 16) magenta-and-orange split-screen divider with opposing chevrons. Use actual transparency everywhere outside the artwork; no colored tile backgrounds or panels. Make the spacing and visual weight consistent across all cells, and do not let any artwork touch or cross cell edges.
```

## 裁切、入库与验收

- 可复跑提取及登记脚本：[extract_video_annotation_guide_atlas_20260927.py](../scripts/extract_video_annotation_guide_atlas_20260927.py)。裁成 16 张 512×512 RGBA PNG；裁切前逐格检查可见像素和 8 像素边界安全区。
- 素材已登记在 `builtin_stickers.json`；贴纸库“指引”分类从 13 项增至 29 项。新条目使用项目内部原创 AI 生成许可说明，来源字段记录图集哈希、格行列和 PNG 哈希。
- 16/16 均生成编辑器预览，完成插入、缩放编辑、保存重开、撤销/重做、淡入淡出和 MP4 导出机器审计。最大单项预览/导出平均 RGB 差为 0.298；浅色和深色底接触表及实际编辑器预览均已逐项视觉复核。
- 全库贴纸证据核验：**548/548 合格、548/548 验证、0 失败**；最大预览/导出平均 RGB 差为 1.985。报告见 `output/acceptance/jy-r20-sticker-integrity-20260927-video-guide.json`。
- 相关贴纸浏览器回归 **4/4 通过**，覆盖学习科普、手帐装饰、自然光影和新增指引图集；TypeScript 检查通过。`tests.test_sticker_library`、`tests.test_resource_pack_manifest`、`tests.test_effect_coverage` 与 `tests.test_builtin_preset_catalog` **30/30 通过**。

## 离线资源包

内置资源包升至 **v1.32.0**，含 747 项资源、4,237 个文件。ZIP：`output/acceptance/resource-pack-v1.32.0-video-guide/cutvoke-builtin-resources-v1.32.0.zip`，67,467,952 字节，SHA-256 `9306af909a4326fa27a9cb7733158475184830b76b0fe824b999eaf00fee7ccd`。资源清单 `--check` 通过，ZIP 全部条目校验通过。

贴纸预览和导出验收使用合成浅色/深色背景；它们不能替代复杂实拍画面或独立剪辑用户的体验验收。本批图集未单独授权对外再分发。
