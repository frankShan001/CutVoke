"""Render an opt-in Quick Whip contribution using original synthetic artwork only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw
from cutvoke.domain.model import AssetReference, Clip, Project, Sequence, Track
from cutvoke.domain.rational import Rational
from cutvoke.rendering.effects import EffectRegistry
from cutvoke.rendering.render import RenderService

EFFECT_ID = "cutvoke.transition.community.quick-whip"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory for generated evidence")
    parser.add_argument("--duration", type=float, default=0.25)
    args = parser.parse_args()
    registry = EffectRegistry()
    registry.discover([ROOT / "effects" / "transitions"], strict=True)
    params = registry.validate_params(EFFECT_ID, {"duration": args.duration})
    output = args.output.resolve()
    if output.exists():
        parser.error("--output must be a new directory; preserve earlier evidence")
    output.mkdir(parents=True)
    clips = []
    width, height, fps = 640, 360, 60
    palettes = [("#102c49", "#27d9cb", "#ffca6a"), ("#3b1b50", "#ff786a", "#ffe1ae")]
    for index, (background, accent, light) in enumerate(palettes):
        image = Image.new("RGB", (width, height), background)
        draw = ImageDraw.Draw(image)
        for x in range(-height, width, 80):
            draw.line((x, height, x + height, 0), fill=accent, width=3)
        draw.rounded_rectangle((64, 64, 270, 296), radius=24, fill=accent)
        draw.ellipse((340, 84, 532, 276), fill=light)
        draw.rectangle((92, 100 + index * 64, 242, 132 + index * 64), fill=background)
        source = output / f"synthetic-{index + 1}.png"
        image.save(source)
        clips.append(Clip(f"scene-{index + 1}", AssetReference(f"source-{index + 1}", str(source)),
            Rational.of(index * 3, 2), Rational.of((index + 1) * 3, 2), Rational.of(0),
            effects=[{"effectId": EFFECT_ID, "params": params}] if index else []))
    project = Project("1", "quick-whip-demo", "0", Sequence("sequence", width, height,
        Rational.of(fps), tracks=[Track("video", "video", clips=clips)]))
    renderer = RenderService(registry=registry, gpu_effects=False)
    renderer.gpu_acceleration = False
    video = output / "quick-whip.mp4"
    result = renderer.render(project, str(video), quality="high")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-f", "rawvideo",
        "-pix_fmt", "rgb24", "-"], check=True, capture_output=True, timeout=60).stdout
    frame_size = width * height * 3
    assert len(raw) == 3 * fps * frame_size, "Unexpected decoded frame count"
    middle = round((1.5 + args.duration / 2) * fps)
    Image.frombytes("RGB", (width, height), raw[middle * frame_size:(middle + 1) * frame_size]).save(output / "preview.png")
    (output / "project.json").write_text(json.dumps(project.to_dict(), indent=2), encoding="utf-8")
    report = {"effectId": EFFECT_ID, "params": params, "decodedFrames": 3 * fps,
        "previewFrame": middle, "previewTimeSeconds": middle / fps, "export": result,
        "provenance": "Original procedural geometry, generated locally. No external or personal media. MIT."}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "decodedFrames": 3 * fps, "duration": args.duration}))


if __name__ == "__main__":
    main()
