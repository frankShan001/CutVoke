"""Record the manual image review performed in this Codex task."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from review_sticker_library import apply

OUT = ROOT / "output/acceptance/product-completion-20261003/artwork"
# These are the exact images opened and directly inspected in this task.
# Rerendered/edited evidence requires a new visual review, not this old decision.
OBSERVED_SHEETS = {
    "gift.overview.png": "8444dfd306f5f99fd4c1873acd57cea866cdfb87695419e9f990f7a39dfb4a32",
    "gift.library-all-frames.png": "761e38e62085e54d2d12141ffc4221d5465c581489f8745663c22bd9bfe63c1e",
    "gift_float.overview.png": "9008589a0dd2e8566017a20dd87883d72470ed9a3c7b553089d3251a711e81d3",
    "gift_float.library-all-frames.png": "523613cd0d7c2495590adb9019dd7577ac743e35e810c4e9703a96e3ade1de5b",
    "gift_float.all-frames.png": "34493f575fb0a634832b53e685aa79cb713cecabf0cbd5e1825f0974e21516d9",
}
for name, expected in OBSERVED_SHEETS.items():
    if hashlib.sha256((OUT / name).read_bytes()).hexdigest() != expected:
        raise ValueError(f"media changed after direct inspection; new review required: {name}")
review = json.loads((OUT / "real-project-review.json").read_text(encoding="utf-8"))
decisions = []
for item in review:
    stem = item["stickerId"].rsplit(".", 1)[-1]
    sheet_names = [f"{stem}.overview.png", f"{stem}.library-all-frames.png"]
    if stem == "gift_float":
        sheet_names.append(f"{stem}.all-frames.png")
    observations = (
        "从原图集重提取后，蝴蝶结两侧圆弧和白色描边完整。已目视前后对照、静态库预览全部30帧与潮汐工程10个时刻；盒体与蝴蝶结稳定，透明外部保留底片，无裁切。"
        if stem == "gift" else
        "蝴蝶结顶部圆弧与描边完整。已目视动态库预览全部30帧、潮汐工程全部120帧及10时刻大图；礼物平滑上下浮动，无形变、间歇消失或边缘裁切。")
    item.update(visualDecision="approved", observation=observations,
                reviewedSheets=[{"path": name, "sha256": hashlib.sha256((OUT/name).read_bytes()).hexdigest()}
                                for name in sheet_names])
    decisions.append({"stickerId": item["stickerId"], "decision": "approved",
        "sourceSha256": item["sourceSha256"], "previewSha256": item["previewSha256"],
        "observation": observations})
payload = {"schemaVersion": 1, "reviewer": "Codex direct visual inspection",
           "reviewedAt": datetime.now(timezone.utc).isoformat(),
           "method": "Direct inspection of before/after alpha artwork, every library frame, and actual Tide project frames; dynamic 120-frame sequence inspected",
           "decisions": decisions}
path = OUT / "visual-decisions.json"
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
apply(path)
(OUT / "real-project-review.json").write_text(json.dumps(review, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
print("approved 2 corrected existing stickers after direct visual inspection")
