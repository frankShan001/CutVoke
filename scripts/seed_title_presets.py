"""Maintain editable title and title-animation candidates in the built-in catalog.

Each candidate has a different composition or typography treatment. Running
this script never approves a preset; real preview media and review evidence
are generated separately.
"""

from __future__ import annotations

import json
from pathlib import Path


CATALOG = Path(__file__).resolve().parents[1] / "src/cutvoke/core/builtin_presets.json"
SAMPLE = "创作标题"

# slug, display name, subcategory, visual treatment
STYLES: tuple[tuple[str, str, str, dict], ...] = (
    ("minimal_white", "留白正题", "简约", dict(fontSize=150, scale=1.6, color="#ffffff", strokeColor="#14213d", strokeWidth=1, bold=True, y=.47)),
    ("minimal_ink", "白底黑字", "简约", dict(fontSize=140, scale=1.5, fontFamily="Noto Serif SC", color="#17202d", strokeColor="#17202d", strokeWidth=0, bold=True, background="#f7f2e8", y=.48)),
    ("minimal_left", "左对齐引言", "简约", dict(fontSize=150, scale=1.6, fontFamily="Noto Serif SC", color="#ffffff", strokeColor="#13252d", strokeWidth=2, align="left", x=.12, y=.49)),
    ("minimal_vertical", "侧边细题", "简约", dict(fontSize=116, scale=1.35, color="#f5f4ed", strokeColor="#1d2634", strokeWidth=1, rotation=-90, x=.16, y=.65)),
    ("minimal_lower", "底部短题", "简约", dict(fontSize=145, scale=1.45, color="#ffffff", strokeColor="#111827", strokeWidth=3, y=.80)),
    ("minimal_slate", "石板灰题", "简约", dict(fontSize=143, scale=1.45, fontFamily="Noto Serif SC", color="#e9f3ff", strokeColor="#1e293b", strokeWidth=1, background="#435a75", bold=True, x=.54, y=.46)),
    ("minimal_cream", "奶油卡片", "简约", dict(fontSize=142, scale=1.5, fontFamily="Noto Serif SC", color="#483728", strokeColor="#483728", strokeWidth=0, background="#ffe8c6", bold=True, x=.5, y=.46)),
    ("minimal_coral", "珊瑚重点", "简约", dict(fontSize=152, scale=1.55, color="#ff8e78", strokeColor="#182638", strokeWidth=3, bold=True, y=.48)),
    ("flower_gold", "金边大片", "花字", dict(fontSize=148, fontFamily="Noto Serif SC", color="#ffd24a", strokeColor="#281809", strokeWidth=6, bold=True, shadow=6, y=.43)),
    ("flower_neon", "双光霓虹", "花字", dict(fontSize=130, color="#30f4ff", strokeColor="#df24c8", strokeWidth=6, bold=True, shadow=8, y=.43)),
    ("flower_candy", "糖果粉蓝", "花字", dict(fontSize=126, color="#ff7cbf", strokeColor="#30ddf0", strokeWidth=7, bold=True, shadow=4, rotation=-7, y=.43)),
    ("flower_flame", "烈焰橙红", "花字", dict(fontSize=146, color="#ffb21e", strokeColor="#a91d1d", strokeWidth=8, bold=True, shadow=6, rotation=4, y=.43)),
    ("flower_mint", "薄荷冰晶", "花字", dict(fontSize=138, color="#cbfff6", strokeColor="#117f9b", strokeWidth=6, bold=True, shadow=7, y=.43)),
    ("flower_violet", "紫电花字", "花字", dict(fontSize=132, color="#f1b9ff", strokeColor="#651bb8", strokeWidth=7, bold=True, shadow=7, rotation=6, y=.43)),
    ("flower_silver", "银幕金属", "花字", dict(fontSize=142, fontFamily="Noto Serif SC", color="#ecf5ff", strokeColor="#394865", strokeWidth=7, bold=True, shadow=9, y=.42)),
    ("flower_pop", "撞色波普", "花字", dict(fontSize=138, color="#fff149", strokeColor="#e11671", strokeWidth=8, bold=True, shadow=2, rotation=-5, y=.43)),
    ("label_navy", "深蓝信息条", "信息条", dict(fontSize=120, scale=1.2, color="#ffffff", strokeColor="#ffffff", strokeWidth=0, background="#152e5a", panelWidth=.82, panelHeight=.19, bold=True, y=.79)),
    ("label_lime", "荧绿角标", "信息条", dict(fontSize=116, scale=1.18, color="#172325", strokeColor="#172325", strokeWidth=0, background="#c8fb69", panelWidth=.42, panelHeight=.20, bold=True, align="left", x=.12, y=.25)),
    ("label_red", "红色提示牌", "信息条", dict(fontSize=126, scale=1.15, color="#ffffff", strokeColor="#ffffff", strokeWidth=0, background="#b62d39", panelWidth=.38, panelHeight=.20, bold=True, x=.72, y=.79)),
    ("label_black", "黑白新闻条", "信息条", dict(fontSize=108, scale=1.2, fontFamily="Noto Serif SC", color="#141414", strokeColor="#141414", strokeWidth=0, background="#f6f6f6", panelWidth=.86, panelHeight=.20, bold=True, align="left", x=.13, y=.79)),
    ("label_teal", "青色章节牌", "信息条", dict(fontSize=122, scale=1.18, color="#08384a", strokeColor="#08384a", strokeWidth=0, background="#72e7dc", panelWidth=.52, panelHeight=.20, bold=True, x=.5, y=.29)),
    ("label_orange", "橙色强调条", "信息条", dict(fontSize=124, scale=1.18, color="#241e1a", strokeColor="#241e1a", strokeWidth=0, background="#ffb347", panelWidth=.60, panelHeight=.19, bold=True, x=.38, y=.78)),
    ("label_purple", "紫色直播牌", "信息条", dict(fontSize=120, scale=1.18, color="#ffffff", strokeColor="#ffffff", strokeWidth=0, background="#673ab7", panelWidth=.36, panelHeight=.20, bold=True, x=.76, y=.28)),
    ("label_outline", "深灰提示牌", "信息条", dict(fontSize=128, scale=1.2, color="#ffffff", strokeColor="#0e1527", strokeWidth=4, background="#293548", panelWidth=.70, panelHeight=.21, bold=True, y=.78)),
    ("motion_type_reveal", "逐字书写", "文字动画", dict(fontSize=142, color="#f5f7ff", strokeColor="#22334d", strokeWidth=2, bold=True, animIn=900, animInStyle="typewriter", animOut=250, animOutStyle="fade", animLoopStyle="none", animLoopMs=1000)),
    ("motion_scale_burst", "聚焦弹现", "文字动画", dict(fontSize=154, color="#ffcc6f", strokeColor="#8b3d1e", strokeWidth=4, bold=True, animIn=450, animInStyle="scale", animOut=250, animOutStyle="fade", animLoopStyle="none", animLoopMs=1000)),
    ("motion_scale_retreat", "缩放来回", "文字动画", dict(fontSize=145, color="#aaf8ff", strokeColor="#174f78", strokeWidth=3, bold=True, animIn=650, animInStyle="scale", animOut=550, animOutStyle="scale", animLoopStyle="none", animLoopMs=1000)),
    ("motion_pulse", "呼吸强调", "文字动画", dict(fontSize=142, color="#ffd1e7", strokeColor="#8d3159", strokeWidth=3, bold=True, animIn=250, animInStyle="fade", animOut=250, animOutStyle="fade", animLoopStyle="pulse", animLoopMs=650)),
    ("motion_blink", "信号闪烁", "文字动画", dict(fontSize=140, color="#bfff81", strokeColor="#183c2a", strokeWidth=3, bold=True, animIn=150, animInStyle="fade", animOut=250, animOutStyle="fade", animLoopStyle="blink", animLoopMs=400)),
    ("motion_type_pulse", "逐字呼吸", "文字动画", dict(fontSize=146, fontFamily="Noto Serif SC", color="#ffe2a1", strokeColor="#574022", strokeWidth=3, bold=True, animIn=850, animInStyle="typewriter", animOut=450, animOutStyle="scale", animLoopStyle="pulse", animLoopMs=550)),
    ("motion_scale_blink", "缩放闪现", "文字动画", dict(fontSize=150, color="#eac7ff", strokeColor="#6833a2", strokeWidth=4, bold=True, animIn=550, animInStyle="scale", animOut=400, animOutStyle="scale", animLoopStyle="blink", animLoopMs=500)),
    ("motion_type_blink", "逐字霓虹", "文字动画", dict(fontSize=136, color="#88f4ff", strokeColor="#bf2abd", strokeWidth=5, bold=True, animIn=750, animInStyle="typewriter", animOut=250, animOutStyle="fade", animLoopStyle="blink", animLoopMs=450)),
)


def main() -> None:
    manifest = json.loads(CATALOG.read_text(encoding="utf-8"))
    existing = {item["presetId"]: item for item in manifest["presets"]}
    for slug, name, subcategory, visual in STYLES:
        preset_id = f"cutvoke.preset.text.{slug}"
        scale = 1.3 if subcategory == "简约" else 1.8 if subcategory == "花字" else 1.0
        params = {"content": SAMPLE, "animIn": 300, "animOut": 200,
                  "scale": scale, **visual}
        old = existing.get(preset_id)
        if old and old.get("status") == "approved":
            continue  # 已审核媒体与参数的版本不可被重新播种脚本覆盖。
        version = ("1.2.0" if subcategory in ("简约", "信息条") else
                   "1.0.0" if subcategory == "文字动画" else "1.1.0")
        unchanged = old and all(old.get(key) == value for key, value in (
            ("version", version), ("name", name),
            ("subcategory", subcategory), ("params", params)))
        entry = dict(old) if unchanged else {}
        entry.update({
            "presetId": preset_id, "version": version, "name": name,
            "family": "text", "subcategory": subcategory,
            "effectId": "cutvoke.text", "params": params,
            "defaultDuration": 2.5, "license": "MIT",
            "adjustable": ["fontSize", "fontFamily", "lineSpacing", "color", "strokeColor", "strokeWidth",
                           "background", "panelWidth", "panelHeight", "x", "y", "scale", "rotation", "animIn", "animOut",
                           "animInStyle", "animOutStyle", "animLoopStyle", "animLoopMs"],
            "downloadState": "bundled", "status": "candidate",
        })
        existing[preset_id] = entry
    non_text = [item for item in manifest["presets"] if item.get("family") != "text"]
    manifest["presets"] = non_text + [existing[f"cutvoke.preset.text.{slug}"] for slug, *_ in STYLES]
    CATALOG.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Maintained {len(STYLES)} title entries; existing approvals preserved")


if __name__ == "__main__":
    main()
