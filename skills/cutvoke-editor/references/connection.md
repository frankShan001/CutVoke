# Connection and recovery

## MCP first

Use a locally installed CutVoke executable or the Python environment that has CutVoke installed:

```bash
cutvoke doctor --json
cutvoke mcp --data /absolute/path/to/cutvoke-data
```

These commands assume the executable is on PATH. In a source checkout, activate `.venv`, or use `.venv/Scripts/python.exe -m cutvoke` on Windows and `.venv/bin/python -m cutvoke` on POSIX. A packaged Windows engine can run `cutvoke-engine.exe mcp --data …`. Configure the host to launch the process over stdio, rather than running it as an HTTP URL. Use the host's documented MCP configuration; this JSON illustrates clients that accept `mcpServers`:

```json
{
  "mcpServers": {
    "cutvoke": {
      "command": "/absolute/path/to/cutvoke",
      "args": ["mcp", "--data", "/absolute/path/to/cutvoke-data"]
    }
  }
}
```

Replace paths with those on the **engine's machine**. MCP and `cutvoke serve --data …` must select the same data directory for an editor handoff. Use a new isolated directory for an experiment. Do not copy an active user's database casually; create/clone a project through the service instead.

First call `editor_help({})`, then `runtime` and `project_list`. Request `editor_help({"topic":"recipes"})` for maintained examples and `editor_help({"topic":"command","command":"clip.insert"})` for a payload schema. `commands_list` provides `previewSupported`, so check before using `command_preview`. MCP guide resources expose the same guidance when the client supports them; no source-code access is necessary for ordinary editing.

Restart/reconnect the MCP host process after an engine or effect-manifest update. Existing processes cache their loaded code and effect registry; there is no supported general hot reload.

Known current boundary: MCP `media_import` stores files under `media/imports/<hash>.*`, while the Web asset-availability/media routes accept the existing upload layout `media/<assetId>.*`. MCP-imported files can render yet appear missing in the browser media library. For a browser-visible handoff, use the editor's upload workflow (`POST /api/v1/assets?name=<filename>` with raw bytes and `application/octet-stream`), then use the returned `assetId` over MCP. Do not move files behind the ledger or weaken the media allowlist. The included demo uses this supported upload route by default; `--import-mode mcp` is an engine-only smoke check of the alternate import route.

## HTTP fallback

If the AI host can call local HTTP but has no MCP adapter, start `cutvoke serve --data …`. The default is `http://127.0.0.1:8787`. The same Agent tools are available as `POST /api/v1/agent/<tool-name>` with the tool arguments as the JSON body. For example, POST `{}` to `/api/v1/agent/editor_help`, then inspect the returned help before editing. `GET /api/v1/runtime` checks the service identity. Image responses contain `images`; decode and view them rather than treating a base64 string as visual review.

When session-token protection is enabled, use the supplied `X-CutVoke-Token` locally; never print or save the token in a project, transcript, README or screenshot. Do not enable a non-loopback listener to work around a client connection problem. The CLI also supports local edits, but it is not a one-to-one spelling of every MCP tool: inspect `cutvoke <command> --help` before constructing CLI arguments.

## Dependencies and a concrete next step

| Problem | Next action |
| --- | --- |
| CutVoke cannot start | In the checkout's activated Python 3.10+ environment, run `python -m pip install -e .`. A packaged Windows engine needs no separate Python install. |
| FFmpeg/ffprobe missing or codec/filter unavailable | Install a suitable FFmpeg build, make both tools available on PATH, and rerun `cutvoke doctor --json`. The source renderer needs H.264/AAC, overlay, drawtext and subtitles support. |
| Source checkout lacks Web editor | With Node.js 20+, run `npm ci` and `npm run build` in `web/app`, then rerun doctor. The built application serves its own editor. |
| Caption font preparation fails | Reinstall the project's `fontTools` dependency and inspect the reported cache permissions; do not substitute a different font silently. |
| Optional local transcription is needed | Use the repository's `docs/user/LOCAL_ASR.md` setup, check the actual model, and run the supported command. Do not promise transcription from the base install. Downloads or use of another service need the user's requested scope. |
| No local media access from a remote AI host | Explain the engine runs locally; use an authorized local connector or have the user select accessible material. Do not upload media implicitly. |

## Recovery

- Read `ok`, `error.code`, `committed`, and `error.recovery`. Query the project/events when commit status is uncertain. Preserve command and job IDs in the work notes.
- For `INVALID_ARGUMENT`, read the indicated command/effect schema, repair the field, and preview again. Avoid repeating the same invalid request.
- For `REVISION_CONFLICT`, reread the target, reconcile the user's goal with the concurrent edit, and create a new command only if still appropriate.
- For `EDIT_LOCKED`, wait for the current owner or request a handoff. Release only the lease you acquired.
- For missing or changed assets, locate the intended source with `media_list` / `media_import`, then use the maintained relink command. Do not fill gaps with unrelated material.
- A failed/cancelled/interrupted job is terminal. Read its error, correct the cause, and submit a new task. A timeout does not mean failure: return the job ID and state, and query/cancel that job rather than queueing duplicates.
- Preview jobs live in the current service process. After restart, prepare a new preview; disk cache may be reusable. Export history is persisted. Release leases after failure and retain the partial project for repair.
