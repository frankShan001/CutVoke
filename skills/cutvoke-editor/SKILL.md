---
name: cutvoke-editor
description: Edit local footage, images and audio into a reviewable CutVoke video project through MCP or the local HTTP API. Use for planning an edit, assembling a timeline, refining captions and sound, and exporting with visual checks and a human handoff.
---

# CutVoke editor

Deliver a purposeful short edit, its editable project, and evidence of review. CutVoke executes edits; you supply story, selection, rhythm and judgment. Installing this skill adds guidance, not a model, media generator or guarantee of artistic quality.

## Connect and establish scope

- Read [connection.md](references/connection.md) when connecting or troubleshooting. Start with the connected `editor_help({})` and `runtime` tools; confirm the data directory and target project. The MCP engine and human editor must use the same database to hand off work.
- Gather the intended audience, one takeaway, target duration/aspect ratio, permitted source folders, delivery path, and any required words or brand assets. Infer ordinary choices from supplied context; ask only for facts that change the edit.
- Use `create_project` for a new edit, or `project_clone` for an alternate version. Edit an existing project only within the user's requested scope. Use a new output filename and `overwrite:false`; retain source media and an editable project. Do not silently replace files, rename originals, scan unrelated personal folders, upload media or add a paid/external generation service.

## Build the story from available media

Read [editing.md](references/editing.md) for a creative planning pass and operation mapping.

1. Inventory authorized media with `media_list`, `media_inspect` and actual `preview_frames`. For a browser-visible handoff, follow the existing Web upload workflow in connection.md: current MCP `media_import` files can render but appear unavailable in the Web media library. Do not weaken its allowlist or move files behind the ledger. Note duration, framing, usable action, speech, background sound and licensing. Technical inspection does not establish story meaning, rights, identity or artistic quality. Listen when audio playback is available; disclose when it is not.
2. State a compact edit plan: opening hook, supporting beats, final takeaway; source in/out points, timeline lengths, title/caption copy and sound treatment. Match pace to the audience and material. Make the essential story readable before embellishing it.
3. Discover supported effects with `effects_list` / `resources_list`. Request `editor_help(topic="command", command="…")` for each unfamiliar edit; add `effectId` for its precise schema. Help schemas describe the **payload**, not the outer `command_apply` arguments. Never invent IDs, parameters or a video generation capability.

## Execute with a recoverable project

- Use exact rational seconds for timeline edits: `{"num":"3","den":"2"}` means 1.5 seconds. Keyframe time is clip-local; title/caption positions are normalized. Query the running help for other units.
- Acquire `edit_lock` before grouped Agent edits. For supported commands, `command_preview` checks a proposed edit without rendering or committing. Commit the same payload/revision with `command_apply`, a stable `commandId`, and the acquired `editLeaseId`.
- Use `edit.batch` for related edits that fit its current `allowedCommands` and batch limit. Keep the returned revision for the next operation. Release your lease in a finally step, including after failure; renew it during long editing work and release it before a long export.
- For a local correction, use `project_lookup` and update only the requested entities. Do not rebuild the whole timeline to change a subtitle or swap one shot.
- Read `error.recovery` on failure. A lost response is retried with identical arguments and the same command ID. For a revision conflict, reread and reconcile the affected entities; do not blindly overwrite concurrent work. See [connection.md](references/connection.md) for dependency and job recovery.

## Review, refine and deliver

Read [review.md](references/review.md) before claiming completion.

Inspect opening/final frames, title and caption intervals, and both sides of each cut/transition with `preview_frames` (at most six times per call). Prepare continuous preview with `preview_prepare` / `preview_job`; watch timing and listen to the mix when possible. Fix specific problems and check the new revision.

Run `project.preflight`, then `export.enqueue` with a new absolute output path. Keep the returned job ID; poll `export_job` about once per second with a bounded timeout. Export uses the submitted snapshot. Inspect the final MP4 rather than assuming preview success proves delivery. Return its path, project ID/revision, a concise change summary, and remaining review limits. Use `project_package` when an editable portable handoff is requested.

For a dependency and interface smoke check, run [scripts/demo.py](scripts/demo.py) with a **new** output directory. It uses only original procedural illustrations and synthetic chords, imports through the existing loopback Web upload API, edits over real stdio MCP, and creates an MP4, editable database, `.cvkpkg`, review frames and measured evidence. It starts/stops only its own temporary upload service. It is an example edit, not an automatic creative evaluator or footage selector.
