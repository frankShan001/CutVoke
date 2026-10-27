"""Transactional compositing commands; one shared UI/Agent protocol."""
from __future__ import annotations

import copy
import uuid

from cutvoke.domain.compositing import BLEND_MODES, clip_compositing, effect_compositing, validate_sequence
from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational


def payload_schema(command):
    from cutvoke.editing.command_schema import RATIONAL, SECONDS, STRING, BOOL
    region = {"type": ["object", "null"], "properties": {
        **{key: {"type": "number", "minimum": 0, "maximum": 1} for key in ("x", "y", "width", "height", "feather")},
        "invert": BOOL}, "additionalProperties": False}
    if command == "effect.replace":
        properties = {"clipId": STRING, "instanceId": STRING, "effectId": STRING, "params": {"type":"object"}}
        required = ["clipId", "instanceId", "effectId", "params"]
    elif command == "effect.composite":
        properties = {"clipId": STRING, "instanceId": STRING, "compositing": {"type": "object", "properties": {
            "opacity": {"type": "number", "minimum": 0, "maximum": 1}, "blendMode": {"type": "string", "enum": list(BLEND_MODES)},
            "region": region}, "additionalProperties": False}}
        required = ["clipId", "instanceId", "compositing"]
    elif command == "clip.composite":
        properties = {"clipId": STRING, "compositing": {"type": "object", "properties": {
            "matteOnly": BOOL, "matte": {"type": ["object", "null"], "properties": {
                "clipId": STRING, "channel": {"type": "string", "enum": ["alpha", "luma"]}, "invert": BOOL},
                "required": ["clipId"], "additionalProperties": False}}, "additionalProperties": False}}
        required = ["clipId", "compositing"]
    elif command in {"composition.adjustment","composition.scene3d"}:
        properties = {"clipId": STRING, "trackId": STRING, "timelineStart": SECONDS, "timelineEnd": SECONDS}
        required = ["timelineEnd"]
    elif command=='scene3d.import':
        properties={'clipId':STRING,'instanceId':STRING,'objectId':STRING,'objText':{'type':'string','maxLength':2097152}}
        required=['clipId','instanceId','objText']
    else:
        return None
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def register(service):
    from cutvoke.editing.service import EditError
    from cutvoke.domain.protocol import ErrorCode
    def invalid(message):
        return EditError(ErrorCode.INVALID_ARGUMENT, str(message))
    def target(project, clip_id):
        for track in project.sequence.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    if track.locked:
                        raise invalid("track is locked")
                    return track, clip
        raise invalid(f"clip not found: {clip_id}")
    def composite(project, payload, effect=False):
        track, clip = target(project, payload.get("clipId"))
        try:
            if effect:
                matches = [item for item in clip.effects if item.get("instanceId") == payload.get("instanceId")]
                if len(matches) != 1:
                    raise ValueError("effect instanceId not found or ambiguous")
                item = matches[0]
                if item.get("effectId") in {"cutvoke.transform", "cutvoke.text"} or item.get("effectId", "").startswith("cutvoke.transition."):
                    raise ValueError("use layer opacity/grade/title controls for structural effects")
                item["compositing"] = effect_compositing(payload.get("compositing"))
            else:
                clip.compositing = clip_compositing(payload.get("compositing"))
                validate_sequence(project.sequence)
        except (ValueError, TypeError) as exc:
            raise invalid(exc) from exc
        return [{"type": "clip", "id": clip.id, "change": "compositing"}]
    def adjustment(project, payload):
        def time(value, default=0):
            return Rational.from_json(**value) if isinstance(value, dict) else Rational.from_float(value if value is not None else default)
        try:
            start, end = time(payload.get("timelineStart")), time(payload.get("timelineEnd"))
            if start < Rational.of(0) or end <= start:
                raise ValueError("adjustment requires 0 <= timelineStart < timelineEnd")
            clip_id = payload.get("clipId") or "adjust_" + uuid.uuid4().hex[:12]
            track_id = payload.get("trackId") or "adjustment_" + uuid.uuid4().hex[:10]
            track = next((item for item in project.sequence.tracks if item.id == track_id), None)
            if track is None:
                track = Track(track_id, "video", role="adjustment")
                project.sequence.tracks.append(track)
            if track.locked or track.kind != "video" or track.role != "adjustment":
                raise ValueError("choose an unlocked adjustment track")
            track.clips.append(Clip(clip_id, AssetReference("", ""), start, end, Rational.of(0), role="adjustment"))
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            raise invalid(exc) from exc
        return [{"type": "clip", "id": clip_id, "change": "inserted"}, {"type": "track", "id": track_id, "change": "updated"}]
    def replace(project, payload):
        track, clip = target(project, payload.get('clipId'))
        index = next((i for i,e in enumerate(clip.effects) if e.get('instanceId') == payload.get('instanceId')), None)
        if index is None: raise invalid('effect instanceId not found')
        try:
            spec=service._effects.get(payload.get('effectId',''))
            old=service._effects.get(clip.effects[index].get('effectId',''))
            if spec.category != 'fx' or old.category != 'fx':
                raise ValueError('effect.replace accepts picture/audio FX instances; structural effects use their own controls')
            kind='audio' if track.kind=='audio' else 'image' if clip.asset_ref.source_path.lower().endswith(('.png','.jpg','.jpeg','.webp','.bmp')) else 'video'
            if kind not in spec.applies_to and not (kind=='video' and 'audio' in spec.applies_to):
                raise ValueError('replacement effect does not apply to this clip type')
            parameters=service._effects.validate_params(spec.id,payload.get('params',{}))
            clip.effects[index]={**clip.effects[index],'effectId':spec.id,'version':spec.version,'params':parameters}
            from cutvoke.domain.keyframes import parameter_spec
            prefix='effect:'+payload['instanceId']+':'
            for name in clip.keyframes:
                if name.startswith(prefix): parameter_spec(clip,name)
        except (ValueError, KeyError, TypeError) as error:
            raise invalid(error) from error
        return [{'type':'clip','id':clip.id,'change':'effectReplaced'}]
    def scene(project,payload):
        changes=adjustment(project,payload)
        clip=target(project,changes[0]['id'])[1]
        spec=service._effects.get('cutvoke.fx.scene3d')
        clip.effects.append({'instanceId':'fx_'+uuid.uuid4().hex[:12],'effectId':spec.id,'version':spec.version,'params':service._effects.validate_params(spec.id,{})})
        return changes
    def import_mesh(project,payload):
        clip=target(project,payload.get('clipId'))[1]
        effect=next((item for item in clip.effects if item.get('instanceId')==payload.get('instanceId') and item.get('effectId')=='cutvoke.fx.scene3d'),None)
        if effect is None:raise invalid('exact scene3d effect instance not found')
        from cutvoke.rendering.scene_geometry import import_obj
        try:
            mesh,warnings=import_obj(payload.get('objText'))
            params=copy.deepcopy(effect['params'])
            object_id=payload.get('objectId') or 'mesh_'+uuid.uuid4().hex[:8]
            objects=params.setdefault('objects',[])
            index=next((index for index,value in enumerate(objects) if value['id']==object_id),None)
            previous=objects[index] if index is not None else {}
            replacement={'id':object_id,'color':'#aaddff',**{key:value for key,value in previous.items() if key not in {'shape','vertices','triangles','uvs'}},**mesh}
            if index is None:objects.append(replacement)
            else:objects[index]=replacement
            effect['params']=service._effects.validate_params('cutvoke.fx.scene3d',params)
        except (ValueError,KeyError,IndexError) as error:raise invalid(error) from error
        return [{'type':'clip','id':clip.id,'change':'meshImported','objectId':object_id,'warnings':warnings}]
    service._handlers.update({"effect.composite": lambda p, v: composite(p, v, True), "clip.composite": composite,
                              "composition.adjustment": adjustment,"effect.replace":replace,'composition.scene3d':scene,'scene3d.import':import_mesh})
    for name, description in {
        "effect.composite": "Set exact effect instance opacity, blend mode and normalized local region; replacement metadata, undoable.",
        "effect.replace": "Replace the type and parameters of an exact FX instance while preserving instanceId, position, range, bypass and compositing; rejects incompatible animated lanes, undoable.",
        "clip.composite": "Set selectable Alpha/Luma/inverted matte and matte-only source flag; stable clip references, cycle checked, undoable.",
        "composition.adjustment": "Insert an adjustment layer affecting already composed lower video layers over its time interval; undoable.",
        'composition.scene3d':'Insert a local 3D scene composition layer with camera, light, meshes/materials and per-pixel depth occlusion. No AI required; undoable. Edit its scene3d effect instance parameters.',
        'scene3d.import':'Import bounded OBJ geometry into an exact scene3d effect instance; embeds vertices/triangles/UVs in project JSON. At most 2 MiB text, 4096 expanded vertices, 8192 faces. Never fetches MTL/URLs; undoable.',
    }.items():
        schema = payload_schema(name)
        service.COMMAND_PARAM_SPECS[name] = {"description": description, "params": [
            {"name": key, "description": key, "required": key in schema["required"]} for key in schema["properties"]]}
