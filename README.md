# CutVoke

CutVoke 是一个本地运行的视频工程编辑器。人可以在浏览器里调整时间线，外部 AI Agent 可以通过 MCP、HTTP API 或 CLI 编辑同一份工程。

它解决的是一个很具体的问题：AI 生成的视频通常只需要改几处字幕、节奏或画面位置，却常常要重新读取大量代码并渲染整片。CutVoke 保存的是可继续编辑的工程，而不只是最后导出的视频。局部修改应当保持局部，用户也可以随时接手。

CutVoke 本身不提供聊天或内容生成能力。你可以继续使用熟悉的 Agent，让它通过 CutVoke 完成剪辑操作。

## 现在能做什么

- 在 Web 编辑器中管理素材、轨道、片段、字幕、效果、关键帧和模板。
- 直接播放工程预览；字幕由可编辑叠层显示，修改文字不需要重新编码整段视频。
- 通过 MCP、HTTP API、CLI 或 Python 调用同一套编辑命令。
- Agent 批量编辑时临时锁定人工操作，同时让页面继续显示工程变化。
- 使用版本号、幂等命令、撤销/重做和 SQLite 持久化保护工程状态。
- 使用 FFmpeg 完成多轨合成、音频处理、转场、调色和导出。
- 排队、取消和保留多个导出版本；导出任务使用入队时的工程快照。
- 将工程和素材打包为 `.cvkpkg`，便于迁移或归档。

项目仍处于早期阶段，工程格式和接口可能调整。重要项目请保留原始素材和独立备份。

## 运行环境

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

Agent 可以先读取 `capabilities`，再获取编辑租约并提交命令。时间值使用有理数，例如 `{"num":"15","den":"2"}` 表示 7.5 秒。每次写入都带工程 revision；发生并发修改时，服务会明确返回冲突，而不是覆盖别人的工作。

## 常用命令

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
