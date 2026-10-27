# Give your AI an editing workflow

[中文](agent-skill.zh-CN.md) · [Skill entry point](../../skills/cutvoke-editor/SKILL.md)

The **cutvoke-editor** skill teaches an external AI how to plan an edit, inspect local media, build a recoverable timeline, refine captions and sound, and check the final result. CutVoke also bundles its creative references through `editor_help`; the optional Skill supplies the entry workflow and local working conventions. It does not install a model or guarantee a beautiful result regardless of the source material.

## Three layers, loaded on demand

Use shared principles, the relevant purpose/work-type guide, then a plan for this project. Five branches cover music-video/art visual, documentary/travel, narrative, explanation/interview and promotion/showcase. Genre is a secondary influence; the user's requested expressive intensity determines the approach. A local correction reuses the approved direction and checks affected intervals, without a mandatory new treatment or full export.

MCP-only clients first call `editor_help({"topic":"creative"})`, then load one document, for example `editor_help({"topic":"creative","guide":"music-video"})`. IDs also include `principles`, `project-plan`, `review` and optional `efficiency`. The same documents are available as `cutvoke://creative/<id>` resources. The index contains metadata, not all document bodies; no repository access or Skill installation is required. Build steps copy canonical Skill references verbatim into wheels, desktop and worker packages.

For substantial expressive work, design observable changes and compare a short dynamic passage with a simple baseline before applying it widely. Report technical checks and visual/listening review separately. See the [research and adoption record](../research/AI_EDITING_WORKFLOWS_2026-10-28.md) for sources and limits.

## Copy or install

The self-contained folder is `skills/cutvoke-editor/`: `SKILL.md`, progressively loaded references, and a runnable `scripts/demo.py`. Copy the **whole folder**, so its relative references remain available.

For Codex's personal skill directory, from the checkout:

```powershell
# Windows PowerShell
$skillTarget = Join-Path $env:USERPROFILE '.codex/skills/cutvoke-editor'
if (Test-Path -LiteralPath $skillTarget) { throw 'Skill already exists; review it before replacing it.' }
New-Item -ItemType Directory -Path (Split-Path $skillTarget) -Force | Out-Null
Copy-Item -LiteralPath skills/cutvoke-editor -Destination $skillTarget -Recurse
```

```bash
# POSIX shell
mkdir -p "$HOME/.codex/skills"
test ! -e "$HOME/.codex/skills/cutvoke-editor" && cp -R skills/cutvoke-editor "$HOME/.codex/skills/"
```

If `CODEX_HOME` is customized, use its `skills/` directory instead. Reload the host's skill discovery as required, then invoke `$cutvoke-editor`. The installation convention comes from the local Codex skill workflow; the actual validation here covers the skill files and CutVoke's stdio MCP, not every AI host's installation UI.

For another tool that can read Markdown instructions, point it at `skills/cutvoke-editor/SKILL.md` and let it follow the linked references on demand. Configure its supported local MCP/HTTP interface using that tool's documentation. This is a portable instruction package, not a verified universal installer or a promise that every remote client can access local files.

## Connect and try

Configure a local MCP process as described in [external-agent-editing.md](external-agent-editing.md). Use an installed `cutvoke` executable, the checkout's activated environment, or the packaged Windows engine. Select the same `--data` directory for MCP and the editor. Start with `editor_help({})` and `runtime`.

Example request after connecting:

This calm example is one possible brief, not the default style for other work.

> Use $cutvoke-editor to make a calm 12-second, 16:9 montage from the local media folder I selected. The audience is friends planning a quiet weekend. Keep one short title, readable captions, gentle motion and restrained transitions. Use only these files; keep intermediate files and the new MP4 inside this project's directory, review the actual frames and audio, and leave the timeline editable. When finished, extract verified, reusable effects without user content and archive them in the CutVoke installation directory. If music or media rights are unclear, flag that before adding material.

Supply the media folder/project, intended audience and takeaway, duration/aspect ratio, required copy/brand assets and destination. A thoughtful edit also depends on accessible media and the AI's ability to view/listen; the engine does not decide artistic quality automatically.

## Project files and reusable effects

Before generating files, the skill requires the AI to bind the target `projectId` to a stable, absolute `projectRoot` and record that association for later sessions. Reuse an existing project's recorded directory; a directory specified by the user takes precedence. For a new project without a directory, create `projects/<projectId>/` under the user's chosen parent directory or the current session workspace. Keep AI-generated plans, scripts, logs, temporary files, frames, audio, review evidence, project packages and final exports inside that project directory, explicitly supplying `outPath` wherever supported. An explicitly requested delivery destination can receive a copy while the project retains the main output. The engine continues to manage its database, imported media storage and caches; do not relocate them to satisfy artifact filing rules. See [workspace.md](../../skills/cutvoke-editor/references/workspace.md) for directory details.

After editing, the AI reviews effects actually used and verified, selecting reusable recipes from supported `effects`, `motion` and `textStyle` settings. Register qualifying recipes with `effect_module_save` in the active data directory's `effect-modules` library, deduplicated by content. Then use `effect_module_export` to write `effects/<moduleId>.zip` inside the project and archive a copy at `user-effects/<moduleId>.zip` under the **verified CutVoke installation directory**. That installation folder is a local collection; the usable module library remains in the data directory. If the destination is unverified or unwritable, retain the project package and report the reason without elevating privileges, silently choosing another location, or overwriting a same-name file with different contents. Report when no qualifying candidate exists. See [effect-library.md](../../skills/cutvoke-editor/references/effect-library.md) for selection and filing rules.

Effect packages contain supported parameter recipes without user media, copy or caption text, images, paths, code or project content; they are not published automatically. The skill also requires a manual handoff, targeted edits, and checks of real previews, final exports and audio. Uploading, publishing or sending content to others requires existing explicit authorization. These are instructions for the AI; filesystem isolation still depends on the host's permissions.

## A runnable integration example

With Python 3.10+, CutVoke and FFmpeg/ffprobe installed, run from the checkout's activated environment:

```bash
python skills/cutvoke-editor/scripts/demo.py --output .artifacts/skill-demo
cutvoke serve --data .artifacts/skill-demo/data
```

Open `http://127.0.0.1:8787` and select **Trail Notes · editable demo**. Run the server on a free port if another service is already using 8787. The script requires a **new** output directory and creates its own original illustrations and synthesized chords. It does not read the default user library, download models or call an external service.

Outputs include `trail-notes.mp4`, `trail-notes.cvkpkg`, `data/projects.sqlite`, managed media, six real preview frames, `result.json`, `calls.json`, and a diagnostic MCP log. Keep the directory for an editable handoff. The default uses a built-in dissolve; [the effects contribution guide](../../effects/README.md) shows the same demo with the local community manifest.

The script imports and edits media over real stdio MCP, reads runtime schemas, preflights, prepares continuous preview, exports H.264/AAC, checks dimensions/frame rate/duration and full decode, and measures audio peaks and endpoint fades. It starts/stops only its own temporary Web service to verify browser asset availability, and checks that command replay does not create another editing revision. See [the demonstration and validation boundaries](../images/readme/README.md). `--import-mode upload` separately exercises the loopback Web upload API.

The demonstration validates integration with original illustrative media. It does not establish speech transcription quality, automatic shot selection, subjective sound quality, arbitrary effects, every AI client's behavior, or large-project performance. Final artistic judgment stays with the creator and reviewing AI.
