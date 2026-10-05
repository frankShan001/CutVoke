"""Add visually distinct filter candidates without altering approved presets.

This only seeds editable effect stacks. Preview, editing and visual review are
separate gates; candidates never become qualified through this script.
"""

from __future__ import annotations

import json
from pathlib import Path


CATALOG = Path(__file__).resolve().parents[1] / "src/cutvoke/core/builtin_presets.json"

# slug, name, subcategory, effect stack, primary numeric controls
FILTERS = (
    ("blueprintCyan", "青蓝线图", "风格", (
        ("cutvoke.fx.edge", {"mode": "canny", "low": .04, "high": .14}),
        ("cutvoke.fx.colorize", {"hue": 186, "saturation": 1, "mix": .92})), ("low", "high")),
    ("posterAmber", "琥珀版画", "风格", (
        ("cutvoke.fx.posterize", {"levels": 5}),
        ("cutvoke.fx.colorize", {"hue": 34, "saturation": .85, "mix": .75})), ("levels",)),
    ("negativeViolet", "紫调负片", "风格", (
        ("cutvoke.fx.invert", {"strength": .88}),
        ("cutvoke.fx.colorize", {"hue": 290, "saturation": .8, "mix": .55})), ("strength",)),
    ("monoMatte", "黑白粗颗粒", "黑白", (
        ("cutvoke.fx.filmgrain", {"strength": 100, "temporal": False}),
        ("cutvoke.fx.grayscale", {"strength": 1}),
        ("cutvoke.fx.curves", {"preset": "strong_contrast"})), ("strength",)),
    ("monoNegative", "黑白负片", "黑白", (
        ("cutvoke.fx.invert", {"strength": 1}),
        ("cutvoke.fx.grayscale", {"strength": 1})), ("strength",)),
    ("monoInk", "黑白线稿", "黑白", (
        ("cutvoke.fx.edge", {"mode": "canny", "low": .03, "high": .12}),
        ("cutvoke.fx.grayscale", {"strength": 1})), ("low", "high")),
    ("monoVignette", "暗角银幕", "黑白", (
        ("cutvoke.fx.grayscale", {"strength": 1}),
        ("cutvoke.fx.vignette", {"angle": "PI/6"}),
        ("cutvoke.fx.curves", {"preset": "medium_contrast"})), ("strength",)),
    ("monoBlocks", "黑白块面", "黑白", (
        ("cutvoke.fx.posterize", {"levels": 3}),
        ("cutvoke.fx.grayscale", {"strength": 1})), ("levels",)),
    ("filmFineGrain", "细颗粒旧片", "胶片", (
        ("cutvoke.fx.filmgrain", {"strength": 35, "temporal": False}),
        ("cutvoke.fx.vintage", {"strength": .9})), ("strength",)),
    ("filmFaded", "褪色柔片", "胶片", (
        ("cutvoke.fx.vintage", {"strength": .9}),
        ("cutvoke.fx.curves", {"preset": "vintage"}),
        ("cutvoke.fx.glow", {"intensity": .35})), ("strength",)),
    ("filmBlue", "冷蓝旧片", "胶片", (
        ("cutvoke.fx.vintage", {"strength": .65}),
        ("cutvoke.fx.colorize", {"hue": 205, "saturation": .45, "mix": .5})), ("strength",)),
    ("filmSunset", "暖橙旧片", "胶片", (
        ("cutvoke.fx.sepia", {"strength": .8}),
        ("cutvoke.fx.colorize", {"hue": 18, "saturation": .75, "mix": .55}),
        ("cutvoke.fx.curves", {"preset": "vintage"})), ("strength",)),
)


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    existing = {entry["presetId"]: entry for entry in catalog["presets"]}
    for slug, name, subcategory, stack, adjustable in FILTERS:
        preset_id = f"cutvoke.preset.filter.{slug}"
        effects = [{"effectId": effect_id, "params": params}
                   for effect_id, params in stack]
        old = existing.get(preset_id)
        if old and old.get("status") == "approved":
            continue
        desired = {
            "presetId": preset_id, "version": "1.0.0", "name": name,
            "family": "filter", "subcategory": subcategory,
            "effectId": effects[0]["effectId"], "params": effects[0]["params"],
            "effects": effects, "adjustable": list(adjustable),
            "license": "MIT", "downloadState": "bundled", "status": "candidate",
        }
        if old and all(old.get(key) == value for key, value in desired.items()):
            continue
        existing[preset_id] = desired
    previous = [entry for entry in catalog["presets"]
                if entry["presetId"] not in {f"cutvoke.preset.filter.{slug}" for slug, *_ in FILTERS}]
    catalog["presets"] = previous + [existing[f"cutvoke.preset.filter.{slug}"] for slug, *_ in FILTERS]
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Maintained {len(FILTERS)} filter candidates; approved entries preserved")


if __name__ == "__main__":
    main()
