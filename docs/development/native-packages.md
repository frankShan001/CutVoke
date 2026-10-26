# Native macOS and Linux distribution candidates

Current downloads are in the [v0.3.2 prerelease](https://github.com/frankShan001/CutVoke/releases/tag/v0.3.2). Use its [SHA256SUMS](https://github.com/frankShan001/CutVoke/releases/download/v0.3.2/SHA256SUMS) and [BUILD-INFO.json](https://github.com/frankShan001/CutVoke/releases/download/v0.3.2/BUILD-INFO.json) for the exact archive bytes, source commit and acceptance runs. The 0.3.1 delivery records below are historical and do not verify 0.3.2.

## Historical 0.3.1 delivery evidence

The [native package workflow](../../.github/workflows/unix-packages.yml) builds
macOS arm64 on `macos-15`, macOS Intel x64 on `macos-15-intel`, and Linux x64 on
`ubuntu-22.04`. These are independent native builds, not cross-compiled Python
environments. See the [user guide](../user/installation.md#macos-arm64--x64) for
downloads, storage and system/security requirements. This build workflow does not publish a Release. The historical accepted archives remain available in the [v0.3.1 prerelease](https://github.com/frankShan001/CutVoke/releases/tag/v0.3.1), after independent full-byte/hash verification during [staging run 38998824276](https://github.com/frankShan001/CutVoke/actions/runs/38998824276). That tag points to `58f890090463300c007f541ac36be8bca71bb4e9`; those binaries retain the build commit below.

The **2026-10-16** delivery was built from
`fdec479c4acbc3c685a5c8adbe8ec91621a7c194`. All three native jobs in
[38845496409](https://github.com/frankShan001/CutVoke/actions/runs/38845496409)
and the Windows regression in
[38845496400](https://github.com/frankShan001/CutVoke/actions/runs/38845496400)
passed. The [verification record](native-packages-verification.json) records
each inner archive's bytes/SHA256, outer artifact digest, direct download,
evidence artifact, actual runner OS and acceptance checks. This records
functional acceptance separately from the explicitly rejected Mac Gatekeeper
assessment described below.

The same code's [ordinary CI 38845496430](https://github.com/frankShan001/CutVoke/actions/runs/38845496430)
also passed all four jobs: Python 3.10, 3.12, 3.13 and the complete browser editing
workflow. A later documentation-only commit does not alter these archived
package bytes; use that commit's own CI status for the final source checkout.

## Build inputs and archive layout

Python 3.13.16, Electron 44.5.1 and the existing frontend/backend dependencies
remain fixed. The backend's binary-only, hash-checked lock adds macOS's required
`macholib`; Windows-only helpers retain platform markers. The frozen CLI works
both as the desktop service and as stdio MCP. The launcher chooses the native
engine filename rather than assuming `.exe`.

Cryptography 50 no longer publishes Intel macOS wheels. Intel uses the same
50.0.2 version compiled from its SHA256-pinned sdist, linked to a locally built
static OpenSSL 4.0.3 with a macOS 13 deployment target. Providers are compiled in
(`no-module`), so no external OpenSSL provider directory is needed. Its PEP 517 build backend
and Python build dependencies are hash-locked; Rust dependencies use the
upstream Cargo.lock. The wheel is hashed before installation and an Ed25519
sign/verify smoke check and provider-availability assertion run before freezing. Corresponding OpenSSL/cryptography
sources, Rust vendor sources/notices, the exact recipe and compiler information
are included. No Homebrew OpenSSL path or Rust compiler is required at runtime.
Upstream now tests macOS on ARM only; the Intel boundary is CutVoke’s own
resource-signature smoke check and packaged application acceptance, not the full
upstream cryptography test matrix. See [upstream static build instructions](https://cryptography.io/en/50.0.2/installation/#building-cryptography-on-macos).

`prepare_desktop_unix.py` verifies official Electron archives against checksums
from its locked npm package, then extracts with permissions and symlinks intact.
`build_ffmpeg_unix.sh` uses the same seven SHA256-pinned upstream sources as the
Windows build. Codec/font libraries are statically linked; operating-system
frameworks/libc remain system dependencies. Autodetection of unrelated runner
libraries is disabled. H.264/AAC, libass, drawtext, bundled fonts and the existing
offline resource qualification gates remain required. No nonfree option is used.

Every download retains GPLv3 FFmpeg corresponding source, the exact native build
recipe, source hashes, configure log, compiler information and component notices.
The MIT CutVoke source license does not describe the whole aggregate. Python,
OpenSSL/runtime, frontend, Electron/Chromium and Noto notices are retained, along
with the existing reviewed-resource bytes. Build metadata includes Python and
cryptography's actual OpenSSL versions.

The shared assembler keeps Windows's folder ZIP, uses `ditto` for a macOS ZIP
containing `CutVoke.app`, and a Linux `.tar.gz` directory archive. This preserves
the executable bits and framework/PyInstaller symlinks that ordinary file-only
ZIP writers lose. A per-file SHA256 manifest, build commit, dependency metadata,
archive bytes and archive SHA256 accompany each download. Mac licenses and build
metadata sit outside the signed `.app` so assembly does not invalidate it.

Pull requests run the same extracted-archive acceptance as main builds. The
workflow seals each newly built Mac app with `codesign --sign -` and verifies it
strictly before assembly, because electron-builder otherwise skips signing in
PR environments. This local integrity seal uses no certificate or keychain and
does not establish a publisher identity. Archive provenance comes from the
checked-out Git HEAD; the PR event's `GITHUB_SHA` can refer to a synthetic merge.

## Actual download acceptance

`verify_portable_unix.cjs` extracts the exact user archive into a Chinese/space
path and checks its hash and file manifest. The application's PATH is an empty
directory, and the frozen entry prepends only its bundled FFmpeg directory. The
test infrastructure can still use its own Python/Node tools; those tools are not
installed by, or exposed on PATH to, the product.

Before GUI acceptance, the extracted frozen `doctor` must pass and resolve
FFmpeg/ffprobe inside the download. Optional ASR/person modules must be absent.
`verify_unix_runtime.py` inspects every native image and symlink: architecture,
executable bits, complete contained symlinks, macOS deployment versions/system
framework references, or Linux `ldd` and maximum required GLIBC symbols (2.35).
For Mac it also verifies the entire ad-hoc signature and records the publisher
identity and Gatekeeper assessment separately from functional execution.

Playwright then drives a visible native Electron window with the renderer
sandbox enabled, verifies the frozen service identity, eleven languages, the
existing offline resource manifest and all preset/sticker qualifications, GUI
media import, shared stdio MCP editing, actual preview video playback, GUI MP4
export and full H.264/AAC decode. The synthetic input is generated with the
download's FFmpeg. Outbound browser requests are blocked during editing.
Screenshots and an exported frame/MP4 remain inspectable evidence.

The same run occupies the default port, launches a second instance, checks
project/language persistence on restart, closes the window and verifies that
its owned backend and port terminate. Closing during the loading phase is also
checked so a canceled startup cannot launch an orphan backend or hold an error
dialog open during quit. A separate ordinary launch has no QA,
debugging, offscreen or sandbox-disable flags and is closed with platform window
controls. A separate launch from an entirely read-only application tree verifies the
real macOS default and Linux XDG data paths. An isolated `chmod 0555` data-profile test inspects the native error
dialog, proves no backend was launched and verifies the existing database bytes
after acknowledgement. Linux ties the visible X11 window to the launched
process, captures the actual GTK dialog and checks its error message with OCR;
macOS uses its native accessibility interface. These are test tools, not product
runtime dependencies. It never changes a personal data directory.

The workflow uploads user downloads only after successful acceptance; evidence
is retained even for failures. Native `.app`/tar archives are nested inside the
Actions download so Actions itself cannot flatten their executable modes/links.
The existing Windows workflow remains independent and exercises its real ZIP,
normal startup, `asInvoker` manifests and Windows ACL failure behavior.

## Security and compatibility boundaries

Mac bundles have ad-hoc integrity signatures only: no Developer ID, signing
credentials or notarization. This is needed for local native code integrity on
Apple Silicon, but does not authenticate a publisher to Gatekeeper. A rejected
`spctl` assessment is recorded honestly. A functional CI launch of an app that
was built locally is not a quarantined internet-download/Finder acceptance. No
quarantine or Gatekeeper settings are changed. Warning-free user launch remains
blocked on future proper publisher signing/notarization resources.

The declared Mac minimum is macOS 13 (Electron 44); native execution is checked
on macOS 15, not every supported macOS version. Linux is built/tested on Ubuntu
22.04 x64, with GLIBC 2.35 as the audited floor and normal desktop libraries as
system requirements. It uses the system's unprivileged user namespace sandbox,
not a privileged installed helper or `--no-sandbox`. Ubuntu 24.04 AppArmor policy,
other distributions, Wayland-only sessions, root, noexec mounts and driver/device
behavior need separate treatment. No global security policy is changed.

Mac data defaults to `~/Library/Application Support/CutVoke/`; Linux defaults to
`${XDG_DATA_HOME:-~/.local/share}/CutVoke/`. Both keep data outside the application,
including logs, font caches and generated MCP configuration. `CUTVOKE_HOME` and
`CUTVOKE_DATA` allow explicit writable locations. Windows portable storage keeps
its existing sibling directory behavior. A failed write never silently creates
an empty replacement library. Normal OS file permissions still apply.

Resource installs reserve short independent temporary basenames exclusively in
the destination directory, verify their bytes and atomically replace the final
file. A long sticker basename must not be repeated in its staging filename.
Filesystem failures retain the operation stage, path lengths (UTF-16 code units),
errno and winerror in the sticker API's `installationError` and engine log. If the
final path itself exceeds the process/filesystem limit, choose a shorter portable
data location (`CUTVOKE_PORTABLE_HOME`); changing Windows system policy is not a
required workaround.

Windows acceptance includes deterministic legacy-boundary tests under real
Python and fresh packaged-app profiles. It checks every sticker's availability,
preview availability and image/preview SHA-256. A controlled profile has 36 old
staging paths at or above 260 characters while all final paths fit. A second
profile measures final paths beyond 260 and requires useful diagnostics where
unsupported. Evidence records read-only LongPathsEnabled policy, both Python and
frozen EXE manifest declarations, actual host-Python boundary opens and frozen
install results separately: 260 characters is not a universal failure boundary.

All recorded execution is hosted-runner automation. It does not claim human
acceptance on a Mac/Linux device, protected-folder permission prompts, native
picker/drag ergonomics, speaker/audio output, GPU acceleration, every effect or
every optional AI client/model.
