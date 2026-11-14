# Quick Whip / 快速甩动

A fast leftward sweep for energetic vlogs and Shorts. Quick Whip configures FFmpeg's existing `smoothleft` transition with a short default; it does not add a renderer, camera simulation or motion blur.

适合活力 Vlog 与短视频的快速向左扫切。默认 0.25 秒，复用 FFmpeg 已有的 `smoothleft` 转场，不额外添加运动模糊，也不改变渲染引擎。

![Actual CutVoke-rendered Quick Whip midpoint using synthetic geometry](preview.png)

- ID: `cutvoke.transition.community.quick-whip`, version `1.0.0`.
- Author: CutVoke contributors. Code, generated artwork and preview: [MIT](LICENSE).
- Inputs: two adjacent image/video clips on a video track. Apply to the incoming clip.
- Engine: `ffmpeg-xfade`, filter `xfade`, transition `smoothleft`.
- Parameter: `duration` in seconds, minimum `0.1`, default `0.25`, maximum `0.6`, control step `0.05`. Not animatable. Unknown parameters are rejected.
- Direction is fixed: the incoming scene is revealed from right to left with a soft boundary; picture geometry is not translated. Use a shorter duration for a snappy cut; `0.6` makes the motion easier to inspect. Existing engine handling still limits transitions at short clips and adjacent cuts.
- Tested on Linux x86_64 (Debian 13), Python 3.12.14, FFmpeg/ffprobe 7.1.5. No Windows/macOS runtime acceptance is claimed.
- All 11 interface locales have machine-drafted names and descriptions, without native-speaker review. Unknown locales fall back to English. 全部 11 种界面语言的名称与说明为机器草拟，尚未经母语审校。

## Load in the editor

This is an **opt-in contribution**, not an automatically bundled gallery preset. From an activated source installation, set the category directory before starting the editor:

```bash
export CUTVOKE_EFFECTS_PATH="$PWD/effects/transitions"
cutvoke effects list --category transition
cutvoke serve --data .artifacts/quick-whip-editor
```

For Windows PowerShell use `$env:CUTVOKE_EFFECTS_PATH = (Resolve-Path effects/transitions).Path`. Restart the service after manifest changes. Open a project, select the incoming clip, and find **Quick Whip / 快速甩动** in the transition list. Use the card's keyboard Enter action or drag it to a cut. See the [contribution guide](../../README.md) for setup and discovery rules.

`preview.png` is review material, not a preset-gallery cover. The compatibility fields are descriptive metadata, not an enforced version check.

## Reproduce and verify

```bash
python tools/verify/validate_effect_contributions.py
python -m pytest -q tests/integration/test_effect_contributions.py tests/integration/test_quick_whip_contribution.py
python tools/verify/demo_quick_whip.py --output .artifacts/quick-whip-visual
```

Use a new output directory for each run. The visual demo draws two original geometric scenes locally, renders the actual manifest at the default `0.25` seconds, fully decodes the three-second/180-frame MP4, and saves its midpoint as `preview.png`. The committed preview is that unmodified decoded frame. It also saves a project snapshot and JSON report. No user library, personal footage, downloads, fonts or third-party art are used. Watch `quick-whip.mp4` around 1.5 seconds; the PNG alone cannot demonstrate motion. To reproduce the bounds, pass `--duration 0.1` or `--duration 0.6` with a new output directory.

The regressions cover all locale entries, defaults and invalid parameters, actual `effect.setTransition` commands, undo/redo and SQLite/project round-trip. Real image and video exports at minimum/default/maximum durations check a 120-frame full decode, endpoints, directional progression, duration and consistency with a bounded preview. These focused checks do not claim a full product or native-package acceptance run.
