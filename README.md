# CutVoke

CutVoke 是一个本地运行的视频工程编辑器。人可以在浏览器里调整时间线，外部 AI Agent 可以通过 MCP、HTTP API 或 CLI 编辑同一份工程。

它解决的是一个很具体的问题：AI 生成的视频通常只需要改几处字幕、节奏或画面位置，却常常要重新读取大量代码并渲染整片。CutVoke 保存的是可继续编辑的工程，而不只是最后导出的视频。局部修改应当保持局部，用户也可以随时接手。

CutVoke 本身不提供聊天或内容生成能力。你可以继续使用熟悉的 Agent，让它通过 CutVoke 完成剪辑操作。

## 现在能做什么

- 在 Web 编辑器中管理素材、轨道、片段、字幕、效果、关键帧和模板；选中已有字幕可一次修改文字和起止时间。
- 直接播放工程预览；播放器按需生成短时间窗，已访问的窗口可复用。字幕由可编辑叠层显示，修改文字不需要重新编码视频。
- 通过 MCP、HTTP API、CLI 或 Python 调用同一套编辑命令。
- Agent 批量编辑时临时锁定人工操作，同时让页面继续显示工程变化。
- 使用版本号、幂等命令、撤销/重做和 SQLite 持久化保护工程状态。
- 使用 FFmpeg 完成多轨合成、音频处理、转场、调色和导出。
- 排队、取消和保留多个导出版本；导出任务使用入队时的工程快照。
- 将工程和素材打包为 `.cvkpkg`，便于迁移或归档。
- 导出前检查素材与自定义 LUT 的缺失，定位片段并重新链接；Agent 可调用只读 `project.preflight`。

项目仍处于早期阶段，工程格式和接口可能调整。重要项目请保留原始素材和独立备份。

## 运行环境

### Windows 安装版

从源码构建后，运行 `dist/desktop/CutVoke-Setup-0.3.1-x64.exe` 安装，或双击根目录的 `start-cutvoke-desktop.cmd` 启动已构建的客户端。安装包内含 Python 引擎、FFmpeg、ffprobe、Web 编辑器和效果资源，基本编辑与导出无需另外安装这些运行环境。客户端单独管理本机编辑服务，退出时关闭自己启动的服务。

默认复用 `%USERPROFILE%\.cutvoke\data\projects.sqlite` 中的现有工程。缓存初始上限为 10 GiB，可在首页或编辑页的“缓存”中调整容量、选择目录、查看用量和清理空闲缓存。缓存按最近访问时间回收，保护正在使用和近期访问的内容；首次打开工程准备完整预览，再次打开或回拖会复用磁盘缓存。局部画面编辑只使相关片段缓存失效，字幕文字修改继续使用画面缓存。

桌面客户端通过本机文件的 HTTP Range 请求读取已完成的连续预览，不把整片复制到浏览器 Blob 内存。智能体可以从“缓存 → 查看客户端 MCP 配置”取得指向内置引擎的配置；应用同时在 `%APPDATA%\CutVoke\mcp-config.json` 保存该配置。缓存诊断也支持 MCP 工具 `preview_cache_status`。

从源码生成安装包：双击 `build-cutvoke-desktop.cmd`，需要 Node.js、uv、FFmpeg 和 ffprobe。构建脚本从官方发行地址下载 Electron/NSIS/7zip 并核对 SHA256，已下载文件可复用。

当前安装包为本地测试版本，尚未配置发行签名。可选语音识别与人物抠像运行环境尚未随安装包交付；素材代理生成、GPU 实时效果合成也仍属于后续引擎工作。

### 从源码运行所需环境

- Python 3.10 或更高版本
- FFmpeg 和 ffprobe，需要能从 `PATH` 找到
- Node.js 20 或更高版本，仅用于构建 Web 编辑器

Windows 可以使用：

```powershell
winget install Gyan.FFmpeg
```

macOS 可以使用：

```bash
brew install ffmpeg
```

## 从源码启动

Windows 用户可以直接双击项目根目录的 `start-cutvoke.cmd`。它会检查环境，必要时构建 Web 编辑器，然后启动服务并打开浏览器。使用期间保留命令窗口；按 Ctrl+C 可停止服务。

启动器会核对服务启动时的程序指纹和工程库，防止更新源码后继续误用旧服务。MCP、独立 wheel 双击启动和版本诊断见 [本地启动与 MCP 接入](docs/LOCAL_DELIVERY_MCP.md)；本轮实际验证与质量范围见 [产品完善记录](docs/PRODUCT_COMPLETION_20261003.md)。

```bash
git clone https://github.com/frankShan001/CutVoke.git
cd CutVoke

python -m venv .venv
python -m pip install -e .

cd web/app
npm ci
npm run build
cd ../..

cutvoke doctor --json
cutvoke serve
```

Windows PowerShell 中，安装 Python 包前可以先运行：

```powershell
.\.venv\Scripts\Activate.ps1
```

启动后打开 `http://127.0.0.1:8787`。工程数据库、导入素材和日志默认保存在用户目录下的 `.cutvoke` 文件夹中。服务默认只监听本机回环地址。

## 连接 Agent

MCP 服务使用和 Web 服务相同的工程数据库：

```bash
cutvoke mcp
```

通用 MCP 配置：

```json
{
  "mcpServers": {
    "cutvoke": {
      "command": "cutvoke",
      "args": ["mcp"]
    }
  }
}
```

连接后，Agent 可以从 MCP 初始化说明及 `editor_help({})` 获取入门流程，无需用户额外安装 Skill 或讲解接口。具体操作先查询 `editor_help({"topic":"command","command":"clip.insert"})` 的参数 schema；范例与错误恢复也随服务提供。详见 [外部 Agent 剪辑接入](docs/external-agent-editing.md)。

时间值使用有理数，例如 `{"num":"15","den":"2"}` 表示 7.5 秒。每次写入都带工程 revision；发生并发修改时，服务会明确返回冲突。持锁期间，命令带同一个 `editLeaseId`；任务较长时续租，结束后释放。

一次要精修多条字幕时，可提交一个 `caption.patch` 命令：

```json
{
  "type": "caption.patch",
  "expectedRevision": "12",
  "editLeaseId": "当前 Agent 持有的租约 ID",
  "actor": {"kind": "agent", "id": "subtitle-agent"},
  "payload": {
    "updates": [
      {"captionId": "cap_intro", "text": "修改后的开场"},
      {"captionId": "cap_end", "text": "修改后的结尾"}
    ]
  }
}
```

这是一笔原子编辑：要么全部写入，要么完全不写入；成功后只产生一个版本和一个撤销点。字幕的文字、时码、位置、样式和淡入淡出都由预览页叠层立即显示，不会因此重编码基础预览视频。`expectedRevision` 应使用刚读取到的工程版本。

Python SDK 提供 `CutVokeClient.get_edit_lock(...)`、`edit_lock(...)` 和 `patch_captions(...)`；Agent 可先查看或获取租约，再把批量字幕修改以一笔命令提交。

如果只想改一句话，先用 MCP 的 `project_lookup` 找到它，不必读取整个工程。例如：

```json
{
  "projectId": "工程 ID",
  "entityType": "caption",
  "textContains": "要找的原句",
  "fields": ["text", "start", "end"],
  "limit": 5
}
```

返回值包含匹配字幕的 ID 和当前 `revision`，可直接用于 `caption.patch`。同一查询也可走 `GET /api/v1/projects/{id}/lookup` 或 Python SDK 的 `project_lookup(...)`；片段可用 `entityType: "clip"` 按 ID、素材名或时间范围查找。

命令目录的 `previewSupported` 表示能否安全预演。编辑命令可使用 `command_preview` / `dryRun:true` 检查变更；导出、文件处理和取消任务需明确执行，不运行预演中的外部副作用。导出前调用 `project.preflight`，检查 `changedEntities[0].report.readyToRender`；缺失项包含素材路径和片段/轨道 ID。文件存在检查不替代媒体完整解码。

播放画面按 8 秒窗口缓存，只编译窗口相关素材与转场邻居；连续播放提前准备下一个窗口。首次查看复杂窗口仍需等待 FFmpeg。新的画面编辑可取消过期预览，纯字幕编辑继续使用已有底片。复杂长工程导出使用串行无损中间窗口，再统一编码成完整影片；预览与导出使用同一套时间线、效果和音频规则。

如果一次改动涉及不同对象，例如建轨、放入片段、加标题，可用 `edit.batch` 顺序执行。它们共用一个版本和撤销点；任何一步出错，整批都不会写入。先向 `POST /api/v1/projects/{projectId}/commands` 发送带 `"dryRun": true` 的请求查看变更，再去掉 `dryRun` 提交：

```json
{
  "type": "edit.batch",
  "commandId": "opening-pass-01",
  "expectedRevision": "12",
  "actor": {"kind": "agent", "id": "editor-agent"},
  "dryRun": true,
  "payload": {
    "commands": [
      {"type": "track.add", "payload": {"trackId": "titles", "kind": "video"}},
      {"type": "clip.insert", "payload": {
        "trackId": "titles", "clipId": "opening", "sourcePath": "C:\\clips\\opening.png",
        "timelineStart": {"num": 0, "den": 1}, "timelineEnd": {"num": 4, "den": 1}
      }},
      {"type": "caption.add", "payload": {
        "captionId": "opening-title", "text": "片头文字",
        "start": {"num": 0, "den": 1}, "end": {"num": 2, "den": 1}
      }}
    ]
  }
}
```

上例的路径需换成已存在的本机素材路径；持有编辑租约时还要带 `editLeaseId`。一次最多 32 步，可组合的命令清单见 `capabilities` 里 `edit.batch` 命令条目的 `allowedCommands`。导出、分析、撤销等工程外操作不能放入批次。

## 常用命令

### 外部 Agent 剪辑流程

CutVoke 提供剪辑执行能力。对话、规划和内容生成由外部 Agent 决定，编辑器无需接入大模型。

1. `project_list` 找到工程，`project_summary` 读取时间线；需要副本时用 `project_clone`。
2. `media_import` 将本机素材复制到持久素材库，重复导入复用同一内容；`media_list`、`media_inspect` 查询素材。
3. `commands_list` 查询具体编辑参数，`resources_list` 搜索预制效果，`effects_list` 查效果参数。
4. `edit_lock` 获取编辑租约。`command_preview` 先试算，随后用 `command_apply` 提交。组合修改使用 `edit.batch`，带上 `expectedRevision`、`editLeaseId` 和稳定的 `commandId`；重试同一命令时复用 ID。
5. `preview_frames` 返回真实 PNG 图片及对应 revision，包含文字与字幕，供 Agent 检查画面。可输入工程 ID，也可检查单个源文件/素材。一次最多 6 帧。
6. `preview_prepare` 准备连续预览，`preview_job` 查询/取消任务；完成返回本机文件路径。预览视频底层含文字轨，字幕由编辑器的可编辑叠层显示。视觉验收使用 `preview_frames`。
7. 通过 `command_apply` 提交 `export.enqueue`，再用 `export_job` 查询进度、结果或取消。导出绑定提交时的工程版本。`project_events` 核对实际修改，`history.undo` / `history.redo` 撤销或重做。
8. `project_package` 保存包含素材的可编辑交接包，完成后释放编辑租约，人工可立即接手同一工程。

这些工作流入口同时支持 `POST /api/v1/agent/{工具名}`，请求体与 MCP 参数相同。HTTP 的画面响应含 `images`，MCP 直接返回图片内容。写工程仍走共用编辑命令，不存在独立的 Agent 时间线。

长任务应使用异步入口，轮询应间隔约 1 秒。预览任务由当前服务进程持有；服务重启后重新提交即可复用磁盘缓存。导出任务账本持久化，客户端与 MCP 可互相查询和取消新建的导出任务。

```bash
# 查看环境
cutvoke doctor --json

# 查看当前支持的命令、效果和导出预设
cutvoke --json capabilities

# 创建工程
cutvoke project create --name demo --width 1920 --height 1080 --fps 30

# 查看可用效果
cutvoke effects list

# 查看模板
cutvoke template list
```

完整参数以命令行帮助为准：

```bash
cutvoke --help
cutvoke <command> --help
```

## 代码结构

```text
src/cutvoke/core/   工程模型、编辑服务、持久化、渲染和协议
src/cutvoke/assets/ 内置模板、音频、贴纸、背景和 LUT
web/app/            React Web 编辑器
scripts/            仓库检查脚本
.github/            CI、Issue 和 PR 模板
```

Web、MCP 和 CLI 都调用同一个编辑服务。接口层不保存自己的工程副本，SQLite 是跨进程的权威状态；预览和导出共用 FFmpeg 合成逻辑。

## 开发

```bash
python -m compileall -q src
python scripts/check_layering.py

cd web/app
npm ci
npm run typecheck
npm run build
```

提交改动前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可

CutVoke 源代码使用 MIT License。FFmpeg 是独立安装的外部程序，其许可取决于你实际使用或分发的 FFmpeg 构建。
