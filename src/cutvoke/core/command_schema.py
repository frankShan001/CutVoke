"""Public command payload schemas, shared by every editor interface.

Effect and title parameter schemas come from the effect registry. These schemas
describe command inputs; EditService remains the authority for edit invariants.
"""
from __future__ import annotations

import copy


STRING = {"type": "string"}
NUMBER = {"type": "number"}
BOOL = {"type": "boolean"}
RATIONAL = {
    "type": "object", "properties": {
        "num": {"type": ["string", "integer"], "pattern": "^-?[0-9]+$"},
        "den": {"type": ["string", "integer"], "pattern": "^[1-9][0-9]*$", "minimum": 1}},
    "required": ["num", "den"], "additionalProperties": False,
    "description": 'Exact seconds. Prefer decimal strings: {"num":"3","den":"2"} = 1.5 seconds.'}
SECONDS = {"anyOf": [NUMBER, RATIONAL]}
IDS = {"type": "array", "items": STRING}
EFFECT = {"type": "object", "properties": {
    "effectId": STRING, "params": {"type": "object", "description": "Obtain this effect's schema from editor_help(command=..., effectId=...)."},
    "version": STRING}, "required": ["effectId"]}
WORDS = {"type": "array", "maxItems": 10000, "items": {
    "type": "object", "properties": {"text": STRING, "start": RATIONAL, "end": RATIONAL},
    "required": ["text", "start", "end"],
    "description": "Absolute timeline times, ordered and inside the parent caption."}}
CAPTION_STYLE = {
    **{k: STRING for k in ("color", "strokeColor", "background", "wordHighlightColor")},
    "fontFamily": {"type": "string", "enum": ["Noto Sans SC", "Noto Serif SC"]},
    **{k: {"type": "integer"} for k in ("fontSize", "strokeWidth", "animIn", "animOut")},
    "align": {"type": "string", "enum": ["left", "center", "right"]}, "bold": BOOL,
    "animInStyle": {"type": "string", "enum": ["none", "fade", "scale", "typewriter"]},
    "animLoopStyle": {"type": "string", "enum": ["none", "pulse", "blink"]},
    "animLoopMs": {"type": "integer", "minimum": 300, "maximum": 3000},
    **{k: {"type": "number", "minimum": 0, "maximum": 1} for k in ("x", "y")},
    "scale": {"type": "number", "minimum": 0.1, "maximum": 5},
    "rotation": {"type": "number", "minimum": -180, "maximum": 180},
    "shadow": {"type": "integer", "minimum": 0, "maximum": 20}}
STYLE = {"type": "object", "properties": CAPTION_STYLE, "additionalProperties": False}
KEYFRAME = {"type": "object", "properties": {
    "param": {"type": "string", "enum": ["opacity", "x", "y", "scale", "rotation"]},
    "time": {**SECONDS, "description": "Clip-local presentation seconds, not absolute timeline time."},
    "value": {"type": "number", "description": "opacity 0..1; scale 0.05..5; rotation -180..180; x/y in canvas pixels."},
    "interpolation": {"type": "string", "enum": ["linear", "ease-in", "ease-out"]}},
    "required": ["param", "time", "value"], "additionalProperties": False}

_BOOLEAN_FIELDS = set("locked muted visible switch enabled bold preservePitch followAttachments applyToProject clearExisting setCanvas overwrite versioned transparent".split())
_INTEGER_FIELDS = set("width height fontSize strokeWidth animIn animOut toIndex limit maxWords videoBitrateKbps audioBitrateKbps".split())
_NUMBER_FIELDS = set("fps volume fadeIn fadeOut pitch stickerScale duration strength amount I seconds minSilence sharpness denoise overlap rotation scale x y value timelineTime t".split())
_RATIONAL_FIELDS = set("timelineStart timelineEnd sourceStart sourceTime start end at fromTime toTime".split())
_ID_ARRAY_FIELDS = set("clipIds targetClipIds captionIds trackIds ids order".split())
_STRING_FIELDS = set("activeTrackId afterClipId align anchorClipId anchorPosition assetId attachedToClipId audioClipId audioPath audioTrackId axis background beforeClipId captionId category clipId color colorSpace createTrackKind createTrackRole device effectId fontFamily frameInterpolation gifPath groupId interpolation jobId keyframeId kind language markerId method mode model name newClipId outDir outPath param path presetId projectId quality role sequenceId slot sourceClipId sourceColorSpace sourcePath sourceSignature sourceTransfer status stickerId strokeColor target templateId text textPresetId toneMap trackId unit version videoPath wordHighlightColor action".split())


def normalize_schema(value):
    """Registry descriptions are localized maps; JSON Schema requires strings."""
    if isinstance(value, list):
        return [normalize_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: normalize_schema(item) for key, item in value.items()}
    if isinstance(result.get("description"), dict):
        result["description"] = result["description"].get("zh-CN", next(iter(result["description"].values()), ""))
    return result


def payload_schema(command: str, params: list[dict], effects: dict | None = None) -> dict:
    """Derive public fields from the registered command catalog, not a tool copy."""
    effects = effects or {}
    properties, required = {}, []
    for param in params:
        name = param["name"]
        if name in _BOOLEAN_FIELDS:
            field = BOOL
        elif name in _INTEGER_FIELDS:
            field = {"type": "integer"}
        elif name in _NUMBER_FIELDS:
            field = NUMBER
        elif name in _RATIONAL_FIELDS:
            field = RATIONAL
        elif name in _ID_ARRAY_FIELDS:
            field = IDS
        elif name in _STRING_FIELDS:
            field = STRING
        elif name in ("speed", "time", "offset"):
            field = SECONDS
        elif name == "words":
            field = WORDS
        elif name == "effects":
            field = {"type": "array", "items": EFFECT}
        elif name == "stickerAnimation":
            field = EFFECT
        elif name == "style":
            field = STYLE
        elif name == "range":
            field = {"type": "object", "properties": {
                "start": {"type": ["number", "string"]}, "end": {"type": ["number", "string"]}},
                "required": ["start", "end"], "additionalProperties": False}
        elif name == "curve":
            field = {"type": "object", "properties": {"points": {
                "type": "array", "minItems": 2, "items": {"type": "object", "properties": {
                    "at": {"type": "number", "minimum": 0, "maximum": 1},
                    "speed": {"type": "number", "minimum": 0.1, "maximum": 8}},
                    "required": ["at", "speed"]}}}, "required": ["points"]}
        elif name == "sources":
            field = {"type": "object", "additionalProperties": STRING}
        elif name == "commands":
            field = {"type": "array", "minItems": 1, "maxItems": 32, "items": {
                "type": "object", "properties": {"type": STRING, "payload": {"type": "object"}},
                "required": ["type", "payload"], "additionalProperties": False}}
        elif name == "segments":
            field = {"type": "array", "minItems": 1, "maxItems": 1000, "items": {
                "type": "object", "properties": {"text": STRING, "start": RATIONAL, "end": RATIONAL, "words": WORDS},
                "required": ["text", "start", "end"]}}
        elif name == "updates":
            field = {"type": "array", "minItems": 1, "items": {
                "type": "object", "properties": {**CAPTION_STYLE, "captionId": STRING,
                    "text": STRING, "start": RATIONAL, "end": RATIONAL, "words": WORDS},
                "required": ["captionId"], "additionalProperties": False}}
        elif name == "params":
            field = {"type": "object", "description": "Effect-dependent fields; request editor_help with command and effectId before editing."}
        else:
            raise ValueError(f"Undocumented command field type: {command}.{name}")
        properties[name] = {**copy.deepcopy(field), "description": param["description"]}
        if param.get("required"):
            required.append(name)
    if command.startswith("caption.") and command in ("caption.add", "caption.update"):
        properties.update(copy.deepcopy(CAPTION_STYLE))
    if command == "clip.insert":
        properties["text"] = normalize_schema(effects.get("cutvoke.text", {"type": "object"}))
        properties["text"]["required"] = ["content"]
        properties["mode"]["enum"] = ["overwrite", "insert", ""]
        properties["createTrackKind"]["enum"] = ["video", "audio", "text"]
    if command == "clip.attach":
        properties["attachedToClipId"]["type"] = ["string", "null"]
    if command == "clip.keyframe":
        properties.update(copy.deepcopy(KEYFRAME["properties"]))
        properties["action"] = {"type": "string", "enum": ["add", "batch", "remove"], "default": "add"}
        properties["keyframes"] = {"type": "array", "minItems": 1, "items": KEYFRAME}
        required = ["clipId"]
    if command == "clip.batch":
        properties["params"] = {"type": "object", "properties": {
            "speed": SECONDS, **{k: NUMBER for k in ("volume", "fadeIn", "fadeOut", "pitch", "opacity")},
            "animation": {"anyOf": [STRING, EFFECT]}, "transition": {"anyOf": [STRING, EFFECT]}},
            "minProperties": 1, "additionalProperties": False}
    if command == "effect.setAnimation":
        properties["slot"]["enum"] = ["入场", "出场", "循环", "组合"]
        properties["keyframePolicy"] = {"type": "string", "enum": ["combine", "replace"], "default": "combine"}
    if "quality" in properties:
        properties["quality"]["enum"] = ["high", "medium", "low"]
    if command == "clip.move":
        properties["mode"]["enum"] = ["move", "reorder"]
        properties["anchorPosition"]["enum"] = ["before", "after"]
    result = {"type": "object", "properties": properties, "required": required,
              "additionalProperties": True}
    if command == "clip.keyframe":
        result["anyOf"] = [
            {"properties": {"action": {"enum": ["add"]}}, "required": ["param", "time", "value"]},
            {"properties": {"action": {"enum": ["batch"]}}, "required": ["action", "keyframes"]},
            {"properties": {"action": {"enum": ["remove"]}}, "required": ["action", "param", "keyframeId"]}]
    if command == "track.add":
        properties["kind"]["enum"] = ["video", "audio", "text"]
    return result
