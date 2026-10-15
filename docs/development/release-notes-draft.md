# CutVoke 0.3.1 candidate notes — draft, not a public release

## Fixes

- Agent `media_import` and Web uploads share the canonical media-library layout. Exact legacy MCP imports remain visible and relinkable without rewriting saved project paths. MCP and Web agree on availability.
- Normalize xfade inputs after timestamp changes for FFmpeg 7.1, which otherwise exposes an invalid `1/0` rate. Animation frame assembly uses the explicit sequence clock; reverse playback retains source timestamps without relying on cleared frame-rate metadata.
- Isolate API test media/cache directories and allow an explicit MCP media directory. File-backed HTTP embedding defaults to the database's sibling media directory.
- Use official npm registry download URLs with the existing dependency versions/integrity hashes; keep the desktop proxy regression independent of a host's loopback NO_PROXY.
- Align Python runtime/package metadata with the existing 0.3.1 desktop/MCP version. Include source workflow files and the built Web editor in the source candidate.

## Repository and delivery

- English/Chinese contribution entries, issue/PR guidance, a security reporting policy and bounded community tasks.
- Installation/tested-environment troubleshooting, candidate packaging and archive inspection, SHA-256 checksums and a manual build-only Actions workflow.

## Compatibility and validation

No project-format migration is required. Existing media paths and edit history are retained. Preview cache version changes so older render results are rebuilt.

Before publishing this draft, attach the originating commit and its completed CI, source/wheel verification report, clean installed-package smoke results and reviewed media evidence. A local candidate is unsigned and is not a claim of Windows installer or macOS acceptance. The wheel/source distribution requires a separately installed compatible FFmpeg/ffprobe; optional speech/person dependencies and models remain separate.

## 简体中文

修复 MCP 素材交接、FFmpeg 7.1 转场/动画时钟兼容与测试目录隔离，补齐双语贡献/安全/安装排障入口，并提供带校验和的源码/wheel 候选包路径。保留旧工程素材引用，重新生成旧预览缓存。

此文是待审核的说明草案。正式发布前附准确 SHA、终态 CI、实际候选包与干净环境验证；Windows 安装包、签名和第三方二进制再分发须另行实机验收与许可审查。
