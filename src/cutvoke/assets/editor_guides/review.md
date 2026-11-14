# Review and delivery

Review technical integrity and the applicable creative goal. A command, `readyToRender:true`, effect count or prepared preview is not perceptual acceptance. Keep checks proportional: a subtitle correction needs its interval, readability and relevant neighbors; a complete creative delivery needs an assessment of the work as a whole.

## What to inspect

- First and last frames, every cut/transition boundary, crop/keyframe extremes, and intervals with titles or captions. `preview_frames` returns composited PNGs including text and captions. Check for black flashes, unintended gaps, clipped text, collisions, unreadable contrast and unwanted scaling.
- Continuous playback for rhythm, subtitle reading time and motion. `preview_prepare` produces video with title tracks; the editor displays captions as an editable overlay. Review the **exported** MP4 for the final subtitle rendering.
- Listen to the final audio where playback is available: dialogue intelligibility, music balance, cut clicks, channel balance and start/end fades. With a shell, FFmpeg loudness/peak analysis is useful supporting evidence. Do not equate a present AAC stream with a good mix.
- `project.preflight` for missing/changed media and LUTs. It checks file availability and project references, not all decode failures or subjective quality.
- Final MP4 codec, dimensions, frame rate, duration and full decode. For example, `ffprobe -v error -show_streams -show_format -of json <output.mp4>` and `ffmpeg -v error -i <output.mp4> -f null -` check streams and decode when these tools are available.

## Refinement and handoff

For a substantial expressive treatment compare a representative simple/untreated passage and designed version at matched source/time/quality conditions. Name the visible or audible difference and whether it serves the brief. Check onset, development, fastest motion/occlusion, handoff and resolution. If strong expression was requested and the addition is barely perceptible, revisit the design, timing or composition; a list of applied effects is not sufficient evidence.

Use applicable dimensions rather than a universal taste score: understandable progression, picture/sound relationship, composition/readability, continuity, factual/message coherence and pacing. For speech/factual edits check source meaning and breaths; for teaching/demo work check the demonstrated action and reading time. Repeated musical sections should develop or deliberately reprise the intended idea. Static samples alone leave rhythm and continuous motion unverified.

Record an actual time range, observation, change, new artifact/revision and recheck. During iteration, inspect changed ranges plus affected transitions, audio handoffs and downstream dependencies. For complete delivery, review the complete exported work where playback is possible. Check returned `audioVerification` / `videoVerification` within their stated limits; a passed seam sample cannot certify every pixel or artistic decision.

Describe problems concretely: “The subtitle collides with the product at 4.2s” or “The last chord ends abruptly.” Repair the specific entities, preview the affected intervals and export the new revision to a new filename. Do not redesign an approved direction simply to add novelty.

Deliver the output path, project ID, exact exported revision, editable project/package when requested, and a short list of applied changes. State what you actually watched/listened to and what remains unverified. If audio cannot be played, report the technical audio checks and explicitly leave subjective listening open. If the host cannot view returned images, ask for visual review or arrange an authorized local viewer; do not claim visual acceptance.

The included demo's `result.json` records stream properties, full decode, preflight, idempotent replay and audio measurements. `frames/` holds real rendered PNGs; `calls.json` records tool calls; `mcp.log` aids repair. These artifacts support a review but do not replace it. A failing demo retains its isolated directory; fix the cause and choose a new directory for a clean run. No default user project database is involved.

### Hear a cut

Call `preview_audio` with `projectId`, the current `expectedRevision`, `time` in timeline seconds and optional `radius` (0.05–2.5 seconds; default 0.5). It returns one stereo WAV MCP audio block and up to 100 per-channel peak bins from the composed export mixer, including source trims, gain, fades and overlapping tracks. Bounds clamp to the active timeline and are reported as `startSample` / exclusive `endSample` at `sampleRate`; `peakBinEdges` uses that same absolute sample clock. `revision` identifies the immutable project snapshot inspected, not a promise that no one edited afterward. Re-query before making any edit.

Peaks retain floating-point overload values above 1; the playable PCM16 WAV clips at full scale without normalization. Nested compounds are currently rejected: review their export instead. Visual pre-rendering is deliberately excluded so it cannot strip embedded source audio. A timeline with no audible stream returns `hasAudio: false` and no audio block, not fabricated silence. An empty active timeline is rejected. Silence inside an audible stream is valid and produces zero peaks. The tool leaves no persistent snippet and changes no project data. It accepts no source/output paths.

This is one window of at most five seconds, not whole-project transcription. Rendering retains mixer history for effect fidelity, so long/complex projects can take longer than the snippet length. A cooperative 60-second cancellation covers scheduler wait/rendering, and decoding has a further 10-second timeout. Preparation steps that do not honor cancellation may exceed that budget. Host audio support varies; peak data remains readable when a host cannot play WAV. Listen for clicks and dropped syllables, but neither peaks nor a successful render prove speech meaning or an artifact-free edit. Review the actual exported deliverable too.
