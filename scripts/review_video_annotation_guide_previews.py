"""Build a contact sheet from the actual rendered sticker preview videos."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from extract_video_annotation_guide_atlas_20260927 import ITEMS, ROOT


PREVIEWS = ROOT / "src/cutvoke/assets/sticker_previews"
OUTPUT = ROOT / "docs/assets/video-annotation-guide-sticker-atlas-20260927-rendered-review.png"
REPORT = ROOT / "docs/assets/video-annotation-guide-sticker-atlas-20260927-rendered-review.json"
CELL = (320, 180)
LABEL_HEIGHT = 28


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build() -> dict:
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 12) if font_path.is_file() else ImageFont.load_default()
    sheet = Image.new("RGB", (CELL[0] * 4, (CELL[1] + LABEL_HEIGHT) * 4), "#202838")
    draw = ImageDraw.Draw(sheet)
    items = []
    with tempfile.TemporaryDirectory(prefix="cutvoke-guide-review-") as temporary:
        temp = Path(temporary)
        for index, (stem, name, _keywords) in enumerate(ITEMS):
            video = PREVIEWS / f"{stem}.mp4"
            frame_path = temp / f"{stem}.png"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", "1.0", "-i", str(video), "-frames:v", "1", str(frame_path),
            ], check=True, capture_output=True)
            with Image.open(frame_path) as opened:
                frame = opened.convert("RGB")
                if frame.size != CELL:
                    raise ValueError(f"unexpected preview frame size for {stem}: {frame.size}")
                row, column = divmod(index, 4)
                x, y = column * CELL[0], row * (CELL[1] + LABEL_HEIGHT)
                sheet.paste(frame, (x, y))
            draw.rectangle((x, y + CELL[1], x + CELL[0] - 1,
                            y + CELL[1] + LABEL_HEIGHT - 1), fill="#303744")
            draw.text((x + 8, y + CELL[1] + 7), f"{index + 1:02d}  {name} · {stem}",
                      font=font, fill="white")
            items.append({"stem": stem, "preview": video.name,
                          "previewSha256": _sha(video), "frameAtSeconds": 1.0})
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(OUTPUT, optimize=True)
    report = {"schemaVersion": 1, "generator": "scripts/review_video_annotation_guide_previews.py",
              "contactSheet": OUTPUT.relative_to(ROOT).as_posix(), "items": items}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    result = build()
    print(json.dumps({"reviewedPreviews": len(result["items"]),
                      "contactSheet": result["contactSheet"]}, ensure_ascii=False, indent=2))
