# 手帐纸艺装饰贴图批次（2026-09-27）

## 生成与裁切

使用 OpenAI ImageGen 生成一张 4×4 透明背景图集，主题为手帐拼贴与纸艺装饰，包含纸片折角、交叉和纸胶带、撕纸框角、描图纸飘带、缝线标签、卷曲纸带、叠层纸花、剪纸星星、叶片、扇形纸边、手绘圈、回形针、撕边贴片、纸屑、双层书签和蝴蝶结。图集与复现提示方向如下：

- 图集：[`scrapbook-decor-atlas-20260927-v2.png`](assets/scrapbook-decor-atlas-20260927-v2.png)，SHA-256 `be90d9c690c2d601b1d47ae77f8498a7f227d62f32fef897ffc6fef2e3dae71c`
- 提示词方向：透明 RGBA 背景；均匀 4×4 网格；每格放置一个完整、独立的 scrapbook / washi / paper-craft 装饰；单品之间留足透明间隔；不加文字、边框或相互遮挡；色彩柔和、材质清晰。
- 逐格坐标、可见边界、Alpha 范围、单品 SHA-256：[`extraction.json`](assets/scrapbook-decor-atlas-20260927-v2.extraction.json)
- 可复跑脚本：[`extract_scrapbook_decor_atlas_20260927.py`](../scripts/extract_scrapbook_decor_atlas_20260927.py)

共裁出 16 张 320×320 RGBA PNG。底行的哑光纸屑和双层书签贴到名义网格上沿，裁切脚本各向上扩展 20 个源像素以保留完整内容；16 张结果均无可见像素触及 8 像素安全边界。透明原图接触表：[`深色底`](assets/scrapbook-decor-atlas-20260927-v2-contact-dark.png)、[`浅色底`](assets/scrapbook-decor-atlas-20260927-v2-contact-light.png)。

## 接入与审核

16 张贴图以可编辑贴纸素材加入“装饰”分类，分类数量由 12 增至 28；逐项登记来源、许可证、默认缩放和视觉审核记录。注册脚本为 [`register_scrapbook_decor_atlas_20260927.py`](../scripts/register_scrapbook_decor_atlas_20260927.py)。逐项视觉备注与哈希绑定记录见 [`scrapbook-decor-atlas-20260927-v2-visual-review.json`](assets/scrapbook-decor-atlas-20260927-v2-visual-review.json)；透明素材实际进入编辑器并渲染的复核图见 [`rendered-review.png`](assets/scrapbook-decor-atlas-20260927-v2-rendered-review.png)。

16/16 已完成贴纸插入、变换编辑、保存重开、撤销和 MP4 导出机器审计；贴纸证据完整性复核为 500/500 verified and qualified，预览与导出的最大 RGB 均差为 1.985。资源覆盖为 119 项注册效果、189 项合格预设、500 项合格贴纸、689 项合格内容；候选数和分类密度缺口均为 0。

全量 Python 测试 **213 项及 500 个子测试通过**。Playwright 全量回归 **65/65 通过**；新增本批专属浏览器用例 **1/1 通过**，验证“装饰 28 / 500”、来源面板、贴纸叠加轨插入、资源版本/哈希、默认缩放 0.28 和重开保存。TypeScript 检查、Vite 构建、资源包 `--check`、贴纸证据核验及 `git diff --check` 均通过。

资源包更新至 v1.29.0，含 699 项资源、3,949 个文件。ZIP 位于 `output/acceptance/resource-pack-v1.29.0-scrapbook/cutvoke-builtin-resources-v1.29.0.zip`，大小 59,140,693 字节，SHA-256 `f2ba037ef8d4fc6f2fe29d4ab645357b02069055c17ae031ea3ee5ecb8ade146`。使用正常安装器在临时目录完成安装验证：缺失文件 0、无效哈希 0、离线可用；完整来源记录 699/699。核验摘要见 `output/acceptance/jy-r20-sticker-integrity-20260927-scrapbook.json`。

## 使用边界

图集为项目内原创 AI 生成素材，清单注明“Project-internal original (AI-generated); not separately licensed for redistribution”。该记录不能作为对外再分发授权。浅色纸片在明亮画面中对比度较低，炭黑手绘圈适合浅色背景；合成画面复核不能代替更多实拍环境及独立剪辑用户的观感验收。
