# 本地启动与 MCP 接入

## Windows 双击启动

双击仓库根目录 `start-cutvoke.cmd`。首次启动会准备 `.venv` 和 Python 依赖；缺少已构建 Web 编辑器时会使用 Node.js 构建。FFmpeg 与 FFprobe 需要可从 `PATH` 运行，`doctor` 会实际检查 H.264/AAC 编码和字幕、画面合成功能。

窗口输出本机编辑器地址；使用期间保留窗口，结束时在该窗口按 Ctrl+C。默认端口为 `8787`，默认工程库为 `%USERPROFILE%\.cutvoke\data\projects.sqlite`。

服务诊断日志位于所选工程库旁的 `logs/serve.jsonl`；指定 `serve --log` 可覆盖。

可在启动前设置：

```powershell
$env:CUTVOKE_PORT = "8799"
$env:CUTVOKE_DATA = "D:\CutVokeProjects"
$env:CUTVOKE_NO_BROWSER = "1"
.\start-cutvoke.cmd
```

重复启动只会复用**同一程序指纹、同一工程库**的服务。检测到源码更新或另一工程库时，启动器会显示旧进程 PID 和具体差异。请在旧启动窗口停止并重新启动，或选用空闲端口。启动器不会结束已有服务。

已启用会话令牌的 HTTP 服务，启动前通过 `CUTVOKE_TOKEN` 提供该令牌，供启动器进行身份查询；令牌不写入运行身份或诊断输出。

运行诊断：

```powershell
.venv\Scripts\python.exe -m cutvoke doctor --json
.venv\Scripts\python.exe -m cutvoke runtime --check --port 8787 --json
```

缺少必要依赖时 `doctor` 返回 `ok:false` 和非零退出码。语音识别与人物模型属于单独安装组件，诊断结果中的 `optionalDependencies` 表示 Python 组件是否安装，不表示模型已下载。

字幕渲染需要随 Python 包安装的 `fontTools`。首次使用某个字体和字重时，会从随包变量字体生成正确的 400/700 字重并原子缓存到系统临时目录 `cutvoke-caption-fonts`；后续导出复用缓存。可设置 `CUTVOKE_FONT_CACHE` 使用专用缓存目录。浏览器预览通过只读白名单 `/api/v1/fonts/NotoSansSC-VF.ttf` 和 `/api/v1/fonts/NotoSerifSC-VF.ttf` 加载同一字体来源。标题片段保留原有 ASS 渲染规则。

`GET /api/v1/runtime` 返回启动时固定的 `sourceFingerprint`、`processId`、`dataPath`、`dataDirectory`、`pythonExecutable` 和 `webDirectory`；同一身份附在 `capabilities.runtime`。磁盘源码变动不会改变旧进程报告的指纹。

## MCP 客户端配置

首次运行 `start-cutvoke.cmd` 准备环境后，在支持 stdio MCP 的客户端使用绝对 Python 路径。以下为 Codex `config.toml` 片段；按实际仓库与工程目录修改。`command`、`args` 和 `env` 配置形式已对照 [OpenAI 官方 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) 核对：

```toml
[mcp_servers.cutvoke]
command = 'E:\myproject\opencut\.venv\Scripts\python.exe'
args = ['-m', 'cutvoke', 'mcp', '--data', 'C:\Users\frank\.cutvoke\data']

[mcp_servers.cutvoke.env]
PYTHONPATH = 'E:\myproject\opencut\src'
PYTHONIOENCODING = 'utf-8'
```

Wheel 安装使用其虚拟环境的 Python，删除 `PYTHONPATH` 配置即可。`cutvoke-mcp --data ...`、`python -m cutvoke mcp --data ...` 和 `start-cutvoke-mcp.cmd --data ...` 使用相同持久化入口。更新源码或升级 wheel 后，需断开并重新连接 MCP 客户端，才能加载新代码。

MCP 与 Web 要共同编辑同一个工程，必须指向同一 `--data` 目录；端口仅属于 Web HTTP 服务，stdio MCP 不占用该端口。MCP 的 stdout 只输出 JSON-RPC，启动诊断与日志输出到 stderr。

## 智能体操作顺序

1. 用 `runtime` 确认版本与数据目录，用 `capabilities` 查询当前命令和参数。
2. 使用 `project_summary` / `project_lookup` 获取当前 revision 与目标对象；需要完整内容时使用 `project_query`。
3. 先检查 `capabilities.commands` 中目标命令的 `previewSupported`；为 true 时可用 `command_preview` 预演。导出、音频处理、创建或取消任务等外部副作用命令不能预演，应明确提交 `command_apply`。编辑多个对象前使用 `edit_lock` 获取租约。
4. `command_apply` 带上当前 `expectedRevision`、租约和可复用的 `commandId`。时间使用 `{ "num": "1", "den": "2" }`。
5. 每次成功编辑已事务保存到 SQLite。关闭、重新连接后可查询重开的工程，并通过 `history.undo` 撤销已保存编辑。
6. 释放租约；先用 `command_apply` 调用只读命令 `project.preflight`（`payload={}`，也可指定导出区间），读取 `changedEntities[0].report.readyToRender`。若为 false，根据 `errors` 定位缺失素材或 LUT、恢复后重新检查。它不会新增编辑 revision 或撤销点。
7. 就绪检查通过后，使用 `export` 输出绝对路径的 MP4。

参数错误以 `isError:true` 与结构化错误返回，客户端可修正后继续调用；版本冲突和业务错误保留 `retryable` / `committed` 信息。读取当前 revision 后再决定重试，不要盲目重复编辑。

## Wheel 交付

发布前必须构建 `web/app`，wheel 会包含 `cutvoke/web`、内置资源与审核文件。缺失 Web 产物时构建直接失败。安装到独立环境后运行：

Windows 可将 `start-cutvoke-wheel.cmd` 放在交付 wheel 旁双击：它准备独立 `.venv`，安装该 wheel，再启动编辑器；检测 wheel 文件内容变更后会重新安装。无需源码仓库或 Node.js。FFmpeg/FFprobe 仍需预先加入 PATH。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install .\cutvoke-0.1.0-py3-none-any.whl
.venv\Scripts\python.exe -m cutvoke doctor --json
.venv\Scripts\python.exe -m cutvoke launch
```

证据验证脚本分别为 `scripts/verify_delivery_mcp.py` 和 `scripts/verify_delivery_launcher.py`。验证使用独立数据目录与自动选择的空闲端口，只结束脚本自己启动的进程。
