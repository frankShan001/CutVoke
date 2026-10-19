# Build effects with CutVoke

[中文贡献指南](../docs/user/effect-contributing.zh-CN.md) · [General contributing guide](../CONTRIBUTING.md)

Help make the editor more expressive: contribute a useful transition, a thoughtful visual treatment, an audio effect, a readable text animation, or a reusable resource. Start with a GitHub PR and a small edit that shows why your contribution helps a creator.

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

Use [the runnable manifest](transitions/cutvoke.transition.community.soft-reveal/manifest.json) as the template. The actual loader is `src/cutvoke/core/effects.py`; consult `EffectSpec.from_manifest`, schema validation and the runtime `effects_list` / `editor_help` rather than maintaining a parallel schema.

- Give a stable ID with a contributor subnamespace. For transitions, use `cutvoke.transition.<contributor>.<slug>`: the current editor and `effect.setTransition` recognize that prefix. Do not reuse a built-in ID; the registry rejects duplicates and this workflow does not enable overrides. Other kinds must follow their current renderer/editor conventions and be verified explicitly.
- Include semantic `version`, English/Chinese `name` and `description`, `author.name`, license, input/output kinds and `appliesTo`.
- Add translated `name` and `description` entries for other interface locales when available. The registry uses the requested locale and falls back to English; keep IDs and category keys stable. See the [translation guide](../docs/development/translations.md) and multilingual sample. State whether translations have received native review.
- Set `category` to the engine category in the table. `subcategory` and `keywords` help discovery; browse categories are distinct from engine categories.
- Declare a parameter object schema with meaningful limits/defaults and `additionalProperties:false`. Only advertise animation on parameters the renderer actually animates. The schema validator implements a JSON Schema subset; do not assume unsupported schema keywords enforce constraints.
- Generic effect and transition controls accept localized `x-cutvoke-label`, positive numeric `x-cutvoke-step`, and `x-cutvoke-enum-labels`. Transition trial rendering uses the authored parameter draft, not only duration. See the [Vlog effects](../docs/user/vlog-effects.md) for a tested implementation and its limits.
- `implementation` names an existing supported engine/filter/kind. For the sample, `ffmpeg-xfade` + `xfade` + `fade` is the implementation. There is no Python/JavaScript module or shader plugin entry point.
- Include `x-cutvoke-min-core` and `x-cutvoke-contribution.testedWith`, plus a `previewFile` pointing to a PNG in the same folder. These fields aid review. The loader stores minimum-core metadata but does not enforce version negotiation; author/review metadata and `previewFile` do not add a gallery card or compatibility gate.

The contribution validator adds review requirements on top of the existing loader. It does not replace the loader or prove rendering/artistic quality.

## Develop and preview locally

Install the source development environment described in [CONTRIBUTING.md](../CONTRIBUTING.md). From the checkout, enable a **category** directory before starting the editor and MCP engine:

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
python scripts/verify/validate_effect_contributions.py
python -m pytest -q tests/test_effect_contributions.py
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
