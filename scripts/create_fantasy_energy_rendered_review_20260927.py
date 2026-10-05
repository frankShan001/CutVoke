"""Build a contact sheet from the actual rendered fantasy-energy sticker previews."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from extract_fantasy_energy_overlay_atlas_20260927 import ITEMS, ROOT


PREVIEWS = ROOT / "src/cutvoke/assets/sticker_previews"
OUTPUT = ROOT / "docs/assets/fantasy-energy-overlay-atlas-20260927-rendered-review.png"
REPORT = ROOT / "docs/assets/fantasy-energy-overlay-atlas-20260927-rendered-review.json"
TILE = (320, 180)
FOOTER = 36


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create() -> dict:
    if OUTPUT.exists() or REPORT.exists():
        raise FileExistsError("refusing to overwrite existing rendered-review artifacts")
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 12) if font_path.is_file() else ImageFont.load_default()
    sheet = Image.new("RGB", (TILE[0] * 4, (TILE[1] + FOOTER) * 4), (27, 32, 41))
    draw = ImageDraw.Draw(sheet)
    entries = []
    with tempfile.TemporaryDirectory(prefix="cutvoke-fantasy-review-") as temporary:
        temp = Path(temporary)
        for index, (stem, name, _keywords) in enumerate(ITEMS):
            video = PREVIEWS / f"{stem}.mp4"
            if not video.is_file():
                raise FileNotFoundError(video)
            frame_path = temp / f"{stem}.png"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", "0.8", "-i", str(video), "-frames:v", "1", str(frame_path),
            ], check=True, capture_output=True)
            with Image.open(frame_path) as rendered:
                frame = rendered.convert("RGB")
                if frame.size != TILE:
                    raise ValueError(f"unexpected rendered size for {stem}: {frame.size}")
                row, column = divmod(index, 4)
                x, y = column * TILE[0], row * (TILE[1] + FOOTER)
                sheet.paste(frame, (x, y))
            draw.rectangle((x, y + TILE[1], x + TILE[0] - 1, y + TILE[1] + FOOTER - 1),
                           fill=(22, 27, 35))
            draw.text((x + 7, y + TILE[1] + 8), f"{index + 1:02d}  {name} · {stem}",
                      font=font, fill=(244, 246, 249))
            entries.append({
                "stickerId": f"cutvoke.sticker.{stem}",
                "name": name,
                "sourceSha256": _sha(ROOT / "src/cutvoke/assets/stickers" / f"{stem}.png"),
                "preview": video.name,
                "previewSha256": _sha(video),
                "sampleTimeSeconds": 0.8,
            })
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(OUTPUT, optimize=True)
    result = {
        "schemaVersion": 1,
        "generator": "RenderService output sampled with ffmpeg",
        "background": "editor preview's dark-to-light gradient",
        "contactSheet": OUTPUT.relative_to(ROOT).as_posix(),
        "contactSheetSha256": _sha(OUTPUT),
        "items": entries,
    }
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return result


if __name__ == "__main__":
    result = create()
    print(json.dumps({"items": len(result["items"]),
                      "contactSheet": result["contactSheet"]}, ensure_ascii=False, indent=2))
