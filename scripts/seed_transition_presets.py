"""Seed transition candidates backed by distinct, registered xfade operators.

This only adds review candidates. Rendering and editing audits provide machine
evidence; a separate visual review is required before approval.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cutvoke.core.effects import EffectRegistry  # noqa: E402


CATALOG = REPO / "src/cutvoke/core/builtin_presets.json"
TRANSITIONS = {
    "叠化": (
        "crossfade", "dissolve", "distance", "fade", "fadefast", "fadegrays",
        "fadeslow", "fadewhite",
    ),
    "擦除": (
        "circleopen", "diagtl", "radial", "squeeze", "wipe", "wipedown",
        "wiperight", "wipeup",
    ),
    "运镜": (
        "slide", "slidedown", "slideup", "smoothleft", "smoothright",
        "smoothup", "squeezev", "zoom",
    ),
    "模糊": ("blur", "vblur", "diagblurdown", "diagblurup", "crossblur",
             "radialblur", "edgeblur", "centerblur"),
    "故障": ("pixelize", "hlslice", "hrslice", "vuslice", "vdslice",
             "hlwind", "hrwind", "vuwind", "vdwind"),
}


def main() -> None:
    registry = EffectRegistry()
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = document["presets"]
    by_id = {item["presetId"]: item for item in entries}
    added = updated = 0
    for subcategory, names in TRANSITIONS.items():
        for name in names:
            effect_id = f"cutvoke.transition.{name}"
            effect = registry.find(effect_id)
            if effect is None or effect.xfade_transition is None:
                raise ValueError(f"missing xfade transition: {effect_id}")
            info = effect.to_dict()
            if info["browseCategory"] != "transition" or info["subcategory"] != subcategory:
                raise ValueError(f"transition classification mismatch: {effect_id}")
            preset_id = f"cutvoke.preset.transition.{name}"
            current = by_id.get(preset_id)
            # Fill missing descriptive fields without invalidating unchanged QA.
            if current is not None and current.get("effectId") == effect_id:
                defaults = {
                    "version": "1.0.0",
                    "name": effect.label(),
                    "family": "transition",
                    "subcategory": subcategory,
                    "defaultDuration": 0.5,
                    "adjustable": ["duration"],
                    "license": effect.license,
                    "downloadState": "bundled",
                }
                missing = [key for key in defaults if key not in current]
                for key in missing:
                    current[key] = defaults[key]
                if missing:
                    updated += 1
                continue
            payload = {
                "presetId": preset_id,
                "version": "1.0.0",
                "effectId": effect_id,
                "name": effect.label(),
                "family": "transition",
                "subcategory": subcategory,
                "params": {"duration": 0.5},
                "defaultDuration": 0.5,
                "adjustable": ["duration"],
                "license": effect.license,
                "downloadState": "bundled",
                "status": "candidate",
            }
            if current is None:
                entries.append(payload)
                added += 1
            else:
                current.clear()
                current.update(payload)
                updated += 1
    CATALOG.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    print(f"transition candidates: added={added}, updated={updated}, "
          f"target={sum(len(names) for names in TRANSITIONS.values())}")


if __name__ == "__main__":
    main()
