# 一起打造 CutVoke 特效

[English](../../effects/README.md) · [通用贡献说明](../../CONTRIBUTING.md)

欢迎把有用的转场、滤镜、声音处理、文字动画和可复用资源带进编辑器。以 GitHub PR 为入口，提供一个小工程，展示它帮助创作者解决什么问题。

从 [柔和揭幕 Soft Reveal](../../effects/transitions/cutvoke.transition.community.soft-reveal/README.md) 开始：这是基于现有 FFmpeg `fade` 算法的克制叠化，包含作者、许可证、参数校验、本地发现、真实预览和导出示例。它是现有算法的配置示例，没有新增渲染算法。

## 放在哪里

新贡献按 `effects/<类型>/<效果 ID>/` 保存清单、说明、许可证、真实预览和小型授权素材。既有内置素材继续保留在 `src/cutvoke/assets/` 和现有预设、贴纸注册表中，不为目录整齐搬移已有文件。

| 类型 | 建议目录 | 当前支持边界 |
| --- | --- | --- |
| 转场 | `effects/transitions/<id>/` | `category:transition`，`ffmpeg-xfade`，可用清单映射已支持的转场算法 |
| 视觉特效、滤镜、调色 | `effects/video/`、`effects/filters/`、`effects/color/` | `category:fx`，复用现有 internal 滤镜映射，参数语义与预览/导出必须实际验证 |
| 音频效果 | `effects/audio/<id>/` | `category:fx`，已有均衡、压缩等映射，需要音频输入与听音证据 |
| 片段动画 | `effects/animations/<id>/` | `category:animation`，仅现有 internal kind，沿用动画槽位和 ID 约定；新增 kind 需要核心实现 PR |
| 文字与字幕行为 | `effects/text/<id>/` | 贡献存储约定；实际行为来自现有命令与渲染器，不能靠任意清单代码扩展 |
| 变换 | `effects/transforms/<id>/` | 现有变换语义，清单不能自行增加几何算法 |
| 贴纸、背景、预设、LUT、字体 | 贡献目录保留源资源，再接入现有资源注册表 | 使用 [现有资源包机制](resource-pack-registry.md)，效果清单不会自动生成预设库卡片 |

目前只提供一个可运行转场样例。其余目录是未来贡献约定，不代表已完成每类外部 SDK。需要新行为时，在现有核心或前端模块增加实现和测试，通过正常 PR 审核；不要让清单加载脚本、二进制或任意 FFmpeg 表达式。

## 清单要求

以 [可运行 manifest](../../effects/transitions/cutvoke.transition.community.soft-reveal/manifest.json) 为模板。实际加载器为 `src/cutvoke/core/effects.py`；参数以 `EffectSpec.from_manifest`、实际校验器以及运行时 `effects_list` / `editor_help` 为准，不另维护一套接口定义。

- 使用稳定 ID 和作者子命名空间。转场采用 `cutvoke.transition.<作者>.<名称>`，符合当前编辑器及 `effect.setTransition` 的识别规则。不复用内置 ID；注册表拒绝重复，本贡献流程不启用覆盖。其他类型必须沿用对应的实际实现约定并验证。
- 提供语义版本、中英文名称与说明、`author.name`、许可证、输入/输出与 `appliesTo`。
- 可在 `name`、`description` 中为其他界面语言添加译文；注册表按请求语言返回，缺词回退英语。ID 和分类键保持稳定。见 [翻译贡献说明](../development/translations.zh-CN.md) 与多语言样例，并注明是否经过母语审校。
- 使用表中的引擎 `category`；`subcategory` 和关键词用于发现。浏览分类不等于引擎类别。
- 参数 schema 要有合理范围、默认值及 `additionalProperties:false`。只声明渲染器实际支持的可动画参数；校验器支持 JSON Schema 子集，不能把未支持关键字当成有效约束。
- `implementation` 只能选择已经支持的引擎、滤镜或 kind。样例使用 `ffmpeg-xfade`、`xfade`、`fade`，不包含 Python/JavaScript 模块或任意着色器插件入口。
- 提供 `x-cutvoke-min-core`、`x-cutvoke-contribution.testedWith` 和目录内 PNG 的 `previewFile`。这些是审核元数据，加载器不执行版本协商，作者/预览元数据也不会自动增加预设卡片或兼容性门禁。

新增校验工具在现有加载器之上检查贡献材料，不替代引擎，也不能证明实际渲染和审美质量。

## 本地预览和验证

先按 [CONTRIBUTING.md](../../CONTRIBUTING.md) 安装开发环境。在仓库中已激活的环境，启动编辑器和 MCP 前指定**类型目录**：

```powershell
# Windows PowerShell
$env:CUTVOKE_EFFECTS_PATH = (Resolve-Path effects/transitions).Path
cutvoke effects list --category transition
```

```bash
# POSIX shell
export CUTVOKE_EFFECTS_PATH="$PWD/effects/transitions"
cutvoke effects list --category transition
```

加载器只扫描 `<path>/*/manifest.json`，不会递归扫描类型树。多个绝对目录用 Windows 的 `;` 或 POSIX 的 `:` 连接，也可使用 `~/.cutvoke/effects/<id>/manifest.json`。仓库贡献目录需要主动启用，不会自动安装或进入发布包；修改清单后需重启编辑器并重新连接 MCP，因为注册表在进程内缓存。

```bash
python scripts/verify/validate_effect_contributions.py
python -m pytest -q tests/test_effect_contributions.py
python skills/cutvoke-editor/scripts/demo.py --output .artifacts/community-demo --effects-dir effects/transitions --effect-id cutvoke.transition.community.soft-reveal
cutvoke serve --data .artifacts/community-demo/data
```

打开 `http://127.0.0.1:8787`，选择 **Trail Notes · editable demo**，检查 **lake** 或 **dusk** 片段的转场。服务也需保留上面的环境变量。脚本自制素材，不打开用户工程库，留下 MP4、`.cvkpkg`、可编辑数据库、真实帧和验证报告；每次使用新输出目录。

为自己的效果设计适用媒体与参数样例，检查最小/默认/最大及非法参数、相邻片段和时长限制、真实预览与完整导出。新算法还需要实现覆盖。当前贡献路径不提供生产市场、自动发布、通用热加载或动态代码执行。

## 提交 PR 与审核

Fork 仓库，在分支中新增贡献目录及必要实现/测试，PR 包含：

1. 具体用户收益、前后对比或示例帧；涉及时间变化时提供短动态预览。
2. 实际执行命令、素材来源、测试引擎与 FFmpeg 版本、参数范围和适用媒体。公开证据不含个人路径或私有文件名。
3. 代码与资源的来源链接、许可证、署名。使用原创或可再分发素材，保留授权信息；不提交隐私素材、密钥、个人录音、下载的可执行文件或无法证实的版权声明。
4. 缺失资源、不支持映射及非法参数的行为，以及未验证限制。

维护者审核实用性、可读默认值、授权、参数边界、ID 冲突、工程保存重开、撤销/重做、预览与导出一致性及旧工程影响，可能要求调整或缩小范围。合并和发布由维护者处理，没有自动接收特效的 PR 机器人。

仓库源码使用 MIT；外部素材须准确声明各自许可，FFmpeg 构建的许可义务独立处理。
