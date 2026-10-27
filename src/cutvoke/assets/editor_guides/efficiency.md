# Efficient iteration and review

Read when media volume, render cost or repeated revisions materially affect the task. Efficiency means reducing wasted work while retaining the intended edit and evidence; do not claim a speed gain without measuring comparable work.

## Spend expensive work on uncertain decisions

Use a compact source inventory and source-linked selects to avoid repeatedly inspecting the same media. Transcripts and search can find dialogue candidates; selected pictures reveal framing and action. Recheck source meaning and normal-speed delivery before accepting candidates. Preserve useful reactions and pauses that automatic ranking or silence detection might discard.

For a large source set, triage in stages: inventory technical metadata and available descriptions/transcripts; sample candidate pictures/actions; inspect promising and contextually important intervals in motion/sound where supported. Check an apparent reject when it could supply a missing reaction, context or motif. Record which sources/ranges were fully examined, sampled or not reviewed, and why. Expand coverage where uncertainty affects the brief. Do not require exhaustive full playback for every routine task or describe a sampled inventory as having watched all media. Respect current import/query batch limits.

Use `project_summary` / `project_lookup` for targeted changes; discover only relevant effects and schemas. Group supported related operations with the current `edit.batch` limit and preserve normal undo points. Do not clear and rebuild an effect stack to change one instance when exact instance operations are available.

Prototype the most consequential uncertain passage before multiplying an effect. Reuse unchanged assets, source inspections and valid previews; use the engine's documented cache/preview capabilities rather than silently creating replacement source files. After a local change, inspect that range plus adjoining transitions, audio handoffs and any downstream dependency actually affected. Avoid full preview/export after every small edit.

## Separate fast iteration from delivery quality

Use explicitly supported preview resolution or a short `export.enqueue` range for a representative dynamic sample; read its schema first. `preview_prepare` prepares a complete preview, so do not describe it as a range-render tool. Do not invent proxy-generation commands. Return the sample's revision, range and limitations. A lower-cost preview does not authorize lowering final resolution, frame rate or audio quality.

Bound concurrent heavy jobs and polling, avoid duplicate work, and leave the human editor responsive. Do not change GPU/cache preferences, restart the editor or flatten the timeline for convenience. A timed run must record workload, settings, cache state and competing load; encoder choice alone does not prove an effect used the GPU.

For final delivery check original-media references, the complete exported file and representative full-resolution motion/edges. Report a playback limitation honestly. A successful background job is a technical result, not a record of viewing or listening.

Context: [proxy/ingest distinction in Premiere](https://helpx.adobe.com/premiere/desktop/organize-media/ingest-proxy-workflow/ingest-and-proxy-workflow.html); [practitioner discussion of rough-cut effort and correction cost](https://www.reddit.com/r/editors/comments/1p5nijm/what_tools_are_you_using_to_speed_up_rough_cuts/). These motivate the workflow; neither supplies CutVoke commands or transferable time-saving percentages.
