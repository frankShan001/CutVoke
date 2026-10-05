# 人物氛围叠加贴图图集（2026-09-25）

本批将一张 ImageGen 生成的透明 4×4 PNG 图集裁切为 16 张独立贴图，加入贴纸库“人物氛围”分类。资源适合叠加在已抠像人物附近；它们本身不是自动跟踪人物的特效预设。

## 图像来源

- 图集：`docs/assets/person-fx-overlay-atlas-20260925.png`
- 图集 SHA-256 与逐格坐标、裁片尺寸/可见边界、alpha 范围和裁片哈希：`docs/assets/person-fx-overlay-atlas-20260925.extraction.json`
- 浅深底透明检查接触表：`docs/assets/person-fx-overlay-contact-20260925.png`
- 实际合成预览接触表：`docs/assets/person-fx-overlay-preview-contact-20260925.png`
- 逐项视觉观察及哈希绑定决定：`docs/assets/person-fx-overlay-visual-review-20260925.json`
- 源码裁切器：`scripts/extract_video_overlay_texture_sheet.py`
- 目录登记器：`scripts/register_person_fx_atlas.py`

生成方式：Codex 内置 ImageGen。实际使用的提示词：

> Use case: stylized-concept. Asset type: transparent overlay texture atlas for a video editor's person-cutout compositing library. Create one precisely aligned 4x4 square atlas with sixteen different standalone visual effect overlays, one centered in each cell in row-major order: (1) warm amber aura ring with gap; (2) cyan neon halo ring; (3) peach-lilac bokeh halo; (4) fine cyan-violet electric arc ring; (5) sparse golden orbiting motes; (6) monochrome manga radial rays; (7) pearl crescent strokes; (8) cyan pulse rings; (9) cyan holographic scan bands; (10) prismatic glass refraction wisps; (11) luminous orbital dots and arcs without symbols; (12) soft violet spotlight glow; (13) short cyan/magenta chromatic afterimage streaks; (14) two translucent teal energy ribbons framing an empty center; (15) thin pale-gold expanding shockwave rings; (16) a controlled handful of cyan-violet pixel shards. Each cell should read as a usable transparent video overlay texture, not as a sticker with a solid background. Refined cinematic motion-graphics compositing, crisp alpha edges, restrained detail, luminous but clean, professional editor asset quality. Square 4x4 grid of equal square cells, aligned precisely; no visible grid lines, dividers, frames, numbering, labels, UI, or text. Fully transparent background in all negative space. Consistent scale, every cell centered with visible safe margins. Varied warm amber, pearl, cyan, violet, teal, and restrained magenta, coordinated across the atlas. Actual transparency, sixteen and only sixteen separate cells, one overlay per cell, consistent square tiles, no visible checkerboard, no shadows or opaque rectangles behind overlays, no text or watermarks, preserve clean transparency around all effects.

## 贴图目录顺序

| 图集序号 | 资源 ID 后缀 | 显示名 |
| --- | --- | --- |
| 01 | `personfx_aura_amber` | 琥珀环形气场 |
| 02 | `personfx_halo_cyan` | 青色霓虹光环 |
| 03 | `personfx_bokeh_halo` | 柔彩散景光环 |
| 04 | `personfx_arc_electric` | 青紫电弧光环 |
| 05 | `personfx_orbit_motes` | 金色星点轨迹 |
| 06 | `personfx_manga_rays` | 黑白漫画射线 |
| 07 | `personfx_energy_crescent` | 珍珠弧光 |
| 08 | `personfx_pulse_rings` | 青色脉冲光圈 |
| 09 | `personfx_scan_bands` | 全息扫描光带 |
| 10 | `personfx_prismatic_ribbons` | 棱镜彩虹光弧 |
| 11 | `personfx_orbit_constellation` | 环绕光点 |
| 12 | `personfx_spotlight_violet` | 紫色聚光 |
| 13 | `personfx_chromatic_afterimage` | 青紫色残影 |
| 14 | `personfx_energy_ribbons` | 青绿色能量飘带 |
| 15 | `personfx_shockwave_gold` | 金色冲击波 |
| 16 | `personfx_pixel_shards` | 霓虹像素碎片 |

资源使用项目内部原创素材授权标记，外部分发许可未单独授予。生成图像和裁片的来源可追溯；这不构成对外商业再分发的授权声明。

## 验收

- 16 个裁片均保留 RGBA，alpha 最小值为 0、最大值不低于 232；每格有超过 14,000 个有效 alpha 像素，没有空格或相邻格混入。
- 每项生成 2 秒实际合成预览；机器审计覆盖插入、调整、保存重开、撤销/重做、导出和预览/导出帧对照；16 项全部获得逐项哈希绑定视觉批准。
- `人物氛围` 分类在 Web 贴纸库可预览并添加到独立叠加轨。v1.17.0 离线包 ZIP 为 37,281,946 字节，SHA-256 `8894d9d96d625baa057e09abdf1cd4e429f09cbf6dc8a969793776b3b5c1c8e5`；完整 516 项/2,875 个文件安装、启用并从 API 检查，16 张人物氛围贴图全部可用。包验收摘要：`output/acceptance/resource-pack-v1.17.0/acceptance-summary.json`。
- 定向 Python 回归 28 项和 4 个子测试通过；完整 Python 回归 797 项和 484 个子测试通过。`npm run test:e2e` 中 TypeScript、Vite 构建及 Playwright 43/43 通过；`scripts/build_builtin_resource_pack.py --check` 验证 516 项/2,875 个文件；`effect_coverage.py` 报告 506 项合格内容、候选数与密度缺口均为 0。
- 人物抠像结果可与贴纸轨组合使用；本批没有加入脸部/人体锚点跟随，也未证明运动中的贴纸会跟随人物。
