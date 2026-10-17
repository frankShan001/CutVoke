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

`runtime` and `/api/v1/runtime` report both the loaded and on-disk source
fingerprints, `status` and `restartRequired`. An outdated process rejects schema
discovery and new edits with `RUNTIME_RESTART_REQUIRED` before revision changes.
Reading projects, checking/cancelling jobs and releasing edit leases remain
available. The Web toolbar shows a restart notice. Finish/cancel active jobs,
restart the service and reconnect MCP; live Python modules are not hot-reloaded.

源码更新后，旧进程会明确阻止继续查询旧能力或执行新编辑。先结束或取消任务，
重启服务并重连 MCP，避免 Agent 按旧参数继续操作。
