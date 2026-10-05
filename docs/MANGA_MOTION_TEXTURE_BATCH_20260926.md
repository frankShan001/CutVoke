# 漫画动感贴图图集扩充（2026-09-26）

## 交付

使用 Codex 内置 ImageGen 一次生成 4×4 真透明 RGBA 图集，然后按格裁切为 16 张独立 314×314 PNG，扩充现有“漫画动感”贴纸分类（17→33）。每项默认缩放为 0.92，使冲击线、风旋、光环等能覆盖主要画幅；仍可在时间线上继续变换。

- 原始图集：[`docs/assets/manga-motion-atlas-v2-20260926.png`](assets/manga-motion-atlas-v2-20260926.png)
- 透明底裁切接触表：[`docs/assets/manga-motion-atlas-v2-20260926-contact.png`](assets/manga-motion-atlas-v2-20260926-contact.png)
- 实际叠加预览接触表（深色到浅色画面）：[`docs/assets/manga-motion-atlas-v2-20260926-rendered-fullscale-contact.png`](assets/manga-motion-atlas-v2-20260926-rendered-fullscale-contact.png)
- 坐标、边界、可见像素、许可证与 SHA-256：[`docs/assets/manga-motion-atlas-v2-20260926.extraction.json`](assets/manga-motion-atlas-v2-20260926.extraction.json)
- 逐项视觉决定：[`docs/assets/manga-motion-atlas-v2-20260926-visual-decisions.json`](assets/manga-motion-atlas-v2-20260926-visual-decisions.json)
- 可复跑裁切：[`scripts/extract_manga_motion_atlas_v2.py`](../scripts/extract_manga_motion_atlas_v2.py)
- 可校验来源并注册分类：[`scripts/register_manga_motion_atlas_v2.py`](../scripts/register_manga_motion_atlas_v2.py)

## 本批 16 项

| 项目 | 预设 | 项目 | 预设 |
| --- | --- | --- | --- |
| 黑白放射冲击线 | 黑白渐隐放射射线 | 珊瑚红排线裂纹 | 不规则冲击碎裂 |
| 青柠螺旋动线 | 粗细交替的旋涡线 | 象牙白墨烟擦拭 | 墨烟云团和擦拭线 |
| 钴蓝弧形加速线 | 右向弧形残线 | 紫罗兰星屑旋涡 | 星形碎片环绕旋涡 |
| 青绿双环脉冲 | 双重脉冲环 | 洋红错位残影 | 水平拖影 |
| 琥珀尘爆云团 | 烟团和尘屑 | 冰青风洞旋线 | 纵向风旋 |
| 珍珠碎光扇束 | 偏心虹彩碎片射线 | 青柠电光折线 | 多支折线闪电 |
| 紫色半调浪花 | 疏密渐变网点 | 朱红斩击弧线 | 倾斜长斩击线 |
| 金色火花轨迹 | 星芒弧线和光点 | 蓝珊瑚水墨爆发 | 双色综合水墨喷溅 |

ImageGen 提示要求精确 4×4、每格独立透明底图、无文字/水印/边框、轮廓和方向多样。透明图集中的半透明像素被保留；低于 Alpha 8 的噪点清零。裁切前检查有效区域与安全边界，裁切坐标及每项输出哈希写入 extraction JSON。图集和提取贴图为本项目内部原创 AI 生成素材，不单独授权再分发。

## 集成与审计

16 项以 `manga_v2_*` 注册在“漫画动感”类别，来源包含图集 SHA-256、行列坐标和单项 PNG SHA-256；默认缩放 0.92。逐项审阅者字段明确为 `Codex AI visual review`，审阅依据是透明裁切表及深浅渐变实际合成帧。审阅通过不代表真实相机素材上的商业观感已经验收。

- 16/16 生成两秒实际渲染预览，均可见；接触表展示深浅背景下的缩放效果。
- `scripts/audit_sticker_library.py --prefix cutvoke.sticker.manga_v2_` **16/16** 通过应用、变换编辑、保存重开、撤销/重做、淡入淡出、预览/MP4 同帧与导出检查。
- Web E2E 检查类别 33/373、来源图集信息、可用预览、贴纸插入资源引用和 0.92 默认缩放。
- 最终回归：主仓库 Python pytest **197 项通过、500 个子测试通过**；`npm run test:e2e`（含 TypeScript 检查与生产构建）**44/44 通过**；`unittest discover -s tests` **190 项通过**。
- 资源包 v1.20.0：572 项资源、3,187 个文件；373 项合格贴纸、189 项合格预设、10 项背景；`scripts/build_builtin_resource_pack.py --check` 通过。
- `scripts/effect_coverage.py --json` 报告 562 项合格内容、候选 0、子类密度缺口 0。

pytest 默认发现范围由 `pyproject.toml` 限定为仓库维护的 `tests/`，避免把 Git 忽略的 `本地开发/tests` 历史快照混入正式回归。

这批素材是可编辑、可变换的静态贴纸叠加图，不是新的实时画面特效算子，也不能替代真人/相机素材上的特效、人物跟踪或独立用户体验验收。
