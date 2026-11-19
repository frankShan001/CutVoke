# Community tasks / 可认领的社区任务

Open tasks below are maintainer-scoped contribution invitations, not release commitments; completed entries are marked explicitly. Comment on the relevant Issue before starting; the maintainer confirms scope and reviews the PR. The MCP path, FFmpeg frame-clock and test-isolation fixes are maintained in the current code and are not deferred to newcomers.

## 1. Review one interface language with a native speaker

[Claim / 认领：Issue #2](https://github.com/frankShan001/CutVoke/issues/2)

Scope: pick one of Arabic, Japanese or Traditional Chinese; review home, media import, caption editing, preview errors and export in that language. Start with the [translation guide](translations.md). Keep project content, placeholders and API identifiers unchanged.

Acceptance: a short checklist of reviewed screens; corrections in the relevant Web/desktop locale catalogs where applicable; screenshots with synthetic content; `npm run test:i18n` and Web build pass, plus desktop i18n tests if shell strings change. For Arabic, check focus order and RTL while time still increases left to right. Identify what remains unreviewed.

中文：认领一种自己熟悉的语言，实看上述界面、修正文案并保留占位符；提供脱敏截图和翻译检查结果，不将机器翻译直接标成已人工审核。

## 2. Add a documented macOS source-install smoke check

[Claim / 认领：Issue #3](https://github.com/frankShan001/CutVoke/issues/3)

Scope: use one actual macOS machine with a clean Python environment and an identified FFmpeg build. Follow [installation](../user/installation.md), use a new demo directory and document any reproducible setup corrections. This is a source workflow, not a signed/notarized macOS installer.

Acceptance: OS/architecture, Python/Node/browser and FFmpeg versions; passing doctor; default MCP demo with available media in the browser; manual caption edit, playback, MP4 full decode and editable package round trip. Record failures and limits honestly; add documentation corrections without private paths or binaries.

中文：需要真实 macOS 环境；只验收源码安装路径，不把云 Linux 或未测试的安装包写成 macOS 支持证明。

## 3. Completed: original audio-first editing tutorial / 已完成：音频优先教程

[Completed Issue #4 / 已完成任务](https://github.com/frankShan001/CutVoke/issues/4) · [Merged PR #43 / 已合并实现](https://github.com/frankShan001/CutVoke/pull/43)

Use the existing [English tutorial](../../examples/audio-first/README.md) or [中文教程](../../examples/audio-first/README.zh-CN.md); this is no longer an unclaimed implementation task.

The offline example uses original generated media and supported commands for audio roles, waveform/audition, volume/fades and editable captions. Recorded acceptance covers real export/full decode, objective audio checks, editable package relocation, and automated editor checks on original/restored projects. Follow the tutorial's source-build prerequisites; these checks do not establish desktop-package compatibility or subjective listening quality.

中文：教程已在 #43 合入并完成 #4 的限定验收，请勿重复认领实现。按双语教程的源码构建前提运行；客观音频检查与自动化编辑器验收不等于主观听评，也不代表桌面发行包兼容性已验证。

## 4. Add variable-frame-rate footage regression coverage

Scope: generate a small offline VFR source with FFmpeg and known timestamp/color changes, then exercise a trimmed, retimed clip next to an image and a transition. Build on `tests/rendering/test_transition_frame_rate.py`; do not remove existing frame/pixel assertions.

Acceptance: assert timeline duration, expected sequence frame count, identifiable pictures around the cut, bounded preview/export agreement and audio alignment where present. Run on an identified FFmpeg 6.1 build and 7.1 build, record commands, and leave no generated fixtures in Git. Any newly found rendering defect must include a minimal failing case and a separately reviewed fix.

中文：补充真实 VFR 源素材的时间戳回归，检查画面与音画时钟；不是重新认领已修的 `1/0` 帧率问题。
