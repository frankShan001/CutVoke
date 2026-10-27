# After Effects 调研与 CutVoke 改进

调研日期：2026-10-27。CutVoke 基线：`ad6624d` 加本地未提交的独立工程目录与重度剪辑修复。
依据 Adobe 在线帮助、Adobe Community 原帖、Video Copilot 的作者说明和 Reddit 用户原帖；没有运行 AE 实机对照，不能据此声称性能或完整功能对齐。
社区帖子反映具体用户的经历，不代表故障发生率；2022 年的 Beta 反馈也不当作 2026 年正式版缺陷。

## 结论与产品边界

AE 值得借鉴的核心是可组合的编辑对象、上下文属性、精确动画控制、可复用配方，以及快速验证的工作流。
AE 的一层通常对应一个素材，CutVoke 的一轨可以包含多个剪辑片段，因此应借鉴操作原则，并保持 CutVoke 的剪辑时间线模型。[创建图层](https://helpx.adobe.com/after-effects/desktop/work-with-layers/create-layers/creating-layers.html)

先完成快速应用、时间线筛选、保持插值、缓动助手、效果栈筛选和复杂参数表单；随后按优先级扩展合成、实例模板、空间路径、基础算子和实际性能反馈。
人工和外部 Agent 使用同一工程、同一版本控制、同一撤销和渲染结果；自动规划与艺术判断继续交给外部 Agent。

## 官方能力与社区经验

### 1. 更短的查找与应用路径

AE Quick Apply 把效果、动画预设和菜单命令集中到一个键盘入口，支持 `e:`、`p:`、`m:` 范围筛选。
Video Copilot 的 FX Console 更早将效果快速检索与快照放进创作流程。CutVoke 已有资源卡片、收藏和预览，缺的是无需切换面板的即时入口。
本轮新增 Ctrl/⌘+Enter 与工具栏入口，复用现有注册表和应用命令。
[Adobe Quick Apply](https://helpx.adobe.com/after-effects/desktop/animate-in-after-effects/animation-keyframes/quick-apply.html)，[FX Console 作者说明](https://www.videocopilot.net/blog/2016/10/new-workflow-plug-in-fx-console-is-now-available/)

用户反馈也提醒不能仅提供“模糊搜索”：一则原帖反映无关结果挤占列表。
因此 CutVoke 采用词项匹配、名称优先排序和明确的结果类型；不把散落的同序字母当成命中。
[Quick Apply 搜索反馈](https://www.reddit.com/r/AfterEffects/comments/1uzz74p/why_my_quick_apply_shows_more_options_i_didnt_ask/)

### 2. 找到要编辑的对象，而非滚动所有属性

AE 的属性快捷键以及 U、UU 可以集中显示动画或已修改属性。时间线搜索与面板搜索减少大工程中的扫描负担。
CutVoke 本轮增加片段/素材/效果名称搜索、仅已选和仅有关键帧筛选；效果栈增加名称搜索和已修改筛选。
这些是视图状态，不写入工程，也不改变导出。过滤不会偷偷清掉选择；隐藏的已选片段会显示提示，Ctrl/⌘+A 只选当前筛选可见的片段。
[图层属性](https://helpx.adobe.com/after-effects/desktop/work-with-layers/layer-properties/layer-properties.html)，[界面项目与搜索](https://helpx.adobe.com/ie/after-effects/desktop/get-started/get-familiar-with-the-interface/general-user-interface-items.html)

一则社区帖子指出属性快捷键会让搜索条件消失，导致大量图层重新展开。这是 CutVoke 保留筛选条件、区分视图过滤与图层显隐的依据。
AE 的 Solo 会影响预览和渲染，不能把本轮的“仅已选”称为渲染 Solo。
[搜索条件消失反馈](https://www.reddit.com/r/AfterEffects/comments/1bqk9mx/timeline_searchbar_query_disappears/)，[图层开关](https://helpx.adobe.com/after-effects/desktop/work-with-layers/manage-layers/layers.html)

### 3. 属性面板必须覆盖真正可编辑的参数

Properties Panel 的价值是把当前选择的常用控制放在一起；早期 Beta 原帖也记录过重置后显示不同步、复杂路径无法完整展示等问题。
因此 CutVoke 不另建一套参数模型：普通数字和枚举、跟踪点、遮挡轮廓、反射区域、方向点与阈值曲线都由效果 schema 驱动。
本轮让原来仅能经 MCP 输入的受限几何结构进入人工表单；可添加、删除、调整，并在范围或时间点顺序无效时保留草稿、显示提示。
[Properties Panel 原始公告及历史反馈](https://community.adobe.com/questions-534/introducing-the-properties-panel-in-after-effects-beta-314071)

### 4. 动画控制需要明确插值语义

AE 把数值变化与变化速度分开观察，提供 Easy Ease 和保持插值。保持插值在下一个关键帧前不产生中间值。
CutVoke 本轮增加 `hold`，贯通数据、命令、裁剪、前端求值和 FFmpeg 表达式；增加缓入缓出、线性、保持的批量助手，保留节点 ID、时间和值。
F9 在关键帧编辑区作用于所选节点，一次操作一次撤销；上一/下一关键帧按片段局部时间换算工程播放头。
变化速度图仍是当前标量曲线的变化率观察视图；另设空间运动路径，提供二维空间贝塞尔手柄、独立时间缓动和中间点按弧长分配时间。
[Adobe 速度与图表说明](https://helpx.adobe.com/after-effects/desktop/animate-in-after-effects/speed-between-keyframes/speed.html)，[Adobe Graph Editor 教程](https://www.adobe.com/learn/after-effects/web/adjusting-keyframes-dynamic-movement)

### 5. 效果是可定位、可重置、可复用的实例

AE 的效果控制与预设允许重用参数组合，也允许调节效果与原画面的合成关系。CutVoke 已有实例 ID、配方库、效果时间区间、部分光效融合控制。
本轮核查发现，右侧效果条和部分时间线删除操作还只传效果类型；实时交互草稿也仍寻找首个同类效果。这些路径一并改为精确实例。
增加编辑参数入口与静态参数重置；`effect.update` 的 `replaceParams=true` 允许删除可选几何参数，并保持旧的局部合并协议。
重置静态参数不会移除关键帧，按钮提示明确这一点。
[效果与动画预设](https://helpx.adobe.com/after-effects/desktop/apply-effects-and-animation-presets/effects-and-animation-presets/effects-animation-presets-overview.html)

### 6. 缓存与性能反馈应解释实际成本

AE 的 Composition Profiler 能显示图层和效果耗时，并区分缓存情况；空闲缓存有范围、延迟和资源设置。
社区用户也有缓存重建、自动缓存占磁盘的反馈。因此 CutVoke 应先显示实际阶段、缓存命中和重算原因，保留停止/范围选择，不能默认后台渲染整片。
在已有有界预览、工程内效果缓存和阶段耗时上，加入实际 FFmpeg 效果区间计时、程序图层生成指标和首次/重复运行对照。
滤镜区间包含调度等待且可能重叠，不能相加当作独占 CPU 或总渲染时间；编码器使用 NVENC 也不表示所有效果使用 GPU。
空闲准备增加显式开关、范围、延迟、提前秒数和单次时间预算，离开或修改工程会取消旧任务。
[Composition Profiler](https://helpx.adobe.com/after-effects/desktop/work-with-compositions/composition-profiler/composition-profiler.html)，[多帧渲染和空闲缓存](https://helpx.adobe.com/after-effects/desktop/render-and-export/multi-frame-rendering/multi-frame-rendering.html)，[缓存重建反馈](https://community.adobe.com/questions-529/after-effects-preview-keeps-rebuilding-the-cache-instead-of-playing-smoothly-1641251)，[空闲缓存占空间反馈](https://community.adobe.com/questions-529/ae-2022-keeps-automatically-ram-previewing-44830)

### 7. 复杂合成与模块化应独立推进

Track Matte、预合成和 Essential Properties 分别解决图层间遮罩、结构复用和实例级参数暴露；这三件事不能用“多加几个预设”代替。
新增合成保持稳定对象引用、明确渲染顺序、依赖循环检查、素材打包与实例覆盖语义。
通用 Alpha/Luma 遮罩与效果专用光效区域分开处理；共享母版的公开控制与实例覆盖也独立于安全配方。
实例覆盖优先于对应母版关键帧、绑定和空间路径通道，其他通道保留；修改实例不写回母版。
[Track Mattes](https://helpx.adobe.com/after-effects/using/track-mattes-and-traveling-mattes.html)，[Selectable Track Matte 公告与反馈](https://community.adobe.com/questions-534/feature-focus-selectable-track-matte-layers-have-arrived-314150)，[预合成与预渲染](https://helpx.adobe.com/after-effects/desktop/work-with-compositions/precomposing-and-nesting/precomposing-nesting-pre-rendering.html)，[Essential Properties](https://helpx.adobe.com/after-effects/desktop/motion-graphics/essential-properties/essential-properties.html)

## 借鉴清单与验收边界

“本轮实现”仅指下面列出的具体能力；最后的回归记录确认验证范围。

| 优先级 | 借鉴点 | CutVoke 处理 | 验收重点 |
| --- | --- | --- | --- |
| P0 | 快速应用 | 本轮：效果、内置预设、常用命令的统一检索 | 词项排序、键盘选择、焦点恢复、输入/弹窗/锁定保护 |
| P0 | 上下文禁用原因 | 本轮：无片段、不兼容类型、锁定显示原因 | 禁用项不提交命令，项目切换不误写 |
| P0 | 时间线搜索 | 本轮：名称/素材/效果 + 已选/动画过滤 | 仅视图变化，条件保留，Ctrl+A 不选隐藏对象 |
| P0 | 精确实例控制 | 本轮：补齐效果条、时间线删除与实时草稿 | 多个同类实例的预览/提交一致，重排后仍精确 |
| P0 | 重置与删除几何参数 | 本轮：完整替换参数的显式模式 | 默认仍局部合并；可撤销；保留关键帧 |
| P1 | Hold 插值 | 本轮：保持阶梯及边界裁剪 | 精确关键帧时间跳变，预览/导出一致 |
| P1 | Easy Ease | 本轮：批量缓动助手和 F9 | 多于 32 个节点也只一次撤销，非法选择零污染 |
| P1 | 数值/速度图 | 本轮：可切换的标量变化率观察图 | 数值与插值共享求值；速度视图不误作可拖动值节点 |
| P1 | 关键帧导航 | 本轮：前/后关键帧 | 非零片段起点正确换算，空通道有禁用状态 |
| P1 | 属性覆盖 | 本轮：受限几何与关键点表单 | schema 驱动、可选项可移除、错误输入不写入工程 |
| P1 | 已修改属性 | 当前片段 U/UU 总览及效果栈筛选 | 静态、动画、混合、遮罩、绑定、路径和实例覆盖；可进入编辑区 |
| P1 | 效果预览与收藏 | 复用已有资源卡片、真实预览、收藏 | 本轮快速应用接入同一注册表，不引入第二份效果清单 |
| P1 | 图层/效果 Profiler | 有界采样 UI、逐效果滤镜区间和程序生成指标 | 实际尺寸、首次/重复缓存状态与工作线程/墙钟时间分开 |
| P1 | 工作区与面板 | 左右面板/时间线缩放、8 个命名布局和重置 | 窄屏折叠、键盘调宽、保存恢复及多语言布局 |
| P1 | 预览范围和空闲预算 | 空闲开关、范围、提前量、延迟和时间预算 | 旧请求取消、工程隔离、前台优先和导出让行 |
| P1 | 调整图层 | 源无关的独立调整轨与按顺序合成 | 时间范围、下方画面作用域、Alpha、预览/导出 |
| P1 | 通用 Effect Opacity/局部合成 | 精确实例透明度、五种混合模式、羽化区域 | 预乘 Alpha 混合；结构变换/标题/转场保留专门控制 |
| P1 | 可选择 Track Matte | Alpha/Luma、反相、隐藏源及一对多引用 | 禁环、窗口依赖、文字遮罩、打包和独立目录移动 |
| P1 | Essential Properties | 共享母版、公开控制与实例覆盖/重置 | 母版不变、覆盖优先级、稳定引用及复制重映射 |
| P1 | 预合成导航与依赖图 | 局部编辑窗口、面包屑和依赖表 | 进出嵌套、局部时间、循环检测与工程内缓存 |
| P2 | 空间运动路径/速度手柄 | 二维空间手柄、独立时间贝塞尔、弧长分配 | 窗口/裁剪时钟、固定速度变换、撤销；标量速率图仍只观察 |
| P2 | 新增噪声/扭曲/位移效果族 | 原创分形噪声、湍流置换和波浪扭曲算子 | 确定性种子、不同素材尺寸、Alpha、预览/导出像素 |
| P2 | 形状路径与修剪/重复器 | 矢量路径效果、空间切线、填充/描边、修剪和重复器 | 可编辑几何、修剪时间点、顺序与保存；仍作为片段效果 |
| P2 | 表达式控制器 | 属性引用、正弦、时间和固定值的声明式绑定 | 有界计算、稳定引用、禁环，不执行用户脚本 |
| P2 | 3D、摄像机、复杂 Roto | 本机网格/材质/摄像机/灯光/深度，光流轮廓，以及可选本地神经抠像接口 | 实际深度与 Alpha、软件/GPU 一致性、预览/导出和视觉能力声明；景深与摄像机解算仍未覆盖 |

新增效果族的方向来自 AE 对扭曲算子的组合方式，而不是复制预设数量。[Distort effects](https://helpx.adobe.com/after-effects/desktop/apply-effects-and-animation-presets/list-of-effects/distort-effects.html)
工作区与通用动画交互另外参考 [工作区与面板](https://helpx.adobe.com/in/after-effects/desktop/get-started/get-familiar-with-the-interface/workspaces-panels-viewers.html) 和 [Animation basics](https://helpx.adobe.com/after-effects/desktop/animate-in-after-effects/animation-basics/animation-basics.html)。

## 验证方法与边界

使用 `tests/rendering/test_ae_workflow.py` 检查保持插值的裁剪、真实预览/导出、批量缓动的原子撤销和精确参数替换。
`apps/editor/scripts/ae-workflow.test.mjs` 检查搜索排序、筛选作用域和前端曲线求值。
`apps/editor/e2e/ae-workflow.spec.ts` 检查键盘、慢预设加载、筛选保留、参数表单、重复效果与窄屏布局。
测试素材、截图、阶段验收结果与性能测量保存在 Git 忽略的 `.artifacts/ae-workflow/`，不打包用户工程。
预设资格检查复用现有文件哈希缓存，大小或修改时间变化时重新校验；冷缓存与热缓存耗时分别记录，不能把热缓存收益当作首次启动或整片渲染的提速。
合成回归还覆盖实际 Alpha/Luma 像素、重复效果的混合顺序、隐藏依赖、共享实例、覆盖优先级、空间路径时钟、摄像机透视、跟踪失效和不同输入尺寸。
独立工程经历“打包 → 删除测试源 → 解包渲染 → 移动目录 → 重开渲染”，检查素材与所有新引用保留。
浏览器检查调整层、遮罩、嵌套编辑、工作区、U/UU、摄像机姿态和跟踪结果的人工修正；截图与一次性性能测量保存在 `.artifacts/composition-workflow/`。
后续补充了本机网格、材质、摄像机、灯光、逐像素深度和有限层透明剥离，以及可选本地 SAM2 等模型的 MCP 契约和人工到 AI 请求队列。景深、投影阴影、骨骼动画、摄像机解算和从普通视频自动推断深度仍未实现；本地分割也不代表已复现 Adobe Roto Brush 的全部边缘细化和交互能力。使用方法与边界见[本机三维与模型](../user/LOCAL_3D_AND_MODELS.md)。
当前结果是本地源码与 Web 构建，不表示安装包已经重建或代码已经推送。

操作与 API 示例见 [动画工作流](../user/ANIMATION_WORKFLOW.md) 和 [合成、模板、路径与摄像机](../user/COMPOSITING_WORKFLOW.md)。
