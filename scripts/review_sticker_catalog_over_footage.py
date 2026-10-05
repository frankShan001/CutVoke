#!/usr/bin/env python3
"""Create a read-only visual screening atlas for built-in stickers over real footage.

This is a contact-sheet aid only. It does not render through the editor, judge
commercial quality, or replace independent human acceptance.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from cutvoke.core.render import RenderService


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src/cutvoke/core/builtin_stickers.json"
STICKER_DIR = ROOT / "src/cutvoke/assets/stickers"
SOURCE_PACK = ROOT / "output/acceptance/jy-camera-commons-pack-20260926"
SOURCE_MANIFEST = SOURCE_PACK / "source-manifest.json"
OUTPUT = ROOT / "output/acceptance/jy-sticker-footage-catalog-review-20260927"

CANVAS = (1920, 1080)
TILE = (184, 122)
PREVIEW = (176, 99)
LABEL_HEIGHT = 23
COLUMNS = 11
PAGE_SIZE = 110


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_canvas_frame(ffmpeg: str, video: Path, timestamp: float, target: Path) -> None:
    filter_graph = (
        f"scale={CANVAS[0]}:{CANVAS[1]}:force_original_aspect_ratio=decrease,"
        f"pad={CANVAS[0]}:{CANVAS[1]}:(ow-iw)/2:(oh-ih)/2:color=black"
    )
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{timestamp:.3f}",
         "-i", str(video), "-frames:v", "1", "-vf", filter_graph, str(target)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    if result.returncode or not target.is_file():
        raise RuntimeError(f"frame extraction failed for {video.name}: {result.stderr[-1200:]}")


def make_sheet(
    background: Image.Image,
    records: list[dict],
    artwork: dict[str, Image.Image],
    camera_name: str,
    page_number: int,
    font: ImageFont.ImageFont,
) -> Path:
    start = page_number * PAGE_SIZE
    page = records[start:start + PAGE_SIZE]
    rows = (len(page) + COLUMNS - 1) // COLUMNS
    sheet = Image.new("RGB", (COLUMNS * TILE[0], rows * TILE[1]), "#27313b")
    draw = ImageDraw.Draw(sheet)
    backdrop = background.resize(PREVIEW, Image.Resampling.LANCZOS).convert("RGBA")
    sticker_px = round(PREVIEW[1] * 0.28)

    for local_index, record in enumerate(page):
        row, column = divmod(local_index, COLUMNS)
        x, y = column * TILE[0], row * TILE[1]
        tile = Image.new("RGBA", TILE, "#27313b")
        preview = backdrop.copy()
        sticker = artwork[record["stem"]].resize(
            (sticker_px, sticker_px), Image.Resampling.LANCZOS
        )
        preview.alpha_composite(
            sticker,
            ((PREVIEW[0] - sticker_px) // 2, (PREVIEW[1] - sticker_px) // 2),
        )
        tile.alpha_composite(preview, ((TILE[0] - PREVIEW[0]) // 2, 2))
        sheet.paste(tile.convert("RGB"), (x, y))
        label = f"{record['index']:03d} {record['stem']}"
        draw.text((x + 4, y + PREVIEW[1] + 5), label[:26], font=font, fill="#f2f4f7")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / f"{camera_name}-stickers-{page_number + 1:02d}.jpg"
    sheet.save(path, quality=88, optimize=True, progressive=True)
    return path


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite existing review evidence: {OUTPUT}")

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    source_manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    videos = [item for item in source_manifest["files"]
              if item["filename"].lower().endswith((".webm", ".mp4"))]
    if len(videos) != 6 or not source_manifest.get("localOnly"):
        raise ValueError("expected the existing six-video, local-only Commons footage pack")

    records = []
    artwork: dict[str, Image.Image] = {}
    for index, item in enumerate(catalog["stickers"], start=1):
        path = STICKER_DIR / f"{item['stem']}.png"
        if not path.is_file():
            raise FileNotFoundError(path)
        image = Image.open(path).convert("RGBA")
        if abs(image.size[0] - image.size[1]) > 1 or image.getchannel("A").getbbox() is None:
            raise ValueError(f"invalid transparent sticker artwork: {path}")
        artwork[item["stem"]] = image
        records.append({
            "index": index,
            "stem": item["stem"],
            "name": item["name"],
            "subcategory": item["subcategory"],
            "asset": path.relative_to(ROOT).as_posix(),
            "assetSha256": sha256(path),
        })

    OUTPUT.mkdir(parents=True)
    frame_dir = OUTPUT / "source-frames"
    frame_dir.mkdir()
    renderer = RenderService()
    font_path = Path("C:/Windows/Fonts/segoeui.ttf")
    font = ImageFont.truetype(str(font_path), 10) if font_path.is_file() else ImageFont.load_default()
    source_rows = []
    sheet_rows = []

    for source in videos:
        video = SOURCE_PACK / source["localPath"]
        if not video.is_file() or sha256(video) != source["sha256"]:
            raise ValueError(f"source footage hash mismatch: {video}")
        duration = float(source["probe"]["format"]["duration"])
        timestamp = round(duration / 2, 3)
        frame_path = frame_dir / f"{video.stem}-midpoint.png"
        extract_canvas_frame(renderer.ffmpeg, video, timestamp, frame_path)
        source_rows.append({
            "filename": source["filename"],
            "page": source["page"],
            "author": source["author"],
            "license": source["license"],
            "description": source["description"],
            "sha256": source["sha256"],
            "durationSeconds": duration,
            "sampleTimestampSeconds": timestamp,
            "frame": frame_path.relative_to(ROOT).as_posix(),
            "frameSha256": sha256(frame_path),
        })
        with Image.open(frame_path) as opened:
            background = opened.convert("RGB")
            if background.size != CANVAS:
                raise ValueError(f"unexpected frame canvas: {background.size}")
            for page_number in range((len(records) + PAGE_SIZE - 1) // PAGE_SIZE):
                sheet_path = make_sheet(
                    background, records, artwork, video.stem, page_number, font
                )
                sheet_rows.append({
                    "sourceFilename": source["filename"],
                    "page": page_number + 1,
                    "firstStickerIndex": page_number * PAGE_SIZE + 1,
                    "lastStickerIndex": min((page_number + 1) * PAGE_SIZE, len(records)),
                    "image": sheet_path.relative_to(ROOT).as_posix(),
                    "sha256": sha256(sheet_path),
                })

    report = {
        "schemaVersion": 1,
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "generator": "scripts/review_sticker_catalog_over_footage.py",
        "status": "visual_screening_only",
        "purpose": "Review the built-in sticker catalog on varied licensed camera footage.",
        "limitations": [
            "Contact sheets approximate the editor's 1920x1080 canvas and centered 0.28 scale.",
            "This does not use the editor render path and is not export evidence.",
            "No human readability score or commercial-quality acceptance is implied.",
            "Motion variants share the same still artwork and are not motion-tested here.",
            "Commons footage remains local-only QA material and is not bundled with product resources.",
        ],
        "catalog": {
            "path": CATALOG.relative_to(ROOT).as_posix(),
            "sha256": sha256(CATALOG),
            "version": catalog["version"],
            "stickerCount": len(records),
            "subcategoryCount": len({record["subcategory"] for record in records}),
            "motionVariantCount": len(catalog["motionVariants"]),
        },
        "renderApproximation": {
            "canvas": list(CANVAS),
            "stickerScale": 0.28,
            "placement": "centered",
            "previewPixels": list(PREVIEW),
            "sheetPageSize": PAGE_SIZE,
            "columns": COLUMNS,
        },
        "sources": source_rows,
        "stickers": records,
        "contactSheets": sheet_rows,
    }
    (OUTPUT / "review-manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    readme = """# Built-in sticker catalog on real footage

This is a visual screening aid for all built-in still sticker assets. It overlays each sticker at the editor's documented initial centered 0.28 scale on midpoint frames from six locally retained Wikimedia Commons videos. The source videos are not copied or bundled here; source URLs, authors, licenses, timestamps, hashes, sticker hashes, and sheet hashes are in `review-manifest.json`.

The sheets approximate composition at 176x99 pixels per tile. They are not generated through the editor renderer, do not test motion, and do not claim human, export, commercial, or independent acceptance. Any specific issue found in this screen needs confirmation in the real editor/export path.
"""
    (OUTPUT / "README.md").write_text(readme, encoding="utf-8")
    print(f"Created {len(sheet_rows)} contact sheets for {len(records)} stickers across {len(source_rows)} real-footage sources.")
    print(OUTPUT.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
