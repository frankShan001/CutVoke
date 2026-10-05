# 庆祝派对贴图批次

本批使用 OpenAI ImageGen 一次生成 4×4 透明图集，再按固定网格裁切为 16 张独立贴图。原图、裁切坐标、源图哈希和每张 PNG 的哈希都保留在 `docs/assets/`，便于复核与复现。

## 贴图清单

| # | 名称 | 文件 |
|---:|---|---|
| 1 | 派对气球束 | `party_balloon_bouquet.png` |
| 2 | 彩纸礼花筒 | `party_confetti_popper.png` |
| 3 | 蜡烛庆祝蛋糕 | `party_candle_cake.png` |
| 4 | 彩带礼物盒 | `party_gift_box.png` |
| 5 | 条纹派对帽 | `party_cone_hat.png` |
| 6 | 卷曲庆祝彩带 | `party_curled_streamer.png` |
| 7 | 星芒庆祝闪光 | `party_starburst.png` |
| 8 | 碰杯果汁杯 | `party_juice_cheers.png` |
| 9 | 粉色庆祝绶带 | `party_rosette_badge.png` |
| 10 | 彩钻派对皇冠 | `party_paper_crown.png` |
| 11 | 奶油庆祝纸杯蛋糕 | `party_cupcake.png` |
| 12 | 派对吹龙 | `party_horn.png` |
| 13 | 彩色烟花绽放 | `party_fireworks.png` |
| 14 | 三角旗派对拉旗 | `party_bunting_garland.png` |
| 15 | 彩纸碎片 | `party_confetti_cluster.png` |
| 16 | 双支庆祝仙女棒 | `party_sparklers.png` |

## 构建与授权说明

- 图集：`docs/assets/celebration-party-sticker-atlas-20260927.png`
- 裁切证据：`docs/assets/celebration-party-sticker-atlas-20260927.extraction.json`
- 贴图总览：`docs/assets/celebration-party-sticker-atlas-20260927-contact.png`
- CutVoke 实际渲染总览：`docs/assets/celebration-party-sticker-rendered-review-20260927.png`
- 渲染帧哈希记录：`docs/assets/celebration-party-sticker-rendered-review-20260927.json`
- 视觉审核记录：`docs/assets/celebration-party-sticker-visual-review-20260927.json`（Codex AI 视觉检查，不代表独立人工验收）
- 提取脚本：`scripts/extract_celebration_party_sticker_atlas.py`
- 目录登记脚本：`scripts/register_celebration_party_sticker_atlas.py`
- 目录验收：16 张均完成预览、工程内应用/调整/保存重载/撤销/导出审核，审核记录与 PNG/预览哈希绑定。
- 资产来源注明为 AI 生成；当前标记为项目内部素材，未单独授予再分发许可。
