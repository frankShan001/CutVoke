"""Build contact sheets from real engine exports for natural-light stickers."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
PREVIEW_ROOT = ROOT / "src/cutvoke/assets/sticker_previews"
ATLAS_REPORT = ROOT / "docs/assets/natural-light-shadow-overlay-atlas-20260927.extraction.json"
OUTPUT_DARK = ROOT / "docs/assets/natural-light-shadow-overlay-rendered-review-dark-20260927.png"
OUTPUT_LIGHT = ROOT / "docs/assets/natural-light-shadow-overlay-rendered-review-light-20260927.png"
REPORT = ROOT / "docs/assets/natural-light-shadow-overlay-rendered-review-20260927.json"
FRAME_TIME = 0.75


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render() -> list[dict]:
    extracted = json.loads(ATLAS_REPORT.read_text(encoding="utf-8"))
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 16) if font_path.exists() else ImageFont.load_default()
    frame_records = []
    dark = Image.new("RGB", (4 * 400, 4 * 254), "#263246")
    light = Image.new("RGB", (4 * 400, 4 * 254), "#f1eee8")
    with tempfile.TemporaryDirectory(prefix="natural-light-review-") as temporary:
        temp = Path(temporary)
        for index, item in enumerate(extracted["items"]):
            stem = item["stem"]
            audit_path = PREVIEW_ROOT / f"{stem}.audit.json"
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            video = PREVIEW_ROOT / audit["auditExport"]
            if _sha(video) != audit["auditExportSha256"]:
                raise ValueError(f"audit export hash changed: {stem}")
            frame_path = temp / f"{stem}.png"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", str(FRAME_TIME), "-i", str(video), "-frames:v", "1",
                str(frame_path),
            ], check=True, capture_output=True)
            with Image.open(frame_path) as source:
                frame = source.convert("RGB").resize((360, 203), Image.Resampling.LANCZOS)
            row, column = divmod(index, 4)
            x, y = column * 400, row * 254
            dark.paste(frame, (x + 20, y + 12))
            light.paste(frame, (x + 20, y + 12))
            label = f"{index + 1:02d}. {item['name']}"
            ImageDraw.Draw(dark).text((x + 18, y + 221), label, font=font, fill="#f3f4f6")
            ImageDraw.Draw(light).text((x + 18, y + 221), label, font=font, fill="#20242a")
            frame_records.append({
                "stickerId": f"cutvoke.sticker.{stem}",
                "frameTimeSeconds": FRAME_TIME,
                "auditExport": video.name,
                "auditExportSha256": _sha(video),
                "frameSha256": _sha(frame_path),
                "previewExportMeanRgbDifferences": audit["previewExportMeanRgbDifferences"],
            })
    dark.save(OUTPUT_DARK, optimize=True)
    light.save(OUTPUT_LIGHT, optimize=True)
    REPORT.write_text(json.dumps({
        "schemaVersion": 1,
        "generator": "scripts/render_natural_light_shadow_overlay_review.py",
        "frameSource": "per-sticker RenderService export audit",
        "frameTimeSeconds": FRAME_TIME,
        "items": frame_records,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return frame_records


if __name__ == "__main__":
    print(json.dumps(render(), ensure_ascii=False, indent=2))
