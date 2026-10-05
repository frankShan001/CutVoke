# 东方水墨与金彩动势叠加贴图（2026-09-26）

按用户建议使用内置 ImageGen 一次生成 4×4 透明 RGBA 图集，再逐格裁切为独立贴图。原始图集为 [`docs/assets/east-asian-ink-motion-overlay-atlas-20260926.png`](assets/east-asian-ink-motion-overlay-atlas-20260926.png)，逐格坐标、SHA-256、透明范围和完整提示词记录在 [`docs/assets/east-asian-ink-motion-overlay-atlas-20260926.extraction.json`](assets/east-asian-ink-motion-overlay-atlas-20260926.extraction.json)。透明底联系表为 [`docs/assets/east-asian-ink-motion-overlay-contact-20260926.png`](assets/east-asian-ink-motion-overlay-contact-20260926.png)，真实合成预览联系表见 [`output/acceptance/jy-r20-ink-motion-overlays-20260926/composite-contact-sheet.png`](../output/acceptance/jy-r20-ink-motion-overlays-20260926/composite-contact-sheet.png)。提取与登记脚本为 `scripts/extract_east_asian_ink_motion_atlas.py` 和 `scripts/register_east_asian_ink_motion_atlas.py`。

本批为浓墨飞溅、朱红飞白笔势、金彩墨点爆发、青碧水墨晕染、枯笔扫痕、墨色回旋烟丝、粉樱花瓣弧线、金箔流线、翡翠水纹环、白梅金粉花簇、暖金云母尘迹、悬浮浓墨滴、抽象书写弧线、朱金流体涡旋、炭墨轻雾、青铜碧色流体环，共 16 张 320×320 独立 PNG。所有格子保留生成图集的 Alpha 通道，来源和裁切列/行保存在贴纸详情及 SHA-256 清单中。许可字段标注为项目内部 AI 生成素材，不单独授权再分发。

16/16 均完成合成预览、贴纸插入、变换、保存重开、撤销/重做及 MP4 导出机器审计，并逐项检查透明原图和深浅渐变合成效果。贴图归入“贴纸 / 特效贴图”，新批次默认缩放 1.1；E2E 覆盖分类数量、来源展开、预览和独立叠加轨插入。

资源包更新至 v1.24.0，共 635 项资源与 3,565 个登记文件；资源库为 189 项合格预设、436 项合格贴纸、625 项合格内容，候选 0、必需分类密度缺口 0。“特效贴图”由 47 项扩至 63 项。本次视觉审阅使用生成的深浅渐变合成画面；真实摄影素材上的逐项商业观感仍需实拍样片验收。

回归结果：本批贴纸库机器审计 **16/16 通过**，新增分类/预览/来源/插入 E2E 覆盖通过；完整 `uv run python -m pytest tests -q` 为 **201 项通过、500 个子测试通过**；最终 `npm run test:e2e` 的 TypeScript 检查、Vite 生产构建通过，Playwright **56/56 通过**；资源包 `--check` 核验 635 项/3,565 个文件通过，`git diff --check` 通过。离线 ZIP 位于 `output/acceptance/jy-r20-ink-motion-overlays-20260926/cutvoke-builtin-resources-v1.24.0.zip`，大小 **49,110,161 字节**，SHA-256：`446C2ADF34782E377C6D87345CC6EBE7E03D2698BE8B6D6D267321DAB1C44A20`。

这些是本地生成、合成预览和机器回归证据。实拍环境观感、正式发布者密钥、公开目录及生产 CDN 尚未验收。
