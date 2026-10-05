"""Maintain visually distinct, editable basic image-effect candidates.

This seeds candidates only. Real preview rendering, edit/export audit and
visual review are separate qualification gates.
"""

from __future__ import annotations

import json
from pathlib import Path


CATALOG = Path(__file__).resolve().parents[1] / "src/cutvoke/core/builtin_presets.json"

# Distinct image transformations, each with at least one editable control.
BASIC = (
    ("wireOutline", "锐化轮廓", (
        ("cutvoke.fx.sharpen", {"amount": 2.5}),
        ("cutvoke.fx.edge", {"mode": "wire", "low": 0.05, "high": 0.22}),
    ), ("amount",)),
    ("boldPoster", "色块网格", (
        ("cutvoke.fx.grid", {"cell": 88, "thickness": 2,
                              "opacity": 0.48, "color": "white"}),
        ("cutvoke.fx.posterize", {"levels": 3}),
    ), ("cell", "thickness", "opacity", "color")),
    ("negativeFilm", "发光反相", (
        ("cutvoke.fx.glow", {"intensity": 2.2}),
        ("cutvoke.fx.invert", {"strength": 1.0}),
    ), ("intensity",)),
    ("yellowGrid", "黄色构图网格", (
        ("cutvoke.fx.grid", {"cell": 64, "thickness": 3,
                              "opacity": 0.72, "color": "#FFD60A"}),
    ), ("cell", "thickness", "opacity", "color")),
    ("circleAperture", "圆形聚焦视窗", (
        ("cutvoke.fx.mask", {"shape": "circle", "x": 0.5, "y": 0.5,
                             "w": 0.68, "h": 0.84, "feather": 0.025,
                             "invert": False}),
    ), ("x", "y", "w", "h", "feather")),
    ("sharpMirror", "镜面锐化", (
        ("cutvoke.fx.sharpen", {"amount": 2.6}),
        ("cutvoke.fx.mirror", {}),
    ), ("amount",)),
    ("invertedLens", "倒置曲面", (
        ("cutvoke.fx.lens", {"k1": -0.62, "k2": -0.12}),
        ("cutvoke.fx.vflip", {}),
    ), ("k1", "k2")),
)


def main() -> None:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = {item["presetId"]: item for item in document["presets"]}
    ids = {f"cutvoke.preset.fx.{slug}" for slug, *_ in BASIC}
    for slug, name, stack, adjustable in BASIC:
        preset_id = f"cutvoke.preset.fx.{slug}"
        old = entries.get(preset_id)
        if old and old.get("status") == "approved":
            continue
        effects = [{"effectId": effect_id, "params": params}
                   for effect_id, params in stack]
        desired = {
            "presetId": preset_id, "version": "1.0.0", "name": name,
            "family": "fx", "subcategory": "基础",
            "effectId": effects[0]["effectId"], "params": effects[0]["params"],
            "effects": effects, "adjustable": list(adjustable),
            "license": "MIT", "downloadState": "bundled", "status": "candidate",
        }
        if not old or any(old.get(key) != value for key, value in desired.items()):
            entries[preset_id] = desired
    document["presets"] = [item for item in document["presets"]
                           if item["presetId"] not in ids] + [
        entries[f"cutvoke.preset.fx.{slug}"] for slug, *_ in BASIC]
    CATALOG.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    print(f"Maintained {len(BASIC)} basic FX candidates; approved entries preserved")


if __name__ == "__main__":
    main()
