"""Maintain motion-based FX candidates with actual per-frame transformations."""

from __future__ import annotations

import json
from pathlib import Path


CATALOG = Path(__file__).resolve().parents[1] / "src/cutvoke/core/builtin_presets.json"

DYNAMIC = (
    ("rhythmSwing", "节奏摆动", (
        ("cutvoke.fx.swing", {"degrees": 9.0, "hz": 1.4}),
    ), ("degrees", "hz")),
    ("handheldShake", "手持震动", (
        ("cutvoke.fx.shake", {"pixels": 14.0, "hz": 5.2}),
    ), ("pixels", "hz")),
    ("ghostTrail", "幻影残迹", (
        ("cutvoke.fx.shake", {"pixels": 16.0, "hz": 2.2}),
        ("cutvoke.fx.trail", {"frames": 10, "decay": 0.78}),
    ), ("pixels", "hz")),
    ("neonStrobe", "电光轮廓频闪", (
        ("cutvoke.fx.flicker", {"hz": 4.4}),
        ("cutvoke.fx.edge", {"mode": "wire", "low": 0.06, "high": 0.24}),
        ("cutvoke.fx.glow", {"intensity": 2.4}),
    ), ("hz",)),
    ("mirrorSwing", "镜向摇摆", (
        ("cutvoke.fx.swing", {"degrees": 6.5, "hz": 0.85}),
        ("cutvoke.fx.mirror", {}),
    ), ("degrees", "hz")),
    ("solarBeat", "暖色节拍", (
        ("cutvoke.fx.flicker", {"hz": 2.1}),
        ("cutvoke.fx.posterize", {"levels": 3}),
        ("cutvoke.fx.colorize", {"hue": 34.0, "saturation": 0.78,
                                 "lightness": 0.6, "mix": 0.72}),
    ), ("hz",)),
)


def main() -> None:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = {item["presetId"]: item for item in document["presets"]}
    ids = {f"cutvoke.preset.fx.{slug}" for slug, *_ in DYNAMIC}
    for slug, name, stack, adjustable in DYNAMIC:
        preset_id = f"cutvoke.preset.fx.{slug}"
        old = entries.get(preset_id)
        if old and old.get("status") == "approved":
            continue
        effects = [{"effectId": effect_id, "params": params}
                   for effect_id, params in stack]
        desired = {
            "presetId": preset_id, "version": "1.0.0", "name": name,
            "family": "fx", "subcategory": "动感",
            "effectId": effects[0]["effectId"], "params": effects[0]["params"],
            "effects": effects, "adjustable": list(adjustable),
            "license": "MIT", "downloadState": "bundled", "status": "candidate",
        }
        if not old or any(old.get(key) != value for key, value in desired.items()):
            entries[preset_id] = desired
    document["presets"] = [item for item in document["presets"]
                           if item["presetId"] not in ids] + [
        entries[f"cutvoke.preset.fx.{slug}"] for slug, *_ in DYNAMIC]
    CATALOG.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    print(f"Maintained {len(DYNAMIC)} motion FX candidates; approved entries preserved")


if __name__ == "__main__":
    main()
