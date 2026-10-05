"""Render every bundled sticker over a real video canvas for content review.

Static artwork remains static. A short MP4 still lets the user inspect its
compositing on a background; only motion variants must change over time.
This script records media checks, not visual approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from PIL import Image, ImageChops

from cutvoke.core.builtin_stickers import STICKER_ROOT, load_builtin_stickers
from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(sticker_id: str | list[str] | None = None,
         prefix: str | None = None) -> None:
    output = STICKER_ROOT.parent / "sticker_previews"
    output.mkdir(parents=True, exist_ok=True)
    renderer = RenderService()
    audit: list[dict] = []
    sticker_ids = ({sticker_id} if isinstance(sticker_id, str) else
                   set(sticker_id or ()))
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        background = temp / "background.png"
        texture_background = Image.new("RGB", (320, 180))
        background_pixels = []
        for y in range(180):
            vertical = y / 179
            for x in range(320):
                horizontal = x / 319
                dark = (30 + round(7 * vertical), 38 + round(8 * vertical),
                        52 + round(10 * vertical))
                light = (238 - round(10 * vertical), 229 - round(9 * vertical),
                         210 - round(7 * vertical))
                background_pixels.append(tuple(
                    round(dark[channel] * (1 - horizontal) + light[channel] * horizontal)
                    for channel in range(3)))
        texture_background.putdata(background_pixels)
        for sticker in load_builtin_stickers():
            if sticker_ids and sticker["stickerId"] not in sticker_ids:
                continue
            if prefix is not None and not sticker["stickerId"].startswith(prefix):
                continue
            is_texture_overlay = (
                sticker["subcategory"] in {
                    "电影叠加", "材质纹理", "氛围纹理", "背景纹理", "镜头光效",
                    "数码故障", "人物氛围", "复古拼贴", "产品广告",
                }
                or sticker["stickerId"].startswith("cutvoke.sticker.fxoverlay_")
                or sticker.get("defaultScale", 0) >= 0.8
            )
            background_image = texture_background if is_texture_overlay else Image.new(
                "RGB", (320, 180), (235, 239, 245))
            background_image.save(background)
            project = EditService().create_project(sticker["name"], width=320, height=180,
                                                   fps=Rational.of(15))
            project.sequence.tracks = [
                Track("base", "video", [Clip(
                    "background", AssetReference("background", str(background)),
                    Rational.of(0), Rational.of(2), Rational.of(0),
                )]),
                Track("stickers", "video", [Clip(
                    "motion", AssetReference(sticker["assetId"], sticker["path"]),
                    Rational.of(0), Rational.of(2), Rational.of(0), role="sticker",
                    effects=[
                        {"effectId": "cutvoke.transform", "version": "1.0.0",
                         "params": {
                             "scale": (scale := sticker.get(
                                 "defaultScale", 0.92 if is_texture_overlay else 0.46)),
                             "position": {
                                 "x": round((320 - round(180 * scale)) / 2),
                                 "y": round((180 - round(180 * scale)) / 2),
                             },
                         }},
                        *([{"effectId": sticker["effectId"], "version": "1.0.0",
                            "params": sticker["params"]}]
                          if sticker["kind"] == "dynamic" else []),
                    ],
                )], role="sticker"),
            ]
            target = output / f"{sticker['stickerId'].split('.')[-1]}.mp4"
            renderer.render(project, str(target), overwrite=True)
            frames = []
            for index, at in enumerate((0.2, 0.55, 0.9, 1.3)):
                frame = temp / f"{index}.png"
                renderer.extract_frame(project, at, str(frame))
                frames.append(Image.open(frame).convert("RGB"))
            changed = max(
                sum(1 for pixel in ImageChops.difference(left, right).get_flattened_data()
                    if max(pixel) > 18)
                for left, right in zip(frames, frames[1:])
            )
            visible = sum(
                1 for pixel in ImageChops.difference(frames[1], background_image)
                .get_flattened_data() if max(pixel) > 18
            )
            for frame in frames:
                frame.close()
            record = {"schemaVersion": 1, "stickerId": sticker["stickerId"],
                      "version": sticker["version"], "kind": sticker["kind"],
                      "generator": "scripts/generate_sticker_previews.py",
                      "sourceSha256": _sha(Path(sticker["path"])),
                      "previewSha256": _sha(target), "preview": target.name,
                      "bytes": target.stat().st_size, "visiblePixels": visible,
                      "changedPixels": changed,
                      "motionVisible": changed >= 100}
            (output / f"{sticker['stickerId'].split('.')[-1]}.qa.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            audit.append(record)
            if visible < 100 or (sticker["kind"] == "dynamic" and changed < 100):
                raise RuntimeError(f"sticker sample failed visibility/motion: {sticker['stickerId']}")
            print(f"{sticker['stickerId']}: {visible} visible, {changed} changed pixels")
    if not audit:
        raise ValueError(f"no sticker found: {sticker_id or prefix}")
    all_items = load_builtin_stickers()
    record_paths = [output / f"{item['stickerId'].rsplit('.', 1)[-1]}.qa.json"
                    for item in all_items]
    if all(path.is_file() for path in record_paths):
        records = [json.loads(path.read_text(encoding="utf-8")) for path in record_paths]
        (output / "qa.json").write_text(json.dumps(records, ensure_ascii=False, indent=2)
                                        + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sticker-id", action="append",
                        help="Render one sticker; repeat to render a selected batch")
    parser.add_argument("--prefix", help="Render a new sticker family without rewriting older previews")
    args = parser.parse_args()
    main(args.sticker_id, args.prefix)
