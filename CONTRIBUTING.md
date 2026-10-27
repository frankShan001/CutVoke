# Contributing to CutVoke

**English** · [简体中文](CONTRIBUTING.zh-CN.md)

Start with the editing problem and expected behavior. Discuss larger changes in an Issue before implementation. The [community tasks](docs/development/community-tasks.md) have bounded scopes and acceptance criteria; leave a comment before claiming one to avoid duplicate work. Bugs and feature requests can be written in English or Chinese.

Read [SECURITY.md](SECURITY.md) before reporting a vulnerability. Never put exploit details, credentials or private media in a public Issue.

## Development setup

Only sharing reusable settings from an AI-assisted edit? Use the [local effect module workflow](effects/README.md#create-while-editing-no-development-setup), which needs no development environment. Its exported ZIP excludes all user project content; do not attach project media, text, screenshots or archives. The setup below is for source/engine development.

Use Python 3.10+, Node.js 20+, FFmpeg and ffprobe. See [installation and tested environments](docs/user/installation.md) for runtime capabilities and troubleshooting.

```bash
git clone https://github.com/frankShan001/CutVoke.git
cd CutVoke
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
cd apps/editor
npm ci
npm run build
cd ../..
cutvoke doctor --json
```

## Checks

For backend changes:

```bash
python -m compileall -q src
python tools/verify/check_layering.py
python -m pytest -q
```

Resource installation changes must retain the staging/concurrency/cleanup
coverage in `tests/storage/test_resource_install_paths.py`. Model legacy path
limits deterministically and keep real Windows Python plus frozen-app deep-data
acceptance; runner registry/manifest support can otherwise hide a long-name bug.
Keep exclusive allocation, checksum verification and atomic same-directory
replacement. Preserve filesystem failure evidence instead of silently skipping.

For frontend or shared editing workflow changes, from `apps/editor`:

```bash
npm run typecheck
npm run test:i18n
npm run build
npx playwright install chromium
npm run test:e2e
npm run test:e2e:regressions
```

Browser tests create a temporary database and synthetic media. They do not use personal projects. On Windows they use installed Chrome; Linux can use an installed Chromium by setting `CUTVOKE_E2E_BROWSER=/usr/bin/chromium`. Keep the activated Python environment on PATH so the test server and MCP subprocess use the same dependencies. Test APIs must receive an explicit temporary `media_dir`; a file-backed `HttpApi` also derives its media directory from its database. `MCPServer(..., media_dir=...)` allows isolated embedding. Do not change HOME or relax filesystem permissions to run tests.

The regression subset repeats menu dismissal, both sides of the preview debounce, cancelled preparation, delayed native decoding, pending effect controls, mask gestures across delayed acknowledgements and German toolbar bounds across five viewport widths three times. Control timers and every competing readiness response when testing an ordering; automation speed is not a debounce guarantee. Scope form locators to the intended editor. Interactive-frame mocks must combine the saved project with pending commands: an empty commands array after save is valid. Playback tests must distinguish a decoded full preview from legitimate window handoffs: retain strict full-source identity and inspect actual audio element/time continuity across a decoded window boundary. Gate save acknowledgement before and after obsolete response delivery, and verify the same strict pixels in both cases. Fixed shells must not become scroll areas when native focus/reveal scrolling runs; check toolbar children as well as the outer header bounds. Independently executed effects get fresh instance IDs: compare their reference graph while retaining parameters, order and keyframe targets. Native package checks run on relevant pull requests as well as main pushes; report results for the exact tested SHA and wait for browser and all four native targets before calling a change verified.

For desktop dependency changes, run `npm ci --ignore-scripts`, `npm run test:build-deps` and `npm run test:i18n` in `apps/desktop`. The proxy test uses disposable loopback servers. Downloader overrides live in `apps/desktop/package.json`; preserve the actual downloader's API and proxy support when changing them.

Rendering changes need real media, inspected frames and relevant audio checks. Record the input, command and result in the PR; successful process exit alone does not establish correct pictures. Do not weaken pixel, alpha, duration or frame-count assertions to make an incompatible build pass.

Native playback samples must follow a submitted compositor frame, not just a
readyState value or an advancing audio clock. Preserve the strict pixel check
on that first submitted frame; never poll until a nonblack picture appears.
Keep controlled frame-submission delay and submitted-black-frame coverage in
the shared acceptance-helper browser tests.

Keyframe selection and mutation must wait for both command completion and the
refreshed editor snapshot. A server revision or API node count alone does not
prove UI readiness; gate the compact project sync response and retain exact
multi-node timing, selection and single-command undo/redo assertions.

Timeline actions after reorder or undo must verify clip identity together with
position in the backend and the rendered snapshot before reading drag geometry.
Sorted start times can remain identical when clip order changes. Use stable clip
IDs and control delayed sync delivery; preserve exact movement and independent
audio assertions.

## Engine boundaries

1. Keep project time as `Rational`; floats belong at display/FFmpeg boundaries.
2. Send project writes through `EditService.execute` across Web, HTTP, MCP and CLI.
3. Write commands require a revision and command ID. Reject conflicts and keep replay idempotent.
4. SQLite is the cross-process authority. Do not trust an old in-memory snapshot.
5. Preview and export must share compositing semantics.
6. Agents use editing leases for grouped changes; humans can observe during the lease and edit after handoff.

Core modules must not import CLI, HTTP or MCP adapters. `check_layering.py` enforces that boundary.

New commands need a registered service handler, input schema, clear errors, persistence and undo/redo support. Exercise success, invalid arguments, revision conflicts and duplicate IDs through the relevant public interfaces.

## Effects, translations and docs

Use the [effect contribution guide](effects/README.md) ([中文](docs/user/effect-contributing.zh-CN.md)) and runnable sample. Include the effect ID, parameters, implementation, preview/export evidence, attribution and license. Manifests can reuse supported algorithms; new algorithms need implementation and tests. Manifests do not execute arbitrary external code.

Use the [translation guide](docs/development/translations.md) ([中文](docs/development/translations.zh-CN.md)) for message keys, placeholders, RTL and resource metadata. Preserve API identifiers and user project content. Existing translations still need native-speaker review.

Keep documentation about current behavior. Update both README languages when a shared workflow changes. Avoid internal acceptance IDs, test-count marketing and unconfirmed roadmap promises.

## Pull requests and files

Use a focused PR with the problem, resulting behavior, validation commands and compatibility/license implications. Include inspectable screenshots or short original-media samples for visual changes. Conventional Commit messages such as `fix: preserve queued export revisions` are welcome.

Follow the [project layout](docs/development/project-layout.md). Put generated logs, media, caches and test databases under ignored `.artifacts/`; keep personal projects and footage outside the repository. Never commit credentials, dependency folders or local build outputs.

Applications live in `apps/editor` and `apps/desktop`. The Python engine is split into `domain`, `editing`, `rendering`, `storage` and `interfaces`, with corresponding test groups. Run `python tools/verify/check_repository_layout.py` to catch obsolete imports and build paths.

Keep reproducible artwork inputs and provenance in `resources/source/` and published documentation images in `docs/images/`. Personal development journals, one-off acceptance reports and review screenshots belong outside the repository. Runtime resource metadata and third-party notices remain part of the product. Follow the [repository content policy](docs/development/repository-content.md) and run `python tools/verify/check_asset_layout.py` before submitting.

The [candidate packaging workflow](docs/development/prerelease.md) builds and verifies source/wheel artifacts without publishing a Release. Windows installers require separate Windows acceptance and third-party binary/license review.
