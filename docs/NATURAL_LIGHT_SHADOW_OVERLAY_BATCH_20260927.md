# 自然光影覆叠贴图批次（2026-09-27）

## 图集生成

使用内置 OpenAI ImageGen 一次生成一张 4×4 透明 RGBA 图集，再按等分网格提取 16 张独立覆叠贴图。原图为 1254×1254，SHA-256：`e1395dd3c0824fcff72d15618baf3aed43dfe0e874de8093ffe5e1451aec43de`。

原始图集：[`natural-light-shadow-overlay-atlas-20260927.png`](assets/natural-light-shadow-overlay-atlas-20260927.png)。

生成提示词：

```text
Use case: stylized-concept
Asset type: a project-bound 4x4 atlas of transparent video-editor overlay texture stickers
Primary request: create one square atlas containing exactly 16 distinct, polished natural-light and shadow overlays that can be cropped as standalone transparent RGBA PNGs for compositing over live-action footage.
Style/medium: refined photographic light phenomena with subtle translucency and clear silhouette, premium post-production overlay assets
Composition/framing: exact regular 4 by 4 grid in reading order; equal square cells; one complete isolated overlay per cell; generous transparent padding around every item; each item stays fully inside its own cell
Lighting/mood: soft, cinematic and usable over real footage; maintain visible midtone detail
Color palette: varied but restrained warm amber, cool teal, soft rose, leaf green, moonlit blue and neutral white
Materials/textures: natural volumetric light, soft-edged reflections and translucent shadow patterns
Text (verbatim): none
Constraints: genuine transparent alpha background (not a checkerboard, not white or black); no lines or gutters; no overlap across cell boundaries; no text, labels, numbers, logos, frames, or background scenes. Each cell's art must have enough contrast to remain legible at a 512-pixel crop size. Exactly these 16 different assets in reading order: 1 warm window sunbeam shafts; 2 moving leaf-dapple shadows; 3 rippling pool caustic reflections; 4 soft amber candlelight bloom; 5 pale sunrise cloud glow; 6 slanted window-blind light bands; 7 cool moonlit water shimmer; 8 gentle peach atmospheric haze; 9 teal volumetric forest rays; 10 sparkling snow-reflection glints; 11 soft colored prism refraction arc; 12 distant storm flash glow with branching light only, no bolts or scenery; 13 golden-hour dust motes in a diagonal beam; 14 translucent moving-cloud shadow wisps; 15 delicate aurora glow ribbons; 16 warm reflected firelight flicker. Make each texture materially distinct and suitable as an independent compositing overlay.
Avoid: grain, noise, speckles, dirty artifacts, tiny scattered dots except the intentional dust motes in cell 13, hard opaque panels, opaque cell backgrounds, photorealistic objects, lettering, watermarks, or falsely transparent checkerboard patterns.
```

## 裁切、接入与审核

- 可复跑裁切脚本：[`extract_natural_light_shadow_overlay_atlas.py`](../scripts/extract_natural_light_shadow_overlay_atlas.py)；来源网格坐标、透明边界与单张文件 SHA-256：[`extraction.json`](assets/natural-light-shadow-overlay-atlas-20260927.extraction.json)。
- 16 张 512×512 RGBA PNG 注册进“自然光影”分类，默认缩放 0.92，用于铺满画面。每项来源字段绑定原图哈希、格行列、裁切后文件哈希；注册脚本：[`register_natural_light_shadow_overlay_atlas.py`](../scripts/register_natural_light_shadow_overlay_atlas.py)。
- 原始透明裁切的深/浅底接触表：[`深色背景`](assets/natural-light-shadow-overlay-atlas-20260927-contact-dark.png)、[`浅色背景`](assets/natural-light-shadow-overlay-atlas-20260927-contact-light.png)。来自编辑器实际 RenderService 导出帧的复核接触表：[`深色渐变`](assets/natural-light-shadow-overlay-rendered-review-dark-20260927.png)、[`浅色渐变`](assets/natural-light-shadow-overlay-rendered-review-light-20260927.png)。逐项视觉决定绑定原图、预览和机器审计哈希：[`natural-light-shadow-overlay-visual-review-20260927.json`](assets/natural-light-shadow-overlay-visual-review-20260927.json)。
- 16/16 均通过编辑器的插入、变换、保存重开、撤销/重做、淡入淡出、MP4 导出和预览/导出帧差审核；单项记录在 `src/cutvoke/assets/sticker_previews/natural_light_*.audit.json`。生成器将新分类加入纹理覆叠审计背景。
- 全库贴纸证据复核 **516/516 合格、0 失败**，预览/导出最大 RGB 均差 1.985；验证报告：`output/acceptance/jy-r20-sticker-integrity-20260927-natural-light.json`。该接触表使用合成渐变，不替代复杂实拍素材的商业观感或独立用户验收。浅色柔雾在亮背景上对比较弱，适合中暗调画面或轻量氛围叠加。

## 离线资源包

内置资源包升至 **v1.30.0**，包含 715 项资源、4,045 个文件。ZIP：`output/acceptance/resource-pack-v1.30.0-natural-light/cutvoke-builtin-resources-v1.30.0.zip`，64,412,503 字节，SHA-256 `7468e1d3c43c57ccde61e4a153071bf6c8bba01d37ff9e10a695f5e672f7956c`。项目安装器校验为离线可用；缺失文件和哈希错误均为 0，来源与许可字段覆盖 715/715。复核摘要：`output/acceptance/jy-r20-resource-pack-install-20260927-natural-light.json`。

自动化浏览器验收通过自然光影分类 16/516、来源信息、卡片预览、独立覆叠轨插入、0.92 默认缩放、保存重开及 v1.30.0 资源哈希引用。该图集为本项目内部原创 AI 生成素材，未单独授权对外再分发。
