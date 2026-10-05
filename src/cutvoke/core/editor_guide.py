"""Bundled MCP onboarding. Only editing documentation; no model service."""
from __future__ import annotations

import copy
import json


INSTRUCTIONS = (
    "CutVoke is a local video editor controlled by this MCP server. Start with editor_help({}) "
    "for the workflow and conventions; no external skill or source-code access is needed. "
    "Use project_list/project_summary to identify the project, media_import/media_list for assets. "
    "Before an unfamiliar edit, call editor_help(topic='command', command='<type>') or commands_list; "
    "for effects also supply effectId to obtain its exact params schema. Never invent IDs or parameters. "
    "Batch edits use edit_lock, command_preview, then command_apply with the current expectedRevision, "
    "a stable commandId and editLeaseId. Read error.recovery after failure. Verify actual pictures with "
    "preview_frames, export asynchronously, and release the lease for human takeover. "
    "Intelligence remains in your external agent; this application executes edits and returns evidence."
)

GUIDES = {
    "overview": {
        "title": "Start editing with CutVoke",
        "rules": [
            "Use only the connected tools. No shell, repository, external documentation or additional Skill is required for ordinary editing.",
            "The user supplies the intended edit and access to source media. If several projects fit the request, clarify the target; do not guess.",
            "IDs returned by queries are authoritative. Never infer a clip ID from its position or filename.",
            "All changes persist in the shared editable project. The desktop editor and MCP see the same database.",
            "Inspect actual preview_frames images before claiming visual quality. A successful command or export alone is not visual acceptance."],
        "workflow": [
            {"tools": ["runtime", "project_list", "project_summary"], "purpose": "Check the connected data directory; locate the requested project and current revision. Use create_project for a new edit or project_clone for an alternate version."},
            {"tools": ["media_list", "media_import", "media_inspect", "preview_frames"], "purpose": "Locate/import local files, inspect source duration and actual pictures. Imported assetId can be used directly in clip.insert."},
            {"tools": ["editor_help", "commands_list", "resources_list"], "purpose": "Get command schemas or a recipe; discover compatible effects/presets instead of guessing. resources_list is the preset catalog, distinct from MCP document resources."},
            {"tools": ["edit_lock", "command_preview", "command_apply"], "purpose": "Acquire a lease, preview supported edits without committing, apply with revision and stable commandId. edit.batch commits 1..32 changes atomically as one undo point. Use each returned revision for the next edit; renew the lease during long editing sessions."},
            {"tools": ["preview_frames", "preview_prepare", "preview_job"], "purpose": "Check text/effects/transition boundaries visually; prepare continuous preview if needed. Preparation is asynchronous. Keep jobId; poll about once per second with a bounded timeout, not a busy loop."},
            {"tools": ["command_apply", "export_job", "project_package", "edit_lock"], "purpose": "Run project.preflight, enqueue export, poll until succeeded, return result.output_path. Export uses the submitted snapshot; later edits do not change it. Package the editable project when requested. Release the lease in a finally step, including after failure."}],
        "help": {"conventions": {"topic": "time"}, "editing": {"topic": "editing"},
                 "recovery": {"topic": "errors"}, "examples": {"topic": "recipes"},
                 "command": {"topic": "command", "command": "clip.insert"}},
        "modelServices": False},
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
            "For a precise correction, project_lookup locates clip/caption IDs and selected fields. Change only requested fields. history.undo/redo changes revision too; reread after undo."]},
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


def rat(seconds):
    return {"num": str(seconds), "den": "1"}


RECIPES = {
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
            {"tool": "export_job", "arguments": {"projectId": "<PROJECT>", "jobId": "<JOB_ID>"}, "repeat": "About once per second until status=succeeded; use result.output_path. Stop and read error on failed/cancelled/interrupted, or report a bounded timeout."},
            {"tool": "project_package", "arguments": {"projectId": "<PROJECT>", "expectedRevision": "<REVISION>", "outPath": "<ABSOLUTE_PACKAGE_PATH>"}}]},
}


class EditorGuide:
    def __init__(self, service):
        self.service = service

    def help(self, arguments):
        topic = arguments.get("topic", "overview")
        if topic == "command":
            name = arguments.get("command")
            if not name:
                raise ValueError("topic=command requires command; use commands_list to discover names")
            found = next((entry for entry in self.service.command_catalog() if entry["type"] == name), None)
            if found is None:
                raise ValueError(f"Unknown command: {name}; use commands_list(query=...) to find supported commands")
            result = copy.deepcopy(found)
            if arguments.get("effectId"):
                from .command_schema import normalize_schema
                from .effects import EffectNotFound
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
        return result

    def read(self, uri):
        if not isinstance(uri, str):
            raise ValueError("Resource URI must be a string")
        if uri == "cutvoke://commands":
            value = {"commands": [{"type": item["type"], "description": item["description"],
                      "uri": f"cutvoke://command/{item['type']}"} for item in self.service.command_catalog()]}
        elif uri.startswith("cutvoke://command/"):
            value = self.help({"topic": "command", "command": uri.removeprefix("cutvoke://command/")})
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
        "ASSET_MISSING": "Inspect the reported path; locate/import the intended source and use asset.swap to relink affected clips.",
        "ASSET_CHANGED": "Inspect the intended source again, confirm its identity/duration, and relink before retrying.",
        "EFFECT_UNAVAILABLE": "Discover a supported effect using resources_list/effects_list and request its exact schema.",
        "INSUFFICIENT_HANDLES": "Inspect the incoming and preceding clips on the same track; shorten the transition or provide enough adjacent media."}
    help_args = {"topic": "command", "command": command} if command and code == "INVALID_ARGUMENT" else {"topic": "errors"}
    return {"message": hints.get(code, "Inspect the error and job state, correct the cause, and retry only when appropriate. Check committed before replaying an edit."),
            "help": {"tool": "editor_help", "arguments": help_args}}
