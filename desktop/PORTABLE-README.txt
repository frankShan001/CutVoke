CutVoke Windows x64 portable candidate / Windows x64 便携候选版

Extract the WHOLE ZIP into a writable folder, then double-click CutVoke.exe.
Do not run it inside the ZIP. Keep the other files beside the executable.
No installer, administrator permission, Python, Node.js or system FFmpeg needed.
Windows 10/11 x64. This candidate is unsigned; Windows may show SmartScreen.
Signing credentials are not included. No global PATH or registry setup is done.

请完整解压 ZIP 到可写文件夹，再双击 CutVoke.exe；不要在压缩包内运行。
请保留 exe 旁的所有文件。无需安装向导、管理员、Python、Node.js 或系统 FFmpeg。
支持 Windows 10/11 x64。本候选版未签名，Windows 可能显示 SmartScreen 提示。
程序不修改系统 PATH，也不注册全局文件关联。

Your files / 文件位置（相对于 CutVoke.exe）
  CutVoke Data/data/projects.sqlite       projects / 工程数据库
  CutVoke Data/data/media/                imported media / 导入素材
  CutVoke Data/data/media/.preview-cache-v2/   disposable preview cache / 可清理预览缓存
  CutVoke Data/data/font-cache/            generated font cache / 生成字体缓存
  CutVoke Data/data/logs/serve.jsonl       backend diagnostics / 后端诊断
  CutVoke Data/profile/                   language, browser cache / 语言与浏览器缓存
  CutVoke Data/profile/logs/engine.log    startup diagnostics / 启动日志
  CutVoke Data/profile/mcp-config.json    local agent connection / 本地 Agent 配置

Close CutVoke before backing up or updating. Keep CutVoke Data and extract a new
build separately; never replace your data with another build's test database.
Existing source/installed libraries are separate and are not overwritten.
Media references may be absolute: use project packaging or relink missing media
when moving a project to another drive/computer. Export locations are your choice.
For a read-only location, move the whole app to a writable folder, keeping your
data. Advanced: CUTVOKE_PORTABLE_HOME sets an explicit writable data root;
CUTVOKE_DATA sets an explicit project database/directory. No silent fallback.
Preview-cache settings can choose another drive and limit its disk budget.
Short-lived renderer work files can also use the Windows temporary directory.

更新/备份前先关闭程序，保留 CutVoke Data；请将新版解压到独立文件夹，
再带上原数据，不要用任何测试库覆盖工程。原源码版/安装版工程库不会被覆盖。
媒体引用可能是绝对路径：换盘/换电脑请使用工程打包或重新关联丢失素材。
目录只读时请连同数据移动到可写位置；高级用法可设置 CUTVOKE_PORTABLE_HOME
指定数据根目录，或 CUTVOKE_DATA 指定工程库。程序不会悄悄切换到空工程库。
预览缓存设置可选择其他磁盘并限制占用，导出路径由你选择。
短期渲染工作文件也可能使用 Windows 临时目录。

Basic local import, editing, preview and MP4 export work without an AI account.
Select interface language in the top bar (11 choices, including English/简体中文).
External AI providers require their own client/account/network/key. Optional
speech/person models and cloud services are not bundled or claimed offline.
基础导入、剪辑、预览及 MP4 导出不需要 AI 账号；顶部可选择 11 种界面语言。
外部 AI 需自行配置客户端/账号/联网/密钥；可选语音/人物模型与云服务未捆绑，
不属于本包所保证的基础离线功能。

MCP: copy profile/mcp-config.json into your agent's MCP configuration. It uses
the bundled engine and this same project library; no system Python is needed.
MCP：将上述 mcp-config.json 内容配置到 Agent；使用内置后端和同一工程库。

Licenses / 许可证
CutVoke source: MIT, LICENSE.txt. Electron/Chromium notices are in this folder.
Python/frontend/font notices and dependency versions are in licenses/.
Portions of this software are copyright © 2024 The FreeType Project
(www.freetype.org). All rights reserved.
FFmpeg 7.1.5 + libx264 is a separate GPLv3 executable; its full unmodified source
archives, build recipe and configuration are in licenses/ffmpeg/Corresponding-Source.zip.
FFmpeg is a trademark of Fabrice Bellard. https://ffmpeg.org/legal.html
The portable ZIP is an aggregate of separately licensed components, not all MIT.
