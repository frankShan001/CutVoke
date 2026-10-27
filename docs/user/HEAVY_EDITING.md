# 重度剪辑：音轨检查、光效融合与精确实例编辑

## 导出音轨

正常速度的音轨不再经过 `atempo=1`。该滤镜仍会执行时间伸缩算法，可能改变完整母带的波形位置。
片段位置改用采样数对齐；密集时间线的分窗口导出只分割画面，音频连续混合一次，最后统一编码。

正式导出在发布文件前对比连续 PCM 参考与编码结果：按 4 秒块检查全曲、估算 ±50 毫秒内的偏移，并检查信号缺失、音轨长度与音视频起点。
检查失败时任务报错，临时结果不会替换目标文件。结果中的 `audioVerification` 包含相关度、估算偏移与检查时长。
这属于时序检查，不能代替试听，也不表示每种音色变化都能检测出来；预览画质的直接导出不执行完整音轨对比。

默认不透明 SDR 导出明确转换为 BT.709、有限范围 YUV420P，并返回 `color_space`、`color_primaries`、`color_transfer`、`color_range` 和音视频起点。
透明输出和显式色彩管理沿用各自设置。

## 工程内素材引用

对独立目录工程调用 `media_import` 时传入 `projectId`。返回的 `assetId` 可直接交给同一工程的 `command_preview`、`command_apply`、`asset.swap` 与模板。
查询、预演、提交与打包使用该工程的素材库；其他工程不能凭同一个 ID 读取它。目录移动并重新打开后，引用会指向移动后的 `media/`。
一次导入最多 32 个文件，超过上限时会返回实际数量和分批建议。

## 精确操作效果实例

从工程片段的 `effects` 读取 `instanceId`；`effect.add` 的返回结果也会给出该 ID。
下面的 payload 分别用于 `command_preview` 和带版本、租约及命令 ID 的 `command_apply`：

```json
{"type":"effect.update","payload":{"clipId":"shot-2","instanceId":"fx-instance-id","params":{"intensity":0.6}}}
```

`effect.remove`、`effect.bypass`、`effect.reorder` 同样支持 `instanceId`。
重排可用 `instanceId` + `toIndex`，或 `instanceOrder: ["second-id", "first-id"]`；部分 ID 列表之后保留其余实例。
只给 `effectId` 时保留既有批量删除/旁路语义，更新仍选择该类型首个实例。
人工编辑器逐个显示重复效果，参数修改、删除、旁路和上下移均针对对应实例。

## 光效与原画面融合

`cutvoke.fx.lightWeave` 与 `cutvoke.fx.opticalField` 共用以下可选参数：

| 参数 | 用途 |
| --- | --- |
| `anchorFrames` | 按片段秒数提供 `{time,x,y}` 跟踪点；坐标归一化 |
| `blendMode` | `normal`、`screen`、`add` |
| `occlusionPolygon` | 3–128 个 `{x,y}` 点组成的遮挡轮廓，阻止光效覆盖主体 |
| `occlusionFeather` | 遮挡轮廓羽化，0–0.2，按画面短边比例 |
| `occlusionFollowAnchor` | 遮挡轮廓随跟踪点平移；固定反射边界保持在场景坐标内 |
| `reflectionRegion` | `{x,y,width,height,feather}`，限制当前光效层的作用区域，适合单独的水面反射层 |
| `highlightProtect` / `highlightThreshold` | 保护原画面亮部，减少叠加过曝 |

`opticalField` 另支持 `angleFrames: [{time,angle}]`，让光束方向随时间变化。
时间点必须严格递增。跟踪数据由人工或外部 Agent 提供；软件不自动识别光源、物体或水面。
这些参数已进入统一效果 schema 和安全模块目录，可保存、导出和重新导入配方。
模块只接受受限数值、布尔、颜色、枚举及封闭几何结构，拒绝素材、文件路径、字幕和自由文本。
旧 v1 亮度转场模块保留原内容 ID 与包格式。

## 亮度转场与对比预览

`cutvoke.transition.lumaReveal` 增加 `spatialBlur`（0–32 像素）、`temporalFrames`（1–9）、`dissolveMix`（0–1）、
`thresholdCurve`（2–16 个 `{at,value}` 点，`at` 为 0–1 且严格递增）和 `localRegion: {x,y,width,height,feather}`。
空间模糊与时间平滑仅处理亮度键，原画面保持清晰；局部区域之外使用普通叠化。
默认值保留原转场行为。标量参数可在转场面板调整，几何和曲线数据通过命令参数提供。

调用 `transition_preview(projectId, clipId, fractions=[0.25,0.5,0.75], compareDissolve=true)`，
可在同一接点对比当前转场和普通叠化，各返回最多 3 张图片。它不提交编辑，不推进工程版本。

## 缓存与性能证据

独立工程的程序光效缓存放在 `cache/effects/`，供后续预览和导出复用。
仅改工程名不会重算光效；只改混合方式或高光保护可复用生成的光效图层。
宽半径光晕采用低分辨率采样后恢复尺寸，细线核心保持原分辨率。

导出结果 `timings` 区分准备、连续音频、窗口编译、窗口合成和最终编码与检查等阶段，并记录光效生成帧数、工作线程耗时、内存/磁盘缓存命中。
窗口合成时间包含 FFmpeg 解码和滤镜执行，目前不单独统计每个解码器的耗时。
工作线程耗时是累计值，不能与墙钟时间直接相加。`effectProcessor` 和 `gpuEffects` 说明实际执行路径；NVENC 只代表编码器，不能据此判断光效使用 GPU。
目前这两类程序光效仍由 CPU 生成。缓存复用能减少重复计算，整片性能需在相同工程、硬件、缓存状态且无并行负载下比较。

租约释放结果另外返回 `status`：`released`、`expired`、`not-held`、`not-owner`，以及独立的 `released` 和 `locked` 字段，避免把“没有释放”误读成“仍锁定”。
