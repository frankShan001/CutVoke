# 可复用 Vlog 特效

[English](vlog-effects.md) · **简体中文**

本轮新增四项真实可渲染能力，共用现有 FFmpeg 预览与导出链：两项使用 `geq` 做逆向镜头采样，两项使用 `xfade` 做双镜头时钟与混合。受限参数及采样表达式由 [`vlog_fx.py`](../../src/cutvoke/core/vlog_fx.py) 自行实现，没有导入第三方着色器代码、跟踪模型、付费素材或常驻服务；它们不是仅有名字的预设。

| 效果 ID | 实际行为 | 主要参数与素材条件 |
| --- | --- | --- |
| `cutvoke.fx.subjectLock` | 把手工两点源路径移至固定目标位置，必要时自动增加裁边缩放，覆盖画框边缘 | 适合近似直线移动的单一主体。`fromX/Y`、`toX/Y`、`targetX/Y`、`start`、`duration`、`path`、最小 `zoom` |
| `cutvoke.fx.punchIn` | 围绕指定目标推近、停留并平滑返回，可带轻微回弹 | 反应、包袱或细节强调。`anchorX/Y`、`zoom`、`start`、`enter`、`hold`、`leave`、`overshoot` |
| `cutvoke.transition.matchAlign` | 对两个镜头分别移动、缩放、旋转，在交接中点对齐主体，再恢复各自构图 | 需要形状或主体可匹配的相邻素材。`aX/Y`、`bX/Y`、`aScale`、`bScale`、`aAngle`、`bAngle`、`mixWidth`、`duration` |
| `cutvoke.transition.lumaReveal` | 根据画面实际亮度逐像素揭示下一镜头，可选择暗部优先 | 适合有明暗结构的 SDR 素材。`softness`、`reverse`、`keySource`、`curve`、`duration` |

已有甩镜、拉远、漏光、快门运动模糊、颗粒、震动、蒙版及普通 `xfade` 转场继续复用，不重复计数。仓库原有 `media.trackPoint` 能返回只读光流附着点；本轮手工主体锚定不调用它，也不声称自动识别或追踪。

## 编辑器与 AI 调用

选中图片或视频，在右侧属性的画面/片段特效中启用效果并调参。坐标是素材画面在适配画布之前的归一化坐标，左上角为 `(0,0)`。主体锚定的起点、终点和目标限制为 `0.2–0.8`；自动裁边倍率最高 `4×`。路径外保持端点位置，不处理遮挡、独立缩放、旋转或任意曲线路径；需要时分段或使用普通变换关键帧。

转场挂在后一段。点击接缝或选中后一段，在转场面板选择效果、修改额外参数，点击“预览当前转场”，再应用/切换。试算使用当前草稿参数，不改工程；只改时长会保留已有转场的其它参数。匹配转场在运动时镜像延展边缘，适合克制的位移和角度；缩放是交接时的手工变换，不是自动物体大小匹配。正角度在图像坐标中顺时针旋转。

内核转场时长范围是 `0.05–2 秒`，现有编辑器接缝策略至少 `0.1 秒`，并按相邻片段长度限制生效时长。强调推近在 `start + enter + hold + leave` 后返回；片段应覆盖完整动作。效果不改变音频或时间线长度。没有宣称参数支持关键帧动画；已有特效时间区间可以限制几何效果的启用区间，但不会重置内部时钟。转场遵守既有接缝与素材把手规则。

AI 通过 `effects_list` / `resources_list` 查询，再用 `editor_help(topic="command", command="effect.add", effectId="…")` 获取参数。几何效果用 `effect.add`，转场用 `effect.setTransition`；[英文示例](vlog-effects.md#editor-and-ai-use) 给出了完整命令。分组写入先获取编辑租约，试算通过 `command_preview`，提交使用 `command_apply` 的当前 revision、稳定 commandId 和租约 ID。`command_preview` 本身不接受 `editLeaseId`。越界值、未知参数、把布尔值当数字以及 NaN/Infinity 会被拒绝。撤销重做、旁路、保存重载和工程打包沿用普通效果实例。

效果名、说明、参数与枚举标签覆盖全部 11 种界面语言，尚未经过母语审校。API 标识和用户工程文字不会随界面语言翻译。

## 参考依据与观察限制

[研究记录](vlog-effect-references.json) 保存了 URL、观察方法和 **2026-10-19** 的页面/索引热度快照。它们不是实时计数或排名，也不能证明某种特效造成了高播放。

| 来源 | 快照及其支持范围 |
| --- | --- |
| [Daniel 主体锁定教程](https://www.youtube.com/watch?v=iPlpAuY7nO4)；[Alli and Will 文字教程](https://www.alliandwill.com/blog/locked-on-stabilization-effect-in-davinci-resolve) | 视频原页约 222 万观看。文字明确描述高对比目标跟踪、反向移动、裁边；本轮简化为手工两点输入。 |
| [Emma 烹饪视频](https://www.youtube.com/watch?v=_kJjvzC2QdI)；[本人采访](https://www.highsnobiety.com/p/emma-chamberlain-interview/) | 索引 5,973,996 次观看。采访描述包袱处放大与快切，支持强调镜头配方；不声称烹饪视频某个具体镜头采用了本实现。 |
| [Daniel 糖果匹配剪辑教程](https://www.youtube.com/watch?v=q-ILDPws_Z4)；[烹饪 B-roll](https://www.youtube.com/watch?v=JtitcwwEl-A) | 前者索引 87,098 次，后者原页约 311 万。教程元数据支持匹配剪辑的手法；热门 B-roll 仅作创作者背景。锚点、尺度、角度参数是本轮实现选择。 |
| [Peter Luma Fade](https://www.youtube.com/watch?v=1Qm1EGxdZ6E) | 索引 783,022 次。具名手法启发内容亮度蒙版；具体阈值公式和控件自行实现。 |
| [KOLD My Year 2016](https://www.youtube.com/watch?v=QJbpJQscn9E)；[Casey Make It Count](https://www.youtube.com/watch?v=WxfZkMm3wcg)；[Peter The Bucket Shot](https://www.youtube.com/watch?v=r25IWquxe9s) | 分别为原页 8,242,321、索引 33,421,051、索引 3,866,297 次。只作热门 Vlog/创作者影片的发现背景，不把新增效果归因于未观看的具体镜头。 |

**播放限制：**云浏览器实际尝试了三片，页面和广告可以加载，但正片在刷新重试后仍停在 `0:00` 缓冲，字幕产物也不可读。因此只核验来源元数据和明确文字描述，没有逐镜观看、连续观片或时间码证据。没有复制视频、编造镜头时间点或声称精确复刻。四项是依据创作者文字制作的可用配方；原创程序插画样片验证行为，真实实拍素材的艺术验收仍待后续完成。

## 复现、产物与社区扩展

```bash
python -m pytest -q tests/test_vlog_effects.py tests/test_effect_contributions.py
python scripts/verify/demo_vlog_effects.py --output .artifacts/acceptance/vlog-effects-new
cutvoke serve --data .artifacts/acceptance/vlog-effects-new/data
```

每次选择新的输出目录。静音样片用原创程序插画，通过真实 stdio MCP 制作四组 `640×360 / 24fps` 前后对照，以及 16 秒 `1280×720` 总览。`projects/*.cvkpkg` 和 `projects/*.json` 保留四项效果各自的可编辑工程与参数，`data/` 是持久素材库。`comparison.cvkpkg` 是对渲染结果的可编辑展示排版；修改效果参数请打开对应单项工程。`frames/`、`contact.png`、`calls.json`、`result.json` 保留真实画面、调用、每次导出耗时、预检、流信息与完整解码证据。耗时仅代表该次环境，不是跨机器性能保证。样片无音轨，不声称试听验收。

![原创素材前后对照](../assets/vlog-effects-contact.png)

[验证快照](../assets/vlog-effects-verification.json)记录真实参数、输出哈希、帧数、本地导出耗时及便携工程解包后重渲染对比。完整浏览器运行通过 102 项；剩余录屏场景起初缺少 Playwright 专用 FFmpeg，补齐既有测试依赖后复测通过，没有放宽产品断言。

[暗墨揭示社区示例](../../effects/transitions/cutvoke.transition.community.luma-ink/README.zh-CN.md) 使用同一个亮度渲染器和不同默认值，是扩展配方而非第五个算法。内置四项正常打包，社区目录仍需 `CUTVOKE_EFFECTS_PATH` 显式启用，修改清单后重启服务。需要包含 `vlog_fx.py` 的最新源码；旧 `0.3.1` 二进制可能不具备这些映射。最低内核版本字段仅供说明，不负责能力协商。

遵循[贡献指南](effect-contributing.zh-CN.md)：稳定 ID、作者、许可、来源、可支持实现、参数边界、真实本地预览、非法输入测试、帧数/透明度/局部预览对照和可编辑样片。`x-cutvoke-label`、`x-cutvoke-step`、`x-cutvoke-enum-labels` 可驱动通用控件及转场面板；清单不能引入任意代码或任意 FFmpeg 表达式。

## 成熟开源实现的复用边界

[固定提交调查记录](../development/vlog-upstream-review.json) 保存 commit、文件 blob 与许可证据；**本轮没有导入这些项目的代码或素材**。

- [gl-transitions](https://github.com/gl-transitions/gl-transitions/tree/e1e4bde9b4dad3cb7bdd7ed28e4b97f469d90448) 的 `CrossZoom.glsl`、`GlitchMemories.glsl` 文件头明确 MIT。根许可让单文件声明优先，不能概括为所有文件一律 MIT；先与已有转场去重。
- [gl-transition-libs](https://github.com/gre/gl-transition-libs/tree/72077dad05804b70d38d39bcc1a4282eccad78e9) 是 MIT 接入参考，含 headless 单帧 CLI；不等同于完整视频解码、音频、时间线和导出链。
- [Pixi filters](https://github.com/pixijs/filters/tree/e9d1ca987864f121680bb0d7e9612c05b37748de) 的已核验根许可为 MIT。辉光、RGB 分离、老胶片、CRT、冲击波和缩放模糊仍需离线适配，验证时间、随机种子、透明度与色彩；其中多项已有同类能力。
- [Editly 转场适配](https://github.com/mifi/editly/blob/dc46674052eacd15ef6e0f563a58eb378b3804ad/src/transition.ts) 提供 MIT/headless-gl 的双帧纹理上传与 RGBA 读回参考。Node/OpenGL 依赖、默认裁切、随机性和逐帧资源管理都要另行评估。

FFmpeg 保持具体构建的独立许可；CutVoke 原创效果采用 MIT，不会把外部 FFmpeg 重新许可成 MIT。本轮没有增加 GPU 栈、frei0r/MLT/movit 依赖或自编 FFmpeg，也没有宣称已经接入 gl-transitions/Pixi。
