# Installation and troubleshooting / 安装与排障

[English README](../../README.md) · [中文 README](../../README.zh-CN.md) · [Contributing](../../CONTRIBUTING.md)

**[v0.3.1 prerelease / 预发布](https://github.com/frankShan001/CutVoke/releases/tag/v0.3.1)** · [SHA256SUMS](https://github.com/frankShan001/CutVoke/releases/download/v0.3.1/SHA256SUMS). Release assets are the actual archives, ready to extract. The tag is `58f890090463300c007f541ac36be8bca71bb4e9`; binaries remain the original `fdec479c4acbc3c685a5c8adbe8ec91621a7c194` build. 标签对应文档最终提交，二进制未重建；本页保留原始 runner 证据。

## Windows portable (x64)

**[Download 0.3.1 portable](https://github.com/frankShan001/CutVoke/releases/download/v0.3.1/CutVoke-0.3.1-windows-x64-portable.zip)** · [successful Windows run](https://github.com/frankShan001/CutVoke/actions/runs/38845496400) · [screenshots and test evidence](https://github.com/frankShan001/CutVoke/actions/runs/38845496400/artifacts/12023445833)

The Release download is `CutVoke-0.3.1-windows-x64-portable.zip`; download `SHA256SUMS` separately for verification. Extract the ZIP completely to a writable folder and double-click **CutVoke.exe**. No installer, administrator permission, Python, Node.js or system FFmpeg configuration is needed. The original Actions wrapper and runner evidence remain provenance records with their own retention limits.

| Portable ZIP | Value |
| --- | --- |
| Filename | `CutVoke-0.3.1-windows-x64-portable.zip` |
| Size | **380,760,157 bytes / 363.1 MiB** |
| SHA256 of this inner portable ZIP | `2aab6699ee1644f1afe7d6daa4afa9d1fd99311838c46fc4bb4c6ad202cc1dbc` |
| Build commit | `fdec479c4acbc3c685a5c8adbe8ec91621a7c194` |
| Actions run | `38845496400` |
| Signature | Unsigned CutVoke candidate; Windows may display SmartScreen |

The checksum above is for the named **portable ZIP**, not GitHub's outer artifact download. It can be checked in PowerShell with `Get-FileHash -Algorithm SHA256 .\CutVoke-0.3.1-windows-x64-portable.zip`. `build-info.json` and `file-manifest.json` inside the app record component versions and file hashes. These exact bytes are published in the [v0.3.1 prerelease](https://github.com/frankShan001/CutVoke/releases/tag/v0.3.1).

| Relative to CutVoke.exe | Purpose |
| --- | --- |
| `CutVoke Data/data/projects.sqlite` | Project database |
| `CutVoke Data/data/media/` | Imported media |
| `CutVoke Data/data/media/.preview-cache-v2/` | Disposable preview cache; location/budget can be changed in settings |
| `CutVoke Data/data/font-cache/` | Generated caption fonts |
| `CutVoke Data/profile/` | Language, browser cache and settings |
| `CutVoke Data/profile/mcp-config.json` | Agent configuration using the bundled engine and this database |
| `CutVoke Data/profile/logs/engine.log` and `data/logs/serve.jsonl` | Startup and backend diagnostics |

Close the app before backup/update and keep `CutVoke Data`. Extract a newer build separately and preserve your existing data; the package contains no replacement project database. Source/installed libraries remain separate. Media references may be absolute: use project packaging or relink when changing paths/computers. A read-only location displays an explanation and exits without switching to an empty project library. Advanced overrides are `CUTVOKE_PORTABLE_HOME` (whole data root) and `CUTVOKE_DATA` (database/directory). Short-lived rendering work can use Windows' temporary directory.

The package contains the built Web editor, Electron 44.5.1, Python 3.13.16 frozen backend, an FFmpeg 7.1.5/ffprobe build, Noto fonts and notices. `licenses/ffmpeg/Corresponding-Source.zip` includes the exact FFmpeg/libx264/libass and supporting-library sources, hashes and build recipe; these standalone tools use GPLv3. Other components retain their own licenses, including font OFL and the bootloader exception. [Build/provenance details](../development/windows-portable.md).

Basic local import, editing, preview and MP4 export work without an AI account. Optional external AI services/clients need their own configuration/account/network/key; optional speech/person models and dependencies are not bundled or claimed as part of basic offline operation.

**中文启动说明：** 从 Release 下载上述便携 ZIP，核对 SHA256SUMS，完整解压到可写目录，再双击 `CutVoke.exe`。无需安装或配置开发环境。工程、素材和缓存位置如上表；更新先关闭程序，保留 `CutVoke Data`，不要用测试库覆盖工程。包未签名，可能有 SmartScreen 提示。基础剪辑可本机离线完成，可选 AI/语音/人物功能另行配置。Windows runner 的自动验收已完成，人工设备体验边界见下表。

## macOS (arm64 / x64)

**Verified 0.3.1 downloads:** [successful native run 38845496409](https://github.com/frankShan001/CutVoke/actions/runs/38845496409), build `fdec479c4acbc3c685a5c8adbe8ec91621a7c194`. The original verified archives are now available directly from the [v0.3.1 prerelease](https://github.com/frankShan001/CutVoke/releases/tag/v0.3.1); Actions evidence retains its original expiry.

| Inner archive / download | Size | SHA256 of the inner archive | Evidence |
| --- | --- | --- | --- |
| [CutVoke-0.3.1-macos-arm64.zip](https://github.com/frankShan001/CutVoke/releases/download/v0.3.1/CutVoke-0.3.1-macos-arm64.zip) | 352,156,692 bytes / 335.8 MiB | `24b4c0615618cabf5a641d0aa5abc0b76a5bf768b9291001c0b90fd557b0576b` | [arm64 screenshots/reports](https://github.com/frankShan001/CutVoke/actions/runs/38845496409/artifacts/12022249773) |
| [CutVoke-0.3.1-macos-x64.zip](https://github.com/frankShan001/CutVoke/releases/download/v0.3.1/CutVoke-0.3.1-macos-x64.zip) | 422,388,587 bytes / 402.8 MiB | `93116f49ca982398766abb20efc580bc1f6282581885b1cbb6b95a1a4f7d1854` | [Intel screenshots/reports](https://github.com/frankShan001/CutVoke/actions/runs/38845496409/artifacts/12023680611) |

The original **Upload verified download** steps in the [native package workflow](https://github.com/frankShan001/CutVoke/actions/workflows/unix-packages.yml) produced the accepted archives. The Release uploads those same inner archives after fresh byte/hash checks. Its attachments are ready to extract; the original outer Actions ZIP, metadata and verification reports remain provenance evidence. The [durable verification record](../development/native-packages-verification.json) also records the outer artifact hashes; the table above hashes the inner named ZIP. On macOS use `shasum -a 256 CutVoke-0.3.1-macos-arm64.zip` (or the x64 filename).

1. Apple Silicon (M-series): choose **CutVoke-macos-arm64**. Intel: choose **CutVoke-macos-x64**. These are separate native builds; no Rosetta requirement is introduced for Apple Silicon.
2. Verify and extract the directly downloaded `CutVoke-0.3.1-macos-<arch>.zip`. Keep `START-HERE.txt`, hashes and `licenses/` for reference/redistribution.
3. Move `CutVoke.app` into `/Applications` or `~/Applications`, then open it. No Python, Node.js or FFmpeg install is needed for basic local editing.

The declared minimum is **macOS 13 (Ventura)**, required by [Electron 44](https://www.electronjs.org/docs/latest/breaking-changes#removed-macos-12-support). Native images are audited for architecture, deployment target, unresolved build-machine paths and signature integrity. The actual automated execution baseline is macOS 15 on GitHub's separate Apple Silicon and Intel runners; macOS 13/14 and user hardware are not manually accepted by that run.

**Signing boundary:** these apps have an **ad-hoc integrity signature only**, no Apple Developer ID publisher identity and no notarization. Internet-download quarantine may cause Gatekeeper to block launch. The package is self-contained, but this candidate cannot guarantee a normal warning-free first launch on another Mac. Both `spctl --assess` checks rejected automatic trust (exit 3). After checking the source and matching SHA256, users who trust the unmodified app may follow Apple's app-specific **System Settings → Privacy & Security → Open Anyway** approval after attempting to open it. Stop if trust cannot be established or the system still refuses. Users need no developer account purchase. Keep global security protections enabled; no signing credentials, quarantine removal or Gatekeeper disabling are part of this process. The evidence records `codesign --verify --deep --strict`, identity information and `spctl --assess`; direct execution of a non-quarantined CI build does not establish acceptance of Finder's quarantined download path. See [Apple's explanation of app security checks](https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unknown-developer-mh40616/mac).

Data uses **`~/Library/Application Support/CutVoke/`**, outside the `.app`. The application bundle can be read-only. `data/projects.sqlite`, uploaded media, preview caches and `data/font-cache` share that root; `profile/` contains language settings, Chromium state, `logs/engine.log` and generated `mcp-config.json`. Keep this data when replacing the app. If you move the app, launch it once to refresh the MCP command path. macOS may request normal file-access permissions for protected folders; denied permissions are not bypassed. Optional AI clients/accounts and ASR/person models are separate.

**中文：**Apple Silicon 选 arm64，Intel 选 x64；需 macOS 13+，实际自动执行基线为 macOS 15。完整解压后把 `.app` 移到应用程序目录。基础功能无需 Python/Node/FFmpeg；数据在 `~/Library/Application Support/CutVoke/`。仅 ad-hoc 签名、没有 Developer ID 或公证，网络下载可能被 Gatekeeper 阻止；确认来源可信且 SHA256 一致后，可在尝试启动后的“系统设置 → 隐私与安全性”中针对该 app 确认“仍要打开”；无需购买开发者账号，不关闭全局安全保护。无法确认来源或仍被拒绝时停止并保留错误。runner 直接启动不等于已通过用户 Mac 的隔离下载/Finder 验收。

## Linux (x64)

**[Download verified CutVoke-linux-x64](https://github.com/frankShan001/CutVoke/releases/download/v0.3.1/CutVoke-0.3.1-linux-x64.tar.gz)** · [Linux screenshots/reports](https://github.com/frankShan001/CutVoke/actions/runs/38845496409/artifacts/12023600328) · [successful native run](https://github.com/frankShan001/CutVoke/actions/runs/38845496409).

The inner `CutVoke-0.3.1-linux-x64.tar.gz` is **359,522,533 bytes / 342.9 MiB**, SHA256 **`c8df79f6a184b9dcf5d12506516db4ef2fca7073d6e091de640b532023010662`**. It uses the same binary build commit as the Mac packages. Check the actual Release archive with `sha256sum CutVoke-0.3.1-linux-x64.tar.gz` or `sha256sum -c SHA256SUMS`. Preserve executable permissions and symlinks when extracting the directly downloaded archive:

```bash
tar -xzf CutVoke-0.3.1-linux-x64.tar.gz
cd CutVoke-0.3.1-linux-x64
./cutvoke
```

A file manager can also open `cutvoke` if it permits launching local executables. Keep the full directory. There is no FUSE/AppImage dependency or package installer, and no system Python, Node.js or FFmpeg is needed. The archive includes its own media codecs and filters.

The supported baseline is **Ubuntu 22.04 x64 / glibc 2.35+**, a graphical desktop, and Electron's system libraries: GTK3, NSS/NSPR, ATK/AT-SPI, X11/XCB, GBM/DRM, ALSA, libstdc++ and related distribution dependencies. A minimal/server image may need administrator installation of those desktop libraries; a display server and usable audio/video drivers remain system requirements. Native ELF headers, `ldd` resolution and required GLIBC symbol versions are audited. Other distributions, Wayland-only environments, device audio/GPU and newer/older desktops require their own acceptance.

Run as a **normal desktop user**, from a filesystem which permits execution. Chromium's sandbox must work through the system's unprivileged user namespaces. Root and systems whose policy blocks that mechanism are outside this directory package's supported scope. In particular, Ubuntu 24.04 AppArmor may restrict user namespaces; an administrator-reviewed application policy or a future installed package may be required. This package does not install a root-owned setuid helper, set global sysctls or disable the sandbox. Do not use `--no-sandbox` as a workaround. The GUI acceptance explicitly keeps the renderer sandbox enabled. See [Ubuntu's user-namespace policy](https://documentation.ubuntu.com/security/security-features/privilege-restriction/unprivileged-user-namespace-restrictions/).

Data uses **`${XDG_DATA_HOME:-~/.local/share}/CutVoke/`** with the same `data/` and `profile/` structure as macOS. No write access to the extracted application directory is required. Keep the data root when updating. Desktop packages accept `CUTVOKE_HOME` to select a different writable root and `CUTVOKE_DATA` to select an explicit database; `CUTVOKE_DATA` also changes the associated media/cache location. A read-only data directory produces a native error and exits after acknowledgement, preserving the existing database. The launcher does not fall back to an empty library. For moving media between paths/computers, use project packaging and relink.

**中文：**下载 x64 的 tar.gz，完整解压后运行 `./cutvoke`。基线为 Ubuntu 22.04 / glibc 2.35+，需桌面系统库、可执行文件系统及普通用户可用的 Chromium 沙箱。无需 FUSE 或另装 Python/Node/FFmpeg，但精简系统可能需要管理员安装 GTK/NSS/音频等桌面库。Ubuntu 24.04 的 AppArmor 可能限制用户命名空间；不要用 `--no-sandbox` 或降低全局策略。数据在 `${XDG_DATA_HOME:-~/.local/share}/CutVoke/`，更新程序时保留数据。

## Install from source (developers)

Use Python **3.10+**, Node.js **20+**, and `ffmpeg` plus `ffprobe` on PATH. FFmpeg must include `libx264`, AAC, overlay, drawtext and subtitles/libass. Optional ASR/person features need their own dependencies and model preparation; they are not required for the basic editor/demo.

```bash
git clone https://github.com/frankShan001/CutVoke.git
cd CutVoke
python -m venv .venv
```

Activate with `source .venv/bin/activate` on Linux/macOS, or `.\.venv\Scripts\Activate.ps1` in Windows PowerShell. Then:

```bash
python -m pip install -e .
cd web/app
npm ci
npm run build
cd ../..
cutvoke doctor --json
python skills/cutvoke-editor/scripts/demo.py --output .artifacts/first-edit
cutvoke serve --data .artifacts/first-edit/data
```

Use a new output directory for each demo. Open `http://127.0.0.1:8787` and select the demo project. The MCP process, HTTP service and desktop must all use the same data directory. `--data` takes precedence over `CUTVOKE_DATA`; the CLI default is `~/.cutvoke/data`. No HOME override or special filesystem permission is needed when a writable `--data` directory is provided.

## Tested environments and limits

| Environment | Evidence and scope |
| --- | --- |
| Linux cloud, Python 3.12.14, Node 24.19.0, Chromium 154.0.8037.57, Debian FFmpeg 7.1.5 | Real stdio MCP import, Web availability/thumbnails, manual caption editing, playback, GUI MP4 export and full decode; the 12-second community-transition demo also completes preview/export/package and audio measurements |
| Ubuntu 24.04 CI, Python 3.10 / 3.12 / 3.13, Node 24, apt FFmpeg | The [CI workflow](../../.github/workflows/ci.yml) runs backend regressions, browser editing and installed-wheel checks; use the status for the exact commit being installed |
| Windows Server 2022 x64, packaged 0.3.1 portable, Python 3.13.16, Electron 44.5.1, bundled FFmpeg 7.1.5 | [Run 38845496400](https://github.com/frankShan001/CutVoke/actions/runs/38845496400): shipped ZIP extraction in Chinese/space paths, visible GUI and frozen health, eleven languages, real media upload + stdio MCP, playback, GUI export/full H.264/AAC decode, restart, duplicate/port conflict, shutdown and real ACL failure dialog with unchanged DB. Native normal startup has no QA/debug flags; PE manifests use asInvoker. |
| Windows 10/11 devices, manual picker/drag behavior, device audio/GPU and SmartScreen | Not manually accepted on the user's computer by this workflow; automated server evidence is not a substitute for those device-specific checks |
| macOS 15 arm64 / Intel and Ubuntu 22.04 x64 native distributions | See the [native package verification record](../development/native-packages-verification.json) for the exact build and successful runs. Automated extracted-archive GUI, frozen backend, MCP, offline resources, import/preview/export/full decode and lifecycle checks; human audio/GPU and quarantined Mac download launch remain separate. |

FFmpeg 6.1 remains part of the existing Ubuntu regression path. FFmpeg 7.1's `setpts` clears frame-rate metadata: the renderer restores CFR after retiming and uses the explicit sequence rate when assembling animation frames. A cached behavior probe enables the extra xfade normalization only when needed, preserving the last alpha frame and short source handles on builds such as 6.1. Upgrading FFmpeg to hide an invalid graph is not required. A distribution's version number alone does not guarantee the required codecs or filters.

The project version is aligned at **0.3.1** in Python, MCP and desktop metadata. The four original desktop archives above are published in the [v0.3.1 prerelease](https://github.com/frankShan001/CutVoke/releases/tag/v0.3.1). Wheel/source candidates are developer distributions that still need Python and FFmpeg; they are not substitutes for the self-contained portable ZIP. See [candidate packaging](../development/prerelease.md).

## Troubleshooting

| Symptom | Check and action |
| --- | --- |
| Portable app cannot start | Fully extract the whole portable ZIP, keep its neighboring runtime files and use a writable folder. Preserve `CutVoke Data`; check `profile/logs/engine.log`. Do not install Python/Node/FFmpeg to repair a partial extraction. |
| Source install: `ffmpeg` or `ffprobe` missing; codec/filter failures | Run `cutvoke doctor --json`, `ffmpeg -version` and `ffprobe -version` in the activated terminal. Install a suitable build, confirm PATH, then restart the editor's own service. Preserve the exact error if a filter is absent. |
| Permission error under `~/.cutvoke` | Use `cutvoke serve --data .artifacts/my-edit/data` and `cutvoke mcp --data .artifacts/my-edit/data`. Keep this directory writable and identical across interfaces. For tests, pass a temporary `media_dir`; do not change HOME or weaken permissions. |
| Agent-imported media missing in Web | Update the code and restart both processes on the same database. New imports use `media/<assetId>.<ext>`; exact legacy `media/imports/<sha256>.<ext>` entries remain accessible without moving files. Truly missing media still needs relinking to the intended source. |
| `xfade` says `1/0` is invalid | Restart the updated service, then reproduce with the same project and identify the FFmpeg build. The known `setpts`/CFR issue is fixed in the renderer. Report a small reproducer if the updated graph still fails; do not swap binaries without recording that difference. |
| `npm ci` downloads fail | The lockfiles use npm's official registry and preserve package integrity hashes. Check the failing URL, proxy and network access; do not disable TLS or discard the lockfile to obtain a different dependency graph. |
| Blank editor or doctor reports missing Web assets | Run `npm ci && npm run build` inside `web/app`, then restart the service. Wheels include the built editor; Node is needed to build from source, not to run the installed wheel. |
| Port occupied or an old build still responds | Use `cutvoke doctor --json` and `/api/v1/runtime`; stop only the service launched for that workspace, or select another `serve --port`. Rebuild/restart after code changes. |
| Browser-test download blocked | With a trusted installed browser, set `CUTVOKE_E2E_BROWSER=/usr/bin/chromium` and keep the Python environment on PATH. Otherwise install Chromium with Playwright in `web/app`. |
| Optional speech/person processing unavailable | Follow [ASR setup](LOCAL_ASR.md) or [local setup](LOCAL_DELIVERY_MCP.md); model downloads and their licenses are separate from the basic install. |

## 简体中文

源码安装需 Python 3.10+、Node.js 20+ 和具备所需编解码器/滤镜的 FFmpeg、ffprobe。先激活虚拟环境，再安装 Python 包、运行 Web 的 `npm ci` 和构建，最后执行 `cutvoke doctor --json`。

云端已分别完成 Windows Server 2022、macOS 15（arm64/Intel）和 Ubuntu 22.04 x64 发行包真实解压后的自动验收；用户设备的人工体验仍需各自验收。Mac 签名和 Linux 沙箱边界见上方专节。源码版默认个人数据目录只读时，给 Web 和 MCP 指定同一个可写 `--data`；Windows 便携版使用 exe 旁的数据目录，Mac/Linux 使用上方系统数据位置。更新程序时保留数据。上表列明准确范围。
