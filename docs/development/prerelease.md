# Candidate packaging / 预发布准备

This workflow prepares inspectable artifacts. It does not create a tag, GitHub Release, upload public binaries, deploy or publish to PyPI. A maintainer must approve an actual artifact set and its supported platforms before publication.

## Source and wheel candidate

From an activated development environment with the Web editor already built:

```bash
python -m pip install build
python scripts/build/prepare_candidate.py --output dist/candidate-local
python scripts/verify/verify_distribution.py dist/candidate-local
```

Use a new output directory for each attempt. The script produces one `.tar.gz` source archive, one `py3-none-any.whl`, `VERIFICATION.json`, `BUILD-INFO.json`, a release-note draft and `SHA256SUMS`. The source includes the Web source/built editor, contribution/security docs, examples, AI Skill and build/verification tools. The wheel includes the built editor and runtime resources. Python, cryptography/fontTools and a compatible system FFmpeg/ffprobe remain required. The wheel tag alone is not proof of Windows/macOS acceptance.

`BUILD-INFO.json` records the Git SHA, dirty-worktree flag and tool versions. A dirty candidate is for local review only; rebuild from the approved clean commit before publication. Checksums detect byte changes, not publisher identity; these candidates are unsigned.

Verify on Linux/macOS from the candidate directory:

```bash
sha256sum -c SHA256SUMS
```

On Windows, use `Get-FileHash -Algorithm SHA256 <file>` and compare every deliverable against `SHA256SUMS`. A checksum failure blocks reuse of that candidate.

## Installed-package smoke check

Create a separate clean virtual environment, install the candidate wheel with its dependencies and run outside the source checkout:

```bash
python -m venv /tmp/cutvoke-wheel-check
/tmp/cutvoke-wheel-check/bin/python -m pip install /absolute/path/to/candidate/cutvoke-0.3.1-py3-none-any.whl
cd /tmp
/tmp/cutvoke-wheel-check/bin/cutvoke doctor --json
/tmp/cutvoke-wheel-check/bin/python /absolute/path/to/checkout/skills/cutvoke-editor/scripts/demo.py --output /tmp/cutvoke-candidate-demo
```

Use equivalent venv paths on Windows. Confirm that `cutvoke.__file__` resolves inside that new environment. Then launch its `cutvoke serve --data /tmp/cutvoke-candidate-demo/data`, open the browser and check available media, playback, a saved caption change and an export. Inspect pictures around each cut, verify stream size/rate/duration, fully decode MP4, check non-silent/unclipped audio and editable-package reimport. Do not infer subjective listening from audio measurements.

Also build a wheel from the source archive in a fresh directory, without the original checkout's `.artifacts/`:

```bash
python -m pip wheel --no-deps /absolute/path/to/candidate/cutvoke-0.3.1.tar.gz --wheel-dir /tmp/cutvoke-source-rebuild
```

The **Package candidate** manual Actions workflow performs these source/wheel checks and retains artifacts for review. It requests only read access to repository contents. It does not publish a Release. The normal CI still runs the supported Python matrix and browser workflow before an approved candidate is selected.

## Windows portable candidate

The Windows x64 **folder ZIP** is now a separately verified deliverable; see
[the user download guide](../user/installation.md#windows-portable-x64) and
[its originating build/evidence](windows-portable-verification.json). The
workflow builds fixed dependencies, retains third-party notices/corresponding
FFmpeg source, extracts the shipped ZIP and actually starts `CutVoke.exe` on
Windows. It validates GUI/health, offline resource integrity, stdio MCP,
import/preview/export/full decode, persistence, cleanup and read-only data
protection. It does not run an installer or modify global PATH/registry.
The unsigned Actions candidate is not a formal Release/tag. Human device,
native picker, GPU/audio and SmartScreen checks remain separate.

## Legacy Windows installer boundary

The existing `scripts/build/build-cutvoke-desktop.cmd` builds a Windows x64 Electron/NSIS installer using a PyInstaller engine. It bundles the discovered FFmpeg/ffprobe binaries. Its config currently disables executable signing; a generated `.exe` must be described as unsigned until a verified signing process exists.

Before offering that installer target, use an actual Windows host to build, install under a normal user, start, import through MCP, hand off to the GUI, preview/edit/export, restart, uninstall and verify project preservation. Record the installer SHA, Windows version/architecture, FFmpeg build, signature status and actual results. Portable ZIP evidence does not claim installer/uninstaller acceptance.

Inspect the exact FFmpeg build's license and enabled libraries before redistribution. Record upstream source/build attribution and required license/notices/source obligations for FFmpeg, Electron and any bundled assets. CutVoke's MIT license does not relicense third-party binaries. The source/wheel candidate above does not bundle FFmpeg; this avoids asserting that a particular FFmpeg binary is cleared for redistribution.

## Publication decision

Return the exact filenames and hashes, originating SHA/CI, tested platforms, outstanding platform checks, signature status and license restrictions to the maintainer. Recommend source + Python wheel first if only the Linux source/wheel path has been accepted. Create a GitHub pre-release or publish binaries only after that actual set is approved. No credentials or extra permissions are required for local preparation.

## 简体中文

先生成候选源码包/wheel、校验清单与版本说明草案，在干净环境安装并从源码包重建。候选包不是正式发行；`dirty:true` 的产物只供本地审查，公开前需从获准的干净提交重新构建。

Windows 便携 ZIP 已在实际 Windows runner 上自动验收，下载、准确哈希、提交和证据见上方专门入口；基础使用无需安装或配置 Python/Node/系统 FFmpeg。该候选带完整许可与对应 FFmpeg 源码，未签名，也未创建正式 Release/标签。传统安装包、macOS 和用户设备的人工体验仍需各自验收；正式 Release 需在核对具体成品后另行确认。
