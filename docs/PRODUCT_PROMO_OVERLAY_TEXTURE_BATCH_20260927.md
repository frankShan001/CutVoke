# 产品广告叠加贴图批次（2026-09-27）

为 OpenCut 新增一组适用于商品展示、产品短片和宣传视频的静态叠加贴图。最终图集由一次 ImageGen 编辑生成 4×4 RGBA 雪碧图；首版因底行两格素材互相跨格而弃用，最终版重新生成后再裁切，未把被裁坏的图片加入素材库。

## 最终使用的生成提示

```text
Redraw this atlas as a clean, production-usable transparent RGBA sprite sheet for a professional video editor. Preserve the same 4 columns × 4 rows, the same 16 premium product-ad decoration concepts, and the restrained champagne gold, pearl, opal, silver, and muted teal style, but recompose every item smaller and centered. Critical extraction rule: each item's visible pixels must fit entirely inside the central 58% of its own equal square cell, leaving at least 20% transparent padding on every side; absolutely no shape, glow, sparkle, shadow, ray, line, or antialiasing may touch or cross a cell edge or enter a neighboring cell. For wide concepts (orbit, ribbon, callout lines, frame corners), shorten or compact the shape so it still fits within the central 58% width and height of ONE cell. Keep the grid positions exactly regular, 4×4, but do not draw separators, cell borders, labels, text, letters, numbers, watermarks, products, mockups, or backgrounds. Use smooth clean alpha edges and no colored noise around the art. One distinct isolated motif per cell, centered with generous transparent negative space, crisp at thumbnail size.
```

## 图集与素材

- 源图：[`product-promo-overlay-atlas-20260927.png`](assets/product-promo-overlay-atlas-20260927.png)，1254×1254 RGBA；SHA-256：`c84c1b462af2320b99a3c093f71797610260193f6f2963a626a714dd301e6dac`。
- 提取脚本：`scripts/extract_product_promo_overlay_atlas_20260927.py`；登记脚本：`scripts/register_product_promo_overlay_atlas_20260927.py`。
- 裁切报告：[`逐格坐标、透明像素和 16 张 PNG 哈希`](assets/product-promo-overlay-atlas-20260927.extraction.json)。每格独立裁为 320×320 RGBA；16/16 通过 8 像素安全边距和串格检测，素材均含透明与可见像素。
- 深/浅底接触表：[`深色`](assets/product-promo-overlay-atlas-20260927-contact-dark.png)、[`浅色`](assets/product-promo-overlay-atlas-20260927-contact-light.png)。Codex 检查了每格轮廓、裁切、Alpha 边缘、缩略图辨识度和两种背景下的对比。
- 实际播放器合成表：[`product-promo-overlay-rendered-review-20260927.png`](assets/product-promo-overlay-rendered-review-20260927.png)；预览文件 SHA-256 汇总：[`JSON`](assets/product-promo-overlay-rendered-review-20260927.json)。视觉决定及其绑定素材/预览哈希：[`product-promo-overlay-visual-review-20260927.json`](assets/product-promo-overlay-visual-review-20260927.json)。

16 项分别为：香槟金圆角构图角、珍珠轨道环、奶油金丝绸环、虹彩棱镜折射、金箔弧形飘带、阶梯产品展示台、留白标注引线、奶油金放射柔光、欧泊玻璃光环、青瓷叶片柔影、空白双线圆章、三枚珍珠星芒、青瓷流动玻璃带、金箔角花、珍珠对焦光圈、空白鎏金徽章。素材登记在“产品广告”分类，默认缩放 0.86，可在贴纸库筛选、预览并插入独立叠加轨。

## 验收结果

- 16/16 生成了真实播放器 MP4 预览，并完成贴纸插入、缩放编辑、保存重开、撤销/重做、淡入/淡出、导出和预览/导出画面对照机器审计；共 80 项核心编辑检查。该批预览与导出帧的最大平均 RGB 差为 0.238。
- 全库贴纸证据复核为 **596/596 合格、0 失败**；新分类 **16/16**。完整机器报告：`output/acceptance/product-promo-overlays-20260927/sticker-catalog-integrity.json`。
- Web 专项用例确认 16/596 分类计数、素材来源、卡片视频预览、独立叠加轨、默认缩放和刷新重开。完整 Playwright **77/77 通过**（含生产构建和 TypeScript 检查）；全量 Python **227 项、504 个子测试通过**。
- 离线资源包更新为 **v1.35.0**：795 项资源、4,525 个文件。ZIP：`output/acceptance/product-promo-overlays-20260927/cutvoke-builtin-resources-v1.35.0.zip`，72,998,135 字节，SHA-256 `c031d17508954df47369abb54fe419d6a4dcccdf2545525b9e30aa992f3bee8e`；ZIP 完整性、安装测试和资源清单 `--check` 均通过。
- 素材许可为“项目内部原创（AI 生成）；未单独取得再分发许可”。Codex 图像复核和合成渐变视频审计不等于独立剪辑用户或目标实拍素材验收；外部体验与正式发行条件仍按任务单保留。
