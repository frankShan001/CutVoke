# Installation and troubleshooting / 安装与排障

[English README](../../README.md) · [中文 README](../../README.zh-CN.md) · [Contributing](../../CONTRIBUTING.md)

## Install from source

Use Python **3.10+**, Node.js **20+**, and `ffmpeg` plus `ffprobe` on PATH. FFmpeg must include `libx264`, AAC, overlay, drawtext and subtitles/libass. Optional ASR/person features need their own dependencies and model preparation; they are not required for the basic editor/demo.

```bash
git clone https://github.com/frankShan001/CutVoke.git
cd CutVoke
python -m venv .venv
```

Activate with `source .venv/bin/activate` on Linux/macOS, or `.\.venv\Scripts\Activate.ps1` in Windows PowerShell. Then:

```bash
python -m pip install -e .
cd web/app
npm ci
npm run build
cd ../..
cutvoke doctor --json
python skills/cutvoke-editor/scripts/demo.py --output .artifacts/first-edit
cutvoke serve --data .artifacts/first-edit/data
```

Use a new output directory for each demo. Open `http://127.0.0.1:8787` and select the demo project. The MCP process, HTTP service and desktop must all use the same data directory. `--data` takes precedence over `CUTVOKE_DATA`; the CLI default is `~/.cutvoke/data`. No HOME override or special filesystem permission is needed when a writable `--data` directory is provided.

## Tested environments and limits

| Environment | Evidence and scope |
| --- | --- |
| Linux cloud, Python 3.12.14, Node 24.19.0, Chromium 154.0.8037.57, Debian FFmpeg 7.1.5 | Real stdio MCP import, Web availability/thumbnails, manual caption editing, playback, GUI MP4 export and full decode; the 12-second community-transition demo also completes preview/export/package and audio measurements |
| Ubuntu 24.04 CI, Python 3.10 / 3.12 / 3.13, Node 24, apt FFmpeg | The [CI workflow](../../.github/workflows/ci.yml) runs backend regressions, browser editing and installed-wheel checks; use the status for the exact commit being installed |
| Earlier Windows source workflow | Previously exercised source/desktop development path; the current cloud run is not a new Windows installer acceptance result |
| macOS, other FFmpeg/platform builds | Not verified by this cloud run; record an actual machine/build before claiming support |

FFmpeg 6.1 remains part of the existing Ubuntu regression path. FFmpeg 7.1's `setpts` clears frame-rate metadata: the renderer restores CFR after retiming and uses the explicit sequence rate when assembling animation frames. Upgrading FFmpeg to hide an invalid graph is not required. A distribution's version number alone does not guarantee the required codecs or filters.

The project version is aligned at **0.3.1** in Python, MCP and desktop metadata. This does not mean a public 0.3.1 binary Release exists. Wheel/source candidates are built locally; see [candidate packaging](../development/prerelease.md) for checksums and validation before any publication.

## Troubleshooting

| Symptom | Check and action |
| --- | --- |
| `ffmpeg` or `ffprobe` missing; codec/filter failures | Run `cutvoke doctor --json`, `ffmpeg -version` and `ffprobe -version` in the activated terminal. Install a suitable build, confirm PATH, then restart the editor's own service. Preserve the exact error if a filter is absent. |
| Permission error under `~/.cutvoke` | Use `cutvoke serve --data .artifacts/my-edit/data` and `cutvoke mcp --data .artifacts/my-edit/data`. Keep this directory writable and identical across interfaces. For tests, pass a temporary `media_dir`; do not change HOME or weaken permissions. |
| Agent-imported media missing in Web | Update the code and restart both processes on the same database. New imports use `media/<assetId>.<ext>`; exact legacy `media/imports/<sha256>.<ext>` entries remain accessible without moving files. Truly missing media still needs relinking to the intended source. |
| `xfade` says `1/0` is invalid | Restart the updated service, then reproduce with the same project and identify the FFmpeg build. The known `setpts`/CFR issue is fixed in the renderer. Report a small reproducer if the updated graph still fails; do not swap binaries without recording that difference. |
| `npm ci` downloads fail | The lockfiles use npm's official registry and preserve package integrity hashes. Check the failing URL, proxy and network access; do not disable TLS or discard the lockfile to obtain a different dependency graph. |
| Blank editor or doctor reports missing Web assets | Run `npm ci && npm run build` inside `web/app`, then restart the service. Wheels include the built editor; Node is needed to build from source, not to run the installed wheel. |
| Port occupied or an old build still responds | Use `cutvoke doctor --json` and `/api/v1/runtime`; stop only the service launched for that workspace, or select another `serve --port`. Rebuild/restart after code changes. |
| Browser-test download blocked | With a trusted installed browser, set `CUTVOKE_E2E_BROWSER=/usr/bin/chromium` and keep the Python environment on PATH. Otherwise install Chromium with Playwright in `web/app`. |
| Optional speech/person processing unavailable | Follow [ASR setup](LOCAL_ASR.md) or [local setup](LOCAL_DELIVERY_MCP.md); model downloads and their licenses are separate from the basic install. |

## 简体中文

源码安装需 Python 3.10+、Node.js 20+ 和具备所需编解码器/滤镜的 FFmpeg、ffprobe。先激活虚拟环境，再安装 Python 包、运行 Web 的 `npm ci` 和构建，最后执行 `cutvoke doctor --json`。

云端已实际检查 Linux 上的 MCP 导入、人工接手、预览和导出；没有用 Linux 代替 Windows 安装包或 macOS 实机验收。默认个人数据目录只读时，给 Web 和 MCP 指定同一个可写 `--data` 即可，不要伪造 HOME 或扩大权限。上表分别列明已修问题、环境问题与仍需实机检查的边界。
