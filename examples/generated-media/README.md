# Code scenes in an editable CutVoke project

Run from an installed source checkout with Pillow and FFmpeg/ffprobe on PATH:

```bash
python skills/cutvoke-editor/scripts/generated_media_demo.py --output .artifacts/code-bridge
```

Use a **new** output directory. No downloaded media, AI provider, models or user
library are needed. `scene.py` is original MIT-licensed demo code, invoked
explicitly in a separate process by the demonstration script. The CutVoke bridge
does not run it. The three synthetic scenes exercise a kinetic background, an
animated chart and a transparent annotation. Titles and a generated tone remain
native editable text/audio tracks.

The script uses real stdio MCP to import versioned manifests, preview/commit an
edit batch, rerender only the chart with another color, swap its selected clip,
undo/redo, export a nine-second 640×360/24 fps MP4 and round-trip an editable
package. It checks all 216 output frames decode, checks source alpha bytes and
asserts the other scene references and native titles survive. It retains initial
and final preview PNGs, receipts, runtime identities and render timings under
`project/evidence/`. `result.json` explicitly marks continuous AV review, subjective
listening and artistic acceptance unassessed. This is an engineering fixture,
not proof of better artistic output or a comparison against HyperFrames/Remotion.

Deliver `project/exports/editable.cvkpkg`: it contains editable media/text/audio
and verified source ZIPs for referenced outputs. Unpacking retains inert ZIPs;
`generated_source` explicitly restores inputs to a new directory. Source ZIPs contain the manifest and declared inputs;
they exclude rendered media and do not execute or install anything on extraction.
Retain the full project directory for original renders and easy local iteration.

For another renderer, read `editor_help(topic="generated-media")` and write the
same v1 manifest around its output. This is a renderer-neutral material contract;
no native HyperFrames or Remotion runtime/adapter is installed. See the
[bridge guide](../../docs/user/generated-media.md) for the exact supported scope.

## 中文

此示例用原创代码生成三段 3 秒素材，通过真实 MCP 导入，并与原生文字、
音轨组合成可编辑工程。只重做图表并替换指定片段，演示撤销/重做与工程包迁移。
所有输出写入新建的独立示例目录，不碰已有工程。示例代码由脚本显式调用，
CutVoke 服务不会执行清单中的代码。

交付 `.cvkpkg` 即携带已引用素材的源码 ZIP；解包仅保留归档，
`generated_source` 可显式恢复到新目录，不执行源码。自动检查覆盖帧数、解码、源 Alpha 像素和编辑状态，未完成连续
视听或艺术效果验收，也未证明比其他代码视频引擎更快。
# Parameterized cache and local frame repair

From the repository root with CutVoke's development dependencies, Pillow, NumPy,
FFmpeg and ffprobe installed:

```bash
python skills/cutvoke-editor/scripts/generated_render_demo.py --output .artifacts/generated-render-demo
```

Use a fresh output directory. The example explicitly invokes the reviewed
`render_request.py`, which calls only this repository's fixed original
`scene.frame`; it does not execute supplied request source. CutVoke itself never
starts it. The request contains bounded kind/RGB parameters, exact source/runtime
hashes and an absolute frame window. The example uses real stdio MCP, imports
three silent 640×360/24fps scenes, checks warm reuse after deleting external
outputs, previews a covering hit with its source offset, renders only chart
frames 24–47 with a new color, and repairs the middle second in one native
split/trim/swap undo point. Native text and synthetic audio remain editable.

It compares raw RGBA hashes against frames 24–47 of a full revised chart,
exports nine seconds, checks actual before/middle/after pixels, transparent
compositing, non-silent unclipped audio/fades, frame count/full decode and package
roundtrip. `project/evidence/result.json` includes three measured trials for
cold 216-frame rendering, three warm cache plans, 24-frame repair and a full
72-frame chart. Render times include Python/FFmpeg startup and proof hashing;
registration and warm plan/hash costs are recorded separately. Timing results
are host-specific, not a cross-machine performance promise or artistic review.

Keep `project/` for the cache index. `.cvkpkg` includes referenced source ZIPs
but excludes cache metadata; use `generated_source` for explicit input recovery.
Continuous playback, subjective listening, hostile-code isolation and
cross-machine reproducibility are unassessed. See the [request/cache contract](../../docs/user/generated-render.md).
