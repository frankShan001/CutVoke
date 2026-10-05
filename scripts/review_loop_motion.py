"""Create four-frame contact sheets for visual review of loop candidates."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "src/cutvoke/core/builtin_presets.json"
OUTPUT = REPO / "output/preset_review"
FONT = REPO / "src/cutvoke/assets/fonts/NotoSansSC-VF.ttf"
SAMPLE_AT = (0.08, 0.25, 0.55, 1.0)


def main() -> None:
    entries = json.loads(CATALOG.read_text(encoding="utf-8"))["presets"]
    loops = [entry for entry in entries if entry.get("family") == "animation"
             and entry.get("subcategory") == "循环"]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(str(FONT), 17)
    for batch in range(0, len(loops), 4):
        canvas = Image.new("RGB", (1280, 4 * 216), "#18212d")
        draw = ImageDraw.Draw(canvas)
        with tempfile.TemporaryDirectory(prefix="cutvoke-loop-review-") as temporary:
            temp = Path(temporary)
            for row, entry in enumerate(loops[batch:batch + 4]):
                video = (CATALOG.parent / entry["motionPreview"]).resolve()
                for column, at in enumerate(SAMPLE_AT):
                    frame = temp / f"{row}-{column}.png"
                    subprocess.run([
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", str(at), "-i", str(video), "-frames:v", "1", str(frame),
                    ], check=True, capture_output=True)
                    with Image.open(frame) as image:
                        canvas.paste(image.convert("RGB"), (column * 320, row * 216))
                draw.text((9, row * 216 + 184),
                          f"{entry['name']}  {entry['effectId'].rsplit('.', 1)[-1]}",
                          font=font, fill="#e8edf5")
        output = OUTPUT / f"loop-motion-{batch // 4 + 1}.png"
        canvas.save(output)
        print(output)


if __name__ == "__main__":
    main()
