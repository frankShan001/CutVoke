"""Mixer command registration kept separate from the general edit service."""
from __future__ import annotations

import copy
import uuid

from cutvoke.domain.audio_mix import validate_mix


def payload_schema(command):
    string = {"type": "string"}
    mix = {"type": "object", "properties": {
        "gainDb": {"type": "number", "minimum": -96, "maximum": 24},
        "pan": {"type": "number", "minimum": -1, "maximum": 1},
        "solo": {"type": "boolean"}, "muted": {"type": "boolean"}, "busId": string,
        "envelope": {"type": "array", "maxItems": 1000, "items": {"type": "object", "properties": {
            "time": {"type": "number", "minimum": 0},
            "gainDb": {"type": "number", "minimum": -96, "maximum": 24}},
            "required": ["time", "gainDb"], "additionalProperties": False}},
        "effects": {"type": "array", "maxItems": 16, "items": {"type": "object", "properties": {
            "effectId": string, "params": {"type": "object"}, "enabled": {"type": "boolean"}},
            "required": ["effectId"]}}}, "additionalProperties": False}
    if command == "audio.mixer":
        return {"type": "object", "properties": {"trackId": string, "busId": string,
            "master": {"type": "boolean", "enum": [True]}, "mix": mix}, "required": ["mix"],
            "oneOf": [{"required": ["trackId"]}, {"required": ["busId"]}, {"required": ["master"]}],
            "additionalProperties": False}
    if command in {"audio.busAdd", "audio.busRemove"}:
        return {"type": "object", "properties": {"busId": string, **({"name": string} if command.endswith("Add") else {})},
                "required": [] if command.endswith("Add") else ["busId"], "additionalProperties": False}
    if command == "audio.separate":
        return {"type": "object", "properties": {"audioPath": string, "outDir": string,
            "model": {"type": "string", "enum": ["htdemucs", "htdemucs_ft"]},
            "device": {"type": "string", "enum": ["cpu", "cuda", "auto"]},
            "stems": {"type": "string", "enum": ["vocals", "all"]}},
            "required": ["audioPath", "outDir"], "additionalProperties": False}
    return None


SPECS = {
    "audio.mixer": {"description": "设置轨道/总线/主混音增益、声像、独奏、绝对序列时间包络和音频效果链；可撤销。", "params": [
        {"name": name, "description": description, "required": name == "mix"} for name, description in
        (("trackId", "轨道ID；与busId/master三选一"), ("busId", "总线ID"), ("master", "true选择主混音"), ("mix", "待合并的混音字段"))]},
    "audio.busAdd": {"description": "新建音频子混音总线；可撤销。", "params": [
        {"name": "busId", "description": "显式总线ID；缺省自动生成", "required": False},
        {"name": "name", "description": "总线显示名称", "required": False}]},
    "audio.busRemove": {"description": "删除音频总线，所辖轨道返回主混音；一次撤销恢复。", "params": [
        {"name": "busId", "description": "总线ID", "required": True}]},
    "audio.separate": {"description": "本地Demucs真实神经网络音源分离；首次下载模型，输出无损WAV人声/伴奏或四轨，不改工程。", "params": [
        {"name": name, "description": description, "required": name in {"audioPath", "outDir"}} for name, description in
        (("audioPath", "输入音频/视频路径"), ("outDir", "新的输出目录；不覆盖已有分离结果"), ("model", "htdemucs/htdemucs_ft"),
         ("device", "cpu/cuda/auto"), ("stems", "vocals=人声伴奏两轨，all=四轨"))]},
}


def register(service):
    from cutvoke.editing.service import EditError
    from cutvoke.domain.protocol import ErrorCode

    def invalid(message):
        return EditError(ErrorCode.INVALID_ARGUMENT, str(message))

    def mixer(project, payload):
        targets = [key for key in ("trackId", "busId", "master") if payload.get(key)]
        if len(targets) != 1:
            raise invalid("choose exactly one of trackId/busId/master")
        try:
            patch = validate_mix(payload.get("mix"), allow_route=targets[0] == "trackId")
            for effect in patch.get("effects", []):
                effect["params"] = service._effects.validate_params(effect["effectId"], effect.get("params", {}))
        except (ValueError, KeyError) as error:
            raise invalid(error) from error
        seq = project.sequence
        if "busId" in patch and patch["busId"] and not any(bus["id"] == patch["busId"] for bus in seq.audio_buses):
            raise invalid("audio bus does not exist")
        if targets[0] == "trackId":
            track = next((track for track in seq.tracks if track.id == payload["trackId"]), None)
            if track is None or track.kind not in {"audio", "video"}:
                raise invalid("mixer requires an audio or video track")
            if track.locked:
                raise invalid("track is locked")
            track.audio_mix = {**track.audio_mix, **patch}
            return [{"type": "track", "id": track.id, "change": "audioMix"}]
        if targets[0] == "busId":
            bus = next((bus for bus in seq.audio_buses if bus["id"] == payload["busId"]), None)
            if bus is None:
                raise invalid("audio bus does not exist")
            bus["mix"] = {**bus.get("mix", {}), **patch}
            return [{"type": "audio_bus", "id": bus["id"], "change": "updated"}]
        seq.master_audio = {**seq.master_audio, **patch}
        return [{"type": "sequence", "id": seq.id, "change": "masterAudio"}]

    def add_bus(project, payload):
        bus_id = payload.get("busId") or f"bus_{uuid.uuid4().hex[:12]}"
        name = payload.get("name", bus_id)
        if not isinstance(bus_id, str) or len(bus_id) > 128 or not isinstance(name, str) or len(name) > 256:
            raise invalid("invalid bus ID/name")
        if len(project.sequence.audio_buses) >= 32 or any(bus["id"] == bus_id for bus in project.sequence.audio_buses):
            raise invalid("duplicate bus ID or 32-bus limit reached")
        project.sequence.audio_buses.append({"id": bus_id, "name": name, "mix": {}})
        return [{"type": "audio_bus", "id": bus_id, "change": "added"}]

    def remove_bus(project, payload):
        bus_id = payload.get("busId")
        if not any(bus["id"] == bus_id for bus in project.sequence.audio_buses):
            raise invalid("audio bus does not exist")
        if any(track.locked and track.audio_mix.get("busId") == bus_id for track in project.sequence.tracks):
            raise invalid("bus has a locked routed track")
        project.sequence.audio_buses = [bus for bus in project.sequence.audio_buses if bus["id"] != bus_id]
        for track in project.sequence.tracks:
            if track.audio_mix.get("busId") == bus_id:
                track.audio_mix.pop("busId")
        return [{"type": "audio_bus", "id": bus_id, "change": "removed"}]

    def separate(_project, payload):
        from cutvoke.rendering.audio_separation import separate_audio
        try:
            result = separate_audio(payload.get("audioPath", ""), payload.get("outDir", ""),
                                    model=payload.get("model", "htdemucs"), device=payload.get("device", "auto"),
                                    stems=payload.get("stems", "vocals"))
        except (ValueError, RuntimeError, OSError) as error:
            raise invalid(error) from error
        return [{"type": "audio_separation", "change": "separated", **result}]

    service._handlers.update({"audio.mixer": mixer, "audio.busAdd": add_bus, "audio.busRemove": remove_bus, "audio.separate": separate})
    service.COMMAND_PARAM_SPECS.update(copy.deepcopy(SPECS))
