CutVoke — macOS / Linux prerelease / 预发布包

Exact version and source commit: build-info.json beside this file.
准确版本与来源提交见本文件旁的 build-info.json。

No Python, Node.js or system FFmpeg installation is needed for basic local
import, editing, preview and H.264/AAC MP4 export. Keep the entire distribution.
基础本地导入、剪辑、预览和 MP4 导出无需安装 Python、Node.js、FFmpeg。请保留完整目录。

macOS: choose arm64 for Apple Silicon, x64 for Intel. Requires macOS 13+.
Unzip, move CutVoke.app to /Applications or ~/Applications, and open it.
The app has an ad-hoc integrity signature only: no Apple Developer ID and no
notarization. A quarantined internet download may be blocked by Gatekeeper.
This candidate cannot promise a warning-free first launch. If blocked, stop;
ask for a future Developer ID signed/notarized build. Do not disable Gatekeeper,
remove quarantine, or weaken system security. Hosted-runner tests do not accept
Gatekeeper or Finder's internet-download launch path on your Mac.
macOS：Apple Silicon 选 arm64，Intel 选 x64，系统需 macOS 13+。解压后将
CutVoke.app 移到 /Applications 或 ~/Applications，再打开。只有 ad-hoc 完整性
签名，没有 Developer ID 和公证；网络下载隔离可能导致 Gatekeeper 阻止运行。
受阻时请停止并等待正式签名/公证版，不要关闭安全保护或移除隔离标记。

Linux: x64, Ubuntu 22.04+ / glibc 2.35+, a graphical desktop and GTK3, NSS,
ATK, X11/XCB, GBM/DRM, ALSA and related Electron system libraries are required.
Extract the .tar.gz (preserves executable bits and links). In its directory,
run ./cutvoke, or open that executable in a file manager which permits it.
No FUSE or package installer is needed. A noexec filesystem cannot run it.
Use an ordinary desktop user with usable unprivileged user namespaces for the
Chromium sandbox. Root and desktops whose policy blocks the sandbox are not
supported by this directory package. Do not use --no-sandbox or change global
security policy. Ubuntu 24.04 AppArmor can restrict user namespaces; an admin-
reviewed application policy or a future installed/signed package may be needed.
Linux：x64、Ubuntu 22.04+ / glibc 2.35+，需桌面及上述系统库。完整解压 tar.gz，
在目录运行 ./cutvoke，或由允许执行程序的文件管理器打开。无需 FUSE。
需普通用户和可用的 Chromium 沙箱；root、noexec 文件系统或阻止沙箱的系统策略
不在支持范围。不要用 --no-sandbox 或降低全局安全策略。

Data / 数据位置：
  macOS: ~/Library/Application Support/CutVoke/
  Linux: ${XDG_DATA_HOME:-~/.local/share}/CutVoke/
  data/projects.sqlite, uploaded media, preview cache, data/font-cache
  profile/: settings, browser cache, logs/engine.log, mcp-config.json
Applications can live in a read-only folder; data must be writable. Updates do
not replace your data. Preserve this folder and backups. CUTVOKE_HOME can select
an explicit alternate data root; CUTVOKE_DATA overrides the database location.
程序目录可只读；数据目录须可写。更新请保留数据。搬家/换电脑用工程打包与重链接。

After first launch, profile/mcp-config.json contains the bundled engine command
for an external MCP client and points at the same database. Regenerate it by
opening the relocated app if the app path changes. AI clients/accounts and
optional ASR/person models are separate and not bundled. No model or API key is
needed for the basic offline workflow. Sensitive macOS folders may require the
user's normal OS permission; denied access is not bypassed.
首次启动生成的 MCP 配置使用内置引擎；移动应用后再次打开可更新路径。外部 AI、
语音/人物模型另有要求。系统权限由用户正常授权，不绕过拒绝。

build-info.json, file-manifest.json, SHA256SUMS.txt and verification evidence
describe the exact build. licenses/ includes the GPLv3 FFmpeg corresponding
source, build recipe, dependencies, Python, web, Noto and runtime notices.
CutVoke source is MIT; the aggregate is not all MIT. Preserve component notices.
This is an Actions candidate, not a formal Release/tag. Tests are automated on
hosted runners, not human acceptance of audio, GPU, all distributions or Macs.

Guide / 说明： https://github.com/frankShan001/CutVoke/blob/main/docs/user/installation.md
