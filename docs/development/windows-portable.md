# Windows x64 portable build

The current Windows ZIP is published in the [v0.3.2 prerelease](https://github.com/frankShan001/CutVoke/releases/tag/v0.3.2), rebuilt from its tagged source commit. Exact source and acceptance runs are recorded in [BUILD-INFO.json](https://github.com/frankShan001/CutVoke/releases/download/v0.3.2/BUILD-INFO.json), with archive hashes in [SHA256SUMS](https://github.com/frankShan001/CutVoke/releases/download/v0.3.2/SHA256SUMS).

For the current cross-platform delivery, see the release metadata above and
the [installation guide](../user/installation.md). The record below preserves
the original Windows baseline rather than replacing its historical evidence.

The historical [0.3.1 candidate download](https://github.com/frankShan001/CutVoke/actions/runs/38788916823/artifacts/11999831137) is available from successful Windows run **38788916823**, built from **1fd2f1fcb48c2c9af54555c558e40354812fc786**. See the [durable verification record](windows-portable-verification.json) and [user download/storage guide](../user/installation.md#windows-portable-x64). This is an unsigned Actions candidate, not a formal Release/tag.

The `Windows portable` workflow produces a folder ZIP containing `CutVoke.exe`,
Electron, a PyInstaller backend, the built Web editor, product assets, Noto fonts,
FFmpeg and ffprobe. It does not create an installer or publish a Release/tag.
Successful runs upload the user download as `CutVoke-windows-x64-portable` and
evidence as `windows-portable-evidence` (90-day Actions retention; GitHub login
may be required). A failed acceptance does not upload a user download.

## Build and provenance

- Electron 44.5.1 is verified against the checksum shipped by its official npm
  package. Desktop/frontend npm trees use their committed integrity lockfiles.
- Python 3.13.16 is provided by `actions/setup-python`. The isolated backend
  environment uses `apps/desktop/requirements-build.lock` with fixed versions,
  SHA256 hashes and binary-only installs. Build tools and optional AI packages
  are not copied into the product folder.
- FFmpeg 7.1.5 is built from exact upstream source commits in
  `apps/desktop/ffmpeg-sources.json`. Every input archive has a pinned SHA256.
  `tools/build/build_ffmpeg_windows.sh` cross-compiles Windows x64 static
  FFmpeg/ffprobe with libx264, libass, FreeType, HarfBuzz, FriBidi and zlib.
  It excludes nonfree options. The resulting standalone tools use GPLv3;
  CutVoke invokes them as separate processes.
- The ZIP includes `licenses/ffmpeg/Corresponding-Source.zip`: all seven exact
  unmodified upstream source archives, their hashes, the complete build recipe,
  configure log and compiler version/package information. Library notices,
  Python/frontend notices, Electron/Chromium notices and Noto OFL/provenance
  are retained. Read `START-HERE.txt` and the component licenses before further
  redistribution. The aggregate is not described as entirely MIT.
- Product resources retain the sidecars and audited exports pinned by the
  runtime resource manifest: the existing qualification gate needs them.
  Unreferenced development evidence, retired title previews, development
  environments, user projects and private media are excluded. The assembler
  checks x64 PE headers and emits a per-file SHA256 manifest, build commit,
  dependency versions and outer ZIP SHA256/size.
- No signing keys are generated or added. The candidate is **unsigned** and
  SmartScreen behavior is not guaranteed. No installer/registry/PATH setup is
  required by the portable application.

## Storage and verification

`portable.json` next to the executable enables a sibling `CutVoke Data` root.
The project database, uploaded media, preview cache and diagnostics live under
`data/`; language preferences, Chromium profile and MCP configuration live under
`profile/`. Generated caption fonts are cached in `data/font-cache/`, and the
generated MCP config points its engine at that same cache.
`CUTVOKE_PORTABLE_HOME` explicitly overrides the whole data root;
`CUTVOKE_DATA` explicitly overrides the database. A write failure displays a
bilingual native dialog and exits; there is no automatic empty-library fallback.
The launcher never copies a test database over an existing project library.
Normal transient renderer work can also use Windows' temporary directory.
Media references may be absolute; project packaging/relink remains the supported
way to transfer a project to another path/computer.

The Windows job extracts **the shipped ZIP** to a path containing Chinese and
spaces. It removes development runtimes from the application's PATH, runs the
bundled doctor, and drives the actual visible `CutVoke.exe` using Playwright's
Electron support. It verifies the frozen service identity, eleven language
selections, all 189 preset/612 sticker qualifications and the offline resource
manifest, GUI media upload, real stdio MCP on the same database, actual video
preview playback, GUI MP4 export and full H.264/AAC decode. It also occupies the
default port, starts a duplicate instance, restarts and checks persisted state,
closes the window and checks owned-backend cleanup, and applies an isolated
Windows deny-write ACL to inspect the real native storage failure dialog and
prove the existing database is unchanged.

Hashed JSON resources retain their original CRLF bytes using scoped Git
attributes. Cold-checkout tests verify the existing media/review hashes and
manifest without regenerating approvals or relaxing the qualification gate.

Screenshots, exported test MP4, FFmpeg version and JSON evidence are retained.
The synthetic source footage is generated on the runner with the shipped
FFmpeg. The browser's external traffic is blocked during editing; no AI client,
account, downloaded model or API key is used. Optional ASR/person functionality
is reported as absent, not advertised as part of basic offline editing.

This is automated Windows runner evidence. Human testing of file-picker/drag
ergonomics, device audio/GPU behavior, SmartScreen and different Windows 10/11
machines remains separate from that evidence.

## Local maintainer commands (Windows)

Use the exact Python/Node versions and verified FFmpeg output from the workflow.
Build `apps/editor`, freeze `apps/desktop/engine.spec` with `CUTVOKE_FFMPEG_DIR` pointing
to the verified tool folder, run `prepare_desktop_tools.ps1 -Portable`, then
`npm run portable` in `desktop`. Run `assemble_portable.py` using the isolated
backend Python and `node tools/verify/verify_portable_windows.cjs` from the
repository root. NSIS remains an independent legacy `npm run dist` target.
