# CutVoke v0.3.4 — Cross-platform preview / 跨平台预览版

This prerelease brings the current project, composition and animation workflows to Windows x64, macOS Apple Silicon, macOS Intel and Linux x64. All four archives are rebuilt with version 0.3.4. Publication requires successful CI and actual packaged-application acceptance at the exact tagged main commit; this release's BUILD-INFO.json records the source, build runs and archive hashes.

Changes since the published v0.3.3 packages:

- New projects use independent directories for their database, media, previews, exports and backups. Migration, moving/reopening, original media names and shared GUI/MCP asset resolution preserve project ownership and isolation.
- Composition and animation workflows add adjustment layers, effect opacity/local blending, Alpha/Luma track mattes, shared precompositions and instance overrides, local time windows, sequence duplication, spatial Bezier paths, easing and bounded declarative bindings.
- Added procedural noise, displacement, vector paths, 3D planes and native meshes/materials/cameras/lights, with OpenGL acceleration and software fallback. Optional local cutout-model tasks share status, cancellation and application between the editor and external agents. Model packages remain separate downloads; the optional one-click model installer is accepted on Windows x64, and unsupported platform combinations report their boundary.
- Improved AI editing guidance, quick application, timeline filtering, property views, performance feedback and cancellable idle previews. External AI clients still need their own wake-up integration for unattended assistance requests.
- Fixed windowed-export color/alpha continuity, finite last-frame clocks and buffered audio tails. Interactive audio follows its audio-device clock under load. Fixed copied-media identities, original-name migration/search and effect parameters accidentally treated as file paths.
- Fixed delayed preset metadata triggering an unnecessary fallback preview, late directory/history responses replacing authored export paths, and stale background preview conflicts replacing successful edit feedback. Pixel, alpha, duration, playback and interaction assertions remain strict.
- Restored Python 3.10 compatibility in release tooling and built the pinned macOS Intel dependency from verified source where its wheel is unavailable. Draft publication is bound to release ID, tag and source commit, with uploaded and public-download digest checks.

Download the archive matching your device and verify it against this release's SHA256SUMS. Windows is a portable folder ZIP. Close CutVoke before replacing application files; back up and preserve existing project/data directories, and keep a copy before migrating older projects. All four archives contain the built editor, frozen backend, FFmpeg/ffprobe, resources, fonts, third-party notices and FFmpeg 7.1.5 corresponding source. Basic editing needs no separate Python, Node.js or system FFmpeg. Optional models and PyTorch are separate downloads.

Windows and Linux packages are unsigned. macOS uses an ad-hoc integrity signature without Developer ID signing or notarization. Hosted acceptance does not establish physical-device GPU/audio/microphone/native-picker or quarantined Finder/SmartScreen/Gatekeeper first-launch behavior. Windows deep-directory acceptance runs with long paths enabled and longPathAware host/frozen manifests; disabled-policy frozen-app behavior remains untested. A previously observed Intel export-preparation timeout still lacks a proven root cause; repeated GUI exports and live diagnostics retain evidence if it recurs. Existing optional/environment-dependent Python skips remain outside the validated coverage. This remains a preview release, with installer/uninstaller and production-stability acceptance outstanding. No system security-policy change is required by this publication.

Existing tags and downloads, including v0.3.1, v0.3.2 and v0.3.3, remain available. Use this release's embedded source commit, BUILD-INFO.json and SHA256SUMS for these archives. GitCode synchronization is outside this publication.

本版为 v0.3.4 四平台预发布，将工程独立目录、合成与动画、本机三维和可选模型任务、AI 剪辑指引等当前功能打入新包，并保留原有功能。工程数据库、素材、预览、导出和备份统一归属工程目录；迁移、移动重开、GUI/MCP 素材解析及原始名称保持一致。

新增调整层、效果局部混合、Alpha/Luma 轨道遮罩、共享预合成及实例覆盖、局部时间编辑、空间贝塞尔与有界参数绑定，以及程序噪声、矢量路径和三维工作流。可选模型与 PyTorch 不包含在基础包内；模型一键安装的已验收平台为 Windows x64，其他不支持组合会明确提示。外部 AI 的空闲唤醒仍需对应客户端集成。

修复分段画面的颜色与 Alpha 连续性、末帧时钟、音频尾部与负载下交互音频反复跳转；修复素材身份/名称、效果文件归属、延迟预设目录、手填导出路径被覆盖及旧预览冲突覆盖编辑反馈等问题。发布须核对同一 main 提交的 CI、四平台真实成品、包内版本/源 SHA、上传哈希及六个公开下载文件，不放宽像素、交互或时长断言。

更新前关闭程序，备份并保留原工程与数据目录；旧工程迁移前另留副本。Windows 是完整解压即可启动的便携 ZIP；基础使用无需另装 Python、Node.js 或系统 FFmpeg。包包含第三方许可及对应 FFmpeg 源码。

本版仍未完成发行签名或公证，不宣称稳定版。实体设备 GPU/音频/麦克风/原生选择器、隔离首次启动、禁用长路径策略的 Windows 冻结程序未验证。一次历史 Intel 导出准备超时的根因尚未查明，现已加入重复真实导出与现场诊断保留。既有可选/环境跳过仍是未测边界。旧版本及下载保持不变，本次不包含 GitCode 同步。
