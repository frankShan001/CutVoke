"""Audit built-in preset coverage without counting registered operators as finished content.

Run with ``python scripts/effect_coverage.py`` or ``--json`` for CI/reporting.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from cutvoke.core.effects import EffectRegistry
from cutvoke.core.preset_catalog import FAMILIES, PresetCatalog
from cutvoke.core.builtin_stickers import load_builtin_stickers


FAMILY_NAMES = {
    "fx": "画面特效", "transition": "转场", "animation": "视频动画",
    "text": "文字花字/动画", "filter": "滤镜", "sticker": "贴纸",
    "personFx": "人物特效",
}


def coverage() -> dict:
    effects = EffectRegistry()
    presets = PresetCatalog.builtin(effects)
    registered = Counter(
        (item["browseCategory"], item["subcategory"])
        for item in (spec.to_dict() for spec in effects.all())
        if item["browseCategory"] in FAMILIES
    )
    candidates = Counter((preset.family, preset.subcategory) for preset in presets.all()
                         if preset.status == "candidate")
    stickers = load_builtin_stickers()
    candidates.update(("sticker", sticker["subcategory"]) for sticker in stickers
                      if not sticker["qualified"])
    qualified = Counter((preset.family, preset.subcategory) for preset in presets.all()
                        if preset.qualified)
    qualified.update(("sticker", sticker["subcategory"]) for sticker in stickers
                     if sticker["qualified"])
    rows = []
    for family in FAMILIES:
        subcategories = sorted({subcategory for group, subcategory in registered
                                if group == family} |
                               {subcategory for group, subcategory in candidates
                                if group == family} |
                               {subcategory for group, subcategory in qualified
                                if group == family})
        for subcategory in subcategories:
            key = (family, subcategory)
            rows.append({
                "family": family, "familyName": FAMILY_NAMES[family],
                "subcategory": subcategory, "registered": registered[key],
                "candidate": candidates[key], "qualified": qualified[key],
                "gapToEight": max(0, 8 - qualified[key]),
            })
        for index in range(max(0, 3 - len(subcategories))):
            rows.append({
                "family": family, "familyName": FAMILY_NAMES[family],
                "subcategory": f"待新增细分类 {index + 1}", "registered": 0,
                "candidate": 0, "qualified": 0, "gapToEight": 8,
            })
    return {
        "registeredEffects": len(effects.all()),
        "curatedCandidates": sum(p.status == "candidate" for p in presets.all()),
        "stickerCandidates": sum(not item["qualified"] for item in stickers),
        "qualifiedStickers": sum(item["qualified"] for item in stickers),
        "qualifiedPresets": sum(p.qualified for p in presets.all()),
        "qualifiedContent": sum(qualified.values()),
        "minimumFirstBatch": 144,
        "gapAcrossOpenAndRequiredSubcategories": sum(row["gapToEight"] for row in rows),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable coverage")
    args = parser.parse_args()
    report = coverage()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print("| 预设族 | 细分类 | 已注册算子 | 候选预设 | 合格预设 | 距每类8个还差 |")
    print("| --- | --- | ---: | ---: | ---: | ---: |")
    for row in report["rows"]:
        print(f"| {row['familyName']} | {row['subcategory']} | {row['registered']} | "
              f"{row['candidate']} | {row['qualified']} | {row['gapToEight']} |")
    print(f"\n已注册算子 {report['registeredEffects']}，效果候选预设 "
          f"{report['curatedCandidates']}，贴纸候选资源 {report['stickerCandidates']}，"
          f"合格效果预设 {report['qualifiedPresets']}，合格贴纸 "
          f"{report['qualifiedStickers']}，合格内容共 {report['qualifiedContent']}。")
    print("候选不计入 144 个合格条目；每个开放细分类均需独立达到 8 个。")


if __name__ == "__main__":
    main()
