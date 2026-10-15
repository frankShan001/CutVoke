# Review and delivery

Make two passes: verify the edit's technical integrity, then whether the viewer receives the intended story. A passing command or `readyToRender:true` is not an artistic review.

## What to inspect

- First and last frames, every cut/transition boundary, crop/keyframe extremes, and intervals with titles or captions. `preview_frames` returns composited PNGs including text and captions. Check for black flashes, unintended gaps, clipped text, collisions, unreadable contrast and unwanted scaling.
- Continuous playback for rhythm, subtitle reading time and motion. `preview_prepare` produces video with title tracks; the editor displays captions as an editable overlay. Review the **exported** MP4 for the final subtitle rendering.
- Listen to the final audio where playback is available: dialogue intelligibility, music balance, cut clicks, channel balance and start/end fades. With a shell, FFmpeg loudness/peak analysis is useful supporting evidence. Do not equate a present AAC stream with a good mix.
- `project.preflight` for missing/changed media and LUTs. It checks file availability and project references, not all decode failures or subjective quality.
- Final MP4 codec, dimensions, frame rate, duration and full decode. For example, `ffprobe -v error -show_streams -show_format -of json <output.mp4>` and `ffmpeg -v error -i <output.mp4> -f null -` check streams and decode when these tools are available.

## Refinement and handoff

Describe problems concretely: “The subtitle collides with the product at 4.2s” or “The last chord ends abruptly.” Repair the specific entities, preview the affected intervals and export the new revision to a new filename. Do not redesign an approved direction simply to add novelty.

Deliver the output path, project ID, exact exported revision, editable project/package when requested, and a short list of applied changes. State what you actually watched/listened to and what remains unverified. If audio cannot be played, report the technical audio checks and explicitly leave subjective listening open. If the host cannot view returned images, ask for visual review or arrange an authorized local viewer; do not claim visual acceptance.

The included demo's `result.json` records stream properties, full decode, preflight, idempotent replay and audio measurements. `frames/` holds real rendered PNGs; `calls.json` records tool calls; `mcp.log` aids repair. These artifacts support a review but do not replace it. A failing demo retains its isolated directory; fix the cause and choose a new directory for a clean run. No default user project database is involved.
