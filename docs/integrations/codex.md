# Use CutVoke from Codex: free, local video editing over MCP

Let OpenAI Codex drive CutVoke's timeline (rough cuts, captions, reframes, exports) while the edit stays fully editable in the CutVoke editor.

## 1. Get the MCP command

1. [Download CutVoke](https://github.com/frankShan001/CutVoke/releases) and launch it once.
2. Open `profile/mcp-config.json` in the CutVoke data folder ([where is it?](README.md#where-the-config-comes-from)). You need the `cutvoke` entry: `command`, `args` and `env`.

## 2. Add it to Codex

Codex reads MCP servers from `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`). Add:

```toml
[mcp_servers.cutvoke]
command = "/absolute/path/to/cutvoke-engine"
args = ["mcp", "--data", "/absolute/path/to/data/projects.sqlite"]

[mcp_servers.cutvoke.env]
CUTVOKE_HOME = "/absolute/path/to/data-folder"
CUTVOKE_FONT_CACHE = "/absolute/path/to/data-folder/data/font-cache"
CUTVOKE_GPU_ACCELERATION = "1"
```

On Windows, use TOML literal strings (single quotes) so backslashes are kept as-is, e.g. `command = 'D:\CutVoke\resources\engine\cutvoke-engine.exe'`.

You can also add it from the command line:

```bash
codex mcp add cutvoke \
  --env CUTVOKE_HOME="/absolute/path/to/data-folder" \
  --env CUTVOKE_FONT_CACHE="/absolute/path/to/data-folder/data/font-cache" \
  --env CUTVOKE_GPU_ACCELERATION=1 \
  -- "/absolute/path/to/cutvoke-engine" mcp --data "/absolute/path/to/data/projects.sqlite"
```

Start a new Codex session and check that the CutVoke tools are listed (`/mcp` in the TUI).

## 3. (Optional) Add the cutvoke-editor skill

Copy the whole [`skills/cutvoke-editor/`](../../skills/cutvoke-editor/) folder to `~/.agents/skills/cutvoke-editor/` for personal use, or to `.agents/skills/cutvoke-editor/` at your project root for project-only use. Then invoke it with `$cutvoke-editor`. Step-by-step commands for Windows and POSIX are in the [Agent Skill Guide](../user/agent-skill.md#copy-or-install).

## 4. Try it

Keep the CutVoke editor open on the same project to watch the timeline change. Example prompts:

- *"Use $cutvoke-editor to make a 30-second 16:9 montage from ~/Videos/trip, with readable captions and restrained transitions."*
- *"Cut the silences in this interview, then show me frames at the three biggest cuts."*
- *"Make a 9:16 version for Reels and export it to the project folder."*

Automatic captions use an optional local speech model that downloads on first use ([Local ASR](../user/LOCAL_ASR.md)).

## Troubleshooting

- **Server fails to start**: check the `command` path exists and the TOML quotes are correct on Windows.
- **Agent can't see the editor's projects**: `--data` must be the same `projects.sqlite` the desktop app uses; copy it from `mcp-config.json`.
- **Moved the app**: launch it once to regenerate `mcp-config.json`, then update `config.toml`.

More: [all integrations](README.md) · [External agent editing](../user/external-agent-editing.md) · [Agent Skill Guide](../user/agent-skill.md)
