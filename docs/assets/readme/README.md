# Screenshot provenance and demonstration checks

These are actual screenshots of the running CutVoke Web editor, captured in local Chrome through Playwright. No UI was drawn, replaced or composited for the images. They share a 1600×1160 viewport and are embedded by relative path in both root READMEs.

| Image | Actual state |
| --- | --- |
| [editor-overview.png](editor-overview.png) | The isolated **Trail Notes · editable demo** project: three available media thumbnails, the real player, video/audio/text tracks, two transitions and three editable captions |
| [caption-editing.png](caption-editing.png) | The first existing caption selected, with its words, 0–4 second interval and editable canvas overlay visible |
| [community-transition.png](community-transition.png) | The incoming **lake** clip's **Soft Reveal / 柔和揭幕** contribution selected; the existing registry shows its name and effective 0.60-second duration |

The sources are original procedural landscape illustrations and synthesized chords created by [demo.py](../../../skills/cutvoke-editor/scripts/demo.py). They contain no personal material or third-party footage. These original demo assets and screenshots follow the repository's [MIT license](../../../LICENSE). The UI primarily uses Chinese; the same screenshots are used for both README languages.

## Reproduce the demonstration

From the checkout's activated Python environment, with FFmpeg/ffprobe and the built editor available:

```bash
python skills/cutvoke-editor/scripts/demo.py --output .artifacts/readme-demo --effects-dir effects/transitions --effect-id cutvoke.transition.community.soft-reveal
```

Enable the sample's category directory before starting the editor:

```powershell
# Windows PowerShell
$env:CUTVOKE_EFFECTS_PATH = (Resolve-Path effects/transitions).Path
cutvoke serve --data .artifacts/readme-demo/data
```

```bash
# POSIX shell
export CUTVOKE_EFFECTS_PATH="$PWD/effects/transitions"
cutvoke serve --data .artifacts/readme-demo/data
```

Open the service's printed local address, select **Trail Notes · editable demo**, and use the normal timeline divider to show all tracks. Select **字幕** and the first caption for the caption screenshot; select the transition at 4 seconds for the effect screenshot. Do not open advanced path information or include private projects when capturing published evidence. Use a new output directory for another run.

The default demo command without the effect flags uses the built-in dissolve. The script starts only its own temporary loopback upload service, validates that service's selected database, uploads its generated files through the same endpoint as the editor, then performs the edits and export over real stdio MCP. Its temporary upload process is cleaned up; the separately started viewing service remains under the user's control.

## What was checked

The source workflow and example were exercised on Windows 11 with Python 3.12.11, Node.js 22.17.0 and FFmpeg 9.0.1. The output is 12 seconds, 1280×720, 30 fps, H.264 video and AAC audio. The demo checks preflight, command replay without a second editing revision, continuous-preview completion, asynchronous export, project packaging, stream properties, full decode, non-silent unclipped audio and endpoint fades.

The actual rendered review frames at 1, 3.8, 4.2, 7.8, 8.2 and 11 seconds were viewed: titles and captions fit inside the image, the two dissolves visibly blend neighboring shots, and no unintended blank frame appears at those inspected times. Three actual editor screenshots were inspected for readable controls and absence of private paths/tokens. This sampling does not certify every output frame.

Audio measurements were −18.12 dBFS peak and −26.60 dBFS RMS, with lower RMS at the beginning and end than in the body. The sources are simple synthesized chords; subjective listening was **not** performed. These figures do not establish dialogue intelligibility or artistic mix quality.

The built-in dissolve and contribution both map to the same `fade` algorithm; identical output hashes in these runs demonstrate that the manifest configuration uses that existing implementation. They do not prove a new visual algorithm. See [verification.json](verification.json) for the sanitized evidence summary and checks; detailed reproducible logs remain in the local `.artifacts/` directory and are not part of the distributable documentation bundle.

## Known product issue and supported workaround

At the inspected repository revision, **MCP `media_import` and Web media availability use different storage layouts**. This product issue is not fixed by these documentation changes.

- [`AgentTools.media_import`](../../../src/cutvoke/core/agent_tools.py#L128) creates a subdirectory at line 132 and writes `media/imports/<sha256>.<extension>` at line 158.
- [`HttpApi._asset_media_path`](../../../src/cutvoke/core/httpapi.py#L729) recognizes a non-built-in uploaded asset only when its parent is the media root and its filename stem equals `assetId` (lines 738–739).
- The normal Web upload workflow uses [`save_asset`](../../../src/cutvoke/core/httpapi.py#L712), writing `media/<assetId>.<extension>` at line 721.

To reproduce without personal data, run the demo with `--import-mode mcp` in a new directory, then start the editor against that directory. MCP preflight/render/export can succeed, while `GET /api/v1/assets` reports the imported files as unavailable and the media panel shows “丢失”. Source files are still present. The default `--import-mode upload` instead uses the existing `POST /api/v1/assets?name=<filename>` endpoint with `application/octet-stream` bytes, verifies `available:true`, and then uses the returned IDs for MCP editing.

No allowlist was expanded, no ledger/database rows were edited directly, and no files were moved behind the service. The narrow future product fix is to make new MCP imports use the existing canonical upload storage layout, with regression coverage for subsequent Web asset listing/media access. Previously imported assets need an explicit compatibility/relink plan; do not silently move a user's files.

## Boundaries

This is an illustrative short and integration example. It does not verify real-footage selection, speech transcription, subjective sound quality, optional person processing, arbitrary external effect algorithms, multi-hour performance or every AI host. The skill frontmatter/references and its runnable helper were checked, but each AI tool's discovery and local-file/MCP setup still depends on that host.

简体中文说明：三张图均来自真实运行的编辑器；演示采用原创插画与合成音乐。预览、导出、解码和技术音频检查已执行，未声称主观听音。当前 MCP 导入路径与 Web 素材判断仍有差异；默认示例改用既有上传接口，属于受支持流程的绕行方案，不代表产品缺陷已修复。
