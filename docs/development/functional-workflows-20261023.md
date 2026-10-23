# Functional workflow completion — 2026-10-23

This work extends the current local editing model and Web interface. Windows
signing and macOS Developer ID/notarization remain outside this change.

## Editing workflows

- `sequence.deleteRange` removes an interval across all lanes, captions, words,
  markers, mixer automation and multicamera cuts in one undoable transaction.
  Locked affected lanes reject the complete edit. `clip.rippleDelete` also
  accepts `scope: "sequence"`; its existing track scope remains compatible.
- `transcript.delete` uses committed caption or word times to cut the matching
  media interval and close the timeline. It accepts sentence IDs or word
  indices. The caption panel exposes both operations.
- `clip.group` accepts cross-track video, titles, stickers and audio. It retains
  child lane layout and attachment identities; grouping rejects selections
  that would silently change intervening layer order. Nested output is prepared
  as cached alpha video with PCM audio by the same renderer used for export.
  Parent transforms, effects and speed apply to the composed result. Copies
  receive fresh child IDs. Expansion restores original lanes; an already
  transformed compound must have its overall effects/speed reset before
  expansion so those operations are not silently discarded.
  Bound source/follower families must be selected together. Active solo and
  enabled stateful track/bus effects are rejected during grouping because
  moving only part of those streams into a nested mix would change their sound.
  The timeline selection tool can include the complete attachment family.
- Keyframe tracks include volume and supported numeric color/blur effect
  parameters with stable instance references. Cubic Bezier interpolation,
  graph editing and atomic keyframe retiming share their evaluator with render.

## Sound

- Track, subgroup bus and master mixing persist structured gain, pan, solo,
  mute, gain envelopes and supported audio effect chains. Processed audio
  previews use the authoritative server mix.
- Browser microphone recording supports device selection, countdown, transport
  alignment, optional live monitoring, preview/re-record and undoable insertion.
  Native recording uses real OS capture backends. No OS branch synthesizes a
  test tone as a substitute for recording.
- `audio.separate` runs an optional local Demucs model for actual source
  separation. Installation and model readiness are explicit. The older
  `audio.vocalEnhance` keeps its documented mid/side approximation semantics.

## Versions, cameras and interchange

- Editable canvas settings and sequence copies support real dimensions, frame
  rate and contain/cover/stretch framing. Queue export can freeze a specific
  sequence rather than whichever sequence happens to be active later.
- Multicamera switches are persisted at timeline times. Program output keeps
  non-camera overlays, supports camera monitoring and waveform-based alignment.
  Ambiguous or low-confidence synchronization requires manual alignment.
- OTIO JSON, CMX 3600 EDL and FCPXML offer interchange with diagnostics and
  strict rejection of unsupported/lossy structures. Basic cuts are the supported
  interchange scope; this does not claim full application-specific effect or
  AAF compatibility.

## Verification

The evidence directory is `.artifacts/feature-completion-20261023/`. It includes
real local separation input/stems and model/probe measurements. Targeted tests
check time/source mapping, persistence/undo, pixel output, audio output,
multicamera synchronization, output specifications and interchange parsing.
Final test/build/browser results are recorded in that directory after integration.

Physical microphone capture on macOS/Linux requires verification on those
platforms. Browser fake-device tests exercise recording behavior without using
the user's microphone. The local Demucs sample verifies execution and output
integrity; it is not a representative natural-singing quality benchmark.
