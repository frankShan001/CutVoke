"""Seed eight genuinely different loop-animation candidates in the built-in catalog.

Existing QA evidence is retained only if the effect, parameters and version are
unchanged. Preview generation and the independent audit scripts add evidence.
"""

from __future__ import annotations

import json
from pathlib import Path


CATALOG = Path(__file__).resolve().parents[1] / "src/cutvoke/core/builtin_presets.json"
LOOPS = (
    ("breathingFrame", "cutvoke.anim.breathe", "呼吸推近", {"amplitude": 0.10, "period": 1.6}),
    ("floatingFrame", "cutvoke.anim.float", "上下漂浮", {"amplitude": 0.08, "period": 1.6}),
    ("sideSway", "cutvoke.anim.sway", "水平摇摆", {"amplitude": 0.10, "period": 1.5}),
    ("gentleRock", "cutvoke.anim.rock", "轻摇倾斜", {"amplitude": 0.07, "period": 1.5}),
    ("rhythmBounce", "cutvoke.anim.bounce", "节奏弹跳", {"amplitude": 0.12, "period": 1.4}),
    ("ellipseOrbit", "cutvoke.anim.orbit", "椭圆环绕", {"amplitude": 0.09, "period": 1.7}),
    ("doubleHeartbeat", "cutvoke.anim.heartbeat", "双拍心跳", {"amplitude": 0.22, "period": 1.4}),
    ("blinkBeat", "cutvoke.anim.blink", "明灭闪现", {"amplitude": 0.70, "period": 2.0}),
)


def main() -> None:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = document["presets"]
    by_id = {item["presetId"]: item for item in entries}
    added = updated = 0
    for slug, effect_id, name, params in LOOPS:
        preset_id = f"cutvoke.preset.animation.{slug}"
        payload = {
            "presetId": preset_id,
            "version": "1.0.0",
            "effectId": effect_id,
            "name": name,
            "family": "animation",
            "subcategory": "循环",
            "params": params,
            "defaultStrength": params["amplitude"],
            "license": "MIT",
            "downloadState": "bundled",
            "status": "candidate",
        }
        current = by_id.get(preset_id)
        if current is None:
            entries.append(payload)
            added += 1
        elif any(current.get(key) != value for key, value in payload.items()):
            current.update(payload)
            current.pop("checks", None)
            current.pop("evidence", None)
            current.pop("cover", None)
            current.pop("motionPreview", None)
            updated += 1
    CATALOG.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    print(f"loop preset candidates: added={added}, updated={updated}, total={len(LOOPS)}")


if __name__ == "__main__":
    main()
