# Native cinematic compositing

These effects are timeline data, editable through the effect inspector and the
existing `effect.add` / `effect.update` commands. Preview and export share the
same renderer. No precomposed final movie is substituted for the timeline.

## Fragment reconstruction

`cutvoke.fx.shatter` divides the source into slanted cells while preserving its
alpha. `assemble` converges the pieces; `disperse` releases them. Motion uses a
minimum-jerk curve with deterministic per-piece delay. Rows, columns, angle,
gap, spread, heading and seed control the composition. `scatterMode: radial`
uses `originX` / `originY`; `fadeFragments: false` keeps incoming pieces visible.
Assembly restores the original source at its endpoint, without residual seams.

Use a tracked RGBA subject rather than a full rectangular shot to reconstruct
the vehicle itself. These are translated photographic fragments, not simulated
3D rigid bodies.

## Light with foreground occlusion

`cutvoke.fx.lightWeave` generates deterministic light filaments. Place its
`back` layer below an RGBA subject and its `front` layer above it. The
`reflection` layer supplies a compressed, softened reflection for wet ground.
Palette, filament count, intensity, amplitude, span, tilt, phase and timing
remain editable. `anchorFrames` is an optional strictly increasing list of
`{time, x, y}` normalized light-source positions in local clip time. It accepts
tracked positions; the effect does not perform tracking itself.

The renderer caches disposable, lossless RGBA light passes keyed by parameters,
dimensions, frame rate and implementation version. Cache paths are transient
render data and are never saved as project authority. Original sources and
parameters are sufficient to rebuild them.

## Optical layering and packaged fonts

`cutvoke.fx.opticalField` adds independently editable `flare`, `rays`, and
`sparks` passes. They share tracked `anchorFrames`, with palette, angle, span,
ray/streak/particle counts, pulse and timing controls. Sparks use drag and
gravity. Light shafts are directional overlays, not 3D scene reconstruction.
Place these passes around tracked foreground mattes for photographic depth.

Text layers, captions and text masks can use the packaged Barlow Condensed,
Space Grotesk, Cormorant Garamond and IBM Plex Mono families. The same font
files serve the Web and native export; fixed Regular/Bold files and static
instances of variable fonts avoid silent system font substitutions. Source
URLs, hashes and original OFL license files accompany the shipped fonts.

## Source attachment tracking

`media.trackPoint` is a read-only CutVoke command available through the shared
CLI, HTTP and MCP command catalog. Pass `videoPath`, normalized seed `x` / `y`
and source time `t`. It returns source-time `anchors` and explicit `lostFrames`.
Tracking runs bidirectionally with a forward/backward consistency check; it does
not invent positions beyond a lost track. Analysis is bounded to 30 seconds and
900 frames, with width capped at 640 for tracking only. Original media and
export resolution remain unchanged. Map source times to each clip's local
presentation time before setting light `anchorFrames` with `effect.update`.

## Organic and typographic masks

`cutvoke.fx.mask` supports `shape: ink`, with `inkSeed` and `inkDetail`.
Combined with `motion: minimum-jerk`, the mask grows organically from its
normalized `x` / `y` origin. This is procedural erosion, not fluid simulation.
At its endpoints it is completely hidden and completely revealed.

For `shape: text`, enable `tileText` and adjust `tileSpacing` / `tileDrift` to
form a moving text field. Applying it to a tracked subject preserves the
subject's original alpha, so text occupies its silhouette rather than a box.

## Complete example

[CHASE THE LIGHT](../examples/showcases/chase-the-light/README.md) includes the
approved V7 movie, its portable editable effect project, a silent four-shot
source grid followed by the finished edit, and retained production materials.
The project demonstrates SAM2 subject cutouts, tracked source-light anchors,
layered occlusion, fragment reconstruction, a text-filled rider, optical layers
and recovery of the original material. It uses the original 24 fps MiniMax
clips and music, without AI upscaling or interpolation.
