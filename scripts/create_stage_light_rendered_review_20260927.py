"""Contact-sheet real editor-rendered stage-light sticker previews for visual review."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from extract_stage_light_overlay_atlas_20260927 import ITEMS, ROOT


PREVIEWS = ROOT / "src/cutvoke/assets/sticker_previews"
OUTPUT = ROOT / "docs/assets/concert-stage-light-rendered-review-20260927.png"
REPORT = ROOT / "docs/assets/concert-stage-light-rendered-review-20260927.json"
FFMPEG = "ffmpeg"
TILE = (320, 180)
FOOTER = 28


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create() -> dict:
    if OUTPUT.exists() or REPORT.exists():
        raise FileExistsError("refusing to overwrite stage-light rendered review artifacts")
    sheet = Image.new("RGB", (TILE[0] * 4, (TILE[1] + FOOTER) * 4), (26, 30, 38))
    draw = ImageDraw.Draw(sheet)
    entries = []
    with tempfile.TemporaryDirectory(prefix="cutvoke-stage-light-review-") as temporary:
        temp = Path(temporary)
        for index, (stem, name, _keywords) in enumerate(ITEMS):
            video = PREVIEWS / f"{stem}.mp4"
            if not video.is_file():
                raise FileNotFoundError(video)
            frame = temp / f"{stem}.png"
            subprocess.run([
                FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                "-ss", "0.8", "-i", str(video), "-frames:v", "1", str(frame),
            ], check=True, capture_output=True)
            with Image.open(frame) as rendered:
                if rendered.size != TILE:
                    raise ValueError(f"unexpected rendered size for {stem}: {rendered.size}")
                row, column = divmod(index, 4)
                x, y = column * TILE[0], row * (TILE[1] + FOOTER)
                sheet.paste(rendered.convert("RGB"), (x, y))
            row, column = divmod(index, 4)
            x, y = column * TILE[0], row * (TILE[1] + FOOTER)
            draw.rectangle((x, y + TILE[1], x + TILE[0] - 1, y + TILE[1] + FOOTER - 1),
                           fill=(24, 28, 34))
            draw.text((x + 8, y + TILE[1] + 6), f"{index + 1:02d}  {stem}",
                      fill=(242, 244, 248))
            entries.append({
                "index": index + 1,
                "stickerId": f"cutvoke.sticker.{stem}",
                "name": name,
                "sourceSha256": _sha(ROOT / "src/cutvoke/assets/stickers" / f"{stem}.png"),
                "previewSha256": _sha(video),
            })
    sheet.save(OUTPUT, optimize=True)
    result = {
        "schemaVersion": 1,
        "generator": "RenderService output sampled with ffmpeg",
        "sampleTimeSeconds": 0.8,
        "background": "editor preview's generated dark-to-light gradient",
        "contactSheet": OUTPUT.relative_to(ROOT).as_posix(),
        "contactSheetSha256": _sha(OUTPUT),
        "items": entries,
    }
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(create(), ensure_ascii=False, indent=2))
