# 能量光效贴图批次（2026-09-27）

本批按 4×4 透明图集一次生成 16 张能量光效，再裁切为可独立使用的 PNG，并登记到贴纸库“能量光效”分类。每张贴图有独立预览、编辑/导出审计和绑定当前文件哈希的视觉复核记录。

## 图集与提示词

- 图集：[`energy-light-overlay-atlas-20260927.png`](assets/energy-light-overlay-atlas-20260927.png)，1326×1186 RGBA，SHA-256 `23394d62c1b80284f83a0841d1bd96305ae905da9965c77524ed99867ee5738f`。
- 使用 OpenAI 内置 ImageGen 生成。第一版因光晕和杂点跨格被舍弃；这里只保留经透明边缘检查的第二版。
- 生成提示：

```text
Generate one new transparent RGBA sprite atlas for a video editor. Make an exact 4 columns × 4 rows of equal square cells and place one compact, clearly distinct luminous energy effect in each cell. These are isolated overlay motifs, not backgrounds: (1) a short cyan lightning fork, (2) a smooth violet plasma S-ribbon, (3) a champagne-gold curved spark trail, (4) a compact magenta four-point prism flare, (5) an emerald aurora curl, (6) cobalt radial streak burst, (7) a thin rainbow refracted arc, (8) a clean circular pearl halo, (9) five soft luminous orbs, (10) a compact crystal shard burst, (11) two short cyan-violet energy ribbons, (12) a single fine golden calligraphic loop, (13) three tiny falling stars, (14) a narrow violet-cyan light curtain, (15) a coral pulse-wave symbol, (16) a short gold electric zigzag.
Critical extraction geometry: divide the full image into a precise 4×4 grid of 16 identical square cells. Each cell contains one small centered motif whose ENTIRE visible shape, every antialiased pixel, and any glow fits within the central 50% width and 50% height of that cell; leave the outer 25% on all four sides fully transparent. Keep each motif compact; shorten arcs and ribbons rather than letting them approach a cell edge. No particles or stray pixels outside a motif. Use a hard-contained silhouette with only a very subtle tightly attached glow, no wide bloom. The resulting cells must be safe to crop independently without clipping or neighboring pixels.
Visual style: clean polished digital compositing art with smooth colored gradients and crisp outlines; visually distinct and readable at thumbnail size, usable over both light and dark footage.
Background is truly transparent RGBA. No grid lines, separators, borders, cell shadows, labels, text, letters, numbers, watermark, mockup, full-frame texture, grain, noise, dust, scratchiness, or dirty speckles.
```

## 裁切与入库

- 裁切脚本：`scripts/extract_energy_light_overlay_atlas_20260927.py`。按图像实际比例拆分 4×4 单元，清除 Alpha 小于 8 的边缘残留，围绕可见主体裁切，再输出 512×512 RGBA PNG；每张保留 18px 的透明安全边距。
- 逐项裁切坐标、透明范围、输出尺寸和 SHA-256：[`提取报告`](assets/energy-light-overlay-atlas-20260927.extraction.json)。深色与浅色检查表：[`dark`](assets/energy-light-overlay-atlas-20260927-contact-dark.png)、[`light`](assets/energy-light-overlay-atlas-20260927-contact-light.png)。
- 16 张输出位于 `src/cutvoke/assets/stickers/energy_*.png`，每项默认缩放 0.92，素材登记为项目内部 AI 原创，未单独授权再分发。
- 注册脚本：`scripts/register_energy_light_overlay_atlas_20260927.py`。素材来源字段绑定源图集哈希、行列坐标和提取报告。

## 预览与验收

- 真实播放器预览接触表：[`rendered review`](assets/energy-light-overlay-atlas-20260927-rendered-review.png)；逐项文件哈希：[`rendered review JSON`](assets/energy-light-overlay-atlas-20260927-rendered-review.json)。
- 16/16 通过插入、变换编辑、保存重开、撤销/重做、淡入淡出、导出和预览/导出帧对照；本批最大平均 RGB 差 0.24。视觉决定与素材、预览及审计哈希绑定在 [`视觉复核`](assets/energy-light-overlay-atlas-20260927-visual-review.json)。
- 全库证据复核：612/612 合格、0 失败，机器报告位于 `output/acceptance/energy-light-overlays-20260927/sticker-catalog-verification.json`。
- 离线资源包 v1.38.0：843 项资源、4,653 个文件，ZIP 84,886,144 字节；SHA-256 `a2d4c63871fb14211fce813b11161d16ae287d566c8e5960efeb70ee39622da1`。完整性检查通过。
- Python 贴纸库、离线资源包、覆盖率和目录 API 专项回归：30 项测试、4 个子测试通过。完整 `npm run test:e2e`（含生产构建）：78/78 通过；新用例确认分类计数、来源、卡片预览、独立叠加轨、默认缩放和刷新重开。
- 真实商用素材观感与外部剪辑用户验收仍按剪映对齐任务单保留。
