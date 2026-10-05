# 像素游戏特效贴图图集（2026-09-26）

## 交付

为现有“贴纸 / 特效贴图”补充一组像素游戏风格叠加素材。单张 ImageGen 图集裁出 16 项；原分类从 16 项扩至 32 项。每张素材均作为独立透明 PNG，可搜索、预览、变换、上独立叠加轨并进入工程资源包。

- 原始 4×4 图集：[`docs/assets/pixel-game-effect-atlas-20260926.png`](assets/pixel-game-effect-atlas-20260926.png)
- 透明底裁切联系表：[`docs/assets/pixel-game-effect-atlas-20260926-contact.png`](assets/pixel-game-effect-atlas-20260926-contact.png)
- 深浅渐变画面合成预览：[`docs/assets/pixel-game-effect-rendered-review-20260926.png`](assets/pixel-game-effect-rendered-review-20260926.png)
- 网格坐标、有效边界、透明度和逐项 SHA-256：[`docs/assets/pixel-game-effect-atlas-20260926.extraction.json`](assets/pixel-game-effect-atlas-20260926.extraction.json)
- 逐项视觉观察及媒体 SHA-256：[`docs/assets/pixel-game-effect-visual-review-20260926.json`](assets/pixel-game-effect-visual-review-20260926.json)
- 可复跑裁切与登记：[`scripts/extract_pixel_game_effect_atlas.py`](../scripts/extract_pixel_game_effect_atlas.py)、[`scripts/register_pixel_game_effect_atlas.py`](../scripts/register_pixel_game_effect_atlas.py)
- 源素材副本：`src/cutvoke/assets/stickers/fxoverlay_pixel_*.png`

## 贴图清单

| # | 贴图 | # | 贴图 |
| ---: | --- | ---: | --- |
| 1 | 金色像素冲击 | 9 | 紫色像素能量球 |
| 2 | 冰晶像素爆发 | 10 | 紫黑像素烟团 |
| 3 | 橙色像素火球 | 11 | 街机金币叠影 |
| 4 | 蓝色像素电弧 | 12 | 青柠像素毒雾泡泡 |
| 5 | 紫色像素传送门 | 13 | 冰蓝像素护盾 |
| 6 | 绿色像素治疗能量 | 14 | 橙色像素碎石爆发 |
| 7 | 洋红像素冲击波 | 15 | 彩虹像素星光 |
| 8 | 青色像素疾速拖尾 | 16 | 红白像素重击闪光 |

## 生成与裁切

使用内置 ImageGen，一次生成单张 4×4 真透明图集。输出为 1254×1254 RGBA PNG；Alpha 范围为 0–255。按等分坐标逐格裁切，支持 313/314 像素的单元宽高差；把 Alpha 小于 8 的像素归零，并检查空单元及 8 像素安全边界。16 格全部通过边界检查；每格保留半透明辉光，裁切和 SHA-256 记录写入 extraction JSON。素材登记为 `cutvoke.sticker.fxoverlay_pixel_*`，版本 `1.0.0`，默认缩放 `0.92`，子分类为“特效贴图”。

本批生成所用提示词：

```text
Use case: stylized-concept
Asset type: a transparent sprite atlas for a desktop video editor's sticker and overlay library
Primary request: Create exactly one clean 4 by 4 grid atlas containing 16 distinct standalone retro pixel-art video effect sprites. Each sprite is a decorative overlay for gaming clips and must work as an independent sticker when one grid cell is cropped.
Scene/backdrop: genuinely transparent alpha background, no visible backdrop, no checkerboard pattern
Subject: In reading order, make these 16 distinct sprites: 1) golden pixel impact burst, 2) icy cyan 8-bit frost burst, 3) orange pixel fireball with a short upward trail, 4) electric blue pixel lightning fork, 5) purple pixel portal ring, 6) green healing energy swirl with small plus-shaped particles but no text, 7) magenta pixel shockwave rings, 8) turquoise dash-speed streaks, 9) faceted purple power-up orb with small square sparks, 10) dark violet pixel smoke puff, 11) several glowing arcade coins in an angled stack, 12) lime green toxic vapor bubbles, 13) blue pixel shield aura, 14) orange stone debris burst, 15) rainbow 8-bit sparkle cluster, 16) red-white critical impact starburst with blocky afterimage.
Style/medium: polished 16-bit pixel art, crisp intentional square pixels, limited retro palette per sprite, subtle glow contained within sprite edges, consistent detail level
Composition/framing: square 4×4 grid with perfectly equal cells; exactly one centered sprite per cell; generous transparent padding around every sprite; sprites do not touch cell edges or neighboring cells; consistent scale; no grid lines
Lighting/mood: vivid arcade energy, high contrast
Text (verbatim): none
Constraints: preserve real transparency; all 16 items visually distinct; make every asset crop-safe with an empty gutter between grid cells; output the complete atlas only
Avoid: letters, numbers, labels, logos, watermark, UI, characters, weapons, realistic photographs, shadows outside the sprite, opaque background, gradients or grid dividers connecting neighboring cells.
```

## 审阅与验证

- 透明 PNG 联系表及 320×180 深浅渐变合成联系表逐项复核；16 项边缘完整、中心主体可见，颜色、形状和用途彼此有区分。视觉审批以“Codex 视觉审阅”署名并绑定图像与预览哈希；这是对合成样张的图像审阅，不代表独立用户评估或真实摄影素材验收。
- `scripts/generate_sticker_previews.py --prefix cutvoke.sticker.fxoverlay_pixel_`：16/16 实际合成预览通过。
- `scripts/audit_sticker_library.py --prefix cutvoke.sticker.fxoverlay_pixel_`：16/16 通过应用、变换编辑、撤销/重做、保存重开、入场/出场动画、预览与 MP4 导出一致性检查。
- Playwright 验证分类数量、卡片预览、原图集来源、资源 SHA-256、独立叠加轨插入及默认缩放：1/1 通过。
- `scripts/effect_coverage.py --json`：389 项合格贴纸、578 项合格内容、候选 0、必需子类密度缺口 0；“特效贴图”细分类 32 项。
- 内置离线资源包更新为 v1.21.0：588 项资源、3,283 个文件；`scripts/build_builtin_resource_pack.py --check` 通过。
- `npm run test:e2e`：TypeScript 检查、Vite 生产构建和 Playwright 全量回归 47/47 通过；Python 正式 `tests/` 全量 pytest 197 项、500 个子测试通过。

所有 PNG 是本项目内部原创 AI 生成素材，不单独授权再分发。以上自动和图像检查没有验证真实相机素材上的商业观感。
