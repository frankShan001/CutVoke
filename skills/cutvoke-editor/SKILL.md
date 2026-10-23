---
name: cutvoke-editor
description: Edit local footage, images and audio into a reviewable CutVoke project through MCP or the local HTTP API. Use for planning, timeline edits, captions, sound and verified export, keeping generated files in the project folder and collecting reusable effects locally at completion.
---

# CutVoke editor

Deliver a purposeful short edit, its editable project, and evidence of review. CutVoke executes edits; you supply story, selection, rhythm and judgment. Installing this skill adds guidance, not a model, media generator or guarantee of artistic quality.

## Connect and establish scope

- Read [connection.md](references/connection.md) when connecting or troubleshooting. Start with the connected `editor_help({})` and `runtime` tools; confirm the data directory and target project. The MCP engine and human editor must use the same database to hand off work.
- Gather the intended audience, one takeaway, target duration/aspect ratio, permitted source folders, delivery path, and any required words or brand assets. Infer ordinary choices from supplied context; ask only for facts that change the edit.
- Use `create_project` for a new edit, or `project_clone` for an alternate version. Edit an existing project only within the user's requested scope. Use a new output filename and `overwrite:false`; retain source media and an editable project. Do not silently replace files, rename originals, scan unrelated personal folders, upload media or add a paid/external generation service.

## Bind every output to the project

Before generating files or editing the timeline, read [workspace.md](references/workspace.md), bind a stable absolute `projectRoot` to the target project ID and database, and record it in `project-workspace.json` inside that folder. Reuse that binding on subsequent edits. A cloned/new project gets its own folder. A shared media folder or the application's data directory is not automatically a project folder.

All Agent-created files belong under `projectRoot`: plans, prompts, scripts, generated/processed media, subtitles, previews, extracted frames, temporary files, logs, validation evidence, exports and editable packages. Set absolute output paths explicitly wherever supported, and direct child-process working/temp directories there. Do not use Desktop, Downloads, the skill folder or a global temp folder as a convenient output location. Keep the primary artifact in the project even when the user requests an additional delivery copy elsewhere.

CutVoke owns its configured database, imported-media library and internal caches; do not move them or rewrite their ledger. When a tool only produces an engine-managed artifact, retain a project-local review copy when accessible and record its returned ID/path. The other routine destination is the **content-free reusable-effect collection** under the verified CutVoke installation, described below. These instructions constrain Agent actions; they do not create an operating-system sandbox.

## Build the story from available media

Read [editing.md](references/editing.md) for a creative planning pass and operation mapping.

1. Inventory authorized media with `media_list`, `media_inspect` and actual `preview_frames`. Use `media_import` for the persistent library shared with the browser. Check `available:true` and keep MCP and Web on the same database; exact legacy imports remain usable without moving files behind the ledger. Note duration, framing, usable action, speech, background sound and licensing. Technical inspection does not establish story meaning, rights, identity or artistic quality. Listen when audio playback is available; disclose when it is not.
2. State a compact edit plan: opening hook, supporting beats, final takeaway; source in/out points, timeline lengths, title/caption copy and sound treatment. Match pace to the audience and material. Make the essential story readable before embellishing it.
3. Discover supported effects with `effects_list` / `resources_list`. Request `editor_help(topic="command", command="…")` for each unfamiliar edit; add `effectId` for its precise schema. Help schemas describe the **payload**, not the outer `command_apply` arguments. Never invent IDs, parameters or a video generation capability.

## Execute with a recoverable project

- Use exact rational seconds for timeline edits: `{"num":"3","den":"2"}` means 1.5 seconds. Keyframe time is clip-local; title/caption positions are normalized. Query the running help for other units.
- Acquire `edit_lock` before grouped Agent edits. For supported commands, `command_preview` checks a proposed edit without rendering or committing. Commit the same payload/revision with `command_apply`, a stable `commandId`, and the acquired `editLeaseId`.
- Use `edit.batch` for related edits that fit its current `allowedCommands` and batch limit. Keep the returned revision for the next operation. Release your lease in a finally step, including after failure; renew it during long editing work and release it before a long export.
- For a local correction, use `project_lookup` and update only the requested entities. Do not rebuild the whole timeline to change a subtitle or swap one shot.
- Preserve the user's current editing experience. Keep normal undo points and editable titles/effects; release the edit lease for human takeover. Do not restart the app, change GPU/cache/quality preferences, replace source media or flatten the timeline merely to make an edit easier. Do not lower export resolution, frame rate or audio quality for speed unless requested. Run heavy analysis/render jobs with bounded concurrency, avoid duplicate jobs and defer background work if it disrupts active playback or interaction.
- Read `error.recovery` on failure. A lost response is retried with identical arguments and the same command ID. For a revision conflict, reread and reconcile the affected entities; do not blindly overwrite concurrent work. See [connection.md](references/connection.md) for dependency and job recovery.

## Review, refine and deliver

Read [review.md](references/review.md) before claiming completion.

Inspect opening/final frames, title and caption intervals, and both sides of each cut/transition with `preview_frames` (at most six times per call). Prepare continuous preview with `preview_prepare` / `preview_job`; watch timing and listen to the mix when possible. Fix specific problems and check the new revision.

Run `project.preflight`, then `export.enqueue` with a new absolute output path under `projectRoot/exports/`. Keep the returned job ID; poll `export_job` about once per second with a bounded timeout. Export uses the submitted snapshot. Inspect the final MP4 rather than assuming preview success proves delivery. Use an explicit project-local output path for `project_package` when an editable portable handoff is requested.

## Finish by collecting reusable effects

After the intended edit and its review are finished, **automatically review the effects actually used** and follow [effect-library.md](references/effect-library.md). Save worthwhile, verified, content-free recipes with `effect_module_save`; export each with `effect_module_export` to `projectRoot/effects/`, then copy the unchanged ZIP to `<verified CutVoke installRoot>/user-effects/`. Use content IDs to reuse identical entries. This local collection step needs no separate sharing request; it does not authorize publication or expanding the edit.

The registered, immediately reusable library remains beside the current database in `effect-modules/`; the install-directory ZIP collection is an additional archive. Do not claim that copying a ZIP registers it. Never include user footage, audio, text/caption content, screenshots, paths, prompts or project files in an effect package. Do not force unsupported treatments into a recipe, save every ordinary adjustment, modify an approved timeline to demonstrate reuse, or publish automatically. If nothing qualifies, say so. If installation discovery, permission or validation fails, retain the project-local result and report collection as pending instead of silently changing the destination.

Return the primary output path, project ID and exact exported revision, `projectRoot`, a concise change summary, review limits and collection status (module IDs and verified archive paths, no qualifying effects, or a concrete pending reason). When delivering or suggesting an effect package for sharing, include the tool's `privacyNotice` in the user's language verbatim. Preserve all source files and the editable project; clean up only known Agent-created temporary files within this project when cleanup is requested.

For a dependency and interface smoke check, run [scripts/demo.py](scripts/demo.py) with a **new** output directory. It uses only original procedural illustrations and synthetic chords, imports and edits over real stdio MCP, and creates an MP4, editable database, `.cvkpkg`, review frames and measured evidence. It starts/stops only its own temporary Web service to verify browser asset availability; `--import-mode upload` separately exercises HTTP upload. It is an example edit, not an automatic creative evaluator or footage selector.
