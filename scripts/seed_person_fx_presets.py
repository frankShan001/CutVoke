"""Seed three transparent-person effect categories with eight distinct candidates each."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src/cutvoke/core/builtin_presets.json"
LICENSE = "MIT"

GROUPS = {
    "cutvoke.person.halo": (
        "人物轮廓", (
            ("aquaEdge", "海盐青光", {"sigma": 16, "hueShift": 165, "saturation": 1.7, "opacity": 0.95}),
            ("amberEdge", "琥珀柔边", {"sigma": 20, "hueShift": 28, "saturation": 1.45, "opacity": 0.92}),
            ("violetEdge", "紫电轮廓", {"sigma": 14, "hueShift": -78, "saturation": 1.9, "opacity": 0.98}),
            ("roseEdge", "玫瑰柔光", {"sigma": 22, "hueShift": -20, "saturation": 1.4, "opacity": 0.9}),
            ("iceEdge", "冰晶蓝晕", {"sigma": 24, "hueShift": -120, "saturation": 1.8, "opacity": 0.94}),
            ("limeEdge", "青柠能量边", {"sigma": 12, "hueShift": 110, "saturation": 1.8, "opacity": 0.98}),
            ("pearlEdge", "珍珠漫射", {"sigma": 18, "hueShift": -120, "saturation": 0.45, "opacity": 1.0}),
            ("neonEdge", "霓虹聚焦", {"sigma": 10, "hueShift": 178, "saturation": 2.0, "opacity": 1.0}),
        ),
    ),
    "cutvoke.person.echo": (
        "人物残影", (
            ("softEcho", "轻柔动作残影", {"frames": 3, "decay": 0.42}),
            ("blueEcho", "连续速度残影", {"frames": 5, "decay": 0.58}),
            ("longEcho", "长尾跟随", {"frames": 8, "decay": 0.62}),
            ("staccatoEcho", "短促多重影", {"frames": 4, "decay": 0.78}),
            ("mistEcho", "雾化拖影", {"frames": 10, "decay": 0.38}),
            ("pulseEcho", "节奏回声", {"frames": 6, "decay": 0.7}),
            ("fastEcho", "疾速掠影", {"frames": 3, "decay": 0.9}),
            ("dreamEcho", "梦幻漫游影", {"frames": 12, "decay": 0.46}),
        ),
    ),
    "cutvoke.person.tint": (
        "人物色彩", (
            ("warmPortrait", "暖阳人像", {"hue": 18, "saturation": 0.85, "intensity": 0.18, "lightness": True}),
            ("coolPortrait", "青绿撞色", {"hue": 145, "saturation": 0.88, "intensity": 0.02, "lightness": True}),
            ("violetPortrait", "紫雾人像", {"hue": -80, "saturation": 0.9, "intensity": -0.08, "lightness": False}),
            ("rosePortrait", "玫瑰胶片", {"hue": -24, "saturation": 0.82, "intensity": 0.12, "lightness": True}),
            ("mintPortrait", "青柠撞色", {"hue": 105, "saturation": 0.86, "intensity": 0.1, "lightness": False}),
            ("goldPortrait", "橙紫舞台", {"hue": 34, "saturation": 1.0, "intensity": 0.26, "lightness": False}),
            ("monoPortrait", "柔和去色", {"hue": 0, "saturation": -1.0, "intensity": -0.05, "lightness": True}),
            ("cinemaPortrait", "电影青橙", {"hue": 165, "saturation": 0.82, "intensity": 0.16, "lightness": False}),
        ),
    ),
}


def seed() -> int:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    existing = {item["presetId"] for item in document.get("presets", [])}
    pending = []
    for effect_id, (subcategory, entries) in GROUPS.items():
        for slug, name, params in entries:
            preset_id = f"cutvoke.preset.personFx.{slug}"
            if preset_id in existing:
                raise ValueError(f"preset already exists: {preset_id}")
            pending.append({
                "presetId": preset_id,
                "version": "1.4.0" if effect_id == "cutvoke.person.halo" else "1.1.0",
                "name": name,
                "family": "personFx",
                "subcategory": subcategory,
                "effectId": effect_id,
                "params": params,
                "effects": [{"effectId": effect_id, "params": params}],
                "adjustable": list(params),
                "license": LICENSE,
                "source": "CutVoke 内置人物特效算子；要求人物抠像生成并保留 Alpha 通道的视频层。",
                "downloadState": "bundled",
                "status": "candidate",
                "checks": {key: False for key in (
                    "cover", "motionPreview", "visualDistinct", "apply", "edit",
                    "saveReload", "undo", "export",
                )},
                "evidence": {},
            })
    if len(pending) != 24:
        raise ValueError(f"expected 24 presets, got {len(pending)}")
    document["presets"].extend(pending)
    CATALOG.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    return len(pending)


if __name__ == "__main__":
    print(f"seeded {seed()} person FX candidates")
