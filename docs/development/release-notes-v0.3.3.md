# CutVoke v0.3.3 — Cross-platform preview / 跨平台预览版

This patch prerelease contains the editor and playback fixes accepted on main, rebuilt with version 0.3.3 for Windows x64, macOS Apple Silicon, macOS Intel and Linux x64. The publication gate requires CI and native acceptance of the exact release source commit; the generated BUILD-INFO.json records that commit, build runs and archive hashes.

Changes since the published v0.3.2 packages:

- Improved interactive previews and continuous playback, added the default-on GPU acceleration setting, and completed editor, resource and effect workflows. Acceleration availability still depends on the device and driver.
- Fixed context-menu dismissal blocking Export, pending effect controls, mask gestures interrupted by delayed save acknowledgements, and translated toolbar controls escaping the fixed editor viewport.
- Fixed Windows deep-data resource installs: short exclusive staging names, verified immutable resource registration, bounded concurrent publication, and specific filesystem failure diagnostics.
- Fixed a transition EOF clock error that could freeze a valid export on its middle shot, and preserved decoded full-preview playback while saving live audio gain.
- Added controlled late-response, exact-pixel, playback-continuity, resource-hash and packaged-app regression coverage. Browser CI dependency stages now have separate logs and bounded timeouts.

Download the archive matching your device and verify it against this release's SHA256SUMS. Windows is a portable folder ZIP, not an installer. Close CutVoke before replacing application files, preserve existing project/data folders and keep a backup; never replace user data with acceptance-test databases. All four archives include the built editor, frozen backend, FFmpeg/ffprobe, resources, fonts and third-party notices. FFmpeg 7.1.5 corresponding source is included; optional speech/person models remain separate downloads.

Windows and Linux packages are unsigned. macOS uses an ad-hoc integrity signature without Developer ID signing or notarization. Hosted-runner acceptance does not establish physical-device audio, GPU, microphone, native picker or quarantined Finder/SmartScreen/Gatekeeper first-launch behavior. Windows deep-path acceptance runs with long paths enabled and longPathAware host/frozen manifests; a frozen EXE under a disabled long-path policy was not tested. No system security policy is changed. The existing 14 optional Python skips remain outside the validated coverage. This remains an alpha/preview release, not a production-stability or installer/uninstaller acceptance claim.

Existing tags and downloads, including v0.3.1 and v0.3.2, remain available. Use this release's metadata to check these new archives; historical tag names and old verification records do not substitute for the embedded source commit. GitCode synchronization is not part of this publication.

本版为 v0.3.3 四平台预发布，包含交互预览、持续播放、默认 GPU 加速设置和编辑资源流程改进，修复菜单遮挡、蒙版保存竞态、翻译布局、Windows 深目录资源安装、转场画面冻结与实时音量保存打断预览等问题。发布前须通过同一源码提交的 CI 与四平台真实成品验收，并核对包内版本、来源 SHA、附件哈希和公开下载内容。

Windows 下载为免安装便携 ZIP；更新先关闭程序并备份、保留原有工程及数据目录。四个平台包包含前端、冻结后端、FFmpeg/ffprobe、资源、字体、许可及对应 FFmpeg 源码，基础剪辑无需另装 Python、Node.js 或系统 FFmpeg。可选模型仍需单独下载。

包仍未完成发行签名或公证，不宣称稳定版。未覆盖实体设备音频/GPU/麦克风、隔离首次启动及关闭长路径策略的 Windows 冻结程序；保留既有可选测试跳过，不要求用户修改安全策略。旧标签和下载包保持不变；本次不包含 GitCode 同步。
