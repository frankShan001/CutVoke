# 暗墨揭示 · 可选社区配方

[English](README.md) · [贡献指南](../../../docs/user/effect-contributing.zh-CN.md)

先从前镜头的暗部揭示下一镜头。本清单复用原创 **`cutvoke.transition.lumaReveal` 内部实现**，默认 `reverse:true`、`duration:0.8`；它是复合配置，不是第五个渲染算法或下载的着色器。本地预览来自 CutVoke 渲染的原创程序插画，不含参考影片帧。代码、清单与原创插画采用仓库 MIT 许可；外部 FFmpeg 保持独立许可。

[预览参数记录](preview-params.json)保留源文件哈希、默认参数、准确帧号与渲染器版本。PNG 已在亮度蒙版多线程修复后重新生成，取自真实导出视频的第 57 帧（从 0 开始）。

启动编辑器/MCP 前，把 `CUTVOKE_EFFECTS_PATH` 指向 `effects/transitions` 的绝对路径，改清单后重启。此目录显式启用，不自动增加预设商城卡片。需要包含 `vlog_fx.py` 的当前源码；最低内核字段是说明元数据，不能保证旧 `0.3.1` 二进制支持此映射。

选择首尾相邻的图片或视频，把 `cutvoke.transition.community.luma-ink` 用 `effect.setTransition` 挂在后一段，或在编辑器转场面板选择。参数包括 `duration`（内核 `0.05–2 秒`，编辑器至少 `0.1 秒`）、`softness`（`0.005–0.35`）、`reverse`、`keySource`（`outgoing`/`incoming`）和 `curve`（`linear`/`smooth`）。预览与应用共用这些参数。同样或均匀照明的两镜头可能缺乏明显空间变化，应选择有明暗结构的不同 SDR 素材。

```bash
python tools/verify/validate_effect_contributions.py
python -m pytest -q tests/integration/test_effect_contributions.py tests/rendering/test_vlog_effects.py
```

需要可编辑原创样片时，用新的 `--output` 目录运行[隔离演示脚本](../../../tools/verify/demo_vlog_effects.py)。扩展时沿用可支持映射，换成自己的贡献者 ID，并保留来源、许可、边界和实测证据；清单不能携带任意执行代码。

[Peter 的 Luma Fade 教程](https://www.youtube.com/watch?v=1Qm1EGxdZ6E)提供具名手法依据。研究核验了来源元数据和手法名称，但正片始终缓冲，没有复制具体镜头、时间点、素材或算法。完整限制见[来源记录](../../../docs/user/vlog-effect-references.json)。翻译是尚无母语审校的技术草稿。
