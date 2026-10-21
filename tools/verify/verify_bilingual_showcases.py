"""Read-only verification and README images derived from native CutVoke exports.

No video editing/encoding: GIFs are silent, scaled documentation previews of
the complete native MP4 timeline. MP4 sound, timing and effects are untouched.
Requires PyAV for full media decode (the editor itself does not require PyAV).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import zipfile

import av
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
SHOWCASES = ROOT / "examples/showcases"


def digest(path):
    with path.open("rb") as stream:
        value = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
        return value.hexdigest()


def package(path):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None, path
        manifest = json.loads(archive.read("manifest.json"))
        project = json.loads(archive.read("project.json"))
        sources = set()
        for item in manifest["assets"]:
            assert hashlib.sha256(archive.read(item["packagePath"])).hexdigest() == item["sha256"]
            sources.add(item["originalPath"])
        sequence = project["sequence"]
        texts = []
        for track in sequence["tracks"]:
            for clip in track["clips"]:
                source = clip["assetRef"].get("sourcePath")
                assert not source or source in sources, (path, source)
                for effect in clip.get("effects", []):
                    params = effect.get("params", {})
                    if effect["effectId"] == "cutvoke.text" or (
                            effect["effectId"] == "cutvoke.fx.mask" and params.get("shape") == "text"):
                        text = params.get("content", "")
                        texts.append(text)
                        if re.search(r"[\u4e00-\u9fff]", text):
                            assert params["fontFamily"] in ("Noto Sans SC", "Noto Serif SC")
        chinese = any(re.search(r"[\u4e00-\u9fff]", t) for t in texts)
        if "_en." in path.name:
            assert not chinese, path
        else:
            assert chinese, path
            assert not any(re.search("[a-zA-Z]", re.sub("cutvoke", "", t, flags=re.I)) for t in texts), texts
        return dict(path=path.relative_to(SHOWCASES).as_posix(), projectId=project["projectId"],
                    name=project["name"], revision=project["revision"], crc="passed",
                    verifiedAssets=len(manifest["assets"]), editableTextCount=len(texts),
                    hasChineseText=chinese, bytes=path.stat().st_size, sha256=digest(path))


def localization_invariants(base, prefix):
    """Localization must not silently replace sources, retime clips or remix sound."""
    def load(lang):
        with zipfile.ZipFile(base / "projects" / f"{prefix}_{lang}.cutvokepack") as archive:
            return json.loads(archive.read("project.json"))["sequence"]
    en, zh = load("en"), load("zh-CN")
    for key in ("width", "height", "fps", "audioSampleRate"):
        assert en[key] == zh[key]
    assert len(en["tracks"]) == len(zh["tracks"])
    checked = 0
    for a, b in zip(en["tracks"], zh["tracks"]):
        assert (a["id"], a["kind"]) == (b["id"], b["kind"])
        if a["kind"] == "audio":
            assert a == b, a["id"]
        assert len(a["clips"]) == len(b["clips"])
        for x, y in zip(a["clips"], b["clips"]):
            for key in ("id", "assetRef", "timelineStart", "timelineEnd", "sourceStart",
                        "speed", "speedCurve", "volume", "fadeIn", "fadeOut", "pitch",
                        "freezeAt", "keyframes"):
                assert x.get(key) == y.get(key), (prefix, x["id"], key)
            checked += 1
    result = dict(checkedClips=checked, identicalCanvasAndFrameRate=True,
                  identicalSourcesAndClipTiming=True, identicalAudioTracks=True,
                  identicalClipAudioControls=True, identicalFreezeAndKeyframes=True)
    (base / "verification/localization-invariants.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def inspect_media(path, make_preview=False):
    path = path.resolve()
    output = path.parent.parent / "verification"
    output.mkdir(exist_ok=True)
    shots, thumbnails, black = {}, [], []
    count, end, next_gif = 0, 0., 0.
    travel = "travel-vlog" in path.name
    is_case = "case" in path.name
    expected = (42.5 if is_case else 38.5) if travel else (379 / 12 if is_case else 67 / 3)
    targets = ([2, 5, 9, 20.8, 25.5, 35.25, 39.5, 41] if travel and is_case else
               [1.5, 4, 8, 12, 16, 20, 25, 30] if is_case else [1.5, 5, 9, 13, 17, 21])
    cover_at = (20.8 if travel else 19.5) if is_case else 10
    cover = None
    with av.open(str(path)) as container:
        video = container.streams.video[0]
        fps, width, height = float(video.average_rate), video.width, video.height
        assert fps == 24 and (width, height) == ((1080, 1920) if travel else (1024, 576))
        display_width = 216 if travel else 320
        display_height = round(display_width * height / width)
        for frame in container.decode(video=0):
            at = float(frame.time or count / fps)
            pixels = frame.to_ndarray(format="rgb24")
            if pixels.mean() < 1:
                black.append(round(at, 4))
            image = None
            for target in targets:
                if target not in shots and at >= target:
                    image = Image.fromarray(pixels)
                    shots[target] = image.resize((216, round(216 * height / width)))
            if cover is None and at >= cover_at:
                image = image or Image.fromarray(pixels)
                cover = image.resize((360 if travel else 768, 640 if travel else 432))
            if make_preview and at + 1e-5 >= next_gif:
                image = image or Image.fromarray(pixels)
                thumbnails.append(image.resize((display_width, display_height), Image.Resampling.LANCZOS).quantize(colors=128))
                next_gif += 1 / 4
            count += 1
            end = at + 1 / fps
    assert count == round(expected * fps) and abs(end - expected) < .05, (path, count, end)
    peak, clipped, samples = 0., 0, 0
    with av.open(str(path)) as container:
        assert container.streams.audio, path
        for frame in container.decode(audio=0):
            raw = frame.to_ndarray()
            values = raw.astype(float)
            if np.issubdtype(raw.dtype, np.integer):
                values /= max(abs(np.iinfo(raw.dtype).min), np.iinfo(raw.dtype).max)
            peak = max(peak, float(np.abs(values).max()))
            clipped += int((np.abs(values) >= .999).sum())
            samples += values.size
    th = round(216 * height / width)
    sheet = Image.new("RGB", (4 * 216, math.ceil(len(shots) / 4) * (th + 24)), "#203841")
    draw = ImageDraw.Draw(sheet)
    for i, (at, image) in enumerate(shots.items()):
        x, y = i % 4 * 216, i // 4 * (th + 24)
        sheet.paste(image, (x, y + 24))
        draw.text((x + 5, y + 5), f"{at:.2f}s", fill="white")
    sheet.save(output / (path.stem + "-contact.jpg"), quality=88)
    if make_preview:
        previews = path.parent.parent / "previews"
        previews.mkdir(exist_ok=True)
        durations = [250
                     for i in range(len(thumbnails))]
        durations[-1] = max(10, round(expected * 100) * 10 - sum(durations[:-1]))
        thumbnails[0].save(previews / (path.stem + ".gif"), save_all=True,
            append_images=thumbnails[1:], duration=durations, loop=0, optimize=True)
        cover.save(previews / (path.stem + ".jpg"), quality=90)
    report = dict(path=path.relative_to(SHOWCASES).as_posix(), width=width, height=height,
                  fps=fps, duration=round(end, 6), frames=count, hasAudio=True,
                  samplePeakDbFS=round(20 * math.log10(max(peak, 1e-12)), 3),
                  clippedSamples=clipped, decodedAudioSamples=samples, blackFrameTimes=black,
                  sha256=digest(path), bytes=path.stat().st_size,
                  scope="Full decode, sample peaks and sampled visual frames; not a listening or true-peak claim.")
    (output / (path.stem + ".json")).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", nargs="+", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.preview:
        for path in args.preview:
            inspect_media(path, True)
    if args.check:
        for slug in ("chase-the-light", "travel-vlog"):
            base = SHOWCASES / slug
            packages = [package(p) for p in sorted((base / "projects").glob("*.cutvokepack"))]
            assert len(packages) == (4 if slug == "chase-the-light" else 2)
            localization_invariants(base, "motorcycle-edit" if slug == "chase-the-light" else "travel-vlog")
            assert len(list((base / "videos").glob("*.mp4"))) == 4
            assets = json.loads((base / "source-media/index.json").read_text(encoding="utf-8"))["files"]
            for item in assets:
                assert digest(base / "source-media" / item["path"]) == item["sha256"]
            for lang in ("en", "zh-CN"):
                for kind in ("case", "film"):
                    name = ("motorcycle-" if slug == "chase-the-light" else "travel-vlog-") + kind + "_" + lang
                    path = base / "videos" / (name + ".mp4")
                    report = base / "verification" / (name + ".json")
                    if not report.is_file() or json.loads(report.read_text())["sha256"] != digest(path):
                        inspect_media(path)
            manifest = dict(schemaVersion=2, editor="CutVoke", packages=packages, sourceAssets=len(assets),
                files=[dict(path=p.relative_to(base).as_posix(), bytes=p.stat().st_size, sha256=digest(p))
                       for folder in ("projects", "videos", "source-media", "previews")
                       for p in sorted((base / folder).glob("*")) if p.is_file()])
            (base / "bilingual-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"{slug}: {len(packages)} portable projects, {len(assets)} source assets verified")


if __name__ == "__main__":
    main()
