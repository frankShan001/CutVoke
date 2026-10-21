# Build effects with CutVoke

[中文贡献指南](../docs/user/effect-contributing.zh-CN.md) · [General contributing guide](../CONTRIBUTING.md)

Useful effects can come from ordinary AI-assisted editing. Save reusable settings locally, try them in the current edit, and prepare a contribution only when you want to share.

## Create while editing: no development setup

With a connected AI editor, ask: **"Save this treatment as a reusable effect module. When finished, prepare a settings-only contribution package."** MCP onboarding and `editor_help(topic="sharing")` supply the workflow and mandatory privacy wording to the agent.

1. `effect_module_list(includeSchemas=true)` discovers the exact content-free built-in schemas. `effect_module_save(recipe=...)` saves a reusable recipe beside the current database, without reading the project. There is no type-directory setting or restart.
2. `command_preview` / `command_apply` with `type="effectModule.apply"`, `payload={clipId,moduleId}` applies it to an existing clip using the current revision and editing lease. It supports undo/redo and remains manually editable. `effects` combines existing operators; `motion` uses normalized clip time (`at=0..1`, x/y fractions of the canvas); `textStyle` preserves the title's existing text.
3. `effect_module_export(moduleId=...)` writes a local ZIP, without uploading. `effect_module_import(path=...)` lets another installation reuse the exported recipe. Save a changed recipe to get a new content ID.

Example `effect_module_save` arguments:

```json
{"recipe":{"effects":[{"effectId":"cutvoke.fx.vibrance","params":{"intensity":0.4}}],"motion":[{"param":"opacity","at":0,"value":0.2},{"param":"opacity","at":0.25,"value":1}]}}
```

**Privacy is enforced by the package format.** It permits only supported built-in effect IDs, validated numeric/boolean/enum/color parameters, normalized motion and a limited set of text style fields. It rejects free text, captions, media/resource paths, arbitrary code and unknown fields, including inside parameters. Some content-dependent effects are consequently unavailable in this format; consult the returned schemas. New algorithms still require an implementation PR.

The exporter reconstructs exactly four files under `effects/recipes/<content-id>/`: `module.json`, generated `README.md`, MIT `LICENSE`, and `PRIVACY.txt`. It never packages a directory, project, source file or project preview. It revalidates stored settings and their content hash; importing also rejects added files or changed documentation. Local modules live in `effect-modules/` beside the database (the default is `~/.cutvoke/data/effect-modules/`). Modules are separate from project packages and remain available after a project is removed.

Whenever an AI suggests sharing or delivers the package, it must append the returned notice verbatim in the user's language:

> This effect package contains only effect settings and app-generated generic documentation. It contains no personal information, project videos, images, audio, text or captions, project files, file paths or editing history. Preparation is local; nothing has been uploaded. Public submission requires your explicit consent.

This guarantee covers the **unmodified app-generated ZIP**. Do not add screenshots, renders, logs, prompts, original media, personal details or project archives to it or to a PR. Public submission is a separate action requiring explicit user consent. Extract the four generated files into the repository to propose a recipe contribution; run `python tools/verify/validate_effect_contributions.py`. Review and rendering validation still apply: use synthetic media and fixed generic text for any evidence, never the user's project. A structurally valid package does not certify the visual result.

## Develop new rendering behavior

The following manifest workflow is for contributors extending engine behavior or its existing mappings. It does not apply to the settings-only module workflow above.

The runnable starting point is [Soft Reveal](transitions/cutvoke.transition.community.soft-reveal/README.md), a restrained dissolve backed by the existing FFmpeg `fade` algorithm. It demonstrates author metadata, parameter validation, local discovery, actual preview and export. It is an example configuration of an existing algorithm, not a new renderer.

## Storage and implementation boundaries

New contributions live in `effects/<family>/<effect-id>/`. A contribution folder holds its manifest, description, license, local preview, and any small licensed source assets. Avoid moving current built-in resources: they retain their existing paths under `src/cutvoke/assets/` and the existing preset/sticker registries.

| Contribution family | Folder | Existing route / current boundary |
| --- | --- | --- |
| Transitions | `effects/transitions/<id>/` | `category:transition`, `ffmpeg-xfade`; supported FFmpeg transition mappings can run from a manifest |
| Visual effects and filters | `effects/video/`, `effects/filters/`, `effects/color/` | `category:fx`, existing internal filter mappings; match the renderer's actual parameter semantics and prove preview/export |
| Audio effects | `effects/audio/<id>/` | `category:fx`, existing audio mappings such as equalizer/compressor; needs an audio source and listening evidence |
| Clip animations | `effects/animations/<id>/` | `category:animation`; supported internal kinds only, with existing animation slots/ID conventions; an arbitrary new kind needs a core PR |
| Text behavior | `effects/text/<id>/` | Storage convention for reviewed contributions; current text and caption behavior comes from existing commands/renderers, not arbitrary manifest code |
| Transform behavior | `effects/transforms/<id>/` | Existing transform semantics; a manifest alone cannot introduce a new geometry algorithm |
| Stickers, backgrounds, presets, LUTs, fonts | Contribution-local source assets, then maintained resource registries | Use the [existing resource-pack route](../docs/user/resource-pack-registry.md); installing an effect manifest does not automatically add a preset-gallery card |

The transition folder contains Soft Reveal and [Shadow Ink](transitions/cutvoke.transition.community.luma-ink/README.md), a content-luma recipe backed by the tested Vlog renderer. Other folders remain conventions for future PRs, not claims of a complete external SDK for every family. If a proposed behavior needs code, place implementation and tests in the existing core/frontend modules through the normal PR process. Do not ship scripts, native binaries or arbitrary FFmpeg expressions for the manifest loader to execute.

## Manifest contract

Use [the runnable manifest](transitions/cutvoke.transition.community.soft-reveal/manifest.json) as the template. The actual loader is `src/cutvoke/rendering/effects.py`; consult `EffectSpec.from_manifest`, schema validation and the runtime `effects_list` / `editor_help` rather than maintaining a parallel schema.

- Give a stable ID with a contributor subnamespace. For transitions, use `cutvoke.transition.<contributor>.<slug>`: the current editor and `effect.setTransition` recognize that prefix. Do not reuse a built-in ID; the registry rejects duplicates and this workflow does not enable overrides. Other kinds must follow their current renderer/editor conventions and be verified explicitly.
- Include semantic `version`, English/Chinese `name` and `description`, `author.name`, license, input/output kinds and `appliesTo`.
- Add translated `name` and `description` entries for other interface locales when available. The registry uses the requested locale and falls back to English; keep IDs and category keys stable. See the [translation guide](../docs/development/translations.md) and multilingual sample. State whether translations have received native review.
- Set `category` to the engine category in the table. `subcategory` and `keywords` help discovery; browse categories are distinct from engine categories.
- Declare a parameter object schema with meaningful limits/defaults and `additionalProperties:false`. Only advertise animation on parameters the renderer actually animates. The schema validator implements a JSON Schema subset; do not assume unsupported schema keywords enforce constraints.
- Generic effect and transition controls accept localized `x-cutvoke-label`, positive numeric `x-cutvoke-step`, and `x-cutvoke-enum-labels`. Transition trial rendering uses the authored parameter draft, not only duration.
- `implementation` names an existing supported engine/filter/kind. For the sample, `ffmpeg-xfade` + `xfade` + `fade` is the implementation. There is no Python/JavaScript module or shader plugin entry point.
- Include `x-cutvoke-min-core` and `x-cutvoke-contribution.testedWith`, plus a `previewFile` pointing to a PNG in the same folder. These fields aid review. The loader stores minimum-core metadata but does not enforce version negotiation; author/review metadata and `previewFile` do not add a gallery card or compatibility gate.

The contribution validator adds review requirements on top of the existing loader. It does not replace the loader or prove rendering/artistic quality.

## Develop and preview locally

For manifest/core development, install the environment from [CONTRIBUTING.md](../CONTRIBUTING.md). From the checkout, enable a **category** directory before starting the editor and MCP engine:

```powershell
# Windows PowerShell, with .venv activated
$env:CUTVOKE_EFFECTS_PATH = (Resolve-Path effects/transitions).Path
cutvoke effects list --category transition
```

```bash
# POSIX shell, with .venv activated
export CUTVOKE_EFFECTS_PATH="$PWD/effects/transitions"
cutvoke effects list --category transition
```

The loader scans `<path>/*/manifest.json`, not nested family trees. For multiple category directories, join absolute paths with the platform path separator (`;` on Windows, `:` on POSIX). It also reads `~/.cutvoke/effects/<id>/manifest.json`. This contribution checkout is opt-in; its contents are not auto-installed or bundled into a release. Restart/reconnect editor and MCP processes after a manifest change; the process caches its registry.

Run the actual sample through stdio MCP in a new isolated directory:

```bash
python tools/verify/validate_effect_contributions.py
python -m pytest -q tests/integration/test_effect_contributions.py
python skills/cutvoke-editor/scripts/demo.py --output .artifacts/community-demo --effects-dir effects/transitions --effect-id cutvoke.transition.community.soft-reveal
cutvoke serve --data .artifacts/community-demo/data
```

Open `http://127.0.0.1:8787`, select **Trail Notes · editable demo**, and inspect the incoming **lake** or **dusk** clip's transition. The shell setting above must also be present for the editor service to discover the sample. `demo.py` generates its own media; it does not open a user's project library. It leaves an MP4, `.cvkpkg`, editable database, rendered frames and verification report. Use a new `--output` directory on each run to preserve earlier results.

For your own effect, adapt the test edit to its applicable media and parameters. Check minimum/default/maximum and invalid parameters, adjacent cuts or timing limits, an actual preview, and a complete final export. New algorithms require implementation coverage in addition to manifest checks. No production gallery/marketplace, automatic publication, general hot reload or dynamic-code execution is provided by this contribution path.

## Submit a GitHub PR

Fork the repository, work on a branch, and open a PR with the contribution folder plus only the necessary implementation/tests. Include:

1. The user benefit, before/after or sample frames, and a short motion preview when the effect's behavior depends on time.
2. The exact local commands, sample input provenance, tested engine/FFmpeg versions, parameter bounds and applicable media. Avoid personal paths and private filenames in published evidence.
3. Code/data/asset licenses and origin links. Use original or redistributable assets; retain attribution and license notices. Do not commit confidential footage, keys, raw personal recordings, downloaded executables or unverifiable rights claims.
4. Behavior on missing resources, unsupported mappings and invalid parameters. State limitations instead of advertising untested compatibility.

Maintainers review usefulness, readable defaults, provenance, bounded parameters, naming/collision safety, project round-trip behavior, undo/redo, preview/export consistency, and effects on existing projects. A PR may request revisions or a smaller scope. Merge/release remains a maintainer action; no PR bot auto-accepts effects.

Source code uses the repository's MIT license. Contributor assets need their own accurate licensing; FFmpeg is an external dependency with its own build-specific licensing obligations.
