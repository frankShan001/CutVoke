"""Create category contact sheets for manual review of bundled sticker artwork.

This is review material only; a sheet does not approve a sticker.
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from cutvoke.core.builtin_stickers import load_builtin_stickers


def main(output: Path, subcategory: str | None = None,
         sticker_ids: list[str] | None = None) -> None:
    output.mkdir(parents=True, exist_ok=True)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 15) if font_path.exists() else ImageFont.load_default()
    small = ImageFont.truetype(str(font_path), 11) if font_path.exists() else ImageFont.load_default()
    groups: dict[str, list[dict]] = {}
    selected = set(sticker_ids or ())
    for item in load_builtin_stickers():
        if subcategory is not None and item["subcategory"] != subcategory:
            continue
        if selected and item["stickerId"] not in selected:
            continue
        groups.setdefault(item["subcategory"], []).append(item)
    if selected and len(selected) != sum(len(items) for items in groups.values()):
        found = {item["stickerId"] for items in groups.values() for item in items}
        raise ValueError(f"unknown or filtered sticker ids: {sorted(selected - found)}")
    for category, items in groups.items():
        columns = 4
        rows = (len(items) + columns - 1) // columns
        sheet = Image.new("RGB", (columns * 190, rows * 212), "#182637")
        draw = ImageDraw.Draw(sheet)
        for index, item in enumerate(items):
            x, y = (index % columns) * 190, (index // columns) * 212
            checker = Image.new("RGB", (172, 150), "#f5f7fa")
            tile = ImageDraw.Draw(checker)
            for yy in range(0, 150, 20):
                for xx in range(0, 172, 20):
                    if (xx // 20 + yy // 20) % 2:
                        tile.rectangle((xx, yy, xx + 19, yy + 19), fill="#dce4ed")
            with Image.open(item["path"]) as raw:
                art = raw.convert("RGBA")
                art.thumbnail((128, 128))
                checker.paste(art, ((172 - art.width) // 2, (150 - art.height) // 2), art)
            sheet.paste(checker, (x + 9, y + 7))
            draw.text((x + 9, y + 163), item["name"], font=font, fill="white")
            draw.text((x + 9, y + 185), item["stickerId"].rsplit(".", 1)[-1],
                      font=small, fill="#a8bbce")
        path = output / f"sticker-{category}.png"
        sheet.save(path)
        print(path)
        rendered = Image.new("RGB", (640, ((len(items) + 1) // 2) * 215), "#182637")
        rendered_draw = ImageDraw.Draw(rendered)
        with tempfile.TemporaryDirectory(prefix="cutvoke-sticker-review-") as temporary:
            for index, item in enumerate(items):
                x, y = (index % 2) * 320, (index // 2) * 215
                frame_path = Path(temporary) / f"{index}.png"
                subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", "0.55", "-i", item["previewPath"], "-frames:v", "1",
                    str(frame_path),
                ], check=True, capture_output=True)
                with Image.open(frame_path) as frame:
                    rendered.paste(frame.convert("RGB"), (x, y))
                rendered_draw.text((x + 6, y + 184), item["name"], font=font,
                                   fill="white")
                rendered_draw.text((x + 125, y + 188),
                                   item["stickerId"].rsplit(".", 1)[-1],
                                   font=small, fill="#a8bbce")
        rendered_path = output / f"sticker-{category}-rendered.png"
        rendered.save(rendered_path)
        print(rendered_path)
    dynamic = [item for items in groups.values() for item in items if item["kind"] == "dynamic"]
    if not dynamic:
        return
    times = (0.20, 0.55, 0.90, 1.30)
    motion = Image.new("RGB", (150 + len(times) * 170, len(dynamic) * 116), "#182637")
    motion_draw = ImageDraw.Draw(motion)
    with tempfile.TemporaryDirectory(prefix="cutvoke-sticker-motion-") as temporary:
        for row, item in enumerate(dynamic):
            motion_draw.text((8, row * 116 + 30), item["name"], font=font, fill="white")
            for column, time in enumerate(times):
                frame_path = Path(temporary) / f"{row}-{column}.png"
                subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", str(time), "-i", item["previewPath"], "-frames:v", "1",
                    str(frame_path),
                ], check=True, capture_output=True)
                with Image.open(frame_path) as frame:
                    thumb = frame.convert("RGB").resize((160, 90))
                    motion.paste(thumb, (150 + column * 170, row * 116))
                motion_draw.text((150 + column * 170, row * 116 + 94), f"{time:.2f}s",
                                 font=small, fill="#a8bbce")
    motion_path = output / "sticker-dynamic-motion.png"
    motion.save(motion_path)
    print(motion_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/sticker_review"))
    parser.add_argument("--subcategory", help="Review only one sticker category")
    parser.add_argument("--sticker-id", action="append",
                        help="Review one sticker; repeat for a selected batch")
    args = parser.parse_args()
    main(args.output, args.subcategory, args.sticker_id)
