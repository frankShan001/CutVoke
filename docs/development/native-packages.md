# Native macOS and Linux distribution candidates

The [native package workflow](../../.github/workflows/unix-packages.yml) builds
macOS arm64 on `macos-15`, macOS Intel x64 on `macos-15-intel`, and Linux x64 on
`ubuntu-22.04`. These are independent native builds, not cross-compiled Python
environments. See the [user guide](../user/installation.md#macos-arm64--x64) for
downloads, storage and system/security requirements. No Release/tag is created.

## Build inputs and archive layout

Python 3.13.16, Electron 44.5.1 and the existing frontend/backend dependencies
remain fixed. The backend's binary-only, hash-checked lock adds macOS's required
`macholib`; Windows-only helpers retain platform markers. The frozen CLI works
both as the desktop service and as stdio MCP. The launcher chooses the native
engine filename rather than assuming `.exe`.

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
its owned backend and port terminate. A separate ordinary launch has no QA,
debugging, offscreen or sandbox-disable flags and is closed with platform window
controls. An isolated `chmod 0555` data-profile test inspects the native error
dialog, proves no backend was launched and verifies the existing database bytes
after acknowledgement. It never changes a personal data directory.

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

All recorded execution is hosted-runner automation. It does not claim human
acceptance on a Mac/Linux device, protected-folder permission prompts, native
picker/drag ergonomics, speaker/audio output, GPU acceleration, every effect or
every optional AI client/model.
