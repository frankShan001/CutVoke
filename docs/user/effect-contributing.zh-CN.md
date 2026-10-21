# 一起打造 CutVoke 特效

[English](../../effects/README.md) · [通用贡献说明](../../CONTRIBUTING.md)

有用的特效可以来自一次普通的 AI 剪辑。先把效果保存为本地模块，用在当前作品里；需要分享时，再准备贡献包。

## 边剪辑边创作：无需开发环境

连接 AI 编辑器后，可以直接说：**“把这个效果保存成可复用特效模块。剪完后，准备一个只含效果配方的分享包。”** MCP 连接说明与 `editor_help(topic="sharing")` 会把创作约定和必需的隐私话术传递给 AI。

1. `effect_module_list(includeSchemas=true)` 查询实际支持的安全参数；`effect_module_save(recipe=...)` 保存配方，不读取工程。不需要设置类型目录或重启程序。
2. 通过 `command_preview` / `command_apply`，使用 `type="effectModule.apply"`、`payload={clipId,moduleId}` 和当前 revision、编辑租约应用到已有片段。支持撤销、重做和人工调整。`effects` 组合已有算子；`motion` 使用片段相对时间 `at=0..1`，x/y 为画布宽高比例；`textStyle` 只改变已有文字片段的样式，保留用户正文。
3. `effect_module_export(moduleId=...)` 在本地生成 ZIP，不上传。其他用户可用 `effect_module_import(path=...)` 导入。修改后的配方另存为新内容 ID。

`effect_module_save` 参数示例：

```json
{"recipe":{"effects":[{"effectId":"cutvoke.fx.vibrance","params":{"intensity":0.4}}],"motion":[{"param":"opacity","at":0,"value":0.2},{"param":"opacity","at":0.25,"value":1}]}}
```

**隐私限制由包格式强制执行。** 只接受内置白名单效果、经过校验的数值/布尔/枚举/颜色参数、相对时间关键帧和限定的文字样式字段。自由文字、字幕正文、素材或资源路径、任意代码、未知字段以及参数中夹带的内容都会被拒绝。因此依赖用户内容的部分效果不进入这种分享格式，以工具返回的 schema 为准。新增算法仍需代码实现 PR。

导出器重新构造 `effects/recipes/<内容ID>/` 下的四个文件：`module.json`、程序生成的 `README.md`、MIT `LICENSE` 和 `PRIVACY.txt`。不会打包目录、工程、源文件或工程预览；每次导出重新校验配方与内容哈希，导入也会拒绝新增附件或修改过的说明。模块存于数据库旁的 `effect-modules/`（默认 `~/.cutvoke/data/effect-modules/`），独立于工程和工程包，删除工程不会删除模块。

AI 每次建议分享或交付贡献包时，都必须原样追加工具返回的中文说明：

> 准备分享的特效包仅包含效果配方、参数和程序生成的通用说明，不包含您的视频、图片、音频、文字或字幕正文，也不包含工程、个人信息、文件路径和编辑历史。当前仅在本地准备，尚未上传任何内容；公开提交需要您明确同意。

此保证适用于**未经修改的程序导出 ZIP**。不得再添加用户工程的截图、成片、日志、提示词、原始素材、个人信息或工程包，也不得写进 PR 标题与说明。公开提交是单独一步，需要用户明确同意。把 ZIP 中的四个生成文件按原目录放进仓库即可准备配方 PR；运行 `python tools/verify/validate_effect_contributions.py` 校验。实际效果仍须审核与渲染验证；需要证据时，只使用另外生成的合成素材和固定通用文字，不能使用用户工程。结构校验通过不代表画面质量已经验收。

## 开发新的渲染行为

下面的清单流程适用于扩展引擎行为或现有算法映射的开发者，不是上面“只分享配方”流程的前置条件。

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

转场目录提供 Soft Reveal 和[暗墨揭示](../../effects/transitions/cutvoke.transition.community.luma-ink/README.zh-CN.md)两个可运行配方，后者复用经过测试的内容亮度渲染器，不额外计为新算法。其余目录是未来贡献约定，不代表已完成每类外部 SDK。需要新行为时，在现有核心或前端模块增加实现和测试，通过正常 PR 审核；不要让清单加载脚本、二进制或任意 FFmpeg 表达式。

## 清单要求

以 [可运行 manifest](../../effects/transitions/cutvoke.transition.community.soft-reveal/manifest.json) 为模板。实际加载器为 `src/cutvoke/rendering/effects.py`；参数以 `EffectSpec.from_manifest`、实际校验器以及运行时 `effects_list` / `editor_help` 为准，不另维护一套接口定义。

- 使用稳定 ID 和作者子命名空间。转场采用 `cutvoke.transition.<作者>.<名称>`，符合当前编辑器及 `effect.setTransition` 的识别规则。不复用内置 ID；注册表拒绝重复，本贡献流程不启用覆盖。其他类型必须沿用对应的实际实现约定并验证。
- 提供语义版本、中英文名称与说明、`author.name`、许可证、输入/输出与 `appliesTo`。
- 可在 `name`、`description` 中为其他界面语言添加译文；注册表按请求语言返回，缺词回退英语。ID 和分类键保持稳定。见 [翻译贡献说明](../development/translations.zh-CN.md) 与多语言样例，并注明是否经过母语审校。
- 使用表中的引擎 `category`；`subcategory` 和关键词用于发现。浏览分类不等于引擎类别。
- 参数 schema 要有合理范围、默认值及 `additionalProperties:false`。只声明渲染器实际支持的可动画参数；校验器支持 JSON Schema 子集，不能把未支持关键字当成有效约束。
- `implementation` 只能选择已经支持的引擎、滤镜或 kind。样例使用 `ffmpeg-xfade`、`xfade`、`fade`，不包含 Python/JavaScript 模块或任意着色器插件入口。
- 提供 `x-cutvoke-min-core`、`x-cutvoke-contribution.testedWith` 和目录内 PNG 的 `previewFile`。这些是审核元数据，加载器不执行版本协商，作者/预览元数据也不会自动增加预设卡片或兼容性门禁。

新增校验工具在现有加载器之上检查贡献材料，不替代引擎，也不能证明实际渲染和审美质量。

## 本地预览和验证

开发清单或核心算法时，先按 [CONTRIBUTING.md](../../CONTRIBUTING.md) 安装开发环境。在仓库中已激活的环境，启动编辑器和 MCP 前指定**类型目录**：

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
python tools/verify/validate_effect_contributions.py
python -m pytest -q tests/integration/test_effect_contributions.py
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
