# 舞台灯光覆叠贴图批次（2026-09-27）

按用户建议，使用内置 OpenAI ImageGen 一次生成 4×4 透明 RGBA 图集，再逐格裁切并接入贴纸库的“舞台灯光”分类。图集包含 16 种激光扇、聚光灯、摇头灯、棱镜光晕、星芒、光幕和追光等效果；默认缩放 1.35，适合在暗调或夜景视频上叠加并继续调整。

## 图集提示词

```text
Use case: stylized-concept
Asset type: project-bound 4x4 atlas of transparent video-editor light-effect overlay textures
Primary request: create one square image atlas with exactly 16 distinct, polished stage-light overlays that can each be cropped into a standalone transparent RGBA PNG and composited over live-action footage.
Style/medium: premium concert cinematography light phenomena, refined post-production overlays, detailed but clean and believable
Composition/framing: exact regular 4 by 4 grid in reading order, equal square cells, one complete isolated effect per cell, ample transparent padding, each effect fully inside its cell with no boundary crossing
Lighting/mood: vivid but controlled, layered beams and glow that preserve midtone detail in underlying footage
Color palette: varied deep cyan, ultraviolet, magenta, amber, pearl white, and occasional saturated red or green
Materials/textures: volumetric beams, refracted light, soft stage haze, lens bloom, reflective highlights
Text (verbatim): none
Constraints: genuine transparent RGBA background in all empty areas, not a checkerboard and not white or black; no drawn grid, no gutters, no labels, numbers, icons, logos, stage objects, performers, venue, or background scene. Every cell must have a visibly different overlay, crop-safe, useful at 512-pixel output size. In reading order: 1 cyan and violet fan of narrow concert laser beams; 2 warm amber twin spotlight cones; 3 magenta circular moving-head beam sweep; 4 cool blue vertical volumetric light columns; 5 prismatic rainbow lens flare arc with a bright white core; 6 teal and pink crossing laser diagonals; 7 pearl-white anamorphic flare streak with subtle colored refraction; 8 ultraviolet radial beam burst; 9 amber and rose soft-edged stage haze ribbons; 10 cyan elliptical light tunnel rings; 11 emerald and violet reflected disco-light facets, abstract light only; 12 saturated red and blue side-sweeping beam fans; 13 pale gold spotlight bloom with a crisp visible source edge; 14 magenta and cyan layered light curtains; 15 focused cool-white beam with restrained blue bloom; 16 multicolor prism beam fan with clean translucent edges.
Avoid: opaque panels or backgrounds, visual noise, grain, dirty artifacts, tiny random speckles, repeated designs, photography of people or equipment, lettering, watermarks, or false transparency.
```

## 图集与裁切记录

- 原图：[`concert-stage-light-atlas-20260927.png`](assets/concert-stage-light-atlas-20260927.png)，1254×1254 RGBA，SHA-256 `1524cf92579a123dbcb881c3bdeeb96cd1b58f7f99681baf9dea32402a4e5f64`。
- 深色/浅色棋盘接触表：[`深色底`](assets/concert-stage-light-atlas-20260927-contact-dark.png)、[`浅色底`](assets/concert-stage-light-atlas-20260927-contact-light.png)。
- 提取报告记录了 4×4 每格坐标、透明边界、单张文件 SHA-256 和一处跨格紫色碎片清理：[`extraction.json`](assets/concert-stage-light-atlas-20260927.extraction.json)。脚本：[`extract_stage_light_overlay_atlas_20260927.py`](../scripts/extract_stage_light_overlay_atlas_20260927.py)。
- 每项提取为独立 512×512 RGBA PNG，alpha 小于 8 的像素置透明，每格外沿清 2 px，并居中保留 14 px 透明边距。注册脚本：[`register_stage_light_overlay_atlas_20260927.py`](../scripts/register_stage_light_overlay_atlas_20260927.py)。
- 16 项依次为：青紫激光扇、琥珀双聚光灯、洋红环形摇头灯、蓝色体积光柱、彩虹棱镜光晕、青粉交叉激光、珍珠白变形宽银幕光、紫外星芒光束、琥珀玫瑰雾光丝带、青色光环隧道、翡翠紫色镜面光斑、红蓝横扫光束、金色聚光灯晕、洋红青色叠层光幕、冷白舞台追光、彩色棱镜扇形光束。

## 预览、导出和视觉检查

- 16/16 均完成贴图预览、插入独立覆叠轨、变换、保存重开、撤销/重做、淡入淡出和 MP4 导出机器审计。预览/导出最大平均 RGB 差为 **0.864**；实际渲染帧联系表见 [`concert-stage-light-rendered-review-20260927.png`](assets/concert-stage-light-rendered-review-20260927.png)。生成联系表和哈希绑定视觉复核的脚本分别为 [`create_stage_light_rendered_review_20260927.py`](../scripts/create_stage_light_rendered_review_20260927.py) 与 [`apply_stage_light_visual_review_20260927.py`](../scripts/apply_stage_light_visual_review_20260927.py)，逐项决定见 [`visual-review.json`](assets/concert-stage-light-visual-review-20260927.json)。
- 全库贴纸证据现为 **564/564 合格、0 候选、0 失败**；完整核验报告：`output/acceptance/jy-stage-light-sticker-evidence-20260927.json`，最高预览/导出平均 RGB 差为 1.985。
- 新增浏览器用例验证分类计数、卡片预览、来源、添加到独立叠加轨、1.35 默认缩放和保存重开；该用例 **1/1** 通过。相关 Python 回归 **30/30** 通过，TypeScript 与 Vite 生产构建通过。

## 离线资源包

内置资源包升至 **v1.33.0**，含 763 项资源、4,333 个文件；ZIP 位于 `output/acceptance/resource-pack-v1.33.0-stage-light/cutvoke-builtin-resources-v1.33.0.zip`，69,641,420 字节，SHA-256 `20f8a452a1d48892ea425f8820c06948669578c6b4ad009682caa5825b8868a1`。清单 `--check` 和 ZIP 安装校验通过，缺失文件与哈希错误均为 0。

贴图为本项目内部原创 AI 生成素材，未单独授权对外再分发。联系表使用合成渐变背景；浅色素材上部分柔光对比较弱，建议在暗调镜头上使用。这些检查不替代复杂实拍素材的商业观感、外部剪辑用户盲测或正式发行验收，JY-R02 和 JY-R17 仍保留相应缺口。
