# Bilingual README screenshots

The root [English README](../../../README.md) uses only `en/` images. The [Simplified Chinese README](../../../README.zh-CN.md) uses only `zh-CN/` images. All six PNGs are direct Playwright captures of the running CutVoke Web editor at 1600×1160, device scale 1. No UI text, styles or pixels were replaced or composited.

| Scene | English UI | 简体中文界面 | Visible state |
| --- | --- | --- | --- |
| Editor overview | [en/editor-overview.png](en/editor-overview.png) | [zh-CN/editor-overview.png](zh-CN/editor-overview.png) | Three available media thumbnails, real preview at 1 second, video/audio/text/caption tracks and two transitions |
| Caption editing | [en/caption-editing.png](en/caption-editing.png) | [zh-CN/caption-editing.png](zh-CN/caption-editing.png) | First caption selected; its words, 0–4 second interval and editable canvas overlay are visible |
| Community transition | [en/community-transition.png](en/community-transition.png) | [zh-CN/community-transition.png](zh-CN/community-transition.png) | The incoming **lake** clip's **Soft Reveal / 柔和揭幕** selected at the 4-second cut; effective duration **0.60 seconds** |

The original procedural landscape illustrations and synthesized chords come from [demo.py](../../../skills/cutvoke-editor/scripts/demo.py). They contain no user media or third-party footage. These demo assets and screenshots follow the repository's [MIT license](../../../LICENSE).

The capture helper creates two demo clones through the supported API. **Trail Notes · English demo** retains English titles and captions. **山野手记 · 简体中文演示** uses Chinese titles and captions, including **暂别喧嚣。**. The actual language selector is explicitly set to `en` and `zh-CN` for the respective captures. Switching interface language does not translate stored project content; only these newly created demo clones are edited. Source filenames (`dawn.png`, `lake.png`, `dusk.png`, `quiet-score.wav`), track IDs and technical identifiers remain unchanged.

## Reproduce

Use an activated Python environment with CutVoke installed, Node.js, FFmpeg/ffprobe and the built Web editor. Install the checkout's locked browser dependencies with `npm ci` in `web/app`, then run `npm run build` there. The capture helper uses that installation of Playwright.

The captures were made on Linux with Python 3.12.14, Node.js 24.19.0, Chromium 154.0.8037.57 and FFmpeg **n9.0.2-27-g7e93014c5b-20261014**. At capture time, the system FFmpeg 7.1.5 failed the `xfade` graph with invalid `1/0` input frame-rate metadata. The renderer now restores CFR after retiming, and a real FFmpeg 7.1.5 MCP demo completes preview/export/package; no version upgrade is required for that fix. These existing screenshot files retain their original capture provenance. The verified temporary [BtbN 9.0 build](https://github.com/BtbN/FFmpeg-Builds/releases/tag/latest) archive checksum is recorded in [verification.json](verification.json); its binaries are not committed.

From the repository root, choose a **new** output directory:

```bash
python skills/cutvoke-editor/scripts/demo.py --output .artifacts/readme-localized --effects-dir effects/transitions --effect-id cutvoke.transition.community.soft-reveal
```

Start a separate viewing service against that demo's explicit data directory. POSIX shell:

```bash
export CUTVOKE_EFFECTS_PATH="$PWD/effects/transitions"
export CUTVOKE_FONT_CACHE="$PWD/.artifacts/readme-localized/font-cache"
cutvoke serve --data .artifacts/readme-localized/data --port 8798
```

Windows PowerShell:

```powershell
$env:CUTVOKE_EFFECTS_PATH = (Resolve-Path effects/transitions).Path
$env:CUTVOKE_FONT_CACHE = (Resolve-Path .artifacts/readme-localized/font-cache).Path
cutvoke serve --data .artifacts/readme-localized/data --port 8798
```

Leave that service running and, from another activated terminal at the repository root, capture with a local Chromium executable:

```bash
node scripts/verify/capture_readme_screenshots.mjs --data .artifacts/readme-localized/data --url http://127.0.0.1:8798 --browser /usr/bin/chromium
```

Replace `/usr/bin/chromium` with your trusted local Chrome/Chromium path. Alternatively, install Playwright's Chromium with `npx playwright install chromium` in `web/app` and omit `--browser`. The helper verifies the server's selected database, requires data under this checkout's `.artifacts/`, rejects libraries containing unrelated projects, and leaves the original `trail-notes` project unchanged. Do not point it at a personal project library. For another complete run, choose a new demo directory and pass that same directory to all commands. Stop the viewing service with Ctrl+C when finished.

The helper changes language through the visible selector, drags the normal timeline divider to reveal the tracks, seeks with the ruler, selects the first caption and scrolls its editing controls into view, then selects the first transition. It waits for prepared preview video, fonts and media thumbnails. It checks the document language, selected language, caption text, effective transition duration, browser errors, horizontal overflow and unchanged project data during capture. It writes six PNGs to this documentation directory and a `screenshot-capture.json` beside the isolated demo's `data/`. Screenshot timestamps can differ across runs.

Before publishing, **open all six PNGs** and inspect their actual pixels. Check that controls and sample text match each document's language, caption words and timing are readable, the community transition is selected, and no private paths or tokens appear. Refresh the sanitized [verification.json](verification.json) sizes, dimensions, SHA-256 values and review notes after recapturing. Commit only the documentation, screenshots and capture helper; keep databases, media, exports, caches and detailed logs under `.artifacts/` untracked.

## Verification and boundaries

The community demo completed preflight, idempotent command replay, continuous preview, asynchronous MP4 export, project packaging, stream inspection and full decode. Its output is 12 seconds, 1280×720, 30 fps, H.264/AAC. Audio measurements were −18.12 dBFS peak and −26.60 dBFS RMS, with lower RMS at both endpoints than in the body. Subjective listening was not performed. The two localized clones completed real browser preview preparation; these screenshot captures do not claim a separate Chinese export or exhaustive frame/audio review.

All six final editor screenshots were opened and visually checked. English controls, titles and captions appear in `en/`; Simplified Chinese controls, titles and captions appear in `zh-CN/`. Both caption screenshots show their actual text fields, start `0`, end `4` and selected canvas overlay. Both transition screenshots show the localized community effect name and effective `0.60s` duration. No private paths or credentials are visible. See [verification.json](verification.json) for the current source revision, environment, file hashes and scoped checks.

Soft Reveal reuses the existing FFmpeg `fade` implementation. The screenshots demonstrate the existing contribution registry and editable parameters; they do not establish a new visual algorithm, subjective mix quality, arbitrary external effect support, large-project performance or every AI host's compatibility. The existing screenshots retain their original capture provenance; subsequent engine fixes are described below.

### Current MCP handoff behavior

[`AgentTools.media_import`](../../../src/cutvoke/core/agent_tools.py) now writes the canonical `media/<assetId>.<extension>` layout shared with Web uploads. The [Web media check](../../../src/cutvoke/core/httpapi.py) also recognizes exact legacy `media/imports/<sha256>.<extension>` ledger entries, preserving existing project/history references. The demo defaults to real MCP import and verifies browser availability through its own local service. The optional `--import-mode upload` covers the HTTP upload path. Arbitrary local paths and ID mismatches remain rejected; missing sources still require relinking.

简体中文：英文文档只引用 `en/` 的真实英文界面截图，中文文档只引用 `zh-CN/` 的真实简体中文界面截图。两组分别使用对应语言的演示标题与字幕；三种场景均已逐张查看实际像素。上面的命令始终使用独立 `--data`，不会打开默认个人工程库。复拍需保持 demo、查看服务和截图命令的数据目录一致。旧截图沿用原来的 FFmpeg 9 捕获来源；当前代码已修复 FFmpeg 7.1.5 的转场帧率问题和 MCP→Web 素材交接，示例默认使用 MCP 导入。
