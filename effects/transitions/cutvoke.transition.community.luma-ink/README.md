# Shadow Ink · optional contribution recipe

[简体中文](README.zh-CN.md) · [Contribution guide](../../README.md)

Reveal the next shot through the outgoing picture's darker regions first. This manifest reuses **`cutvoke.transition.lumaReveal`'s original internal implementation** with `reverse:true` and `duration:0.8`. It is a reusable configuration, not a fifth renderer or a downloaded shader. The local preview comes from CutVoke rendering original procedural illustrations; no reference-film frame is included. Code, manifest and procedural illustration use the repository MIT license; FFmpeg is an independent external dependency.

[Preview parameters](preview-params.json) record the source hashes, defaults, exact frame and renderer revision. The PNG was regenerated after the stateless luma-mask fix, from frame 57 of a real exported video.

Set `CUTVOKE_EFFECTS_PATH` to the absolute `effects/transitions` directory before starting editor/MCP, then restart them after manifest edits. The loader is opt-in and does not install a gallery preset. Requires the current source containing `vlog_fx.py`; the minimum-core field is descriptive and cannot guarantee that an earlier `0.3.1` binary knows this mapping.

Choose adjacent image/video clips, attach `cutvoke.transition.community.luma-ink` to the incoming clip with `effect.setTransition`, or choose it in the editor transition panel. Tune `duration` (`0.05–2s`, editor minimum `0.1s`), `softness` (`0.005–0.35`), `reverse`, `keySource` (`outgoing`/`incoming`) and `curve` (`linear`/`smooth`). The preview and apply controls share these values. Identical or uniformly lit shots give little visible spatial structure; use distinct SDR pictures with readable highlights and shadows.

```bash
python tools/verify/validate_effect_contributions.py
python -m pytest -q tests/integration/test_effect_contributions.py tests/rendering/test_vlog_effects.py
```

For an editable original-media sample, run [the isolated demo](../../../tools/verify/demo_vlog_effects.py) with a new `--output` directory. The effect contract is the manifest schema and renderer, not a third-party SDK. Change parameters and metadata under a new contributor ID; do not supply executable code in a manifest.

[Peter McKinnon's named Luma Fade tutorial](https://www.youtube.com/watch?v=1Qm1EGxdZ6E) provides technique context. This effect independently uses CutVoke's existing luma-mask implementation; no tutorial footage or algorithm is included. Translations are technical drafts without native-speaker review.
