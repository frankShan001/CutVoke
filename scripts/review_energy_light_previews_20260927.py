"""Create a contact sheet and hash record from rendered energy-light previews."""

from __future__ import annotations

import hashlib
import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
EXTRACTION = ROOT / "docs/assets/energy-light-overlay-atlas-20260927.extraction.json"
PREVIEWS = ROOT / "src/cutvoke/assets/sticker_previews"
CONTACT = ROOT / "docs/assets/energy-light-overlay-atlas-20260927-rendered-review.png"
REPORT = ROOT / "docs/assets/energy-light-overlay-atlas-20260927-rendered-review.json"
CELL = (320, 180)
LABEL_HEIGHT = 30


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(*, replace_existing: bool = False) -> dict:
    extraction = json.loads(EXTRACTION.read_text(encoding="utf-8"))
    if len(extraction.get("items", [])) != 16:
        raise ValueError("rendered preview review requires all 16 atlas cells")
    if (CONTACT.exists() or REPORT.exists()) and not replace_existing:
        raise FileExistsError("refusing to overwrite rendered energy-light review")
    if replace_existing:
        if not REPORT.is_file() or not CONTACT.is_file():
            raise FileNotFoundError("both prior rendered-review files are required for replacement")
        old = json.loads(REPORT.read_text(encoding="utf-8"))
        if (old.get("generator") != "scripts/review_energy_light_previews_20260927.py"
                or old.get("contactSheetSha256") != _sha(CONTACT)):
            raise ValueError("refusing to replace rendered review that is not this script's output")
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 12) if font_path.is_file() else ImageFont.load_default()
    sheet = Image.new("RGB", (CELL[0] * 4, (CELL[1] + LABEL_HEIGHT) * 4), "#202838")
    draw = ImageDraw.Draw(sheet)
    records = []
    with tempfile.TemporaryDirectory(prefix="cutvoke-energy-light-review-") as temporary:
        temp = Path(temporary)
        for index, item in enumerate(extraction["items"]):
            stem = item["stem"]
            video = PREVIEWS / f"{stem}.mp4"
            frame_path = temp / f"{stem}.png"
            if not video.is_file():
                raise FileNotFoundError(video)
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", "0.8", "-i", str(video), "-frames:v", "1", str(frame_path),
            ], check=True, capture_output=True)
            with Image.open(frame_path) as opened:
                frame = opened.convert("RGB")
                if frame.size != CELL:
                    raise ValueError(f"unexpected rendered frame size for {stem}: {frame.size}")
                row, column = divmod(index, 4)
                x, y = column * CELL[0], row * (CELL[1] + LABEL_HEIGHT)
                sheet.paste(frame, (x, y))
            draw.rectangle((x, y + CELL[1], x + CELL[0] - 1,
                            y + CELL[1] + LABEL_HEIGHT - 1), fill="#303744")
            draw.text((x + 7, y + CELL[1] + 7), f"{index + 1:02d}  {item['name']}",
                      font=font, fill="white")
            records.append({
                "stickerId": f"cutvoke.sticker.{stem}",
                "sourceSha256": item["sha256"],
                "preview": video.relative_to(ROOT).as_posix(),
                "previewSha256": _sha(video),
                "frameSha256": _sha(frame_path),
                "frameAtSeconds": 0.8,
            })
    sheet.save(CONTACT, optimize=True)
    report = {
        "schemaVersion": 1,
        "generator": "scripts/review_energy_light_previews_20260927.py",
        "contactSheet": CONTACT.relative_to(ROOT).as_posix(),
        "contactSheetSha256": _sha(CONTACT),
        "items": records,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace-existing", action="store_true",
                        help="replace only this script's own rendered preview review")
    args = parser.parse_args()
    result = build(replace_existing=args.replace_existing)
    print(json.dumps({"renderedPreviews": len(result["items"]),
                      "contactSheet": result["contactSheet"]}, ensure_ascii=False, indent=2))
