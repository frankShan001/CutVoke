"""Render the recovered gift and its existing motion variant on Tide footage."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cutvoke.core.builtin_stickers import PREVIEW_ROOT, load_builtin_stickers
from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.render import RenderService
from cutvoke.core.rational import Rational
from verify_effects_real_projects import make_bases

OUT = ROOT / "output/acceptance/product-completion-20261003/artwork"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def frames(video, indices, width, height):
    runs = []
    for index in indices:
        if runs and index == runs[-1][1] + 1:
            runs[-1][1] = index
        else:
            runs.append([index, index])
    expression = "+".join(f"eq(n\\,{left})" if left == right else
                          f"between(n\\,{left}\\,{right})" for left, right in runs)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf",
        f"select={expression},scale={width}:{height}", "-fps_mode", "vfr", "-an",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
        capture_output=True, check=True, timeout=90).stdout
    stride = width * height * 3
    if len(raw) != stride * len(indices):
        raise RuntimeError(f"decoded {len(raw)//stride} of {len(indices)} expected frames")
    return [Image.frombytes("RGB", (width, height), raw[n * stride:(n+1) * stride])
            for n in range(len(indices))]


def sheet(video, target, indices, fps, width=320, height=180, columns=6):
    images = frames(video, indices, width, height)
    result = Image.new("RGB", (columns * width, ((len(indices)+columns-1)//columns) * (height+24)),
                       (23, 31, 41))
    draw = ImageDraw.Draw(result)
    for position, (frame, index) in enumerate(zip(images, indices)):
        x, y = position % columns * width, position // columns * (height+24)
        result.paste(frame, (x, y))
        draw.text((x+6, y+height+4), f"frame {index} / {index/fps:.3f}s", fill=(235, 239, 245))
    result.save(target)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    base = make_bases(ROOT / "output/acceptance/effects-real-project-20261002")["video"]
    base.sequence.width, base.sequence.height = 640, 360
    base.sequence.fps = Rational.of(30)
    report = []
    renderer = RenderService()
    for item in load_builtin_stickers():
        if item["stickerId"] not in {"cutvoke.sticker.gift", "cutvoke.sticker.gift_float"}:
            continue
        stem = item["stickerId"].rsplit(".", 1)[-1]
        project = copy.deepcopy(base)
        project.project_id = "repaired-" + stem
        project.name = "潮汐藏馆 | 完整礼物贴纸复核"
        effects = [{"effectId": "cutvoke.transform", "params": {
            "scale": 0.6, "position": {"x": 210, "y": 72}}}]
        if item["kind"] == "dynamic":
            effects.append({"effectId": item["effectId"], "params": item["params"]})
        project.sequence.tracks.append(Track("gift-overlay", "video", clips=[
            Clip("gift-clip", AssetReference(item["assetId"], item["path"]),
                 Rational.of(0), Rational.of(4), Rational.of(0), effects=effects, role="sticker")],
                 role="sticker"))
        snapshot = OUT / f"{stem}.project.json"
        snapshot.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=2)+"\n",
                            encoding="utf-8")
        video = OUT / f"{stem}.real-project.mp4"
        renderer.render(project, str(video), quality="medium", overwrite=True)
        subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(video),
                        "-f", "null", "NUL"], capture_output=True, check=True, timeout=90)
        sheet(video, OUT / f"{stem}.overview.png", [0, 5, 15, 30, 45, 60, 75, 90, 105, 119], 30)
        if item["kind"] == "dynamic":
            sheet(video, OUT / f"{stem}.all-frames.png", list(range(120)), 30,
                  width=160, height=90, columns=8)
        preview = Path(item["previewPath"])
        sheet(preview, OUT / f"{stem}.library-all-frames.png", list(range(30)), 15,
              columns=5)
        subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(preview),
                        "-f", "null", "NUL"], capture_output=True, check=True, timeout=90)
        report.append({"stickerId": item["stickerId"], "version": item["version"],
            "sourceSha256": sha(item["path"]), "previewSha256": sha(preview),
            "videoSha256": sha(video), "projectSha256": sha(snapshot),
            "rendererSha256": sha(ROOT / "src/cutvoke/core/render.py"),
            "decoded": True, "visualDecision": "pending"})
    (OUT / "real-project-review.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)
                                                 + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
