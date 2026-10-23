# Project folders and output boundaries

## Establish the folder once

CutVoke identifies projects in a shared database; a timeline does not necessarily have a filesystem folder. Bind one explicitly before producing task files:

1. Confirm the target `projectId` and `runtime.dataPath` (the database file; `dataDirectory` is its containing directory). If a host omits that field, inspect the current runtime/help or connection configuration to establish the exact database; do not infer identity from a project name or directory alone. Use the user's explicit project folder, or the existing `project-workspace.json` binding in the known task workspace. Verify both project ID and database before resuming. Do not search unrelated personal folders for a manifest.
2. When no binding exists, create `projects/<projectId>/` under the user's chosen parent folder or this session's writable workspace. Use the actual returned ID, validate it as a single filename component, and choose a separate new folder for a clone. If there is no accessible local workspace, ask for one before writing files. Do not guess that a source-media folder, app data folder or installation folder is the project folder.
3. Resolve `projectRoot` to an absolute path on the engine's machine and record `schemaVersion: 1`, `projectId`, `dataPath`, `projectRoot` and the verified `installRoot` (or `null` when unknown) in `project-workspace.json`. A record with a different project/database must not be overwritten. Preserve additional existing task notes. Reconfirm the target when switching projects or engines.

For example, a binding may point at `D:/Edits/projects/project_123/`. Its private notes stay here; do not copy this manifest or project identity into the shared effect collection.

## Route all Agent-created files

Use a small predictable layout, creating only the folders needed:

| Location under `projectRoot` | Contents |
| --- | --- |
| `notes/` | Edit plan, shot list, prompts, progress and handoff notes |
| `scripts/` | Task-specific helper scripts |
| `generated/` | Generated or processed image, video and audio assets |
| `subtitles/` | Transcripts and subtitle files |
| `review/` | Preview copies, extracted frames, contact sheets and validation evidence |
| `logs/` | Redacted tool/job logs; never credentials or session tokens |
| `tmp/<run-id>/` | Agent-controlled temporary and intermediate files |
| `exports/` | Versioned final media and requested editable packages |
| `effects/` | Unmodified locally exported reusable-effect ZIPs and private collection status |

Before each file-producing tool or subprocess, select a destination here. Pass absolute `outPath`/output options explicitly; do not rely on `project_archive` or effect export defaults. Set subprocess working directories and `TMP`/`TEMP`/`TMPDIR` to the project's run folder when applicable, without changing the system environment. If a tool returns images/audio as content blocks, save review copies here when file access is supported. A file created by a remote service is not project-local until it is copied through an authorized local interface.

Check resolved paths, including existing symlinks/junctions and the nearest existing ancestor of a new path, before writing or copying. Compare actual path components against the resolved root, not a string prefix (`project-a-backup` is not inside `project-a`). A child filename must stay inside its intended root; reject `..`, absolute child paths and link escapes. Use new version/revision filenames and no-overwrite semantics, and recheck after creating directories. Do not move, rename or replace originals. If an existing binding needs relocation, use the supported path workflow and verify references; editing the manifest alone does not relocate media.

The user can explicitly request a delivery copy outside the project. Keep the primary output and its verification evidence here, then copy the completed file to the requested location using a no-overwrite operation. For a requested directory, choose a new versioned filename on conflict; an explicitly required existing filename without overwrite authorization remains pending. Verify the copied bytes/hash and record the additional location in private project notes.

## Engine-owned storage and failures

The running engine manages its database, imported-media library, derived-media storage, preview/font/decode caches and job records. Keep the configured database shared with the human editor. Do not move its files, repoint global storage or copy an active database to force physical isolation. Use an explicit output destination where the current tool schema supports it; for a tool-owned artifact without a destination option, record its ID/path and save a project-local copy when accessible. If a copy is impossible, identify the engine-owned artifact honestly in the handoff.

The install-directory reusable-effect collection is the only automatic cross-project output destination; follow [effect-library.md](effect-library.md). No user media or project metadata belongs there. User-authorized external delivery copies are separate explicit destinations.

On insufficient space, inaccessible storage or an uncertain target, stop the affected write, retain the recoverable project and report the precise blocked artifact. Do not silently fall back to Desktop, Downloads or another project's folder. Retain partial output and job IDs for repair. Delete only individually known temporary outputs inside the resolved project root when cleanup is requested; never recursively clean shared source folders or engine caches.
