# CutVoke v0.3.2 — Cross-platform preview / 跨平台预览版

This prerelease rebuilds Windows x64, macOS Apple Silicon, macOS Intel and Linux x64 from the same source commit. Each shipped archive is extracted and exercised on its native GitHub runner before publication.

Changes since v0.3.1:

- Updated editor setup, resource workflows and preview preparation; reusable cinematic and travel effects and privacy-safe effect-module sharing.
- Reorganized applications, engine modules and asset sources while preserving approved resource bytes and legacy offline-pack support.
- Fixed Python 3.13 packaging with a hash-locked NumPy release that provides native wheels on all four targets; repaired relocated desktop build paths.
- Pinned macOS NumPy wheels compatible with the macOS 13 baseline and kept project creation reachable in short desktop windows after language changes.
- Corrected FFmpeg filtergraph compatibility tests, deterministic alpha conversion, relocated preset attribution and timeline drag coordinates.
- Added a release gate that requires successful same-commit CI and all native package acceptances, verifies embedded provenance and archive hashes, stages a fresh draft, then verifies public downloads.

The Windows and Linux packages are unsigned. macOS packages use an ad-hoc integrity signature, without Developer ID signing or notarization. Automated acceptance does not establish human device, native picker, GPU/audio or SmartScreen/Gatekeeper acceptance. This remains an alpha/preview release, not a claim of production stability.

FFmpeg 7.1.5 and its corresponding source and third-party notices are bundled. Model downloads for optional speech/person features remain separate. Existing v0.3.1 downloads remain available.

本版为四平台重新构建的预发布，包含编辑器与资源流程改进、可复用效果模块、目录重构及本轮构建修复。发布必须通过同提交的 CI 与各平台真实成品验收，并核对包内来源、附件 SHA256 和公开下载内容。尚未完成跨设备人工体验、签名与公证，不宣称稳定版；保留旧版下载。
