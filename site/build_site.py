#!/usr/bin/env python3
"""Build the CutVoke documentation site into site/out.

Python standard library only. From the repository root:

    python site/build_site.py

Canonical host: https://frankshan001.github.io/CutVoke/
"""

from __future__ import annotations

import html
import json
import re
import shutil
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE_DIR = Path(__file__).resolve().parent
OUT = SITE_DIR / "out"
STATIC = SITE_DIR / "static"

SITE = "https://frankshan001.github.io/CutVoke"
PREFIX = "/CutVoke"
OG_IMAGE = f"{SITE}/images/social-card.png"
REPO = "https://github.com/frankShan001/CutVoke"
RELEASE = f"{REPO}/releases/tag/v0.3.6"
SOCIAL = ROOT / "docs/images/social-card.png"
EDITOR = ROOT / "docs/images/readme/en/editor-overview.png"

ALLOWED_HOSTS = {
    "github.com",
    "www.youtube.com",
    "www.bilibili.com",
    "frankshan001.github.io",
}
BANNED = (
    "google-analytics",
    "googletagmanager",
    "fonts.googleapis",
    "fonts.gstatic",
    "cdn.jsdelivr",
    "unpkg.com",
    "plausible",
    "doubleclick",
    "facebook.net",
)

TITLES = {
    "": "CutVoke: open-source AI video editor with an MCP server",
    "claude-code-video-editing": "How to edit video with Claude Code",
    "codex-cursor-video-editing": "Edit videos with Codex or Cursor over MCP",
    "auto-subtitles-local": "Free local auto subtitles with Whisper, editable, exportable as SRT",
    "open-source-capcut-alternative": "An open-source CapCut alternative an AI agent can drive",
}

DESCRIPTIONS = {
    "": (
        "Open-source, local-first AI video editor with a built-in MCP server. "
        "Claude Code, Codex, and Cursor edit the same timeline you finish by hand."
    ),
    "claude-code-video-editing": (
        "Add CutVoke's local MCP server to Claude Code, ask for a rough cut in plain "
        "language, and review before/after frames on the same timeline."
    ),
    "codex-cursor-video-editing": (
        "Point Codex or Cursor at CutVoke's local MCP server and edit a real "
        "multi-track timeline. The same project stays open in the CutVoke editor."
    ),
    "auto-subtitles-local": (
        "Draft captions on your computer with optional faster-whisper, correct them, "
        "then export SRT or burn them into the MP4. Not bundled in desktop packages."
    ),
    "open-source-capcut-alternative": (
        "CutVoke is a younger open-source preview an agent can drive over MCP. "
        "See what the editor does, and what it does not, including captions and interchange."
    ),
}

NAV = (
    ("", "Overview"),
    ("claude-code-video-editing", "Claude Code"),
    ("codex-cursor-video-editing", "Codex and Cursor"),
    ("auto-subtitles-local", "Auto subtitles"),
    ("open-source-capcut-alternative", "CapCut alternative"),
)

CLAUDE_JSON = """\
claude mcp add-json cutvoke --scope user '{
  "type": "stdio",
  "command": "/absolute/path/to/cutvoke-engine",
  "args": ["mcp", "--data", "/absolute/path/to/data/projects.sqlite"],
  "env": {
    "CUTVOKE_HOME": "/absolute/path/to/data-folder",
    "CUTVOKE_FONT_CACHE": "/absolute/path/to/data-folder/data/font-cache",
    "CUTVOKE_GPU_ACCELERATION": "1"
  }
}'"""

CLAUDE_FLAGS = """\
claude mcp add cutvoke --scope user \\
  -e CUTVOKE_HOME="/absolute/path/to/data-folder" \\
  -e CUTVOKE_FONT_CACHE="/absolute/path/to/data-folder/data/font-cache" \\
  -e CUTVOKE_GPU_ACCELERATION=1 \\
  -- "/absolute/path/to/cutvoke-engine" mcp --data "/absolute/path/to/data/projects.sqlite\""""

CODEX_TOML = """\
[mcp_servers.cutvoke]
command = "/absolute/path/to/cutvoke-engine"
args = ["mcp", "--data", "/absolute/path/to/data/projects.sqlite"]

[mcp_servers.cutvoke.env]
CUTVOKE_HOME = "/absolute/path/to/data-folder"
CUTVOKE_FONT_CACHE = "/absolute/path/to/data-folder/data/font-cache"
CUTVOKE_GPU_ACCELERATION = "1\""""

CODEX_CLI = """\
codex mcp add cutvoke \\
  --env CUTVOKE_HOME="/absolute/path/to/data-folder" \\
  --env CUTVOKE_FONT_CACHE="/absolute/path/to/data-folder/data/font-cache" \\
  --env CUTVOKE_GPU_ACCELERATION=1 \\
  -- "/absolute/path/to/cutvoke-engine" mcp --data "/absolute/path/to/data/projects.sqlite\""""

CURSOR_JSON = """\
{
  "mcpServers": {
    "cutvoke": {
      "command": "/absolute/path/to/cutvoke-engine",
      "args": ["mcp", "--data", "/absolute/path/to/data/projects.sqlite"],
      "env": {
        "CUTVOKE_HOME": "/absolute/path/to/data-folder",
        "CUTVOKE_FONT_CACHE": "/absolute/path/to/data-folder/data/font-cache",
        "CUTVOKE_GPU_ACCELERATION": "1"
      }
    }
  }
}"""

ASR_INSTALL = """\
uv pip install --python .\\.venv\\Scripts\\python.exe -e ".[asr]\""""

ASR_PREPARE = """\
uv run python tools/dev/prepare_asr_models.py `
  --model tiny --model base `
  --output 'D:\\CutVoke-ASR-Models'"""

ASR_OFFLINE = """\
$env:CUTVOKE_ASR_MODEL_DIR = 'D:\\CutVoke-ASR-Models'
$env:HF_HUB_OFFLINE = '1'
uv run cutvoke serve"""


def blob(path: str) -> str:
    return f"{REPO}/blob/main/{path}"


def tree(path: str) -> str:
    return f"{REPO}/tree/main/{path}"


FAQ: list[tuple[str, str]] = [
    (
        "Can I edit video with Claude Code?",
        "<p>Yes. CutVoke ships a local stdio MCP server. Launch the desktop app once, "
        "copy the <code>cutvoke</code> entry from <code>profile/mcp-config.json</code> "
        'into Claude Code (<a href="@@/claude-code-video-editing/">setup guide</a>), and '
        "ask for edits in plain language, for example "
        '"add captions, tighten the pauses and make a 9:16 version." '
        "The agent edits a real project timeline, which you can open and adjust in the "
        "CutVoke editor. Automatic captions need the optional faster-whisper extra, "
        "which is <a href=\"@@/auto-subtitles-local/\">not bundled in the desktop packages</a>.</p>",
    ),
    (
        "Is there an open-source MCP server for video editing?",
        "<p>CutVoke is one: an MIT-licensed video editor whose MCP server exposes project, "
        "timeline, caption, preview and export tools to any MCP client (Claude Code, Codex, "
        "Cursor, Claude Desktop and others). It also has a CLI and a local HTTP API. "
        f'See <a href="{blob("docs/user/LOCAL_DELIVERY_MCP.md")}">Local Delivery MCP</a>. '
        "Desktop packages also include third-party components such as GPLv3 FFmpeg, so the "
        "download is not all MIT. See the license question below.</p>",
    ),
    (
        "Can Codex or Cursor edit videos?",
        "<p>Yes, through the same MCP server. Per-client steps are on the "
        '<a href="@@/codex-cursor-video-editing/">Codex and Cursor page</a>. '
        f'Loading the bundled <a href="{tree("skills/cutvoke-editor/")}">cutvoke-editor skill</a> '
        "gives the agent the timeline schema plus workflow guides for music videos, "
        "documentaries and vlogs, narrative shorts, explainers and product showcases.</p>",
    ),
    (
        "What happens if the AI makes a bad cut?",
        "<p>You fix it by hand. Agent edits land on the same non-destructive timeline as "
        "manual edits, so you can drag clips, retime captions or change keyframes in the "
        "visual editor instead of re-prompting. While an agent holds an editing lease, the "
        "editor shows it, and <strong>Take over editing</strong> hands control back to you. "
        "Agents can also call <code>preview_compare</code> to check before/after frames "
        "without committing a change.</p>",
    ),
    (
        "Does CutVoke work offline?",
        "<p>Editing, previewing and FFmpeg export run on your computer, and local editing "
        "needs no account. FFmpeg and ffprobe are bundled in the desktop packages. The AI "
        "agent you connect (Claude Code, Codex, and others) may be a cloud service with its "
        "own account, cost and data policy. Optional local speech-to-text captions use "
        "faster-whisper. Models download once and can then run offline "
        '(<a href="@@/auto-subtitles-local/">local auto subtitles</a>).</p>',
    ),
    (
        "Is CutVoke free?",
        "<p>The source code is MIT-licensed and the desktop app needs no account or "
        "subscription. The desktop packages also include third-party components under their "
        "own licenses (for example, FFmpeg under GPLv3). Any AI service you connect is "
        "billed by that service, not by CutVoke.</p>",
    ),
    (
        "How is CutVoke different from CapCut or DaVinci Resolve?",
        "<p>CapCut and DaVinci Resolve are mature editors built for hands-on editing, with "
        "large template and effect libraries. CutVoke is a younger, open-source preview "
        "built around one idea: an AI agent and a person working on the same editable "
        "timeline. Use it when you want an agent to do the rough cut, captions or versions "
        "and still control every clip yourself. Simple flat timelines can be exchanged as "
        "FCPXML 1.10, OpenTimelineIO or CMX 3600 EDL. Effects, keyframes and captions are "
        "not carried over. The "
        '<a href="@@/open-source-capcut-alternative/">comparison page</a> lists what '
        "CutVoke does and does not do.</p>",
    ),
    (
        "How is it different from AI video generators or FFmpeg MCP tools?",
        "<p>AI video generators return a finished MP4. FFmpeg-style MCP tools run one-off "
        "commands on files. CutVoke keeps a multi-track project — clips, captions, effects, "
        "keyframes — that both the agent and you can keep editing, then renders it locally "
        "with FFmpeg.</p>",
    ),
    (
        "Can it generate subtitles automatically?",
        "<p>Yes, with the optional local ASR add-on (faster-whisper). Results arrive as a "
        "draft you can correct, then become an editable subtitle track you can restyle, "
        "cut by word or sentence, export as SRT, or burn into the MP4 export. The "
        '<a href="@@/auto-subtitles-local/">auto subtitles page</a> summarizes the '
        f'<a href="{blob("docs/user/LOCAL_ASR.md")}">Local ASR guide</a>, which is written '
        "in Chinese. faster-whisper is not bundled in the desktop packages.</p>",
    ),
    (
        "Which platforms are supported?",
        "<p>Windows x64 (portable), macOS on Apple Silicon and Intel, and Linux x64. "
        "Current builds are previews. macOS packages are ad-hoc signed, not notarized. "
        f'See the <a href="{REPO}/releases">latest release</a> for limitations.</p>',
    ),
]


def fill(template: str, codes: dict[str, str] | None = None) -> str:
    text = template
    for key, value in (codes or {}).items():
        block = "<pre><code>" + html.escape(value).strip("\n") + "</code></pre>"
        token = f"@@CODE:{key}@@"
        if token not in text:
            raise SystemExit(f"Missing code token {token}")
        text = text.replace(token, block)
    if "@@CODE:" in text:
        raise SystemExit("Unreplaced code token in page body")
    if "@@FAQ@@" in text:
        raise SystemExit("Unreplaced FAQ token")
    return text.replace("@@", PREFIX)


def faq_html() -> str:
    parts = ['<section class="faq" id="faq">', "<h2>FAQ</h2>"]
    for question, answer in FAQ:
        parts.append(f"<h3>{html.escape(question)}</h3>")
        parts.append(answer)
    parts.append("</section>")
    return "\n".join(parts)


def data_folder_table() -> str:
    return """
<div class="table-wrap">
<table>
<thead><tr><th scope="col">Platform</th><th scope="col">Data folder</th></tr></thead>
<tbody>
<tr><td>Windows (portable)</td><td><code>CutVoke Data</code> next to <code>CutVoke.exe</code></td></tr>
<tr><td>macOS</td><td><code>~/Library/Application Support/CutVoke/</code></td></tr>
<tr><td>Linux</td><td><code>${XDG_DATA_HOME:-~/.local/share}/CutVoke/</code></td></tr>
</tbody>
</table>
</div>
""".strip()


def home_body() -> str:
    releases = f"{REPO}/releases/download/v0.3.6"
    template = """
<p class="lead">CutVoke is a local-first desktop video editor with a built-in stdio MCP server. Claude Code, Codex, Cursor, Claude Desktop, or any MCP client can edit the project. You finish the same non-destructive, multi-track timeline by hand.</p>
<p>The source is MIT-licensed. Desktop packages also include third-party components under their own licenses, including GPLv3 FFmpeg, so the aggregate is not all MIT. Local editing needs no CutVoke account. An agent you connect may be a separate cloud service, with its own account, cost and data policy.</p>
<img class="shot" src="@@/images/editor-overview.png" width="1600" height="1160" alt="CutVoke editor showing the Trail Notes demo: preview, video, audio, text and caption tracks.">
<p class="caption">Editor overview from the repository docs. The Trail Notes picture is a project created by the repository demo script, not a claim about your footage.</p>

<h2>Download the v0.3.6 preview</h2>
<p>No Python or Node.js setup is required for the desktop packages. FFmpeg and ffprobe are included. Optional speech and person models are not.</p>
<div class="table-wrap">
<table>
<thead><tr><th scope="col">Platform</th><th scope="col">v0.3.6 package</th></tr></thead>
<tbody>
<tr><td>Windows x64, portable</td><td><a href="__WIN__">CutVoke-0.3.6-windows-x64-portable.zip</a></td></tr>
<tr><td>macOS, Apple Silicon</td><td><a href="__MAC_ARM__">CutVoke-0.3.6-macos-arm64.zip</a></td></tr>
<tr><td>macOS, Intel</td><td><a href="__MAC_X64__">CutVoke-0.3.6-macos-x64.zip</a></td></tr>
<tr><td>Linux x64</td><td><a href="__LINUX__">CutVoke-0.3.6-linux-x64.tar.gz</a></td></tr>
</tbody>
</table>
</div>
<p><a href="__SHA__">SHA-256 checksums</a> · <a href="__BUILD__">Build and acceptance record</a> · <a href="__TAG__">v0.3.6 release</a> · <a href="__ALL__">All releases</a></p>
<div class="limit">
<p><strong>v0.3.6 is a frozen preview, not current main.</strong> Later project-format migration and thumbnail-seeking changes are not in that download. macOS packages are ad-hoc signed, not Developer ID signed or notarized. Export-window reuse is disabled in all four packages. Some playback issues remain under investigation. Read the <a href="__TAG__">release limitations</a> and the <a href="__INSTALL__">installation guide</a> before you rely on a package.</p>
</div>

<h2>Connect your agent in three steps</h2>
<ol class="steps">
<li><a href="__TAG__">Download CutVoke v0.3.6</a> and launch it once.</li>
<li>Open <code>profile/mcp-config.json</code> in the CutVoke data folder. Windows portable: <code>CutVoke Data</code> next to the exe. macOS: <code>~/Library/Application Support/CutVoke/</code>. Linux: <code>${XDG_DATA_HOME:-~/.local/share}/CutVoke/</code>. The file already contains the bundled engine command, so no Python install is needed.</li>
<li>Add the <code>cutvoke</code> entry to your agent: <a href="@@/claude-code-video-editing/">Claude Code</a>, <a href="@@/codex-cursor-video-editing/">Codex or Cursor</a>. Then ask: "Cut the silences, add captions, make a 9:16 version, and show me a few frames before you export."</li>
</ol>
<p>That caption request needs the <a href="@@/auto-subtitles-local/">optional local ASR extra</a>. The desktop package does not include it. While you set the client up, the same three guides cover Claude Desktop and any other MCP client: paste the <code>mcpServers</code> block from <code>profile/mcp-config.json</code>.</p>

<h2>What you edit</h2>
<ul>
<li>A multi-track timeline: video, audio, adjustment layers, pre-compositions, overlay stickers, motion titles and subtitle tracks, with non-destructive trimming. <a href="__BASIC__">Basic editing</a> · <a href="__ADV__">Advanced timeline</a></li>
<li>Bezier keyframe animation, alpha and luma track mattes, and moving masks. <a href="__VFX__">Cinematic VFX</a> · <a href="__MASKS__">Moving masks</a></li>
<li>Multi-track audio mixing and background-music ducking. <a href="__AUDIO__">Audio workbench</a></li>
<li>Optional local captions and transcript cuts by word or sentence. <a href="@@/auto-subtitles-local/">Auto subtitles</a></li>
<li>Optional local person-segmentation models (<code>cutvoke-person-model</code>) and a local 3D engine. These models are not part of the basic offline download. <a href="__3D__">Local 3D and models</a></li>
<li>A CLI, a local HTTP API, and the <a href="__SKILL__">cutvoke-editor skill</a> with guides for music videos, documentaries and vlogs, narrative shorts, explainers and product showcases.</li>
</ul>
<p>Reusable effect recipes live in <a href="__EFFECTS__">effects/</a>. Flat timelines can be exchanged as FCPXML 1.10, OpenTimelineIO JSON or CMX 3600 EDL. Effects, keyframes and captions are not carried over. Details are on the <a href="@@/open-source-capcut-alternative/">CapCut alternative</a> page.</p>

@@FAQ@@

<p>These answers follow the <a href="__README__">README FAQ</a>. Per-client setup is in the repository's <a href="__INT__">integration guides</a>.</p>
"""
    faq = faq_html().replace("@@", PREFIX)
    text = template.replace("@@FAQ@@", faq)
    text = (
        text.replace("__WIN__", f"{releases}/CutVoke-0.3.6-windows-x64-portable.zip")
        .replace("__MAC_ARM__", f"{releases}/CutVoke-0.3.6-macos-arm64.zip")
        .replace("__MAC_X64__", f"{releases}/CutVoke-0.3.6-macos-x64.zip")
        .replace("__LINUX__", f"{releases}/CutVoke-0.3.6-linux-x64.tar.gz")
        .replace("__SHA__", f"{releases}/SHA256SUMS")
        .replace("__BUILD__", f"{releases}/BUILD-INFO.json")
        .replace("__TAG__", RELEASE)
        .replace("__ALL__", f"{REPO}/releases")
        .replace("__INSTALL__", blob("docs/user/installation.md"))
        .replace("__BASIC__", blob("docs/user/BASIC_EDITING.md"))
        .replace("__ADV__", blob("docs/user/ADVANCED_TIMELINE.md"))
        .replace("__VFX__", blob("docs/cinematic-vfx.md"))
        .replace("__MASKS__", blob("docs/moving-masks.md"))
        .replace("__AUDIO__", blob("docs/user/AUDIO_WORKBENCH.md"))
        .replace("__3D__", blob("docs/user/LOCAL_3D_AND_MODELS.md"))
        .replace("__SKILL__", tree("skills/cutvoke-editor/"))
        .replace("__EFFECTS__", tree("effects/"))
        .replace("__README__", blob("README.md"))
        .replace("__INT__", blob("docs/integrations/README.md"))
    )
    return fill(text)


def claude_body() -> str:
    template = """
<p class="lead">Claude Code can make a rough cut, captions and a vertical version on a CutVoke timeline. The desktop editor stays open on that same project, so you can change any clip by hand.</p>
<div class="note"><p>v0.3.6 is a preview. Automatic captions use optional faster-whisper and are <a href="@@/auto-subtitles-local/">not bundled in the desktop packages</a>. Copy paths from your own <code>profile/mcp-config.json</code>. The samples below are the placeholders from the Claude Code guide.</p></div>

<h2>1. Get the MCP command</h2>
<ol class="steps">
<li><a href="__RELEASES__">Download CutVoke</a> and launch it once.</li>
<li>Open <code>profile/mcp-config.json</code> in the CutVoke data folder. You need the <code>cutvoke</code> entry: <code>command</code>, <code>args</code> and <code>env</code>.</li>
</ol>
__FOLDERS__
<p>No system Python is needed for the packaged engine. On Windows the bundled command is <code>cutvoke-engine.exe</code>. <code>CUTVOKE_GPU_ACCELERATION</code> is <code>"1"</code> unless GPU acceleration is turned off in the app, which writes <code>"0"</code>. If you move the app, launch it once to refresh the paths. A source checkout writes a different command. <a href="__INTRO__">Where the config comes from</a> covers that case.</p>

<h2>2. Add it to Claude Code</h2>
<p><code>claude mcp add-json</code> accepts the entry as JSON. Replace the placeholders with the values from your file:</p>
@@CODE:json@@
<p>Or, with flags:</p>
@@CODE:flags@@
<p><code>--scope user</code> makes CutVoke available in every project. Omit it to add CutVoke only to the current project. On Windows, the engine path ends in <code>cutvoke-engine.exe</code>. In PowerShell, keep the paths in quotes.</p>
<p>Check it with <code>claude mcp list</code>, then start a new Claude Code session, or run <code>/mcp</code>, so it loads the CutVoke tools.</p>

<h2>3. Optional: add the cutvoke-editor skill</h2>
<p>Copy the whole <a href="__SKILL__">skills/cutvoke-editor/</a> folder to <code>~/.claude/skills/cutvoke-editor/</code> so Claude Code can load CutVoke's editing workflow on demand. Without it, Claude can still read the same guides through the <code>editor_help</code> tool. The skill includes guides for music videos, documentaries and vlogs, narrative shorts, explainers and product showcases.</p>

<h2>4. Example prompts</h2>
<p>Keep the CutVoke editor open on the same project to watch the timeline change.</p>
<ul>
<li>"Create a CutVoke project from the clips in ~/Videos/trip, cut the silences, and add captions."</li>
<li>"Make a 9:16 version of this project for Shorts and show me a few frames before you export."</li>
<li>"Find the sentence where I say 'pricing' in the transcript, remove it, and export a new MP4 next to the original."</li>
</ul>
<p>The caption prompts need the optional local speech model, which downloads on first use.</p>

<h2>5. Review frames before you commit</h2>
<p>For a visual dry-run, the agent calls <code>preview_compare</code> with <code>projectId</code>, the current <code>expectedRevision</code>, the command <code>type</code> and <code>payload</code>, and 1–3 <code>times</code> in seconds. It returns before/after PNG pairs from the same snapshot, including captions, without committing the edit. Times must fit both timelines. <code>imageIndex</code> maps each nonempty side to its image block (zero-based, excluding the JSON text block). Check the pairs, then apply with the latest revision.</p>
<p>The call writes local preview-cache files. Export and audio side-effect commands are rejected. It compares the same timestamp on both timelines. That is not source-time alignment after a ripple edit.</p>
<p>While an agent holds an editing lease, the editor shows the lease owner and the remaining time. <strong>Take over editing</strong> ends that lease and refreshes the project before you continue. Completed edits are preserved. Requests that still use the old lease are rejected.</p>

<h2>Troubleshooting</h2>
<ul>
<li><strong>Tools don't appear.</strong> Restart the session after adding the server, and confirm the <code>command</code> path exists.</li>
<li><strong>The agent can't see projects from the editor.</strong> <code>--data</code> must point at the same <code>projects.sqlite</code> the desktop app uses. Copy it from <code>mcp-config.json</code> rather than typing it.</li>
<li><strong>You moved the app.</strong> Launch it once to regenerate <code>mcp-config.json</code>, then update the Claude Code entry.</li>
</ul>
<p>Source: <a href="__DOC__">Use CutVoke from Claude Code</a> and the README notes on <code>preview_compare</code>. Same server, other clients: <a href="@@/codex-cursor-video-editing/">Codex and Cursor</a>.</p>
"""
    text = template.replace("__FOLDERS__", data_folder_table())
    text = (
        text.replace("__RELEASES__", f"{REPO}/releases")
        .replace("__INTRO__", blob("docs/integrations/README.md"))
        .replace("__SKILL__", tree("skills/cutvoke-editor/"))
        .replace("__DOC__", blob("docs/integrations/claude-code.md"))
    )
    return fill(text, {"json": CLAUDE_JSON, "flags": CLAUDE_FLAGS})


def codex_body() -> str:
    template = """
<p class="lead">Codex and Cursor both talk to CutVoke's local stdio MCP server. Either client can build a rough cut, captions, a reframed version or an export. The edit stays in the CutVoke project, so you can keep working on it in the editor.</p>
<div class="note"><p>Launch the desktop app once, then copy <code>command</code>, <code>args</code> and <code>env</code> from your own <code>profile/mcp-config.json</code>. v0.3.6 is a preview. Caption prompts need <a href="@@/auto-subtitles-local/">optional faster-whisper</a>, which is not in the desktop packages.</p></div>

<h2>Shared setup</h2>
__FOLDERS__
<p>No system Python is needed for the packaged engine. On Windows the bundled command is <code>cutvoke-engine.exe</code>. <code>CUTVOKE_GPU_ACCELERATION</code> is <code>"1"</code> unless GPU acceleration is turned off in the app, which writes <code>"0"</code>. Move the app, and a fresh launch rewrites the paths. <a href="__INTRO__">Where the config comes from</a> also covers a source checkout, which points at that checkout's Python instead of the bundled engine.</p>

<h2>Codex</h2>
<p>Codex reads MCP servers from <code>~/.codex/config.toml</code> (or <code>$CODEX_HOME/config.toml</code>). Add:</p>
@@CODE:toml@@
<p>On Windows, use TOML literal strings (single quotes) so backslashes are kept as-is, for example <code>command = 'D:\\CutVoke\\resources\\engine\\cutvoke-engine.exe'</code>.</p>
<p>You can also add it from the command line:</p>
@@CODE:cli@@
<p>Start a new Codex session and check that the CutVoke tools are listed (<code>/mcp</code> in the TUI).</p>
<h3>Codex skill</h3>
<p>Copy <a href="__SKILL__">skills/cutvoke-editor/</a> to <code>~/.agents/skills/cutvoke-editor/</code> for personal use, or to <code>.agents/skills/cutvoke-editor/</code> at the project root for project-only use. Invoke it with <code>$cutvoke-editor</code>. Step-by-step copy commands are in the <a href="__SKILLDOC__">Agent Skill Guide</a>.</p>
<h3>Codex prompts</h3>
<ul>
<li>"Use $cutvoke-editor to make a 30-second 16:9 montage from ~/Videos/trip, with readable captions and restrained transitions."</li>
<li>"Cut the silences in this interview, then show me frames at the three biggest cuts."</li>
<li>"Make a 9:16 version for Reels and export it to the project folder."</li>
</ul>
<h3>Codex troubleshooting</h3>
<ul>
<li><strong>The server fails to start.</strong> Check that the <code>command</code> path exists and that the TOML quotes are correct on Windows.</li>
<li><strong>The agent can't see the editor's projects.</strong> <code>--data</code> must be the same <code>projects.sqlite</code> the desktop app uses. Copy it from <code>mcp-config.json</code>.</li>
<li><strong>You moved the app.</strong> Launch it once, then update <code>config.toml</code>.</li>
</ul>

<h2>Cursor</h2>
<p>Cursor uses the same <code>mcpServers</code> shape, so you can paste the generated entry:</p>
<ul>
<li><strong>All projects:</strong> <code>~/.cursor/mcp.json</code></li>
<li><strong>One project:</strong> <code>.cursor/mcp.json</code> in the project root</li>
</ul>
@@CODE:cursor@@
<p>If <code>mcp.json</code> already has other servers, add only the <code>"cutvoke"</code> entry inside the existing <code>mcpServers</code> object. On Windows, JSON paths need doubled backslashes (<code>D:\\\\CutVoke\\\\...</code>), which is already how the generated file writes them.</p>
<p>Open Cursor Settings, then MCP (Tools and MCP), and check that <code>cutvoke</code> is enabled and its tools are listed.</p>
<h3>Cursor skill</h3>
<p>Point the agent at <a href="__SKILLMD__">skills/cutvoke-editor/SKILL.md</a>, for example by adding that folder to the workspace and referencing it in chat. Or let it call <code>editor_help</code>, which serves the same guides over MCP.</p>
<h3>Cursor prompts</h3>
<ul>
<li>"Create a CutVoke project from ./footage, cut the silences, add captions, and show me frames before exporting."</li>
<li>"Make a 9:16 version of the current project and export it into this repo's <code>out/</code> folder."</li>
<li>"Tighten the intro to under 5 seconds and keep the rest of the timeline unchanged."</li>
</ul>
<h3>Cursor troubleshooting</h3>
<ul>
<li><strong>The server shows an error.</strong> Confirm the <code>command</code> path exists. Relaunch the desktop app if you moved it.</li>
<li><strong>The agent can't see the editor's projects.</strong> <code>--data</code> must be the same <code>projects.sqlite</code> the desktop app uses.</li>
</ul>

<h2>Review frames before you commit</h2>
<p>Keep the CutVoke editor open on the same project so you can watch the timeline change. For a visual dry-run, call <code>preview_compare</code> with <code>projectId</code>, the current <code>expectedRevision</code>, the command <code>type</code> and <code>payload</code>, and 1–3 <code>times</code> in seconds. It returns before/after PNG pairs from the same snapshot, including captions, and it does not commit the edit. Times must fit both timelines. Export and audio side-effect commands are rejected. The comparison is the same timestamp on both timelines, not source-time alignment after a ripple edit.</p>
<p><strong>Take over editing</strong> ends the agent's current lease and returns the timeline to you. Completed edits stay.</p>
<p>Sources: <a href="__CODEX__">Codex</a> · <a href="__CURSOR__">Cursor</a> · <a href="@@/claude-code-video-editing/">Claude Code</a>.</p>
"""
    text = template.replace("__FOLDERS__", data_folder_table())
    text = (
        text.replace("__INTRO__", blob("docs/integrations/README.md"))
        .replace("__SKILL__", tree("skills/cutvoke-editor/"))
        .replace("__SKILLDOC__", blob("docs/user/agent-skill.md"))
        .replace("__SKILLMD__", blob("skills/cutvoke-editor/SKILL.md"))
        .replace("__CODEX__", blob("docs/integrations/codex.md"))
        .replace("__CURSOR__", blob("docs/integrations/cursor.md"))
    )
    return fill(text, {"toml": CODEX_TOML, "cli": CODEX_CLI, "cursor": CURSOR_JSON})


def asr_body() -> str:
    template = """
<p class="lead">CutVoke can draft captions on your computer with the optional <code>faster-whisper</code> extra, then let you correct them. Confirmed lines become an editable subtitle track you can restyle, cut by word or sentence, export as SRT, or burn into the MP4.</p>
<div class="limit">
<p><strong>Not bundled.</strong> Desktop packages do not include faster-whisper, OpenCC, or the speech models. They are not part of the basic offline edit, preview and export workflow. The source extra is <code>cutvoke[asr]</code>. The detailed guide, <a href="__DOC__">Local ASR</a>, is written in Chinese. This page follows that guide.</p>
</div>

<h2>Install the optional extra</h2>
<p>In a source checkout, the guide installs it with this Windows command from the repository root:</p>
@@CODE:install@@
<p>The desktop download does not gain this extra by itself. Models are downloaded the first time you use each one. <code>CUTVOKE_ASR_MODEL_DIR</code> can point the cache somewhere else. Tiny is for a quick check. Base is the default. Larger models usually use more memory and time.</p>

<h2>Recognize from the editor</h2>
<p>Open a project and select a video or audio clip that has sound. Start recognition from the captions control the Chinese guide labels <strong>字幕 → 自动字幕</strong>. Choose a language and a model, then run it.</p>
<p>The result is a draft. It does not change the project until you confirm it. You can edit each line's text and in/out times, search, or delete candidates. <strong>添加到工程</strong> ("add to project") writes every candidate as one undoable command.</p>
<p>The proposal and the proofreading draft are stored in the current project library's task ledger, so reopening that library can bring them back. Editing stops for about 350 milliseconds, and the proofreading draft is then saved. <strong>放弃识别结果</strong> ("discard recognition results") clears that task's proposal and draft. If the source file, clip position, trim or speed changes, the old draft has to be recognized again so the captions do not drift.</p>
<p>Captions you have added can still be edited one by one. They export as SRT, and MP4 export burns them into the picture. The editor can also cut the timeline by word or sentence on the transcript.</p>

<h2>Where it runs</h2>
<p>The device policy defaults to <code>auto</code>. When a CTranslate2 CUDA device is detected, recognition uses GPU float16. If GPU model setup fails, it falls back to CPU int8. Set <code>CUTVOKE_ASR_DEVICE=cpu</code> before starting the service to force CPU, or <code>cuda</code> to force CUDA. Forcing CUDA reports a clear error when CUDA is absent. The captions panel shows the current policy, and after a run it shows the device and compute type actually used.</p>
<p>Choosing a device only changes where inference runs. Media is not uploaded.</p>

<h2>Download models once, then work offline</h2>
<p>On a machine that has a network, prepare and check models into a directory. Repeat <code>--model</code> for each size. With no <code>--model</code>, the command prepares Base. The first download needs a network. Later runs reuse the cache in that directory:</p>
@@CODE:prepare@@
<p>Copy the directory to the computer you want to use, keeping its subfolders. The model files are large. In the same terminal you use to start CutVoke, point at the cache. Offline, also set the Hugging Face offline variable so the process does not try to download:</p>
@@CODE:offline@@
<p>The prepare command loads each requested model to check it, then prints the directory and the runtime variables. The target computer still needs <code>cutvoke[asr]</code> installed. GPU autodetect and fallback need that computer's NVIDIA driver and the CTranslate2 CUDA libraries. Without them, <code>auto</code> uses the CPU.</p>

<h2>What you should still expect to fix</h2>
<p>Automatic recognition still needs a person to proofread it, especially names, dialects, and material with loud background music. Time and error rate depend on the model, device, language, accent and background sound. A result on synthetic speech does not represent accuracy on a real voice.</p>
<p>At most two recognition tasks are queued or running at once. Cancelled state is kept, and a late model result does not overwrite it. If you close the app mid-run, an unfinished task shows as waiting to resume. Continuing keeps the original model and language and runs recognition again from the start of the clip. It does not write captions by itself.</p>

<h2>Agents, and what is not speech recognition</h2>
<p><code>caption.transcribe</code>, for an external agent, returns a recognition proposal and does not change the project. <code>caption.bulkAdd</code> commits many captions in one step when the source signature is still valid. <code>caption.autoSegment</code> only splits on silence and inserts placeholder text. It is not speech recognition.</p>

<h2>Simplified Chinese</h2>
<p>After Chinese recognition, OpenCC <code>tw2s</code> normalizes the glyphs to Simplified Chinese, based on the detected language. English and other languages are not converted. Word timestamps keep their times. Word text is converted with the whole word sequence as context, so a character is not rewritten without its neighbors. The <code>cutvoke[asr]</code> extra includes OpenCC.</p>
<p>Source: <a href="__DOC__">本地自动字幕</a>. Related: <a href="@@/claude-code-video-editing/">editing with Claude Code</a>.</p>
"""
    text = template.replace("__DOC__", blob("docs/user/LOCAL_ASR.md"))
    return fill(text, {"install": ASR_INSTALL, "prepare": ASR_PREPARE, "offline": ASR_OFFLINE})


def capcut_body() -> str:
    template = """
<p class="lead">CapCut and DaVinci Resolve are mature editors built for hands-on editing, with large template and effect libraries. CutVoke is a younger, open-source preview built around one idea: an AI agent and a person working on the same editable timeline.</p>
<p>Use CutVoke when you want an agent to do the rough cut, captions or versions, and you still want to control every clip. It is not a drop-in copy of CapCut. The table is only what the CutVoke repository itself documents.</p>
<div class="table-wrap">
<table class="compare">
<thead>
<tr><th scope="col">What CutVoke does</th><th scope="col">What it does not do</th></tr>
</thead>
<tbody>
<tr>
<td class="does">Ships as a local desktop editor for Windows x64, macOS (Apple Silicon and Intel) and Linux x64. v0.3.6 is a preview you can <a href="@@/">download from GitHub Releases</a>.</td>
<td class="doesnt">Does not claim production stability. macOS packages are ad-hoc signed, not notarized. Windows and Linux packages are unsigned. Export-window reuse is disabled in the v0.3.6 packages, and some playback issues are still under investigation.</td>
</tr>
<tr>
<td class="does">Publishes the source under MIT. Local editing, preview and FFmpeg export need no CutVoke account or subscription. Desktop packages bundle FFmpeg and ffprobe.</td>
<td class="doesnt">The aggregate download is not all MIT. Packages include third-party components under their own licenses, including GPLv3 FFmpeg. A connected AI service is billed by that service.</td>
</tr>
<tr>
<td class="does">Includes a local stdio MCP server, a CLI and a local HTTP API. Claude Code, Codex, Cursor, Claude Desktop or any MCP client can edit the project. The <a href="__SKILL__">cutvoke-editor skill</a> adds genre workflow guides.</td>
<td class="doesnt">Does not replace the account, price or data policy of the agent you connect. Basic offline editing needs no model or API key. The agent may still be a cloud product.</td>
</tr>
<tr>
<td class="does">Keeps one non-destructive multi-track timeline for the agent and for you: clips, captions, effects and keyframes, plus pre-comps, adjustment layers, track mattes, moving masks and motion titles. <strong>Take over editing</strong> ends the agent's lease. <code>preview_compare</code> shows before/after frames without committing.</td>
<td class="doesnt">Does not hand back only a finished MP4, and it is not a one-off FFmpeg command with no project. AI video generators and FFmpeg-style MCP tools are a different kind of tool.</td>
</tr>
<tr>
<td class="does">Can draft <a href="@@/auto-subtitles-local/">local captions</a> with optional faster-whisper, then keep an editable subtitle track, export SRT, burn captions into the MP4, or cut by word or sentence.</td>
<td class="doesnt">Does not include faster-whisper or speech models in the desktop packages. Recognition is a draft. It still needs proofreading.</td>
</tr>
<tr>
<td class="does">Can exchange a flat timeline as FCPXML 1.10, OpenTimelineIO JSON or CMX 3600 EDL. By default, unmapped content blocks the file instead of writing a partial one. <a href="__INTER__">Interchange notes</a>.</td>
<td class="doesnt">Does not carry effects, keyframes, captions, transitions or speed changes across that exchange. Nested, compound and multicam structure is outside the supported subset. AAF is unsupported (<code>UNSUPPORTED_FORMAT</code>). The supported subset is not yet hand-verified in each commercial editor.</td>
</tr>
<tr>
<td class="does">Stores reusable, inspectable effect recipes in <a href="__EFFECTS__">effects/</a>, instead of only compiled plugins.</td>
<td class="doesnt">Does not ship the large template and effect libraries of a mature hands-on editor such as CapCut or DaVinci Resolve.</td>
</tr>
</tbody>
</table>
</div>
<p>Interchange files reference media. They do not pack the media. Lost local files come back as a relink prompt. FCPXML export checks embedded audio with ffprobe and refuses a file when that audio cannot be verified, rather than writing a document that might be silently mute.</p>

<h2>Watch the editor, then judge it</h2>
<p>Two product-overview videos are linked from the README:</p>
<ul>
<li><a href="https://www.youtube.com/watch?v=2--4_XO15RI">YouTube overview</a></li>
<li><a href="https://www.bilibili.com/video/BV18VHA62EZ1/">Bilibili overview</a></li>
</ul>
<p>Showcase projects in the repository: <a href="__SHOW__">examples/showcases/</a>. They illustrate edits. They are not a benchmark against CapCut.</p>
<p>The wording in the introduction is the README FAQ. The interchange limits are from the production-versions guide, which is written in Chinese.</p>
"""
    text = (
        template.replace("__SKILL__", tree("skills/cutvoke-editor/"))
        .replace("__INTER__", blob("docs/user/PRODUCTION_VERSIONS_INTERCHANGE.md"))
        .replace("__EFFECTS__", tree("effects/"))
        .replace("__SHOW__", tree("examples/showcases/"))
    )
    return fill(text)


def pages() -> list[dict[str, str]]:
    bodies = {
        "": home_body(),
        "claude-code-video-editing": claude_body(),
        "codex-cursor-video-editing": codex_body(),
        "auto-subtitles-local": asr_body(),
        "open-source-capcut-alternative": capcut_body(),
    }
    built = []
    for slug, _label in NAV:
        built.append(
            {
                "slug": slug,
                "title": TITLES[slug],
                "description": DESCRIPTIONS[slug],
                "body": bodies[slug],
            }
        )
    return built


def canonical(slug: str) -> str:
    if slug == "":
        return f"{SITE}/"
    return f"{SITE}/{slug}/"


def nav_html(current: str) -> str:
    items = []
    for slug, label in NAV:
        href = f"{PREFIX}/" if slug == "" else f"{PREFIX}/{slug}/"
        current_attr = ' aria-current="page"' if slug == current else ""
        items.append(f'<a href="{href}"{current_attr}>{html.escape(label)}</a>')
    github = f'<a href="{REPO}">GitHub</a>'
    return "\n".join(items + [github])


def json_ld() -> str:
    def plain(fragment: str) -> str:
        text = re.sub(r"<[^>]+>", "", fragment)
        text = html.unescape(text)
        return re.sub(r"\s+", " ", text).strip()

    faq = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": question,
                "acceptedAnswer": {"@type": "Answer", "text": plain(answer)},
            }
            for question, answer in FAQ
        ],
    }
    software = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "CutVoke",
        "applicationCategory": "MultimediaApplication",
        "operatingSystem": "Windows, macOS, Linux",
        "softwareVersion": "0.3.6",
        "url": f"{SITE}/",
        "downloadUrl": RELEASE,
        "image": OG_IMAGE,
        "description": (
            "Local-first desktop video editor with a built-in stdio MCP server. "
            "v0.3.6 is a preview. Source is MIT; desktop packages also bundle GPLv3 FFmpeg."
        ),
        "license": "https://opensource.org/licenses/MIT",
        "isAccessibleForFree": True,
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
        "featureList": [
            "Local stdio MCP server for Claude Code, Codex, Cursor, Claude Desktop and other MCP clients",
            "Multi-track non-destructive timeline edited by a person and an agent",
            "Optional faster-whisper captions, not bundled in desktop packages, with SRT export",
            "FFmpeg export bundled in the desktop packages",
            "Flat-timeline interchange as FCPXML 1.10, OpenTimelineIO JSON or CMX 3600 EDL",
        ],
    }
    blocks = []
    for payload in (faq, software):
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).replace("<", "\\u003c")
        blocks.append(
            '<script type="application/ld+json">\n' + encoded + "\n</script>"
        )
    return "\n".join(blocks)


TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<meta name="description" content="__DESCRIPTION__">
<link rel="canonical" href="__CANONICAL__">
<meta name="robots" content="index,follow">
<meta name="theme-color" content="#101318">
<meta property="og:type" content="website">
<meta property="og:site_name" content="CutVoke">
<meta property="og:title" content="__TITLE__">
<meta property="og:description" content="__DESCRIPTION__">
<meta property="og:url" content="__CANONICAL__">
<meta property="og:image" content="__IMAGE__">
<meta property="og:image:width" content="1280">
<meta property="og:image:height" content="640">
<meta property="og:image:alt" content="CutVoke — open-source AI video editor. Claude Code, Codex and Cursor edit over MCP.">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="__TITLE__">
<meta name="twitter:description" content="__DESCRIPTION__">
<meta name="twitter:image" content="__IMAGE__">
<meta name="twitter:image:alt" content="CutVoke — open-source AI video editor. Claude Code, Codex and Cursor edit over MCP.">
<link rel="icon" href="__FAVICON__" type="image/svg+xml">
<link rel="stylesheet" href="__CSS__">
__JSONLD__
</head>
<body>
<a class="skip" href="#content">Skip to content</a>
<header>
<div class="wrap bar">
<a class="brand" href="__HOME__">CutVoke</a>
<nav aria-label="Pages">
__NAV__
</nav>
</div>
</header>
<main id="content" class="wrap">
<article>
<h1>__TITLE__</h1>
__BODY__
</article>
</main>
<footer>
<div class="wrap">
<p>CutVoke source is MIT. Desktop packages also include third-party components under their own licenses, including GPLv3 FFmpeg, so the aggregate is not all MIT.</p>
<p>Desktop downloads linked from this site are the <a href="__RELEASE__">v0.3.6 preview</a>, not current <code>main</code>. Unlabelled behavior follows the repository documentation on <code>main</code>.</p>
<p>This site is static. It has no analytics and loads no third-party fonts or scripts. <a href="__REPO__">Source repository</a> · <a href="__INSTALL__">Installation</a></p>
</div>
</footer>
</body>
</html>
"""


def render(page: dict[str, str]) -> str:
    slug = page["slug"]
    title = html.escape(page["title"], quote=True)
    description = html.escape(page["description"], quote=True)
    return (
        TEMPLATE.replace("__TITLE__", title)
        .replace("__DESCRIPTION__", description)
        .replace("__CANONICAL__", canonical(slug))
        .replace("__IMAGE__", OG_IMAGE)
        .replace("__FAVICON__", f"{PREFIX}/assets/favicon.svg")
        .replace("__CSS__", f"{PREFIX}/assets/style.css")
        .replace("__JSONLD__", json_ld() if slug == "" else "")
        .replace("__HOME__", f"{PREFIX}/")
        .replace("__NAV__", nav_html(slug))
        .replace("__BODY__", page["body"])
        .replace("__RELEASE__", RELEASE)
        .replace("__REPO__", REPO)
        .replace("__INSTALL__", blob("docs/user/installation.md"))
    )


def write_sitemap(slugs: list[str]) -> None:
    urls = "\n".join(
        f"  <url><loc>{canonical(slug)}</loc></url>" for slug in slugs
    )
    text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n"
        "</urlset>\n"
    )
    (OUT / "sitemap.xml").write_text(text, encoding="utf-8")


def write_robots() -> None:
    (OUT / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n\nSitemap: "
        f"{SITE}/sitemap.xml\n",
        encoding="utf-8",
    )


def png_ihdr(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise SystemExit(f"Not a PNG: {path}")
    return struct.unpack(">II", data[16:24])


def build() -> None:
    if not SOCIAL.is_file() or not EDITOR.is_file():
        raise SystemExit("Missing social preview or editor screenshot")
    width, height = png_ihdr(SOCIAL)
    social_bytes = SOCIAL.stat().st_size
    if (width, height) != (1280, 640):
        raise SystemExit(f"Social preview is {width}x{height}, expected 1280x640")
    if social_bytes >= 1_000_000:
        raise SystemExit(f"Social preview is {social_bytes} bytes")

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    assets = OUT / "assets"
    images = OUT / "images"
    assets.mkdir()
    images.mkdir()
    shutil.copyfile(STATIC / "style.css", assets / "style.css")
    shutil.copyfile(STATIC / "favicon.svg", assets / "favicon.svg")
    shutil.copyfile(SOCIAL, images / "social-card.png")
    shutil.copyfile(EDITOR, images / "editor-overview.png")

    built = pages()
    for page in built:
        slug = page["slug"]
        target = OUT / "index.html" if slug == "" else OUT / slug / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(page), encoding="utf-8")
        print(f"{canonical(slug)}  {page['title']}")

    write_sitemap([page["slug"] for page in built])
    write_robots()
    validate(built)


def validate(built: list[dict[str, str]]) -> None:
    errors: list[str] = []
    html_paths = sorted(OUT.rglob("*.html"))
    if len(html_paths) != 5:
        errors.append(f"Expected 5 HTML files, found {len(html_paths)}")
    descriptions = [page["description"] for page in built]
    if len(set(descriptions)) != len(descriptions):
        errors.append("Descriptions are not unique")

    for page in built:
        slug = page["slug"]
        path = OUT / "index.html" if slug == "" else OUT / slug / "index.html"
        text = path.read_text(encoding="utf-8")
        for banned in BANNED:
            if banned in text:
                errors.append(f"{path.name} references {banned}")
        h1 = re.findall(r"<h1>(.*?)</h1>", text)
        if h1 != [html.escape(page["title"], quote=True)]:
            errors.append(f"{slug or '/'} h1 {h1!r} != title")
        if f'<title>{html.escape(page["title"], quote=True)}</title>' not in text:
            errors.append(f"{slug or '/'} title mismatch")
        if f'<meta name="description" content="{html.escape(page["description"], quote=True)}">' not in text:
            errors.append(f"{slug or '/'} description mismatch")
        if len(page["description"]) > 170 or len(page["description"]) < 80:
            errors.append(f"{slug or '/'} description length {len(page['description'])}")
        canon = canonical(slug)
        if f'<link rel="canonical" href="{canon}">' not in text:
            errors.append(f"{slug or '/'} canonical mismatch")
        if f'<meta property="og:image" content="{OG_IMAGE}">' not in text:
            errors.append(f"{slug or '/'} missing og:image")
        if f'<meta name="twitter:image" content="{OG_IMAGE}">' not in text:
            errors.append(f"{slug or '/'} missing twitter:image")
        if 'name="twitter:card" content="summary_large_image"' not in text:
            errors.append(f"{slug or '/'} missing twitter card")
        has_faq = '"@type": "FAQPage"' in text
        has_app = '"@type": "SoftwareApplication"' in text
        if slug == "":
            if not has_faq or not has_app:
                errors.append("Home page missing JSON-LD")
            for question, _answer in FAQ:
                if f"<h3>{html.escape(question)}</h3>" not in text:
                    errors.append(f"FAQ heading missing: {question}")
        elif has_faq or has_app:
            errors.append(f"{slug} unexpectedly includes home JSON-LD")
        if "<script src=" in text:
            errors.append(f"{slug or '/'} loads an external script")
        check_links(text, errors, slug or "/")

    robots = (OUT / "robots.txt").read_text(encoding="utf-8")
    if f"Sitemap: {SITE}/sitemap.xml" not in robots or "Allow: /" not in robots:
        errors.append("robots.txt is incomplete")
    sitemap = (OUT / "sitemap.xml").read_text(encoding="utf-8")
    for page in built:
        if f"<loc>{canonical(page['slug'])}</loc>" not in sitemap:
            errors.append(f"sitemap missing {page['slug'] or '/'}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        raise SystemExit(1)
    print(f"Validated {len(built)} pages, sitemap and robots.txt")


def check_links(text: str, errors: list[str], label: str) -> None:
    for href in re.findall(r'(?:href|src)="([^"]+)"', text):
        if href.startswith("#") or href.startswith("mailto:"):
            continue
        if href.startswith(PREFIX + "/") or href == PREFIX:
            path = href.split("#", 1)[0]
            if path.endswith("/"):
                rel = "index.html" if path == PREFIX + "/" else path[len(PREFIX) + 1 :] + "index.html"
            else:
                rel = path[len(PREFIX) + 1 :]
            if not (OUT / rel).is_file():
                errors.append(f"{label} missing local target {href}")
            continue
        if href.startswith("https://"):
            host = href.split("/", 3)[2]
            if host not in ALLOWED_HOSTS:
                errors.append(f"{label} unexpected host {host} in {href}")
            continue
        errors.append(f"{label} unexpected URL {href}")


if __name__ == "__main__":
    build()
