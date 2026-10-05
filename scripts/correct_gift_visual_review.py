"""Correct the root review after enlarged source and library inspection."""
from prepare_effect_visual_review import OUT, save
import json

path = OUT / "reviews/dynamic-stickers.json"
data = json.loads(path.read_text(encoding="utf-8"))
for record in data["records"]:
    if record["stickerId"] == "cutvoke.sticker.gift_float":
        record.update(status="source_artwork_clipped", motionStatus="pass",
            observation="礼盒上下周期漂浮平滑、尺寸稳定；放大原 gift.png 及新库连续帧后确认蝴蝶结顶部源图已截断。此前蝴蝶结完整的观察不准确，现纠正，素材待修。",
            correctionEvidence=[str(OUT / "sticker-library/gift_float-continuous.png"),
                                "E:/myproject/opencut/src/cutvoke/assets/stickers/gift.png"])
save(path, data)
path = OUT / "reviews/sticker-pages.json"
data = json.loads(path.read_text(encoding="utf-8"))
for page in data["pages"]:
    if page["page"] in {2, 38}:
        page["correction"] = "后续放大源图确认 gift/gift_float 的蝴蝶结顶部平切；单项结论修正为 source_artwork_clipped，不能以缩略拼图的完整结论覆盖。"
save(path, data)
print("Corrected gift source-artwork observations without editing media")
