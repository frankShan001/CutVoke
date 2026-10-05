"""Show every sticker's alpha source beside its actual composited frame."""
import json
from pathlib import Path
import sys
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cutvoke.core.builtin_stickers import load_builtin_stickers
OLD = ROOT / "output/acceptance/effects-real-project-20261002"
OUT = ROOT / "output/acceptance/effects-visual-review-20261003"
FONT = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 16)
SMALL = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 11)

dest = OUT / "sheets/stickers"
dest.mkdir(parents=True, exist_ok=True)
stickers = load_builtin_stickers()
records = []
for start in range(0, len(stickers), 16):
    selected = stickers[start:start + 16]
    image = Image.new("RGB", (1200, 1020), "#17202b")
    draw = ImageDraw.Draw(image)
    draw.text((10, 8), f"贴图逐项视觉检查 {start + 1}–{start + len(selected)} / {len(stickers)}", font=FONT, fill="white")
    draw.text((10, 33), "左：实际工程叠加画面 0.55s；右：透明源贴图（棋盘底）；每格标题与 ID", font=SMALL, fill="#c5d3e1")
    page = dest / f"stickers-{start // 16:03}.png"
    for offset, sticker in enumerate(selected):
        index = start + offset
        slot, batch = index % 8, index // 8
        x, y = offset % 4 * 300, 65 + offset // 4 * 235
        draw.text((x + 5, y), f'{index + 1:03} {sticker["name"]} | {sticker["kind"]}', font=FONT, fill="white")
        draw.text((x + 5, y + 25), sticker["stickerId"].removeprefix("cutvoke.sticker."), font=SMALL, fill="#a6c5e5")
        with Image.open(OLD / f"sticker-renders/batch-{batch:03}/export.png") as applied:
            cell = applied.crop((slot % 4 * 160, slot // 4 * 180, slot % 4 * 160 + 160, slot // 4 * 180 + 180))
            image.paste(cell, (x + 5, y + 45))
        checker = Image.new("RGB", (125, 180), "#c5cbd5")
        cd = ImageDraw.Draw(checker)
        for cy in range(0, 180, 12):
            for cx in range(0, 125, 12):
                if (cx // 12 + cy // 12) % 2:
                    cd.rectangle((cx, cy, cx + 11, cy + 11), fill="#f1f3f6")
        with Image.open(sticker["path"]) as source:
            cutout = source.convert("RGBA")
            cutout.thumbnail((120, 165), Image.Resampling.LANCZOS)
            checker.paste(cutout, ((125 - cutout.width) // 2, (180 - cutout.height) // 2), cutout)
        image.paste(checker, (x + 170, y + 45))
        records.append({"stickerId": sticker["stickerId"], "name": sticker["name"], "kind": sticker["kind"],
                        "subcategory": sticker["subcategory"], "page": str(page), "slotOnPage": offset,
                        "source": sticker["path"], "appliedVideo": str(OLD / f"sticker-renders/batch-{batch:03}/export.mp4")})
    image.save(page)
(OUT / "sticker-manifest.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
print("Sticker visual pages:", (len(stickers) + 15) // 16)
