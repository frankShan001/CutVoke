# External code-video material bridge / 代码视频素材桥接

Use `generated_media` over MCP or `POST /api/v1/agent/generated_media` to inspect
and import a rendered code scene with declared source provenance. Ordinary cuts,
timing, subtitles, text, sound and undo remain in CutVoke. Bake complex animation
into a scene; keep simple elements native when they need frequent editing.

```json
{"projectId":"my-project","operation":"inspect","manifestPath":"/absolute/scene/manifest.json"}
```

`inspect` snapshots declared files into temporary storage, checks content hashes,
decodes/counts video frames and verifies zero-based CFR timestamps and color
semantics. It does not retain assets or edit the timeline. Change `operation` to
`import` to retain an ordinary scoped `assetId`, a `bundleId`, a `renderKey` and a
separate inert `sourceArchive`. All temporary copies are cleaned on exit.

Read `editor_help(topic="generated-media")` for an on-demand manifest example,
limits and current boundaries without repository access. A complete original
[runnable example](../../examples/generated-media/README.md) is included.

## Manifest v1

```json
{
  "schemaVersion": 1,
  "renderer": {"name":"your-renderer","version":"exact-version","build":"runtime-and-renderer-build"},
  "entrypoint": "scene.py",
  "files": [{"path":"scene.py","sha256":"REPLACE_WITH_64_LOWERCASE_HEX_DIGITS","role":"source"}],
  "params": {"accent":"teal"},
  "seed": 0,
  "output": {
    "path":"render.mp4","sha256":"REPLACE_WITH_64_LOWERCASE_HEX_DIGITS",
    "width":640,"height":360,"fps":{"num":24,"den":1},"durationFrames":72,
    "alphaMode":"opaque","colorSpace":"bt709","audioMode":"none"
  }
}
```

All shown fields are required; unknown fields and duplicate JSON keys are
rejected. File paths are normalized relative POSIX paths beneath the manifest
directory; absolute paths, `..`, backslashes and symlinks are rejected. Declare
all source, lockfile, asset, font and license files using those exact `role`
values. `entrypoint` must name a declared source. Output must be separate from
inputs. Parameters must be a JSON object without NaN/Infinity; seed is a uint32.
The caller declares dependency completeness and runtime identity; CutVoke does
not discover undeclared reads, validate licenses, install or execute dependencies.

Limits: 256 KiB manifest, 1–128 input files totaling 32 MiB, 512 MiB render,
16–4096 pixels per dimension, 1–120 fps, 1–18000 frames, at most ten minutes.
Inspection times out after 30 seconds and fails without importing media. Use
shorter independent scenes when these bounded synchronous checks are exceeded.

V1 accepts one video stream in MP4/MOV, square pixels, time starting at zero and
explicit BT.709 primaries/transfer/matrix in decoded frames (stream metadata is
used when frame fields are absent). No embedded audio, VFR, HDR or image sequence
is supported. Keep audio on native tracks. `straight` Alpha requires ProRes 4444
MOV with an alpha pixel format; premultiplied Alpha and WebM are not accepted.
Codec metadata cannot prove Alpha pixels or interpretation: inspect transparent
edges over multiple backgrounds in the actual CutVoke output. With some FFmpeg
builds ProRes needs explicit `prores_metadata` bitstream color tags; encoder
flags alone may not retain primaries/transfer. The example writes these tags.

## Replace a scene without flattening the edit

1. Import the newly rendered manifest to the intended `projectId`.
2. Discover `asset.swap` with `editor_help(topic="command", command="asset.swap")`.
3. Acquire an editing lease; preview a payload containing explicit `clipIds` and
   the returned `assetId` at the current revision.
4. Apply that same payload with `expectedRevision`, a stable `commandId` and the
   lease. Existing source-range checks reject a replacement that is too short.
5. Inspect affected frames and continuous output/sound, including neighboring
   cuts. Release the lease for human editing. Undo restores the previous asset.

The generated-media tool changes the media library, never the timeline. Library
imports and source archives are not an undoable edit transaction; a storage error
after media import can leave an unused asset. Retry is content-addressed and does
not create another timeline revision. A corrupt existing source archive or media
file is reported rather than silently reused or overwritten.

`bundleId` hashes the canonical manifest including output hash. `renderKey`
hashes the declared source/runtime/parameters/seed and video contract excluding
the output path/hash. A changed parameter/input/runtime/spec changes that key.
Neither key is a claim of deterministic execution. Reimporting identical bytes
deduplicates the media and archive; there is no code frame cache, compilation
cache, automatic rerender, asynchronous code runner or native parameter panel.

## Source and editable handoff

`sourceArchive` is saved under the selected media directory's `.generated/` with
a content-addressed name. It contains `manifest.json` and only the explicitly
declared input files at their original relative paths. Undeclared siblings,
rendered video and dependency directories are not included. Archives are local;
they can contain private source/data and are not content-free effect recipes.
No upload is performed.

Keep the source ZIP(s) separately alongside `project_package`: the native
`.cvkpkg` retains timeline media and editable native objects, **not** the source
ZIPs. There is no automatic source archive reimport or bidirectional source/UI
synchronization. To rerender, inspect the source and dependencies, use your
trusted renderer environment, write a new manifest/output pair and import it.

## 中文

首版提供可核验的“外部代码渲染 → 静态清单校验 → 普通素材导入 → 指定片段替换”
链路，复用 CutVoke 的版本、租约、预演、撤销和导出。复杂动效烘焙为独立素材，
文字、字幕和音频可以继续原生编辑；素材内部图层不会自动转成原生对象。

`inspect` 只产生临时副本并验证哈希、帧数、帧时钟和色彩；`import` 保留素材和
独立源码 ZIP，不改时间线。不执行代码、不安装依赖、不上传素材。源码与依赖
清单由调用方声明，哈希验证不等于安全、可复现或创意质量认证。

透明素材仅支持 straight Alpha 的 ProRes 4444 MOV，必须另查实际合成像素。
交付时保留 `.cvkpkg` 与源码 ZIP 两类文件；首版尚无源码包自动迁移、任意代码
隔离运行时、编译/帧缓存、自动重渲或 GUI 参数编辑。性能和视听效果应使用
真实作品另行比较，不能由一次导入或导出成功推断。
