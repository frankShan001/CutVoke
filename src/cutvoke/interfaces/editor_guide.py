"""Bundled MCP onboarding. Only editing documentation; no model service."""
from __future__ import annotations

import copy
import json
from cutvoke.editing.effect_modules import PRIVACY
from cutvoke.interfaces.creative_guides import catalog as creative_catalog, document as creative_document, GUIDE_FILES


INSTRUCTIONS = (
    "CutVoke is a local video editor controlled by this MCP server. Start with editor_help({}) "
    "for the workflow and conventions; no external skill or source-code access is needed. "
    "For new or substantial creative edits read editor_help(topic='creative') and load only the relevant principle/workflow/project-plan documents. "
    "Follow the user's expressive intent; demonstrate it with actual moving output and sound where available. Local corrections stay local. "
    "Use project_list/project_summary to identify the project, media_import/media_list for assets. "
    "Before an unfamiliar edit, call editor_help(topic='command', command='<type>') or commands_list; "
    "for effects also supply effectId to obtain its exact params schema. Never invent IDs or parameters. "
    "Batch edits use edit_lock, command_preview, then command_apply with the current expectedRevision, "
    "a stable commandId and editLeaseId. Read error.recovery after failure. Verify actual pictures with "
    "preview_frames, export asynchronously, and release the lease for human takeover. "
    "Intelligence remains in your external agent; this application executes edits and returns evidence. "
    "Optional LOCAL neural matting is available through local_model_status/local_matting_start/local_matting_job/local_matting_apply. "
    "Local models are optional and excluded from the base installer. For first use call extension_status then extension_plan. "
    "When installation is needed, explain purpose/device/disk cost and ask the HUMAN user; use extension_confirm only for their real response, then extension_install with its receipt and extension_job. "
    "A general editing request is not installation consent. Reuse an already explicit installation authorization; respect declines and do not ask again for the same plan or mirror switch. "
    "Use the app-managed installer, not shell/pip/Git commands. Querying readiness never downloads. Read editor_help(topic='local-models'). "
    "Before that workflow read editor_help(topic='local-models') and SELF-ASSESS current image and tool-image input support. "
    "If unsupported or unknown, DO NOT use AI matting or visual review; direct the user to the manual UI. Never guess visual points or pretend to see images. "
    "UI requests are available with agent_requests; read editor_help(topic='agent-bridge'). Merely connecting MCP cannot wake an idle AI host. "
    "When inventing reusable effects during normal editing, read editor_help(topic='sharing'); "
    "save a content-free recipe with effect_module_save and apply through effectModule.apply. "
    "Never include user media, images, audio, text/captions, projects, personal information or paths in contributions. "
    "Use only effect_module_export for sharing; never project_package/project_archive or project screenshots. "
    "Whenever suggesting sharing, show the returned privacyNotice in the user's language verbatim. "
    "Nothing is uploaded by these tools; obtain explicit user consent before public submission. "
    "Required user-facing sharing notice (zh-CN): " + PRIVACY["zh-CN"] + " "
    "Required user-facing sharing notice (en): " + PRIVACY["en"]
)

GUIDES = {
    "generated-render": {
        "title": "Parameterized external render requests, verified cache and frame windows",
        "sourceExecution": False,
        "workflow": [
            "Create a request using requestExample; declare every source/asset/font/lockfile/license with its relative POSIX path and lowercase SHA256. bindings reference declared asset/font paths. Hashes do not establish dependency completeness, reproducibility or safety.",
            "Call generated_render(projectId=..., operation='plan', requestPath=...), optionally params overrides, frameRange and previousRequestKey. Save the returned effective request and requestKey. A plan is not retained or reserved; changedFields compares only a previously registered retained request.",
            "On requiresExternalRender=true, use an independently trusted external renderer. CutVoke never runs source or installs dependencies. Render the effective absolute logical window, but make the physical output timestamp start at zero. Full logical output.durationFrames stays unchanged for a local window.",
            "Call operation='register' with the same request/overrides, expectedRequestKey, renderPath and renderSha256. Stale keys, hashes, codec, frame timing/dimensions/count or explicit BT.709 errors reject registration. The effective request and declared inputs enter a separate inert source archive.",
            "On a verified exact/covering cache hit use assetId AND mediaSourceStart. For covering windows this is (requestedStartFrame-renderedStartFrame)/fps, not absolute logical time. previewCall uses existing preview_frames with source-local times. Inspect returned pictures and the final composite separately.",
            "Native timeline writes still require command_preview, revision, commandId and lease. To repair a subwindow, split its clip at both boundaries, trim the middle sourceStart to the new asset offset, then asset.swap only that middle clip in one edit.batch. Derive IDs from returned changes, preserve native text/audio and review output.",
            "Use operation='status' with projectId only for index counts. Keep the entire project folder for cache/source ZIPs; project_package includes referenced source archives and ordinary media; the cache index stays outside it. Use generated_source for explicit input restoration."],
        "constraints": [
            "paramsSchema is a closed flat object with 1..32 ASCII named fields, all required. Scalars and scalar arrays only. Numbers require finite minimum/maximum within ±1e12, strings minLength/maxLength<=2048, arrays minItems/maxItems<=64; scalar enum<=32. No expressions, refs, patterns, defaults or nested objects.",
            "Requests<=256 KiB, <=127 inputs, 32 MiB total including archived request; output<=512 MiB. Silent H.264 MP4 or ProRes 4444 MOV, explicit BT.709, square pixels, 16..4096 dimensions, rational 1..120 fps, <=18000 frames and <=600 seconds. Straight alpha requires ProRes; alpha pixels and creative quality are unassessed.",
            "Cache keys cover declared parameters/schema, inputs, bindings, renderer/build, seed, profile, full output contract and frame range. Covering reuse differs only in frame range. Logical clock/dependency completeness/profile are declared by the external renderer, not attested by CutVoke.",
            "Every cache hit rehashes managed media and source archive. Changed/missing bytes miss; observed different outputs for one request disable reuse for that retained composition. This is not proof of determinism. LRU removes index metadata only (max256 entries); media/source ZIPs remain.",
            "No arbitrary-code runtime, isolation guarantee, render job, parameter GUI, automatic rerender or delivery scheduling. A miss requires external work. Native clips and project editing remain authoritative."],
        "requestExample": {
            "kind": "cutvoke.generated-render-request", "schemaVersion": 1,
            "renderer": {"name": "trusted-renderer", "version": "exact-version", "build": "runtime/dependency-build"},
            "entrypoint": "scene.py", "files": [{"path": "scene.py", "sha256": "<64 lowercase hex characters>", "role": "source"}],
            "bindings": {}, "paramsSchema": {"type": "object", "additionalProperties": False, "required": ["accent"],
                "properties": {"accent": {"type": "string", "minLength": 1, "maxLength": 16, "enum": ["teal", "orange"]}}},
            "params": {"accent": "teal"}, "seed": 0, "profile": "final",
            "output": {"width": 640, "height": 360, "fps": {"num": 24, "den": 1}, "durationFrames": 72,
                       "format": "mp4", "alphaMode": "opaque", "colorSpace": "bt709", "audioMode": "none"},
            "frameRange": {"startFrame": 0, "frameCount": 72}}},
    "generated-media": {
        "title": "External code-video material bridge (v1)",
        "workflow": [
            "Run trusted source in a separate renderer environment. CutVoke never executes the manifest's source or installs dependencies.",
            "Declare every source, lockfile, font, media and license input with its relative POSIX path and SHA256. Include renderer version/build, params and seed.",
            "Call generated_media(projectId=..., operation='inspect', manifestPath=...). Inspection hashes a temporary snapshot and decodes/counts frames; it creates no persistent asset or timeline edit.",
            "Call operation='import' to retain an ordinary assetId and an inert content-addressed source ZIP. Identical imports reuse media and source identity; this is not a frame-render cache.",
            "Use clip.insert or asset.swap with explicit clipIds, expectedRevision, commandId and edit lease. Preview before applying, then review actual frames, continuous output and sound.",
            "project_package includes verified source ZIPs for referenced media (format 4); unpacking retains ZIPs without extracting inputs. Use generated_source list/inspect, then explicit restore with bundleId and a NEW outPath directory. No execution or dependency installation."],
        "limits": {"manifestBytes": 262144, "inputFiles": 128, "inputBytes": 33554432,
                   "renderBytes": 536870912, "maxDimension": 4096, "maxFrames": 18000,
                   "maxSeconds": 600, "inspectionTimeoutSeconds": 30},
        "constraints": ["One zero-based CFR video stream, square pixels, explicit BT.709 primaries/transfer/matrix on decoded frames; no audio stream.",
                        "MP4/MOV for opaque footage; straight alpha requires ProRes 4444 MOV. Premultiplied alpha, WebM, HDR and audio are outside v1.",
                        "Hashes establish byte identity, not dependency completeness, source safety, reproducibility or artistic quality. Alpha pixels and straight-alpha interpretation require separate compositing checks.",
                        "No native code clip, automatic parameter UI, arbitrary-code runner, source recursive dependency discovery or automatic rerender. Source archives can contain private data; no upload is performed."],
        "manifestExample": {
            "schemaVersion": 1, "renderer": {"name": "your-renderer", "version": "exact-version", "build": "runtime/renderer-build"},
            "entrypoint": "scene.py", "files": [{"path": "scene.py", "sha256": "<64 lowercase hex characters>", "role": "source"}],
            "params": {"accent": "teal"}, "seed": 0,
            "output": {"path": "render.mp4", "sha256": "<64 lowercase hex characters>", "width": 640, "height": 360,
                       "fps": {"num": 24, "den": 1}, "durationFrames": 72,
                       "alphaMode": "opaque", "colorSpace": "bt709", "audioMode": "none"}}},
    "creative": creative_catalog(),
    "overview": {
        "title": "Start editing with CutVoke",
        "privacyNotice": PRIVACY,
        "rules": [
            "Use only the connected tools. No shell, repository, external documentation or additional Skill is required for ordinary editing.",
            "The user supplies the intended edit and access to source media. If several projects fit the request, clarify the target; do not guess.",
            "IDs returned by queries are authoritative. Never infer a clip ID from its position or filename.",
            "All changes persist in the shared editable project. The desktop editor and MCP see the same database.",
            "Inspect actual preview_frames images before claiming visual quality. A successful command or export alone is not visual acceptance.",
            "During normal editing, preserve useful new effects as content-free modules. See sharing help. Whenever suggesting sharing, append its privacyNotice verbatim; do not include user footage, images, audio, text/captions, personal information, project files or paths. Never upload without explicit consent."],
        "workflow": [
            {"tools": ["runtime", "project_list", "project_summary"], "purpose": "Check the connected data directory; locate the requested project and current revision. Use create_project for a new edit or project_clone for an alternate version."},
            {"tools": ["editor_help"], "purpose": "For new or substantial creative work use topic=creative: shared principles, a relevant work type, then this project's plan. Load one document with guide=<id>. Local corrections reuse approved direction. Effect counts and a successful export do not prove expression."},
            {"tools": ["media_list", "media_import", "media_inspect", "generated_media", "generated_render", "generated_source", "preview_frames"], "purpose": "Locate/import local files and inspect actual pictures. generated_media imports external code renders; generated_render plans parameterized absolute frame windows and reuses verified exact/covering cached media. Neither executes source or edits the timeline. project_package includes referenced inert source ZIPs; use generated_source to inspect/restore into a new directory. Use assetId plus returned mediaSourceStart in native edits."},
            {"tools": ["editor_help", "commands_list", "resources_list"], "purpose": "Get command schemas or a recipe; discover compatible effects/presets instead of guessing. resources_list is the preset catalog, distinct from MCP document resources."},
            {"tools": ["edit_lock", "command_preview", "command_apply"], "purpose": "Acquire a lease, preview supported edits without committing, apply with revision and stable commandId. edit.batch commits 1..32 changes atomically as one undo point. Use each returned revision for the next edit; renew the lease during long editing sessions."},
            {"tools": ["preview_frames", "preview_prepare", "preview_job"], "purpose": "Check text/effects/transition boundaries visually; prepare continuous preview if needed. Preparation is asynchronous. Keep jobId; poll about once per second with a bounded timeout, not a busy loop."},
            {"tools": ["command_apply", "export_job", "project_package", "edit_lock"], "purpose": "Run project.preflight, enqueue export, poll until succeeded, return result.output_path. Export uses the submitted snapshot; later edits do not change it. Package the editable project when requested. Release the lease in a finally step, including after failure."}],
        "help": {"creative": {"topic": "creative"}, "conventions": {"topic": "time"}, "editing": {"topic": "editing"},
                 "recovery": {"topic": "errors"}, "examples": {"topic": "recipes"},
                 "command": {"topic": "command", "command": "clip.insert"},
                 "sharing": {"topic": "sharing"},"localModels":{"topic":"local-models"},"humanToAgent":{"topic":"agent-bridge"},
                 "generatedMedia": {"topic": "generated-media"}, "generatedRender": {"topic": "generated-render"}},
        "modelServices": False,"optionalLocalModels": True},
    "sharing": {
        "title": "Create reusable effects while editing; share settings only",
        "privacyNotice": PRIVACY,
        "rules": [
            "Complete the user's intended edit first. When inventing useful reusable effects, save a small declarative module; do not turn every edit into a contribution task.",
            "Call effect_module_list(includeSchemas=true) for the exact supported built-in schemas. Save recipe={effects:[{effectId,params}],motion:[{param,at,value,interpolation}],textStyle:{...}} with effect_module_save. Omit empty sections. Free-form names/descriptions, code, expressions, resources, paths and text/caption content are forbidden.",
            "motion.at is 0..1 of the target clip duration; x/y values are fractions of canvas width/height. Other effect durations remain seconds. textStyle updates an existing editable title while keeping its private text in the project only.",
            "Apply using command_preview then command_apply type=effectModule.apply payload={clipId,moduleId}, current expectedRevision, stable commandId and editLeaseId. It is one undo point. Only the recipe's effect IDs/animation slots and motion channels are replaced; unrelated settings remain. Inspect actual pictures locally.",
            "Modules live beside the current database in effect-modules, independent of projects; no development setup, environment variable or process restart is needed. Save a new recipe to create a changed module; IDs are content hashes. Import a generated ZIP with effect_module_import.",
            "When the edit is finished, mention worthwhile modules and offer sharing. EVERY sharing suggestion/delivery MUST append privacyNotice in the user's language verbatim. Explain that only the app-generated unmodified ZIP has this guarantee; do not make claims about files the agent adds separately.",
            "Use effect_module_export only. It creates four allowlisted files: module.json, README.md, LICENSE (MIT), PRIVACY.txt. It never reads a project, captures a frame, scans a directory for attachments or uploads. Do not use project_package/project_archive for effects: those contain private user content.",
            "Never attach project video, images, audio, title/subtitle text, screenshots, thumbnails, renders, prompts, logs, actor details, file paths or edit history to a contribution. Do not copy private details into settings, descriptions, PR titles/bodies or filenames. For visual evidence use separately generated synthetic media and fixed generic text, never derived from the user's project.",
            "Local preparation does not authorize publication. Before submitting a PR or uploading any file, obtain explicit user consent. Do not upload the user's project content even if asked to share an effect. Maintainers still review behavior and compatibility; this package does not certify visual quality."]},
    "time": {
        "title": "Times, coordinates and units",
        "rules": [
            'Timeline/source times in clip and caption commands use exact rational seconds: {"num":"3","den":"2"} = 1.5s. Prefer string integers, denominator positive. At 30fps, frame 45 is 45/30 seconds.',
            "timelineStart/timelineEnd and caption start/end are absolute timeline times; sourceStart/sourceTime refer to source media. End boundaries are exclusive.",
            "clip.keyframe time is clip-local presentation time. Its x/y values are canvas pixels. opacity is 0..1, scale 0.05..5, rotation -180..180 degrees.",
            "Title text and caption x/y are normalized 0..1. Fonts/strokes use pixels; animIn/animOut/animLoopMs use milliseconds.",
            "preview_frames times, export range start/end, clip.audio fadeIn/fadeOut and effect transition duration use seconds. Query the specific schema for effect parameters.",
            "Volume 1 is original level, 0 is mute. Track kind is video/audio/text. Text clip content belongs inside payload.text; captions use payload.text as a string.",
            "Local file paths must be absolute on the machine running the MCP engine. An imported assetId avoids exposing/guessing managed file paths."],
        "examples": {"oneFrameAt30fps": {"num": "1", "den": "30"},
                     "oneAndHalfSeconds": {"num": "3", "den": "2"}}},
    "editing": {
        "title": "Editing semantics and manual takeover",
        "rules": [
            "clip.insert defaults to a non-overlapping placement. mode=overwrite replaces intersecting content; mode=insert opens space and shifts following content. Use explicit times, not a guessed append mode.",
            "clip.move mode=reorder places a whole clip before/after anchorClipId and adjusts neighbors; plain move refuses same-track overlaps. clip.rippleDelete closes the deleted interval; clip.remove leaves the gap.",
            "A transition is attached to the incoming clip. The preceding clip must be adjacent on the same track and both need sufficient duration. Use resources_list family=transition, then builtinPreset.apply; inspect before, during and after the cut.",
            "Create titles with clip.insert createTrackKind=text and text.content. They remain editable text clips; captions are separate caption objects. clip.keyframe supports add/batch/remove.",
            "effect.setAnimation replaces only its entry/exit/loop/combined slot; an empty effectId clears the selected slot or all animation slots. Preserve or explicitly replace opacity keyframes using keyframePolicy.",
            "edit.batch supports only its returned allowedCommands and cannot nest. Query edit.batch schema and each child command first. Previews must use the same expectedRevision and payload as the eventual commit.",
            "Acquire edit_lock before grouped agent edits. Use lease.leaseId as command_apply.editLeaseId; renew before expiry, release even on failure. Avoid holding a lease throughout a long export.",
            "For a precise correction, project_lookup locates clip/caption IDs and selected fields. Change only requested fields. history.undo/redo changes revision too; reread after undo.",
            "When multiple captions match the same text at the same time (e.g. a subtitle and a callout), request position/style fields such as y and fontSize so you can distinguish them. Ask the user which one to edit instead of patching the first hit.]},
    "errors": {
        "title": "Recovering from errors",
        "rules": [
            "Check ok, error.code, error.committed and error.recovery. Never assume a failed response means nothing was committed.",
            "If a response is lost, retry identical arguments with the same commandId. Changing the ID can duplicate an edit. New payloads require new IDs.",
            "Only adopt a non-empty returned revision. Some project-independent read/analysis commands return an empty revision; keep the last project revision. export.enqueue returns its frozen snapshot revision without advancing it.",
            "REVISION_CONFLICT: reread project_summary and the affected entities, reconcile with the user's intent, then prepare a new command. Do not blindly replace expectedRevision and resend.",
            "INVALID_ARGUMENT: query that command's help/schema, fix the indicated field, and preview again. Do not loop the same invalid payload.",
            "EDIT_LOCKED: wait for the current editor or ask the user to take over; never silently steal/release someone else's lease.",
            "ASSET_MISSING/ASSET_CHANGED: inspect the reported source and use media_list/media_import plus asset.swap to relink the intended media. Do not substitute unrelated files.",
            "EFFECT_UNAVAILABLE or insufficient transition handles: discover a supported effect, check its appliesTo and adjacent clips/durations, then retry a revised edit.",
            "Failed preview/export: read the job error, fix the cause, then submit a new task. Poll at bounded intervals and report failure or timeout honestly." ]},
}
GUIDES['local-models']={
    'title':'Optional local neural matting and agent vision requirements',
    'rules':[
        'Local model inference does not call your LLM or upload footage. Model/runtime installation is optional; status never downloads anything.',
        'FIRST assess whether YOUR CURRENT model can accept images AND YOUR HOST forwards actual tool-returned images. A model name, MCP connection, tool availability or sampling support does not prove this. If either is false or unknown, do not use AI matting/visual review; ask the user to choose points and review the result manually.',
        'Native video understanding is optional: preview_frames returns PNGs at requested times. Images must actually be readable by you. Do not infer a visual target from a filename or invent coordinates.',
        'Call local_model_status. MediaPipe semantic mode keeps people; SAM2 follows a selected object. Missing weights/runtime produce an actionable unavailable status; no fake masks or test audio fallback.',
        'For first use call extension_status then extension_plan(extensionId=person or object). Explain the purpose, download/installed size, disk budget and device suitability briefly. A general editing instruction is not installation consent. Ask the user to accept/decline; an already explicit instruction to install this extension is sufficient. Record the actual response with extension_confirm and pass its confirmationId to extension_install. No shell, Python or Git setup is needed.',
        'Read or cancel extension_job with bounded polling. UI and MCP share the same persisted job. Retrying an unchanged confirmed plan reuses cached downloads and needs no new approval; switching trusted mirrors within that plan needs no new approval. Decline means continue ordinary editing without repeatedly asking. Plan/status never downloads.',
        'sourceMode auto probes trusted origins. china/global prefer regional origins with verified fallbacks; offline makes no network request and requires an extension ZIP. Use installDirectory on a disk with enough space. Unsupported platforms or insufficient RAM/space must remain unavailable, never pretend successful installation. Completion runs a real inference smoke test, not a benchmark or acceptance of the user clip.',
        'Query the source with media_inspect and inspect source images with preview_frames. Points are normalized SOURCE coordinates, labels 1 include/0 exclude; prompt times are absolute source seconds inside the selected clip range, not timeline seconds.',
        'Call local_matting_start with projectId, current expectedRevision, clipId, agentCapabilities={selfAssessed:true,imageInput:true,toolImageInput:true}, trackingMode=sam2, selectionMode=selected, selectionPrompts. Assert true ONLY after assessing actual support.',
        'Poll local_matting_job about once per second with a bounded timeout. Inspect outputPath via preview_frames. A completed job is not proof of correct segmentation, fine hair, alpha edges or tracking continuity.',
        'Acquire an edit lease, reread revision, then local_matting_apply with jobId, expectedRevision, editLeaseId, stable commandId and the same self-assessed vision capabilities. One source swap is one undo; always release the lease. Manual UI jobs and agent jobs share their persisted metadata.',
        'If unsupported, unavailable, cancelled or uncertain, report it honestly. Do not download models silently, create a replacement fake mask, submit guessed prompts or claim to have inspected unseen frames.'],
    'tools':['local_model_status','extension_status','extension_plan','extension_confirm','extension_install','extension_job','extension_export_offline','preview_frames','local_matting_start','local_matting_job','local_matting_apply']}
GUIDES['agent-bridge']={
    'title':'Explicit UI handoff to an external agent',
    'automaticWake':False,
    'rules':[
        'The existing activity/lease UI is not a mechanism to launch a users AI. Human local matting runs independently and requires no external AI.',
        'An explicit Request AI selection/review action creates a persistent project/revision/clip-scoped request. It does not start a model, bill a service, acquire an edit lease or apply edits.',
        'An already active, user-authorized external agent can register agent_session with SELF-ASSESSED image/tool-image capabilities and call agent_requests(operation=wait,projectId,agentId,sessionToken,waitSeconds<=25). That pending tool call returns when the UI submits a request. Unknown/unsupported vision must be false and cannot claim/wait for these visual tasks.',
        'Merely registering or connecting MCP does not wake an idle host. If no suitable active listener exists, the UI shows queued/manual handoff and offers copyable instructions. Full unattended wakeup requires an explicit host-side connector; this server does not pretend universal sampling/notification support.',
        'Use agent_requests operation=claim, inspect the selected source/output frames, and stay inside the request scope. Stale revision requests cannot be newly claimed. Claims expire in 120s, so use a bounded task and claim again after expiry if still valid.',
        'Complete/decline with a small structured result. Completion requires result.reviewedRevision matching the current revision, and actual frame inspection at that revision. Claiming or completing is not a timeline edit or visual acceptance by itself. Requested edits still use current revision/lease and user-visible undo. Never include sessionToken in public output.',
        'Close the session when finished. Do not run an endless polling loop after the authorized task has ended or claim that notifications alone have activated the users model.'],
    'tools':['agent_session','agent_requests']}


def rat(seconds):
    return {"num": str(seconds), "den": "1"}


RECIPES = {
    "layered-shot": {
        "title": "Reuse a layered shot and archive its production evidence",
        "steps": [
            {"tool": "editor_help", "arguments": {"topic": "command", "command": "clip.duplicateGroup"}},
            {"tool": "command_preview", "arguments": {"projectId": "<PROJECT>", "expectedRevision": "<REVISION>",
             "type": "clip.duplicateGroup", "payload": {"clipIds": ["<BASE_CLIP>"], "includeAttached": True, "timelineStart": rat(8)}}},
            {"tool": "command_apply", "reuse": "Commit the same payload with a lease and stable commandId. changedEntities maps sourceClipId to each new clip ID. Explicit clipIds may span tracks. Existing overlap and track-lock rules still apply."},
            {"tool": "preview_frames", "arguments": {"projectId": "<PROJECT>", "expectedRevision": "<REVISION>", "times": [8.5]}},
            {"tool": "project_archive", "arguments": {"projectId": "<PROJECT>", "expectedRevision": "<REVISION>", "outPath": "<ABSOLUTE_CVKPKG_PATH>", "frameTimes": [8.5]}}],
        "notes": ["The archive contains current project media, retained undo/redo snapshots and actor events. Old snapshots may still reference old local paths; frames are not a screen recording."]},
    "assemble": {
        "title": "Build an editable clip with a title and caption",
        "bindings": {"PROJECT": "projectId from create_project", "REVISION": "current revision from the previous result",
                     "ASSET": "assetId returned by media_import/media_list; use an image or a video at least 3 seconds long",
                     "LEASE": "lease.leaseId from edit_lock", "COMMAND_ID": "fresh unique ID for this edit; keep it for retries"},
        "steps": [
            {"tool": "create_project", "arguments": {"name": "New edit", "width": 960, "height": 540, "fps": 30}},
            {"tool": "media_import", "arguments": {"paths": ["<ABSOLUTE_MEDIA_PATH>"]}},
            {"tool": "edit_lock", "arguments": {"projectId": "<PROJECT>", "action": "acquire", "owner": "external-agent", "ttlSeconds": 120}},
            {"tool": "command_preview", "arguments": {"projectId": "<PROJECT>", "expectedRevision": "<REVISION>", "type": "edit.batch", "payload": {"commands": [
                {"type": "clip.insert", "payload": {"trackId": "visual", "createTrackKind": "video", "clipId": "shot", "assetId": "<ASSET>", "timelineStart": rat(0), "timelineEnd": rat(3)}},
                {"type": "clip.insert", "payload": {"trackId": "titles", "createTrackKind": "text", "clipId": "title", "timelineStart": rat(0), "timelineEnd": rat(3), "text": {"content": "我的短片", "fontSize": 48, "x": 0.5, "y": 0.3}}},
                {"type": "caption.add", "payload": {"captionId": "caption", "text": "开始新的故事", "start": rat(0), "end": rat(3), "fontSize": 32}}]}}},
            {"tool": "command_apply", "reuse": "Use the SAME project/revision/type/payload as the successful preview; add editLeaseId=<LEASE> and commandId=<COMMAND_ID>. Capture returned revision."},
            {"tool": "preview_frames", "arguments": {"projectId": "<PROJECT>", "expectedRevision": "<REVISION>", "times": [0.5, 1.5, 2.5], "width": 960}},
            {"tool": "edit_lock", "arguments": {"projectId": "<PROJECT>", "action": "release", "leaseId": "<LEASE>"}}],
        "notes": ["IDs in this example belong to a NEW empty project; use existing real IDs when editing an existing project.", "Check source duration before insertion; choose a shorter interval for a short source. Always release the lease in a finally step."]},
    "caption-fix": {
        "title": "Precisely replace one caption",
        "steps": [
            {"tool": "project_lookup", "arguments": {"projectId": "<PROJECT>", "entityType": "caption", "textContains": "<OLD_TEXT>"}},
            {"tool": "editor_help", "arguments": {"topic": "command", "command": "caption.update"}},
            {"tool": "command_preview", "arguments": {"projectId": "<PROJECT>", "expectedRevision": "<REVISION>", "type": "caption.update", "payload": {"captionId": "<CAPTION_ID>", "text": "<NEW_TEXT>"}}},
            {"tool": "command_apply", "reuse": "Acquire a lease, commit identical preview arguments with stable commandId and editLeaseId, then inspect a frame inside the caption and release the lease."}]},
    "export": {
        "title": "Export and deliver the editable project",
        "steps": [
            {"tool": "command_preview", "arguments": {"projectId": "<PROJECT>", "expectedRevision": "<REVISION>", "type": "project.preflight", "payload": {}}},
            {"tool": "command_apply", "arguments": {"projectId": "<PROJECT>", "expectedRevision": "<REVISION>", "commandId": "<COMMAND_ID>", "type": "export.enqueue", "payload": {"outPath": "<ABSOLUTE_MP4_PATH>", "quality": "high", "overwrite": False}}},
            {"tool": "export_job", "arguments": {"projectId": "<PROJECT>", "jobId": "<JOB_ID>"}, "repeat": "Wait pollAfterSeconds seconds between export_job calls while status is queued/running (currently 1). pollAfterSeconds=null means terminal: stop polling, use result.output_path on succeeded, or read error on failed/cancelled/interrupted. Keep a bounded timeout."},
            {"tool": "project_package", "arguments": {"projectId": "<PROJECT>", "expectedRevision": "<REVISION>", "outPath": "<ABSOLUTE_PACKAGE_PATH>"}}]},
}


class EditorGuide:
    def __init__(self, service):
        self.service = service

    def help(self, arguments):
        topic = arguments.get("topic", "overview")
        if "guide" in arguments and topic != "creative":
            raise ValueError("guide requires topic=creative")
        if topic == "creative" and "guide" in arguments:
            return creative_document(arguments["guide"])
        if topic == "command":
            name = arguments.get("command")
            if not name:
                raise ValueError("topic=command requires command; use commands_list to discover names")
            found = next((entry for entry in self.service.command_catalog() if entry["type"] == name), None)
            if found is None:
                raise ValueError(f"Unknown command: {name}; use commands_list(query=...) to find supported commands")
            result = copy.deepcopy(found)
            if arguments.get("effectId"):
                from cutvoke.editing.command_schema import normalize_schema
                from cutvoke.rendering.effects import EffectNotFound
                try:
                    effect = self.service._effects.get(arguments["effectId"]).to_dict()
                except EffectNotFound as exc:
                    raise ValueError(f"Unknown effect: {arguments['effectId']}; discover IDs with resources_list/effects_list") from exc
                effect["parameters"] = normalize_schema(effect["parameters"])
                result["effect"] = effect
                if "params" in result["inputSchema"]["properties"]:
                    result["inputSchema"]["properties"]["params"] = copy.deepcopy(effect["parameters"])
            result["submission"] = {"tool": "command_apply", "required": ["projectId", "expectedRevision", "type", "payload"],
                                    "recommended": ["commandId", "editLeaseId"], "payloadSchema": "inputSchema above"}
            if name == "clip.keyframe":
                result["conditions"] = {"add": ["param", "time", "value"], "batch": ["keyframes"], "remove": ["param", "keyframeId"]}
            return result
        if topic == "recipes":
            recipe = arguments.get("recipe")
            if recipe:
                if recipe not in RECIPES:
                    raise ValueError(f"Unknown recipe: {recipe}; choose {', '.join(RECIPES)}")
                return copy.deepcopy(RECIPES[recipe])
            return {"recipes": [{"id": key, "title": value["title"]} for key, value in RECIPES.items()]}
        if topic not in GUIDES:
            raise ValueError(f"Unknown help topic: {topic}")
        return copy.deepcopy(GUIDES[topic])

    def resources(self):
        result = [{"uri": f"cutvoke://guide/{topic}", "name": value["title"], "mimeType": "application/json"}
                  for topic, value in GUIDES.items()]
        result.extend({"uri": f"cutvoke://recipe/{key}", "name": value["title"], "mimeType": "application/json"}
                      for key, value in RECIPES.items())
        result.append({"uri": "cutvoke://commands", "name": "Command index (schemas available individually)", "mimeType": "application/json"})
        result.extend({"uri": f"cutvoke://creative/{key}", "name": entry[1], "mimeType": "application/json"}
                      for key, entry in GUIDE_FILES.items())
        return result

    def read(self, uri):
        if not isinstance(uri, str):
            raise ValueError("Resource URI must be a string")
        if uri == "cutvoke://commands":
            value = {"commands": [{"type": item["type"], "description": item["description"],
                      "uri": f"cutvoke://command/{item['type']}"} for item in self.service.command_catalog()]}
        elif uri.startswith("cutvoke://command/"):
            value = self.help({"topic": "command", "command": uri.removeprefix("cutvoke://command/")})
        elif uri.startswith("cutvoke://creative/"):
            value = creative_document(uri.removeprefix("cutvoke://creative/"))
        elif uri.startswith("cutvoke://guide/"):
            topic = uri.removeprefix("cutvoke://guide/")
            if topic not in GUIDES:
                raise ValueError(f"Unknown resource URI: {uri}")
            value = self.help({"topic": topic})
        elif uri.startswith("cutvoke://recipe/"):
            value = self.help({"topic": "recipes", "recipe": uri.removeprefix("cutvoke://recipe/")})
        else:
            raise ValueError(f"Unknown resource URI: {uri}")
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(value, ensure_ascii=False)}]}


def recovery(code, arguments):
    """Return bounded, actionable hints without changing error/commit semantics."""
    command = (arguments.get("type") or arguments.get("command")) if isinstance(arguments, dict) else None
    hints = {
        "REVISION_CONFLICT": "Read project_summary and affected entities; reconcile changes, preview a newly planned edit, and use a new commandId. Do not blindly resend with a changed revision.",
        "IDEMPOTENCY_MISMATCH": "This commandId was already used for different arguments. Retry the original arguments or assign a new ID to the new edit.",
        "INVALID_ARGUMENT": "Read the indicated field and exact schema, correct it, then preview again. Do not repeat identical invalid arguments.",
        "EDIT_LOCKED": "Read project_summary lease state. Wait or ask for human takeover; do not steal another editor's lease.",
        "EDIT_LEASE_INVALID": "Read the latest project and discard the stale edit plan. Wait for a new human handoff before acquiring a fresh lease. Do not retry or renew the old token, and never automatically reclaim editing after human takeover.",
        "ASSET_MISSING": "Inspect the reported path; locate/import the intended source and use asset.swap to relink affected clips.",
        "ASSET_CHANGED": "Inspect the intended source again, confirm its identity/duration, and relink before retrying.",
        "EFFECT_UNAVAILABLE": "Discover a supported effect using resources_list/effects_list and request its exact schema.",
        "INSUFFICIENT_HANDLES": "Inspect the incoming and preceding clips on the same track; shorten the transition or provide enough adjacent media."}
    if code=='AGENT_VISION_REQUIRED':
        return {'message':'Do not use the AI matting/visual-review workflow unless your current model AND host support actual tool-returned images. If unsupported or unknown, use the manual UI; never assert vision=true merely to bypass this check.',
                'help':{'tool':'editor_help','arguments':{'topic':'local-models'}}}
    help_args = {"topic": "command", "command": command} if command and code == "INVALID_ARGUMENT" else {"topic": "errors"}
    return {"message": hints.get(code, "Inspect the error and job state, correct the cause, and retry only when appropriate. Check committed before replaying an edit."),
            "help": {"tool": "editor_help", "arguments": help_args}}
