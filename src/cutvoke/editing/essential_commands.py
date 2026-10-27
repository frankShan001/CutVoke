"""Shared compositions, exposed controls and bounded nested editing."""
from __future__ import annotations

import copy
import uuid

from cutvoke.domain.essential_properties import apply_overrides, control_spec, find_clip
from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational


def payload_schema(command):
    from cutvoke.editing.command_schema import STRING, SECONDS
    if command == "media.trackMask":
        point={"type":"object","properties":{"x":{"type":"number","minimum":0,"maximum":1},"y":{"type":"number","minimum":0,"maximum":1}},"required":["x","y"],"additionalProperties":False}
        properties = {"sourcePath": STRING, "start": {"type":"number","minimum":0}, "seconds":{"type":"number","minimum":.1,"maximum":30},
            "sampleFps":{"type":"number","minimum":1,"maximum":15}, "pathMode":{"type":"string","enum":["linear","smooth","bezier"]},
            "points":{"type":"array","minItems":3,"maxItems":128,"items":{**point,"properties":{**point['properties'],
                "inHandle":point,"outHandle":point,"handlesLinked":{"type":"boolean"}}}}}
        required = ["sourcePath", "points"]
    elif command == "clip.motionPath":
        properties = {"clipId": STRING, "points": {"type": ["array", "null"], "minItems": 2, "maxItems": 32, "items": {
            "type": "object", "properties": {**{key: {"type": "number", "minimum": -100000, "maximum": 100000} for key in ("x", "y", "inX", "inY", "outX", "outY")},
                "at": {"type": "number", "minimum": 0, "maximum": 86400}, "roving": {"type": "boolean"},
                "timeEase": {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "number", "minimum": 0, "maximum": 1}}},
            "required": ["x", "y"], "additionalProperties": False}}}
        required = ["clipId", "points"]
    elif command == "clip.bind":
        properties = {"clipId": STRING, "param": STRING, "binding": {"type": ["object", "null"], "properties": {
            "source": {"type": "object", "properties": {"type": {"type": "string", "enum": ["property", "sine", "time", "constant"]},
                "clipId": STRING, "param": STRING, **{key: {"type": "number"} for key in ("amplitude", "frequency", "phase", "value")}}, "additionalProperties": False},
            **{key: {"type": "number"} for key in ("scale", "offset", "timeOffset")}}, "required": ["source"], "additionalProperties": False}}
        required = ["clipId", "param", "binding"]
    elif command == "composition.profile":
        properties = {"start": {"type": "number", "minimum": 0}, "duration": {"type": "number", "minimum": .1, "maximum": 5},
                      "maximum": {"type": "integer", "enum": [320, 640, 960, 1280]}}
        required = []
    elif command == "composition.instance":
        properties = {"sequenceId": STRING, "trackId": STRING, "clipId": STRING, "timelineStart": SECONDS, "timelineEnd": SECONDS}
        required = ["sequenceId", "timelineEnd"]
    elif command == "composition.expose":
        properties = {"sequenceId": STRING, "clipId": STRING, "controls": {"type": "array", "maxItems": 128, "items": {
            "type": "object", "properties": {key: STRING for key in ("id", "label", "clipId", "param")},
            "required": ["id", "clipId", "param"], "additionalProperties": False}}}
        required = ["controls"]
    elif command == "composition.override":
        properties = {"clipId": STRING, "values": {"type": "object", "maxProperties": 128}}
        required = ["clipId", "values"]
    elif command == "composition.edit":
        properties = {"path": {"type": "array", "minItems": 1, "maxItems": 16, "items": STRING}, "command": {
            "type": "object", "properties": {"type": STRING, "payload": {"type": "object"}},
            "required": ["type", "payload"], "additionalProperties": False}}
        required = ["path", "command"]
    else:
        return None
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def register(service):
    from cutvoke.editing.service import EditError, _BATCH_EDIT_COMMANDS
    from cutvoke.domain.protocol import ErrorCode
    def invalid(message):
        return EditError(ErrorCode.INVALID_ARGUMENT, str(message))
    def target(project, clip_id):
        clip = find_clip(project.sequence, clip_id)
        if clip is None:
            raise invalid(f"composition clip not found: {clip_id}")
        if any(track.locked and clip in track.clips for track in project.sequence.tracks):
            raise invalid("track is locked")
        return clip
    def source(project, clip):
        child = clip.nested or next((seq for seq in project.sequences if seq.id == clip.sequence_ref), None)
        if child is None:
            raise invalid("clip is not a composition instance")
        return child
    def time(value, default=0):
        return Rational.from_json(**value) if isinstance(value, dict) else Rational.from_float(value if value is not None else default)
    def instance(project, payload):
        sequence_id = payload.get("sequenceId")
        if not any(seq.id == sequence_id for seq in project.sequences):
            raise invalid(f"composition not found: {sequence_id}")
        try:
            start, end = time(payload.get("timelineStart")), time(payload.get("timelineEnd"))
            if start < Rational.of(0) or end <= start:
                raise ValueError("instance requires 0 <= start < end")
            track_id = payload.get("trackId") or "comptrack_" + uuid.uuid4().hex[:10]
            track = next((track for track in project.sequence.tracks if track.id == track_id), None)
            if track is None:
                track = Track(track_id, "video")
                project.sequence.tracks.append(track)
            if track.kind != "video" or track.locked or track.role == "adjustment":
                raise ValueError("choose an unlocked picture track")
            clip_id = payload.get("clipId") or "comp_" + uuid.uuid4().hex[:12]
            track.clips.append(Clip(clip_id, AssetReference("", ""), start, end, Rational.of(0), sequence_ref=sequence_id))
        except (ValueError, TypeError, ZeroDivisionError) as error:
            raise invalid(error) from error
        return [{"type": "clip", "id": clip_id, "change": "inserted"}]
    def expose(project, payload):
        seq = source(project, target(project, payload["clipId"])) if payload.get("clipId") else next(
            (seq for seq in project.sequences if seq.id == payload.get("sequenceId", project.sequence.id)), None)
        if seq is None:
            raise invalid("composition not found")
        controls = payload.get("controls")
        if not isinstance(controls, list) or len(controls) > 128:
            raise invalid("controls must be an array of at most 128 entries")
        try:
            for control in controls:
                control_spec(seq, control)
        except (ValueError, TypeError, AttributeError) as error:
            raise invalid(error) from error
        seq.exposed_properties = copy.deepcopy(controls)
        return [{"type": "sequence", "id": seq.id, "change": "exposedProperties"}]
    def override(project, payload):
        clip = target(project, payload.get("clipId"))
        try:
            apply_overrides(copy.deepcopy(source(project, clip)), payload.get("values"))
        except (ValueError, KeyError, TypeError) as error:
            raise invalid(error) from error
        clip.property_overrides = copy.deepcopy(payload["values"])
        return [{"type": "clip", "id": clip.id, "change": "propertyOverrides"}]
    def edit(project, payload):
        path, command = payload.get("path"), payload.get("command")
        if not isinstance(path, list) or not 1 <= len(path) <= 16 or not isinstance(command, dict):
            raise invalid("nested edit requires a path of 1..16 composition clip IDs and command")
        kind, value = command.get("type"), command.get("payload")
        allowed = _BATCH_EDIT_COMMANDS - {"composition.edit"}
        if kind not in allowed or not isinstance(value, dict):
            raise invalid("nested edit only accepts a pure editing command from edit.batch allowedCommands")
        outer = project.sequence
        try:
            for clip_id in path:
                clip = target(project, clip_id)
                project.sequence = source(project, clip)
            changed = service._handlers[kind](project, value)
            return [{**entry, "compositionPath": path} for entry in changed]
        finally:
            project.sequence = outer
    def profile(project, payload):
        from cutvoke.rendering.profiler import measure
        queue = service._export_queue
        if queue is not None and queue.reload_status()["busy"]:
            raise invalid("export is active; run the profiler after export completes")
        try:
            report = measure(project, start=payload.get("start", 0), duration=payload.get("duration", 2), maximum=payload.get("maximum", 640))
        except ValueError as error:
            raise invalid(error) from error
        return [{"type": "composition_profile", **report}]
    def bind(project, payload):
        clip = target(project, payload.get("clipId"))
        from cutvoke.domain.keyframes import parameter_spec
        from cutvoke.domain.controllers import validate
        try:
            parameter_spec(clip, payload.get("param"))
            if payload.get("binding") is None:
                clip.property_links.pop(payload["param"], None)
            else:
                clip.property_links[payload["param"]] = copy.deepcopy(payload["binding"])
            validate(project.sequence)
        except (ValueError, TypeError, AttributeError) as error:
            raise invalid(error) from error
        return [{"type": "clip", "id": clip.id, "change": "propertyLinks"}]
    def motion_path(project, payload):
        clip = target(project, payload.get("clipId"))
        if payload.get("points") is None:
            clip.motion_path = None
        else:
            previous = clip.motion_path or {}
            clip.motion_path = {"points": copy.deepcopy(payload["points"]), "sourceOrigin": previous.get("sourceOrigin", clip.source_start.to_json()), "speedOrigin": previous.get("speedOrigin", clip.speed.to_json())}
            try:
                from cutvoke.domain.spatial_path import validate
                validate(clip)
            except (ValueError, TypeError, KeyError) as error:
                raise invalid(error) from error
        return [{"type": "clip", "id": clip.id, "change": "motionPath"}]
    def track_mask(project, payload):
        from cutvoke.rendering.polygon_tracking import track_polygon
        try:
            report=track_polygon(payload.get('sourcePath',''),payload.get('points'),start=payload.get('start',0),seconds=payload.get('seconds',5),sample_fps=payload.get('sampleFps',8),path_mode=payload.get('pathMode','linear'))
        except ImportError as error:
            raise invalid('mask tracking requires the render extra with OpenCV') from error
        except (ValueError, TypeError, OSError) as error:
            raise invalid(error) from error
        return [{'type':'mask_tracking',**report}]
    service._handlers.update({"composition.instance": instance, "composition.expose": expose,
                              "composition.override": override, "composition.edit": edit, "composition.profile": profile, "clip.bind": bind, "clip.motionPath": motion_path,
                              "media.trackMask": track_mask})
    descriptions = {
        "composition.instance": "Insert a live shared sequence instance; stable sequenceRef, cycle checked, undoable.",
        "composition.expose": "Replace a composition's exposed controls (stable id, label, target clipId and parameter). Existing overrides must remain valid.",
        "composition.override": "Replace only this instance's exposed property overrides; values={} restores the untouched master.",
        "composition.edit": "Edit an embedded/shared nested composition through a bounded clip-ID path; local times, one atomic undo.",
        "composition.profile": "Measure a 0.1..5 second interval twice at a bounded resolution; actual filter timing and cache metrics, read-only.",
        "clip.bind": "Bind a visual scalar property to another property or a bounded sine/time/constant controller; clip-local clocks, stable IDs, cycle checked. binding=null restores underlying keyframes.",
        "clip.motionPath": "Set 2..32 cubic spatial points with separate temporal ease and distance-distributed roving points. Constant-speed clips; points=null restores underlying x/y keyframes.",
        "media.trackMask": "Analyze editable polygon roto with forward/backward optical flow; max 30 source seconds, 128 input anchors, 64 tracked vertices, 256 samples. Supports linear/smooth/bezier source contour. Returns pathFrames, confidence and lostAt without changing the project.",
    }
    for name, description in descriptions.items():
        schema = payload_schema(name)
        service.COMMAND_PARAM_SPECS[name] = {"description": description, "params": [
            {"name": key, "description": key, "required": key in schema["required"]} for key in schema["properties"]]}
