"""Curated built-in presets, separate from effect operators and personal presets.

An effect manifest proves that an operator is registered. A preset becomes
qualified only after explicit visual and edit/export checks are recorded.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .effects import EffectRegistry, animation_slot


CHECKS = ("cover", "motionPreview", "visualDistinct", "apply", "edit",
          "saveReload", "undo", "export")
FAMILIES = ("fx", "transition", "animation", "text", "filter", "sticker", "personFx")
MAX_PRESET_EFFECTS = 8


class PresetInvalid(ValueError):
    """A curated preset refers to an unavailable operator or malformed data."""


@dataclass(frozen=True)
class PresetSpec:
    id: str
    version: str
    name: str
    family: str
    subcategory: str
    effect_id: str
    params: dict[str, Any]
    effects: tuple[dict[str, Any], ...]
    default_duration: float | None
    default_strength: float | None
    applies_to: tuple[str, ...]
    cover: str
    motion_preview: str
    adjustable: tuple[str, ...]
    license: str
    source: str
    download_state: str
    status: str
    checks: dict[str, bool]
    evidence: dict[str, str]
    asset_root: Path

    def _exists(self, reference: str) -> bool:
        if not reference:
            return False
        path = Path(reference)
        if not path.is_absolute():
            path = self.asset_root / path
        return path.is_file()

    def _path(self, reference: str) -> Path:
        path = Path(reference)
        return path if path.is_absolute() else self.asset_root / path

    def _review_matches_media(self) -> bool:
        """A visual decision expires when its media, audit or preset changes."""
        try:
            review = json.loads(self._path(self.evidence["visualDistinct"]).read_text(
                encoding="utf-8"))
            hashes = {
                "coverSha256": self.cover,
                "motionPreviewSha256": self.motion_preview,
                "auditSha256": self.evidence["export"],
            }
            return (review.get("schemaVersion") == 1 and
                    review.get("decision") == "approved" and
                    review.get("presetId") == self.id and
                    review.get("version") == self.version and
                    review.get("effectId") == self.effect_id and
                    review.get("params") == self.params and
                    all(review.get(key) == hashlib.sha256(self._path(path).read_bytes()).hexdigest()
                        for key, path in hashes.items()))
        except (KeyError, OSError, ValueError, TypeError):
            return False

    @property
    def qualified(self) -> bool:
        return (self.status == "approved" and bool(self.license) and
                self.download_state == "bundled" and
                self._exists(self.cover) and self._exists(self.motion_preview) and
                all(self.checks.get(key, False) and self._exists(self.evidence.get(key, ""))
                    for key in CHECKS) and self._review_matches_media())

    def to_dict(self) -> dict[str, Any]:
        return {
            "presetId": self.id, "version": self.version, "name": self.name,
            "family": self.family, "subcategory": self.subcategory,
            "effectId": self.effect_id, "params": self.params,
            "effects": [dict(item) for item in self.effects],
            "defaultDuration": self.default_duration,
            "defaultStrength": self.default_strength,
            "appliesTo": list(self.applies_to), "cover": self.cover,
            "motionPreview": self.motion_preview,
            "adjustable": list(self.adjustable), "license": self.license,
            "source": self.source,
            "downloadState": self.download_state, "status": self.status,
            "mediaAvailable": self._exists(self.cover) and self._exists(self.motion_preview),
            "checks": dict(self.checks), "evidence": dict(self.evidence),
            "qualified": self.qualified,
        }


class PresetCatalog:
    def __init__(self, effects: EffectRegistry, entries: list[dict[str, Any]],
                 asset_root: Path | None = None) -> None:
        specs: list[PresetSpec] = []
        seen: set[str] = set()
        root = asset_root or Path(__file__).parent
        for entry in entries:
            if not isinstance(entry, dict):
                raise PresetInvalid("preset entry must be an object")
            preset_id = entry.get("presetId")
            if not isinstance(preset_id, str) or not preset_id.startswith("cutvoke.preset."):
                raise PresetInvalid(f"invalid stable presetId: {preset_id!r}")
            if preset_id in seen:
                raise PresetInvalid(f"duplicate presetId: {preset_id}")
            seen.add(preset_id)
            raw_stack = entry.get("effects")
            if raw_stack is None:
                raw_stack = [{"effectId": entry.get("effectId"),
                              "params": entry.get("params", {})}]
            if not isinstance(raw_stack, list) or not 1 <= len(raw_stack) <= MAX_PRESET_EFFECTS:
                raise PresetInvalid(f"{preset_id}: effects must contain 1 to {MAX_PRESET_EFFECTS} operators")
            normalized: list[dict[str, Any]] = []
            used_effect_ids: set[str] = set()
            used_animation_slots: set[str] = set()
            applies_to: set[str] | None = None
            primary = None
            for index, item in enumerate(raw_stack):
                if not isinstance(item, dict) or not isinstance(item.get("effectId"), str):
                    raise PresetInvalid(f"{preset_id}: effects[{index}] requires effectId")
                effect_id = item["effectId"]
                if effect_id in used_effect_ids:
                    raise PresetInvalid(f"{preset_id}: duplicate effectId {effect_id}")
                used_effect_ids.add(effect_id)
                slot = animation_slot(effect_id)
                if slot:
                    if slot in used_animation_slots:
                        raise PresetInvalid(f"{preset_id}: duplicate animation slot {slot}")
                    used_animation_slots.add(slot)
                effect = effects.find(effect_id)
                if effect is None:
                    raise PresetInvalid(f"{preset_id}: unknown effectId {effect_id}")
                if index == 0:
                    primary = effect
                    if entry.get("effectId", effect_id) != effect_id:
                        raise PresetInvalid(f"{preset_id}: primary effectId disagrees with effects[0]")
                raw_params = item.get("params", {})
                if not isinstance(raw_params, dict):
                    raise PresetInvalid(f"{preset_id}: effects[{index}].params must be an object")
                if index == 0 and "params" in entry and entry["params"] != raw_params:
                    raise PresetInvalid(f"{preset_id}: primary params disagree with effects[0]")
                try:
                    params = effects.validate_params(effect_id, raw_params)
                except Exception as exc:
                    raise PresetInvalid(f"{preset_id}: effects[{index}] invalid params: {exc}") from exc
                normalized.append({"effectId": effect_id, "version": effect.version,
                                   "params": params})
                allowed = set(effect.applies_to)
                applies_to = allowed if applies_to is None else applies_to & allowed
            if not applies_to:
                raise PresetInvalid(f"{preset_id}: effect stack has no shared target type")
            assert primary is not None
            effect_id = normalized[0]["effectId"]
            params = normalized[0]["params"]
            browse = primary.to_dict()
            family = str(entry.get("family", browse["browseCategory"]))
            subcategory = str(entry.get("subcategory", browse["subcategory"]))
            if family != browse["browseCategory"] or family not in FAMILIES:
                raise PresetInvalid(f"{preset_id}: family disagrees with primary effect")
            if family == "text" and (len(normalized) != 1 or effect_id != "cutvoke.text"):
                raise PresetInvalid(f"{preset_id}: text presets require exactly one cutvoke.text effect")
            if not subcategory.strip() or len(subcategory) > 32:
                raise PresetInvalid(f"{preset_id}: invalid subcategory")
            properties = primary.parameters.get("properties") or {}
            inferred = [key for key, schema in properties.items()
                        if schema.get("type") in ("number", "integer")]
            raw_adjustable = entry.get("adjustable", inferred)
            if not isinstance(raw_adjustable, list) or any(not isinstance(key, str)
                                                            for key in raw_adjustable):
                raise PresetInvalid(f"{preset_id}: adjustable must be a list of parameter names")
            adjustable = tuple(raw_adjustable)
            if any(key not in (primary.parameters.get("properties") or {}) for key in adjustable):
                raise PresetInvalid(f"{preset_id}: adjustable includes an unknown parameter")
            checks = entry.get("checks", {})
            if not isinstance(checks, dict) or any(k not in CHECKS or not isinstance(v, bool)
                                                    for k, v in checks.items()):
                raise PresetInvalid(f"{preset_id}: invalid checks")
            evidence = entry.get("evidence", {})
            if not isinstance(evidence, dict) or any(k not in CHECKS or not isinstance(v, str)
                                                      for k, v in evidence.items()):
                raise PresetInvalid(f"{preset_id}: invalid evidence")
            source = entry.get("source", "")
            if not isinstance(source, str):
                raise PresetInvalid(f"{preset_id}: source must be a string")
            status = str(entry.get("status", "candidate"))
            if status not in ("candidate", "approved", "retired"):
                raise PresetInvalid(f"{preset_id}: invalid status {status}")
            download_state = str(entry.get("downloadState", "bundled"))
            if download_state not in ("bundled", "available", "missing"):
                raise PresetInvalid(f"{preset_id}: invalid downloadState")
            specs.append(PresetSpec(
                id=preset_id, version=str(entry.get("version", primary.version)),
                name=str(entry.get("name", primary.label())), family=family,
                subcategory=subcategory, effect_id=effect_id, params=params,
                effects=tuple(normalized),
                default_duration=_number_or_none(entry.get("defaultDuration", params.get("duration"))),
                default_strength=_number_or_none(entry.get("defaultStrength")),
                applies_to=tuple(sorted(applies_to)),
                cover=str(entry.get("cover", "")),
                motion_preview=str(entry.get("motionPreview", "")),
                adjustable=adjustable, license=str(entry.get("license", primary.license)),
                source=source,
                download_state=download_state, status=status,
                checks={key: checks.get(key, False) for key in CHECKS},
                evidence={key: evidence.get(key, "") for key in CHECKS},
                asset_root=root,
            ))
        self._specs = tuple(specs)

    @classmethod
    def builtin(cls, effects: EffectRegistry | None = None, *,
                package_root: Path | None = None) -> "PresetCatalog":
        path = ((package_root / "core" / "builtin_presets.json") if package_root is not None
                else Path(__file__).with_name("builtin_presets.json"))
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schemaVersion") != 1 or not isinstance(data.get("presets"), list):
            raise PresetInvalid("invalid built-in preset catalog")
        return cls(effects or EffectRegistry(), data["presets"], path.parent)

    def all(self) -> tuple[PresetSpec, ...]:
        return self._specs


def _number_or_none(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PresetInvalid(f"expected numeric preset default, got {value!r}")
    return float(value)
