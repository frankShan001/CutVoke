# 给你的 AI 一套剪辑工作流

[English](agent-skill.md) · [技能入口](../../skills/cutvoke-editor/SKILL.md)

**cutvoke-editor** 技能帮助外部 AI 规划剪辑、盘点本地素材、建立可恢复的时间线、调整字幕与声音，并验收最终结果。CutVoke 已通过 `editor_help` 提供接口入门；这个可选技能补充的是创作流程和交付要求。它不会安装大模型，也不能保证任何素材都自动变成精美成片。

## 复制或安装

完整目录是 `skills/cutvoke-editor/`，含 `SKILL.md`、按需读取的参考文档和可运行的 `scripts/demo.py`。请复制**整个目录**，保留相对引用。

使用 Codex 个人技能目录时，在仓库中运行：

```powershell
# Windows PowerShell
$skillTarget = Join-Path $env:USERPROFILE '.codex/skills/cutvoke-editor'
if (Test-Path -LiteralPath $skillTarget) { throw 'Skill already exists; review it before replacing it.' }
New-Item -ItemType Directory -Path (Split-Path $skillTarget) -Force | Out-Null
Copy-Item -LiteralPath skills/cutvoke-editor -Destination $skillTarget -Recurse
```

```bash
# POSIX shell
mkdir -p "$HOME/.codex/skills"
test ! -e "$HOME/.codex/skills/cutvoke-editor" && cp -R skills/cutvoke-editor "$HOME/.codex/skills/"
```

若自定义了 `CODEX_HOME`，改用其中的 `skills/`。按客户端要求重新加载技能后，可用 `$cutvoke-editor` 调用。安装路径来自本机 Codex 技能约定；本次验证覆盖技能文件及 CutVoke 的 stdio MCP，并未逐一验证所有 AI 客户端的安装界面。

其他支持 Markdown 指令的工具，可以直接读取 `skills/cutvoke-editor/SKILL.md`，再按需读取引用。按该工具的文档配置本地 MCP 或 HTTP 接口。这是可复制的指令包，并非经过全平台验证的安装器；远程客户端也不一定能够访问本机文件。

## 连接并使用

按 [外部 Agent 接入说明](external-agent-editing.md) 配置本地 MCP 进程。可用已安装的 `cutvoke`、仓库中已激活的 Python 环境，或 Windows 安装版内置引擎。MCP 与编辑器必须选择同一 `--data` 目录，首先调用 `editor_help({})` 和 `runtime`。

连接后的任务示例：

> 使用 $cutvoke-editor，把我选定的本地素材文件夹剪成一条安静的 12 秒、16:9 周末短片，面向想放松的朋友。保留一个简短标题、可读字幕、轻微运镜和克制转场。只用这些文件，把过程文件和新 MP4 放进这个工程目录，检查真实画面和声音，并保留可继续编辑的时间线。完成后提取已验证、适合复用且不含用户内容的效果，归档到 CutVoke 安装目录。音乐或素材授权不明确时，先指出问题再添加内容。

需要提供素材目录或目标工程、受众与核心信息、时长与画幅、必要文案或品牌素材，以及输出位置。创作质量也依赖可用素材和 AI 是否能够看图、听音；引擎不会自动判定艺术质量。

## 工程目录与效果沉淀

技能要求 AI 在生成文件前，为目标 `projectId` 绑定一个稳定的绝对 `projectRoot`，记录后续可复用的对应关系。已有工程复用其记录；用户指定的目录优先。新工程没有指定目录时，在用户指定的父目录或当前会话工作区下创建 `projects/<projectId>/`。AI 生成的计划、脚本、日志、临时文件、帧图、音频、预览证据、工程包和最终导出都保存在工程目录内；接口支持 `outPath` 时显式指定。用户另行指定交付位置时可复制交付，工程内保留主产物。引擎管理的数据库、导入媒体存储和缓存继续由引擎管理，不为满足文件归档要求而搬迁。目录细则见 [workspace.md](../../skills/cutvoke-editor/references/workspace.md)。

剪辑完成后，AI 主动检查实际采用并验证过的效果，筛选适合复用的受支持 `effects`、`motion` 和 `textStyle` 配方。合格配方通过 `effect_module_save` 注册到当前数据目录的 `effect-modules` 可用库，按内容去重；再通过 `effect_module_export` 导出到工程内 `effects/<moduleId>.zip`，并归档一份到**已验证的 CutVoke 安装目录**下的 `user-effects/<moduleId>.zip`。安装目录中的文件是本地收藏，可用模块库仍在数据目录。目录不可写或位置未确认时，保留工程内的包并报告原因，不提权、不悄悄改存别处，也不覆盖同名但内容不同的文件。没有合格候选时如实说明。具体筛选与归档规则见 [effect-library.md](../../skills/cutvoke-editor/references/effect-library.md)。

效果包仅含受支持的参数配方，不包含用户媒体、文案或字幕正文、图片、路径、代码和工程内容；不会自动公开发布。技能还要求保留人工接管能力，优先局部修改，验收真实预览、最终导出和声音；上传、发布或向他人发送内容均须已有明确授权。这些是对 AI 的工作指令，文件系统隔离仍取决于客户端权限。

## 可运行的接入示例

安装 Python 3.10+、CutVoke、FFmpeg/ffprobe 后，在仓库中已激活的环境运行：

```bash
python skills/cutvoke-editor/scripts/demo.py --output .artifacts/skill-demo
cutvoke serve --data .artifacts/skill-demo/data
```

打开 `http://127.0.0.1:8787`，选择 **Trail Notes · editable demo**。若已有服务占用 8787，使用空闲端口。脚本要求一个**尚不存在的输出目录**，自行绘制插画并合成和弦，不读取默认用户工程库，不下载模型，不调用外部服务。

产物包含 `trail-notes.mp4`、`trail-notes.cvkpkg`、`data/projects.sqlite`、持久素材、六张真实预览帧、`result.json`、`calls.json` 和 MCP 诊断日志。保留整个目录即可继续编辑。默认使用内置叠化；[特效贡献指南](../../effects/README.md) 演示如何改用本地社区清单。

脚本默认通过真实 stdio MCP 导入、编辑，读取运行时 schema，执行预检、准备连续预览、导出 H.264/AAC，检查尺寸、帧率、时长及全片解码，测量音频峰值和首尾淡入淡出，并检查相同命令重试不会增加编辑版本。它只启停自己创建的临时 Web 服务，核对 MCP 导入的同一批素材在人工编辑器中可用。新导入使用统一路径，旧 MCP 导入按严格规则保持可用。详见 [演示截图及验证边界](../images/readme/README.md)；`--import-mode upload` 可单独检查本机 HTTP 上传路径。

这个示例验证原创插画素材的接入流程，不能证明语音转写、自动选镜、主观声音质量、任意特效、每种 AI 客户端或大型工程性能。最终创作判断仍由创作者和审片 AI 完成。
