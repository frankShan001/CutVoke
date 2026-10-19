"""Seed the reusable lettering selected from the handheld travel case.

Adds candidates only. Existing entries and all review gates are preserved.
Run verify_travel_title_presets.py, inspect its rendered media, then record a
visual decision separately. No reference-image pixels are bundled.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "src/cutvoke/core/builtin_presets.json"
IDS = ["cutvoke.preset.text.travel_sunshine", "cutvoke.preset.text.travel_reaction",
       "cutvoke.preset.text.travel_note"]


def main():
    base = dict(fontFamily="Space Grotesk", bold=True, fontSize=84, color="#FFE16B",
                strokeColor="#FFFFFF", strokeWidth=4.5, shadow=0,
                outerStrokeWidth=4, outerStrokeColor="#4B9FAD",
                blockShadowX=4, blockShadowY=6, blockShadowColor="#2B7183",
                x=.5, y=.5, rotation=-4, animInStyle="impact", animIn=280,
                animOutStyle="fade", animOut=140, animLoopStyle="float",
                animLoopMs=2400, animStrength=.35)
    designs = [
        (IDS[0], "晴日旅拍", "花字", dict(base, content="DAY OUT!")),
        (IDS[1], "俏皮反应", "花字", dict(base, content="Oops!", fontSize=100,
            color="#FFB0CA", outerStrokeColor="#DB7D9B", blockShadowColor="#AB4D77",
            rotation=5, animLoopMs=1800, animStrength=.6)),
        (IDS[2], "旅行手记", "信息条", dict(content="GOOD VIBES ONLY",
            fontFamily="Barlow Condensed", bold=True, fontSize=42,
            color="#233C40", strokeWidth=0, shadow=0, background="#F3EBDD",
            panelWidth=.92, panelHeight=.075, x=.5, y=.86,
            animInStyle="fade", animIn=220, animOutStyle="fade", animOut=180,
            animLoopStyle="none")),
    ]
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    existing = {p["presetId"] for p in data["presets"]}
    for pid, name, category, params in designs:
        if pid in existing:
            continue
        data["presets"].append(dict(presetId=pid, version="1.0.0", name=name,
            family="text", subcategory=category, effectId="cutvoke.text", params=params,
            defaultDuration=3, adjustable=["fontSize", "strokeWidth", "x", "y",
                "rotation", "animIn", "animOut"] + (["outerStrokeWidth", "blockShadowX",
                "blockShadowY", "animStrength", "animLoopMs"] if category == "花字"
                else ["panelWidth", "panelHeight"]),
            license="MIT", downloadState="bundled", status="candidate", checks={}, evidence={},
            source="CutVoke 原创旅行案例沉淀；builtin_presets.json 参数化文字预设；"
                   "使用内置授权字体，无参考图像像素；单片段统一改字、描边、二维错位投影与微动态。"))
    CATALOG.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\r\n")


if __name__ == "__main__":
    main()
