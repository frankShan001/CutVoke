# Reusable Vlog effects

**English** · [简体中文](vlog-effects.zh-CN.md)

Four original effects run through CutVoke's existing FFmpeg preview/export compositor. They reuse `geq` for inverse camera sampling and `xfade` for two-shot timing and mixing. Their bounded schemas and sampling expressions are implemented in [`vlog_fx.py`](../../src/cutvoke/core/vlog_fx.py). No shader library, tracking model, paid asset or new runtime service is installed. These are four renderable capabilities, not four names for existing presets.

| Effect ID | What changes | Good input and main controls |
| --- | --- | --- |
| `cutvoke.fx.subjectLock` | A manual two-point source path is moved to a fixed target; the minimum crop zoom increases to keep every edge covered | A single subject moving approximately linearly, or along the optional smooth path. `fromX/Y`, `toX/Y`, `targetX/Y`, `start`, `duration`, `path`, minimum `zoom` |
| `cutvoke.fx.punchIn` | One clip zooms toward a chosen point, holds, and smoothly returns; optional ease-out overshoot | A reaction, joke or detail. `anchorX/Y`, `zoom`, `start`, `enter`, `hold`, `leave`, `overshoot` |
| `cutvoke.transition.matchAlign` | Two independently positioned/scaled/rotated subjects meet at the centre of the handoff, then return to their native framing | Related shapes or subjects in adjacent shots. `aX/Y`, `bX/Y`, `aScale`, `bScale`, `aAngle`, `bAngle`, `mixWidth`, `duration` |
| `cutvoke.transition.lumaReveal` | The next shot appears according to actual image luminance, rather than a uniform dissolve or white flash | SDR footage with useful light/dark structure. `softness`, `reverse` (shadows first), `keySource`, `curve`, `duration` |

The existing library already supplies whip pan, zoom-out handoff, light leak, temporal motion blur, grain, shake, masks and conventional `xfade` transitions. They remain the first choice when they fit the edit. `media.trackPoint` already returns bounded, read-only optical-flow observations for attachment work; the new manual lock does not invoke it or estimate a path.

## Editor and AI use

Select an image/video clip, open **Inspector → Clip effects**, and enable a camera effect. Numeric labels, bounds and units come from the same schema used by AI tools. Coordinates are fractions of the source picture before the editor fits it to the canvas, with `(0, 0)` at the upper left. The two-point lock constrains positions to `0.2–0.8` and its effective zoom to at most `4×`; it holds the endpoint outside the path interval. It cannot follow occlusions, independent scale changes, rotation or arbitrary paths. Split the edit into suitable sections or author normal transform keyframes when needed.

For transitions, select the seam or the incoming clip, choose the transition, edit its additional parameters, **Preview current transition**, then **Apply/Switch transition**. Trial rendering uses the current parameter draft without modifying the project. Changing just the duration of an existing transition preserves its other authored parameters. Match Align uses reflected edge extension during motion; choose restrained anchors/angles and sufficient matching scales to avoid conspicuous reflections. Its scale controls describe the handoff transform, not automatic object-size estimation. Positive angles rotate clockwise in image coordinates.

The engine supports transition durations `0.05–2 s`; the existing editor seam policy offers at least `0.1 s` and caps the effective duration according to the adjacent clips. Punch In returns after `start + enter + hold + leave`; use a clip long enough to include the whole action. It preserves audio and timeline length. Only the declared controls are supported; no effect parameter is advertised as keyframe-animatable. The existing effect range can enable a geometry effect over a clip-local interval; it does not restart the effect's internal clock. Transitions use the existing seam/handle rules.

AI tools discover these effects with `effects_list` or `resources_list`, and inspect exact schemas with `editor_help`. For example, under an acquired edit lease:

```json
{
  "projectId": "my-edit",
  "type": "effect.add",
  "payload": {
    "clipId": "reaction",
    "effectId": "cutvoke.fx.punchIn",
    "params": {"anchorX": 0.6, "anchorY": 0.45, "zoom": 1.4,
               "start": 0.5, "enter": 0.2, "hold": 0.7, "leave": 0.3}
  },
  "expectedRevision": "12",
  "commandId": "reaction-emphasis-1",
  "editLeaseId": "the-acquired-lease"
}
```

Use `effect.setTransition` for either transition, attaching it to the incoming clip. `command_preview` validates a command without writing it; that tool does not accept an `editLeaseId`. Apply with the same revision, then use the normal preview/export and project packaging tools. Unknown keys, out-of-bounds values, booleans in numeric fields and non-finite numbers are rejected. Undo/redo, bypass, save/reload and portable packages retain ordinary effect instances.

Names, descriptions, parameter labels and enum labels cover all eleven interface locales. These translations have not received native-speaker review. They never translate project text or API identifiers.

## Reference evidence and limits

The [source record](vlog-effect-references.json) preserves the research method, URLs and popularity snapshots from **2026-10-19**. Counts below are historical page/index observations supplied by the source research, not live counters or a ranking. High view counts do not establish that an effect caused popularity.

| Source | Snapshot and role in this implementation |
| --- | --- |
| [Daniel Schiffer: locked-on tutorial](https://www.youtube.com/watch?v=iPlpAuY7nO4), [Alli and Will's written tutorial](https://www.alliandwill.com/blog/locked-on-stabilization-effect-in-davinci-resolve) | About 2.22M views on the video page. The written method tracks a high-contrast target, reverses the motion and crops edges. Our simplified adaptation uses two manually entered source points. |
| [Emma Chamberlain: cooking](https://www.youtube.com/watch?v=_kJjvzC2QdI), [creator interview](https://www.highsnobiety.com/p/emma-chamberlain-interview/) | 5,973,996 indexed views. Her written account describes comedic zooms and fast cuts. This supports the emphasis recipe, not a claim about a specific shot in the cooking video. |
| [Daniel Schiffer: candy match-cut tutorial](https://www.youtube.com/watch?v=q-ILDPws_Z4), [cooking B-roll](https://www.youtube.com/watch?v=JtitcwwEl-A) | 87,098 indexed views and about 3.11M page views respectively. Tutorial metadata identifies matching as a technique; the popular B-roll supplies creator context. Matching anchors, scale and angle are our explicit implementation choices. |
| [Peter McKinnon: Luma Fade](https://www.youtube.com/watch?v=1Qm1EGxdZ6E) | 783,022 indexed views. The named technique motivates a true content-luminance mask; the threshold formula and controls are original. |
| [KOLD: My Year 2016](https://www.youtube.com/watch?v=QJbpJQscn9E), [Casey Neistat: Make It Count](https://www.youtube.com/watch?v=WxfZkMm3wcg), [Peter McKinnon: The Bucket Shot](https://www.youtube.com/watch?v=r25IWquxe9s) | 8,242,321 page views, 33,421,051 indexed views and 3,866,297 indexed views. Popular Vlog/creator-film discovery context only; no particular new effect is attributed to an unseen shot. |

**Playback limitation:** the cloud browser attempted three films; pages and advertisements loaded, but the main films remained buffering at `0:00` after retries. Transcript artifacts were unreadable. Research therefore verified source metadata and explicit written descriptions, not continuous films or frame-by-frame occurrences. There are no invented shot timestamps, viewing claims or copied media. The adaptations are recipes informed by creator descriptions, not exact reproductions of unseen edits. The procedural demo below establishes rendering behavior; natural-footage artistic review remains open.

## Reproduce and contribute

```bash
python -m pytest -q tests/test_vlog_effects.py tests/test_effect_contributions.py
python scripts/verify/demo_vlog_effects.py --output .artifacts/acceptance/vlog-effects-new
cutvoke serve --data .artifacts/acceptance/vlog-effects-new/data
```

Use a new output directory on every run. The silent, original-illustration demo builds four pairs at `640×360, 24 fps` over stdio MCP, plus a `1280×720`, 16-second comparison. `projects/*.cvkpkg` and `projects/*.json` preserve each effect's editable project and exact parameters; `data/` is the persistent library. `comparison.cvkpkg` is the editable presentation of the rendered pairs. Edit an individual effect project to change its algorithm parameters. `frames/`, `contact.png`, `calls.json` and `result.json` retain pictures, commands, per-export wall times, preflight, final streams and full decode evidence. Times are local measurements, not a cross-machine benchmark. The demo uses no audio and makes no listening claim.

![Original procedural before/after comparison](../assets/vlog-effects-contact.png)

The [verification snapshot](../assets/vlog-effects-verification.json) records actual parameters, output hashes, frame counts, local export times and portable-package re-render comparisons. The complete browser run passed 102 scenarios; the remaining recording scenario initially lacked Playwright's FFmpeg binary and passed after that existing test dependency was installed. No product assertions were weakened.

The rendering regressions also run against FFmpeg 6 and 7. Leading black gaps keep the first clip's final picture, and transition lookahead keeps every fade step at a shifted preview origin. Exact cut frames at 24 and 30000/1001 fps, alpha, audio and full/window pixel comparisons remain asserted.

The luma mask uses no mutable expression registers: FFmpeg's [custom xfade evaluation](https://github.com/FFmpeg/FFmpeg/blob/n6.1/libavfilter/vf_xfade.c) shares an evaluator between slice workers. Raw transition output must match byte for byte at one, four and eight slice threads; this catches intermittent colored noise that a single sampled frame can miss.

See [Shadow Ink](../../effects/transitions/cutvoke.transition.community.luma-ink/README.md) for an optional community manifest using the same content-luma renderer with different defaults. It is a contribution example, not a fifth algorithm. `CUTVOKE_EFFECTS_PATH` remains opt-in; restart the service after changing a manifest. Built-ins ship normally. Use the latest source containing `vlog_fx.py`; earlier `0.3.1` binaries may lack these mappings. Minimum-core metadata is descriptive and does not enforce feature negotiation.

Follow the [contribution guide](../../effects/README.md): stable IDs, author/license/provenance, supported mappings, schema limits, a real local preview, invalid-parameter tests, frame-count/alpha/window comparisons and an editable sample. `x-cutvoke-label`, `x-cutvoke-step` and `x-cutvoke-enum-labels` now drive generic controls, including the transition panel. Do not supply arbitrary executable code or raw FFmpeg expressions through a manifest.

## Mature open-source candidates

The [pinned upstream review](../development/vlog-upstream-review.json) records repository commits, file blob IDs and license evidence. **No upstream code or assets were imported in this change.**

- [gl-transitions](https://github.com/gl-transitions/gl-transitions/tree/e1e4bde9b4dad3cb7bdd7ed28e4b97f469d90448): `CrossZoom.glsl` and `GlitchMemories.glsl` declare MIT individually. The repository's license defers to per-file declarations, so this is not a blanket MIT assertion. Existing CutVoke equivalents should be checked before adding another transition.
- [gl-transition-libs](https://github.com/gre/gl-transition-libs/tree/72077dad05804b70d38d39bcc1a4282eccad78e9): MIT integration examples include a headless single-frame CLI. They do not provide CutVoke's decoding, audio, timeline, window preview or export contracts.
- [Pixi filters](https://github.com/pixijs/filters/tree/e9d1ca987864f121680bb0d7e9612c05b37748de): the checked repository license is MIT. Glow/RGB split/old-film/CRT/shockwave/zoom-blur filters still need an offline adapter, exact time/seed handling and alpha/color validation; several overlap existing effects.
- [Editly's transition adapter](https://github.com/mifi/editly/blob/dc46674052eacd15ef6e0f563a58eb378b3804ad/src/transition.ts): a concrete MIT/headless-gl reference for uploading two frame textures and reading RGBA back. Its Node/OpenGL stack, framing, random defaults and per-frame resource handling need separate evaluation before reuse in this Python renderer.

FFmpeg is the existing external dependency and retains its build-specific license; an MIT CutVoke effect does not relicense FFmpeg. This round adds no GPU stack, frei0r/MLT/movit dependency or custom FFmpeg build, and does not claim gl-transitions or Pixi support.
