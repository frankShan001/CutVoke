"""Maintain five editable composition-effect candidates for the built-in library.

This step only creates catalog entries. Rendering, command audit and visual
approval remain separate gates, and existing approved entries are preserved.
"""

from __future__ import annotations

import json
from pathlib import Path


CATALOG = Path(__file__).resolve().parents[1] / "src/cutvoke/core/builtin_presets.json"

# slug, display name, effect, parameters, editable numeric controls
COMPOSITION = (
    ("frameBlue", "蓝色取景框", "cutvoke.fx.shape",
     {"shape": "rect", "mode": "outline", "x": .5, "y": .5, "w": .82, "h": .78,
      "strokeWidth": 8, "color": "#0A84FF", "opacity": 1},
     ("x", "y", "w", "h", "strokeWidth")),
    ("arrowOrange", "橙色方向箭头", "cutvoke.fx.shape",
     {"shape": "arrow", "mode": "fill", "x": .5, "y": .7, "w": .75, "h": .35,
      "strokeWidth": 24, "color": "#FF9F0A", "opacity": 1},
     ("x", "y", "w", "h", "strokeWidth")),
    ("ringGreen", "绿色聚焦圈", "cutvoke.fx.shape",
     {"shape": "ellipse", "mode": "outline", "x": .5, "y": .5, "w": .65, "h": .8,
      "strokeWidth": 15, "color": "#34C759", "opacity": 1},
     ("x", "y", "w", "h", "strokeWidth")),
    ("splitLeft", "左半幅画面", "cutvoke.fx.mask",
     {"shape": "rect", "x": 0, "y": 0, "w": .5, "h": 1,
      "feather": 0, "invert": False},
     ("x", "y", "w", "h", "feather")),
    ("centerCutout", "中心圆形镂空", "cutvoke.fx.mask",
     {"shape": "circle", "x": .5, "y": .5, "w": .48, "h": .48,
      "feather": .02, "invert": True},
     ("x", "y", "w", "h", "feather")),
)


def main() -> None:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = {item["presetId"]: item for item in document["presets"]}
    maintained: list[str] = []
    for slug, name, effect_id, params, adjustable in COMPOSITION:
        preset_id = f"cutvoke.preset.fx.{slug}"
        maintained.append(preset_id)
        old = entries.get(preset_id)
        if old and old.get("status") == "approved":
            continue
        desired = {
            "presetId": preset_id, "version": "1.0.0", "name": name,
            "family": "fx", "subcategory": "构图",
            "effectId": effect_id, "params": params,
            "effects": [{"effectId": effect_id, "params": params}],
            "adjustable": list(adjustable), "license": "MIT",
            "downloadState": "bundled", "status": "candidate",
        }
        if old and all(old.get(key) == value for key, value in desired.items()):
            continue
        entries[preset_id] = desired
    document["presets"] = [item for item in document["presets"]
                           if item["presetId"] not in maintained] + [entries[key] for key in maintained]
    CATALOG.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Maintained {len(COMPOSITION)} composition candidates; approved entries preserved")


if __name__ == "__main__":
    main()
