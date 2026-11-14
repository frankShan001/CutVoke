# Talking-head and interview prompts

[简体中文](prompts-talking-head.zh-CN.md) · [Connect an agent](agent-skill.md) · [Interview editing principles](../../skills/cutvoke-editor/references/workflows/explainer.md)

Copy one request at a time into your connected Codex, Claude Code or Cursor agent. Replace bracketed placeholders with your own local project, file and intended result. These are editing briefs, not commands that CutVoke interprets as natural language. The agent must discover the running version's tools and make the edits.

## Before the first edit

- Connect to the **same data directory** as the human editor. Ask for `editor_help({})`, `runtime`, then `editor_help({"topic":"creative","guide":"explainer"})` when planning an interview edit.
- Identify the project and source explicitly. Use `project_list` / `project_summary`; use `project_query` for caption text and exact timing. Keep generated files in the project's recorded directory unless you explicitly choose another delivery destination.
- Before an unfamiliar command, fetch its payload schema with `editor_help` (`topic: command`). Check `commands_list` for `previewSupported`: `command_preview` is a structural dry run, not rendered video, and is not available for every command.
- Group writes under an `edit_lock`, use the current `expectedRevision` and a stable `commandId`, and release the lease for human handoff. Refresh and reconcile revision conflicts; do not silently apply a stale plan.
- Use only media you may edit. An agent must ask before installing optional models or sending media to another service. These examples do not require cloud video generation.

## Ten requests to try

### 1. Inspect before editing

> Inspect [source file] for a new interview edit. Tell me its duration, dimensions and audio availability, and show representative frames. Do not cut anything yet.

**Agent:** `media_import` → `media_inspect` → `preview_frames` on the asset; importing does not put a clip on the timeline. **Check:** correct file, rotation, sound track and framing. Frames cannot establish intelligibility; listen to the source too.

### 2. Shorten long pauses, keep breathing room

> In [project], shorten pauses longer than 0.8 seconds. Keep about 0.15 seconds at each side of a cut and leave short pauses alone. Show me the proposed intervals first. Preserve the speaker's meaning and review every changed boundary at normal speed.

**Agent:** use `caption.autoSegment` with `minSilence: 0.8` and `applyToProject: false` as a source-time candidate detector, then preview/apply approved `sequence.deleteRange` intervals, latest first. **Check:** breaths, clipped consonants, deliberate hesitation and sound/picture sync.

`minSilence` is a minimum detection duration, not a “keep breathing” setting. The detector returns placeholder segments, not recognized speech, and does not delete video. Its current threshold is fixed at −30 dBFS. Map source times to the selected clip's timeline before deleting; trimmed, moved, repeated, reversed or speed-changed clips need explicit mapping or a separately rendered analysis copy. Do not reuse pre-edit timestamps after a cut. `audio.splitSentences` writes separate audio files; it does not ripple-edit the video timeline. A sequence deletion affects all tracks, captions and markers, so inspect the scope first.

### 3. Remove a sentence or selected words

> Remove the sentence “[exact sentence]” from [project]'s transcript and its matching audio/video. Show its caption ID, time interval and surrounding words first. If it occurs more than once, ask which occurrence I mean.

**Agent:** find the reviewed, committed caption, then preview/apply `transcript.delete` with `captionIds`; for selected words use one `captionId` plus `wordIndices` only when valid word timings exist. **Check:** exact occurrence, surrounding meaning, cut boundaries and all affected tracks. This removes media time, unlike deleting or correcting subtitle text. A summary is not a transcript; do not invent word timestamps.

### 4. Transcribe locally, review before adding

> Make a local caption draft for [clip] in [language]. Use an already installed suitable speech model. If dependencies or a model download are needed, tell me first. Let me review the text before adding it to the timeline.

**Agent:** inspect local capabilities; `caption.transcribe` returns a proposal, then `caption.bulkAdd` commits reviewed segments with the returned source binding. **Check:** names, technical terms, accents, overlap and timing. Re-transcribe if the source or clip timing changed. See [local ASR](LOCAL_ASR.md) for installation and offline use. `caption.autoSegment` is not a substitute for transcription.

### 5. Correct a caption without cutting speech

> In the caption at [time], change “[wrong spelling]” to “[correct spelling]”. Keep the spoken audio, video and caption timing unchanged. Show the corrected frame.

**Agent:** find the exact caption, preview/apply `caption.update`, then `preview_frames` at that caption. **Check:** spelling, line breaks and unchanged media timing. This is a text correction, not `transcript.delete`; do not manufacture replacement word timings when the text changes.

### 6. Make a portrait version

> Keep the original landscape edit. Create a separate 9:16 version with the speaker centred, and show the beginning, middle and end before exporting. If a crop would lose another speaker or useful gestures, show me the trade-off.

**Agent:** `sequence.duplicate` with width/height such as 1080×1920 and `reframe: cover`; inspect frames and adjust supported transforms/keyframes if needed. **Check:** headroom, faces, hands, subtitles and the intact original sequence. `cover` is a geometric centre crop, not face detection or automatic speaker tracking. For two speakers, consider `contain` or a deliberately composed layout instead of claiming both fit.

### 7. Show four frames around the biggest cuts

> Before exporting, show four frames: one just before and one just after each of the two largest removals. Label their current timeline times. Then review those boundaries in motion with audio.

**Agent:** map the approved edits to current times and call `preview_frames` with four valid times and `expectedRevision`; use `preview_prepare` / `preview_job` when a continuous preview is needed. **Check:** jumps, black frames, caption placement and sync. Four stills do not validate speech pacing or audio. A call supports at most six frames; every requested time must be before the sequence end.

### 8. Export without overwriting

> Export the reviewed active sequence as [new filename].mp4 in this project's exports folder. Preserve the source and every existing file, and tell me the final path, dimensions and duration. If the name is taken, ask for another name.

**Agent:** run `project.preflight`, then `command_apply` with `export.enqueue`, an explicit absolute `outPath` and `overwrite: false`; poll `export_job` at a reasonable interval until terminal status. **Check:** only `succeeded` is success. Verify the actual output path, resolution, full-file decode, opening/ending frames and audio. A queued job is not a completed export. Directory-backed projects require engine outputs inside their own `exports/` or `backups/`; inspect `project_directory` before choosing the absolute path. A request to export directly beside an external source is not supported by that engine route. Report the constraint rather than retrying the rejected path, moving the source or bypassing the restriction. If a separate delivery copy is wanted, agree on an authorized destination and available file-transfer method first. Do not upload or publish the file.

### 9. Undo only the last edit

> Undo the last change in [project]. Tell me what was restored and show the affected interval. Do not reset the project or delete exported files.

**Agent:** inspect `project_events` / the current state, then apply `history.undo` if the latest change is the intended one. **Check:** media source offsets, captions and duration return as expected. If somebody edited afterward, stop and clarify instead of undoing their change. Undo is not filesystem cleanup or export cancellation.

### 10. Hand back an editable project

> Stop editing and hand [project] back to me. Release your editing lock, summarize the cuts and unresolved caption questions, and save a new editable package in the project directory without overwriting anything.

**Agent:** `project_summary` / `project_events`, release `edit_lock`, then `project_package` with current `expectedRevision`, explicit `outPath` inside the project's `exports/` and `overwrite: false`. **Check:** the editor is writable, the correct sequence is active, the package exists and source media remains available. Packaging does not authorize sharing; keep the package local unless asked to send it.

## What was actually exercised

Codex selected and ran the operations behind requests **1, 2, 3, 5, 6, 7, 8 and 9** through a real CutVoke stdio MCP subprocess, using the reusable verifier below. This checks the documented tool paths, not natural-language prompt reliability across different agents or their setup UIs. Request 3 used the word-selection variant with explicitly authored fixture text, not recognized speech. Request 1's representative frames were checked later in request 7. Requests 4 (ASR) and 10 (package handoff) were not exercised by this verifier.

The recorded run used Python 3.12.14 and FFmpeg 7.1.5 on Linux, against CutVoke 0.3.5 source at `e45af6d` plus this documentation/verifier change.

The fixture is a ten-second 640×360 video of original color cards with a central marker and synthesized tones. It includes two 1.5-second silences and one 0.2-second silence. No private media, real names, private paths, model download or external service is involved.

- Silence analysis found both long gaps. Approved deletions retained 0.15 seconds on each side, producing a 7.6-second timeline; decoded output audio retained the short pause.
- Deleting one authored word shortened media by 0.5 seconds; undo restored the exact sequence, source offsets and caption word data. A text-only caption correction left media tracks unchanged.
- A separate 360×640 `cover` version preserved the original 640×360 sequence. Four actual cut-adjacent PNGs retained the central marker.
- The portrait export fully decoded with audio at 7.6 seconds. A second export to the same filename was rejected; source and output hashes stayed unchanged.

Run from the checkout with CutVoke's runtime dependencies and FFmpeg/ffprobe available:

```bash
python tools/verify/verify_talking_head_prompts.py --output .artifacts/talking-head-check
python tools/verify/check_asset_layout.py
```

Use a **new output directory** each time. The verifier saves its isolated editable library, synthetic source, preview PNGs, MP4, `calls.json`, `mcp.log` and `result.json` there. Inspect the PNGs and play the output before judging visual or listening quality. Successful synthetic timing/pixel checks do **not** establish natural-speech accuracy, natural breathing, semantic faithfulness, face tracking, full client compatibility or artistic quality.
