"""Maintain editable lighting and lens candidates for visual review.

Only creates catalog candidates. Preview rendering, command audit and human
visual review remain independent qualification gates.
"""

from __future__ import annotations

import json
from pathlib import Path


CATALOG = Path(__file__).resolve().parents[1] / "src/cutvoke/core/builtin_presets.json"

# slug, name, effect stack, editable numeric controls on the primary effect
LIGHTING = (
    ("barrelWide", "广角桶形", (
        ("cutvoke.fx.lens", {"k1": -0.7, "k2": -0.2}),
    ), ("k1", "k2")),
    ("pincushion", "枕形压缩", (
        ("cutvoke.fx.lens", {"k1": 0.6, "k2": 0.2}),
    ), ("k1", "k2")),
    ("warmAura", "暖光漫射", (
        ("cutvoke.fx.glow", {"intensity": 1.5}),
        ("cutvoke.fx.colorize", {"hue": 32, "saturation": 0.8,
                                 "lightness": 0.55, "mix": 0.48}),
    ), ("intensity",)),
    ("iceAura", "冷蓝漫射", (
        ("cutvoke.fx.glow", {"intensity": 1.5}),
        ("cutvoke.fx.colorize", {"hue": 204, "saturation": 0.82,
                                 "lightness": 0.53, "mix": 0.48}),
    ), ("intensity",)),
    ("tunnelShadow", "暗影隧道", (
        ("cutvoke.fx.lens", {"k1": 0.48, "k2": 0.05}),
        ("cutvoke.fx.vignette", {"angle": "PI/4"}),
    ), ("k1", "k2")),
    ("whiteMist", "白雾光晕", (
        ("cutvoke.fx.blur", {"sigma": 5}),
        ("cutvoke.fx.glow", {"intensity": 2.6}),
    ), ("sigma",)),
)


def main() -> None:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = {item["presetId"]: item for item in document["presets"]}
    ids = {f"cutvoke.preset.fx.{slug}" for slug, *_ in LIGHTING}
    for slug, name, stack, adjustable in LIGHTING:
        preset_id = f"cutvoke.preset.fx.{slug}"
        old = entries.get(preset_id)
        if old and old.get("status") == "approved":
            continue
        effects = [{"effectId": effect_id, "params": params}
                   for effect_id, params in stack]
        desired = {
            "presetId": preset_id, "version": "1.0.0", "name": name,
            "family": "fx", "subcategory": "光影",
            "effectId": effects[0]["effectId"], "params": effects[0]["params"],
            "effects": effects, "adjustable": list(adjustable),
            "license": "MIT", "downloadState": "bundled", "status": "candidate",
        }
        if not old or any(old.get(key) != value for key, value in desired.items()):
            entries[preset_id] = desired
    document["presets"] = [item for item in document["presets"]
                           if item["presetId"] not in ids] + [
        entries[f"cutvoke.preset.fx.{slug}"] for slug, *_ in LIGHTING]
    CATALOG.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    print(f"Maintained {len(LIGHTING)} lighting candidates; approved entries preserved")


if __name__ == "__main__":
    main()
