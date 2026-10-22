# Production workflow / 制作工作流

The CHASE THE LIGHT showcase exposed gaps in ordinary production. These fixes
use the same native EditService and renderer as the Web editor and MCP.

## Titles without dummy media / 无需垫底素材的标题卡

A visible text track is renderable content. A project containing only text clips
can be previewed, played, exported and packaged. Titles after the last footage
clip extend the canvas duration. Hidden text and positions outside a clip retain
the empty-frame contract. Transparent title export retains glyph alpha.

纯文字轨可直接预览、播放、导出，不再需要添加黑底图片。文字超出视频末尾时，
画布持续到文字结束。隐藏文字与无片段覆盖的时间点仍按空画面处理。

## Reuse layers / 复用图层

`clip.duplicateGroup` accepts `clipIds` (1–256 IDs across tracks), optional
`timelineStart` (rational seconds), and `includeAttached` (default true).
It deep-copies effects, keyframes, speed and nested content, preserves relative
timing, generates unique IDs and rebinds copied children to copied parents.
Attachments to objects outside the selection are detached. Existing overlap
and track lock rules apply; the whole copy is one undoable edit.

Right-click a clip → **复制片段及绑定图层 / Duplicate clip and attached layers**.
For independent layers, pass their explicit IDs through the shared command API.
This keeps every layer manually editable; it does not flatten a shot.

该命令支持跨轨整组复制，并默认递归带上绑定图层。内部绑定指向新片段，外部绑定
解除；效果、关键帧、变速和相对时序保留。整个操作可一次撤销或重做。

## Responsive preview / 预览反馈

Bounded previews prepare optical and static mask sources only for contributing
clips, retaining outgoing transition handles. Editing authority and effect clocks
remain unchanged. The opening screen reports the active window and elapsed time;
**先进入编辑，按需预览 / Edit now, preview on demand** cancels full preparation
and opens the existing segmented-preview workflow. It does not claim that the
whole timeline is already ready for uninterrupted playback.

Appending unrelated future clips preserves the current window's cache identity,
including animated shots that require full-clock compilation.

Video, images and stickers can be selected directly in the preview. Their native
layers update during transform gestures; releasing the pointer saves one undoable
edit. Captions and effect inputs preview valid drafts while autosaving; Chinese
IME composition waits for completion before saving. Volume and fades update a
per-clip Web Audio mix during playback. Failed saves retain the draft and offer
Retry; undo, export and project switching flush pending edits first.

Complex visual effects use cancellable, latest-request-only frames from the same
renderer as export. Geometry reuses the processed transparent layer. Transitions
and nested compositions render the complete scene so neighboring layers remain
correct. Editing a complex visual effect pauses playback at the current frame;
its calculation still has a measurable cost. Audio effects use isolated sound
previews and do not block the visual-frame queue. Independent pitch and pitch
preservation changes refresh those previews. Projects with sidechain ducking
retain the complete server mix so the other clips remain available as key inputs.

视频、图片、贴纸可在画面中点击选中；拖动、缩放时实际图层同步变化，松手自动保存，
一次手势可一步撤销。字幕支持中文输入法，文字和样式自动保存；音量与淡入淡出可边播
边调。保存失败保留草稿并提供重试；撤销、导出和切换工程前先保存待提交修改。
复杂视觉效果复用导出渲染器，只计算最新请求；调整时暂停在当前帧，计算本身仍有耗时。

## Production archive / 制作归档

Open **Agent activity → Production archive**, or call `project_archive` with
`projectId`, `expectedRevision`, optional `outPath`, `overwrite` and `frameTimes`
(up to six seconds-based positions). Without a path it writes to the data
directory's `archives/` folder. Archiving does not change revision.

The `.cvkpkg` remains a normal editable project package. Its `production/` folder
adds actor events, retained undo/redo snapshots, export metadata, optional native
preview frames, evidence SHA-256 hashes and a runtime identity. The database
snapshot is transactional; rendering/copying releases its lock first. Output is
published atomically after ZIP integrity verification.

制作归档包含当前工程与引用素材、操作者事件、仍保留的历史快照、导出记录和可选
抽帧。历史快照可能引用旧的本机路径，旧版媒体不会自动补存；导出记录不等于成片
文件。抽帧不是剪辑过程录屏，也不能恢复已经清除的历史。

## Runtime changes / 服务版本变化

The standard `cutvoke mcp` / `cutvoke-mcp` entry points keep the stdio connection
in a supervisor. After a stable source/build change, a fresh Python engine is
started and checked before receiving new requests. Existing exports and prepared
previews finish in their original worker; status and cancellation still reach that
worker. Finished preview metadata transfers with its original revision/cache
identity. Projects, idempotent command results, undo history and leases stay in
the same SQLite library. Python modules are never hot-reloaded.

`runtime.autoReload` reports the generation, pending/current state, supervisor PID
and draining workers. A partial or broken installation leaves the connection up
and retries automatically. New writes/schema discovery return retryable
`AUTO_UPDATE_PENDING` until a healthy engine is ready; they are not applied by
the stale engine. A lost write response is never automatically replayed: read
the revision and reuse the original command ID. At most two older generations
drain concurrently; more rapid updates coalesce until a slot is available.

Tool/resource list-change notifications follow the
[MCP tools](https://modelcontextprotocol.io/specification/2024-11-05/server/tools)
and [resources](https://modelcontextprotocol.io/specification/2024-11-05/server/resources)
protocol. Whether newly added tool names appear automatically depends on client
support; existing discovery/editing tools use the new engine without reconnecting.

已经运行的旧 MCP 首次安装此机制后需重连一次，此后源码或 Web 构建更新会自动
切换引擎；配置和数据目录不变。运行中任务继续执行，取消后的实际清理线程结束
之前也不会退役。连接主动关闭时按正常退出流程清理任务。此机制不下载更新，
不迁移不兼容的数据库结构，也不替换正在运行的启动器或安装包可执行文件。

Directly embedded `MCPServer` instances and the Web HTTP service still report
loaded/on-disk fingerprints through `runtime` / `/api/v1/runtime` and reject
stale writes with `RUNTIME_RESTART_REQUIRED`. The Web service needs its own restart;
the MCP supervisor does not stop or restart unrelated services.
