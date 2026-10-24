# Bilingual README screenshots

The root READMEs share [banner.svg](banner.svg), a static multitrack timeline illustration containing the approved [CutVoke CV icon](../../../resources/source/brand/README.md) as an embedded PNG. It is branding artwork, separate from the unmodified application screenshots below, and follows the repository MIT license. Its title and description provide a text alternative; it has no scripts, animation or external resources. Regenerate the embedded icon with `python tools/assets/build_brand_icons.py`.

Application screenshots in the root [English README](../../../README.md) come from `en/`; those in the [Simplified Chinese README](../../../README.zh-CN.md) come from `zh-CN/`. All six PNGs are direct Playwright captures of the running CutVoke Web editor at 1600×1160, device scale 1. No UI text, styles or pixels were replaced or composited.

| Scene | English UI | 简体中文界面 | Visible state |
| --- | --- | --- | --- |
| Editor overview | [en/editor-overview.png](en/editor-overview.png) | [zh-CN/editor-overview.png](zh-CN/editor-overview.png) | Three available media thumbnails, real preview at 1 second, video/audio/text/caption tracks and two transitions |
| Caption editing | [en/caption-editing.png](en/caption-editing.png) | [zh-CN/caption-editing.png](zh-CN/caption-editing.png) | First caption selected; its words, 0–4 second interval and editable canvas overlay are visible |
| Community transition | [en/community-transition.png](en/community-transition.png) | [zh-CN/community-transition.png](zh-CN/community-transition.png) | The incoming **lake** clip's **Soft Reveal / 柔和揭幕** selected at the 4-second cut; effective duration **0.60 seconds** |

The original procedural landscape illustrations and synthesized chords come from [demo.py](../../../skills/cutvoke-editor/scripts/demo.py). They contain no user media or third-party footage. These demo assets and screenshots follow the repository's [MIT license](../../../LICENSE).

The capture helper creates two demo clones through the supported API. **Trail Notes · English demo** retains English titles and captions. **山野手记 · 简体中文演示** uses Chinese titles and captions, including **暂别喧嚣。**. The actual language selector is explicitly set to `en` and `zh-CN` for the respective captures. Switching interface language does not translate stored project content; only these newly created demo clones are edited. Source filenames (`dawn.png`, `lake.png`, `dusk.png`, `quiet-score.wav`), track IDs and technical identifiers remain unchanged.

## Reproduce

Use an activated Python environment with CutVoke installed, Node.js, FFmpeg/ffprobe and the built Web editor. Install the checkout's locked browser dependencies with `npm ci` in `apps/editor`, then run `npm run build` there. The capture helper uses that installation of Playwright.

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
node tools/verify/capture_readme_screenshots.mjs --data .artifacts/readme-localized/data --url http://127.0.0.1:8798 --browser /usr/bin/chromium
```

Replace `/usr/bin/chromium` with your trusted local Chrome/Chromium path. Alternatively, install Playwright's Chromium with `npx playwright install chromium` in `apps/editor` and omit `--browser`. The helper verifies the server's selected database, requires data under this checkout's `.artifacts/`, rejects libraries containing unrelated projects, and leaves the original `trail-notes` project unchanged. Do not point it at a personal project library. For another complete run, choose a new demo directory and pass that same directory to all commands. Stop the viewing service with Ctrl+C when finished.

The helper changes language through the visible selector, drags the normal timeline divider to reveal the tracks, seeks with the ruler, selects the first caption and scrolls its editing controls into view, then selects the first transition. It waits for prepared preview video, fonts and media thumbnails. It checks the document language, selected language, caption text, effective transition duration, browser errors, horizontal overflow and unchanged project data during capture. It writes six PNGs to this documentation directory and a `screenshot-capture.json` beside the isolated demo's `data/`. Screenshot timestamps can differ across runs.

Before publishing, open all six PNGs and check their actual pixels, language,
readable controls and absence of private paths. Refresh the image hashes in
`resources/source/provenance.json` after replacing a screenshot. Keep detailed
capture logs, test media, databases and exports under `.artifacts/`.

Screenshots illustrate the editor and community effect workflow. They do not
establish performance, audio quality or compatibility with every external AI host.

简体中文：两组截图分别展示英文和简体中文界面，素材由仓库演示脚本生成。
复拍时使用独立数据目录；只提交正式图片和必要来源说明，详细过程保存在本地。

## Video platform links

[platform-links/bilibili.svg](platform-links/bilibili.svg) and [platform-links/youtube.svg](platform-links/youtube.svg) are static link badges used in both root READMEs. The platform icon paths come from [Simple Icons: Bilibili](https://github.com/simple-icons/simple-icons/blob/develop/icons/bilibili.svg) and [Simple Icons: YouTube](https://github.com/simple-icons/simple-icons/blob/develop/icons/youtube.svg), distributed under [CC0](https://github.com/simple-icons/simple-icons/blob/develop/LICENSE.md). The platform marks identify their respective video links; they are separate from CutVoke branding. The SVGs include text alternatives and have no scripts or external dependencies.
