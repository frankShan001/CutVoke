"""Render botanical sticker overlays on a cropped scene from a real screen recording.

The input remains untouched. The script saves a short viewport-derived fixture,
three editable SQLite projects, exports, preview comparisons, and a JSON report.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageStat, ImageDraw

from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore


ASPECTS = (
    ("landscape", 1280, 720),
    ("portrait", 720, 1280),
    ("square", 900, 900),
)
STICKERS = ("botanical_magnolia", "botanical_daisies")
SOURCE_START = 90.0
DURATION = 5
VIEWPORT_CROP = {"width": 1040, "height": 584, "x": 1020, "y": 252}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seconds(value: int) -> dict[str, str]:
    return {"num": str(value), "den": "1"}


def _command(service: EditService, project_id: str, kind: str,
             payload: dict, serial: int) -> None:
    project = service.get_project(project_id)
    service.execute(Command(
        type=kind, payload=payload,
        command_id=f"real-sticker-{project_id}-{serial}",
        project_id=project_id, expected_revision=project.revision,
        actor=Actor("agent", "real-footage-sticker-qa"),
    ))


def _frame(video: Path, time: float, output: Path) -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(time), "-i", str(video), "-frames:v", "1", str(output),
    ], check=True, capture_output=True)


def _changed_pixels(left_path: Path, right_path: Path) -> int:
    with Image.open(left_path) as left, Image.open(right_path) as right:
        difference = ImageChops.difference(left.convert("RGB"), right.convert("RGB"))
        return sum(1 for pixel in difference.get_flattened_data() if max(pixel) > 20)


def _mean_rgb_difference(left_path: Path, right_path: Path) -> float:
    with Image.open(left_path) as left, Image.open(right_path) as right:
        stats = ImageStat.Stat(ImageChops.difference(
            left.convert("RGB"), right.convert("RGB")))
        return sum(stats.mean) / 3


def _decode(path: Path) -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-f", "null", "-",
    ], check=True, capture_output=True)


def _render_aspect(source: Path, output_root: Path, name: str,
                   width: int, height: int, renderer: RenderService) -> dict:
    folder = output_root / name
    folder.mkdir(parents=True, exist_ok=True)
    aspect_source = folder / "footage-source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-vf", (f"scale={width}:{height}:force_original_aspect_ratio=increase:"
                f"flags=lanczos,crop={width}:{height},setsar=1"),
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", str(aspect_source),
    ], check=True, capture_output=True)
    project_id = f"botanical-real-{name}"
    database = folder / "project.sqlite"
    baseline_path = folder / "baseline.mp4"
    export_path = folder / "overlays-export.mp4"
    preview_path = folder / "overlays-preview.mp4"
    baseline_frame = folder / "baseline-frame.png"
    export_frame = folder / "export-frame.png"
    preview_frame = folder / "preview-frame.png"
    project_json = folder / "project.json"

    with ProjectStore(str(database)) as store:
        service = EditService(store)
        service.create_project(project_id, name_hint=f"花草贴纸实拍验收-{name}",
                               width=width, height=height, fps=Rational.of(15))
        _command(service, project_id, "clip.insert", {
            "trackId": "footage", "createTrackKind": "video",
            "clipId": "real-footage", "sourcePath": str(aspect_source.resolve()),
            "timelineStart": _seconds(0), "timelineEnd": _seconds(DURATION),
        }, 1)
        base_project = service.get_project(project_id)
        renderer.render(base_project, str(baseline_path), quality="low")
        _decode(baseline_path)
        renderer.extract_frame(base_project, 1.5, str(baseline_frame))

        placements = (
            (STICKERS[0], 48, 42, 0.38),
            (STICKERS[1], width - min(width, height) * 0.40 - 24,
             height - min(width, height) * 0.40 - 24, 0.40),
        )
        for index, (stem, x, y, scale) in enumerate(placements, start=2):
            track_id = f"sticker-{index - 1}"
            _command(service, project_id, "track.add", {
                "trackId": track_id, "kind": "video", "role": "sticker",
            }, index * 10)
            _command(service, project_id, "clip.insert", {
                "trackId": track_id, "clipId": f"overlay-{index - 1}",
                "role": "sticker", "assetId": f"builtin_sticker_{stem}",
                "sourcePath": str((Path(__file__).resolve().parents[1] /
                                    "src" / "cutvoke" / "assets" /
                                    "stickers" / f"{stem}.png").resolve()),
                "timelineStart": _seconds(0), "timelineEnd": _seconds(DURATION),
            }, index * 10 + 1)
            _command(service, project_id, "effect.update", {
                "clipId": f"overlay-{index - 1}",
                "effectId": "cutvoke.transform",
                "params": {"scale": scale, "position": {"x": int(x), "y": int(y)}},
            }, index * 10 + 2)

        project = service.get_project(project_id)
        sticker_tracks = [track for track in project.sequence.tracks
                          if track.role == "sticker"]
        if len(sticker_tracks) != 2 or any(len(track.clips) != 1 for track in sticker_tracks):
            raise RuntimeError(f"{name}: expected two independent sticker tracks")
        project_json.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
        renderer.render(project, str(export_path), quality="low")
        renderer.render_preview_window(project, str(preview_path), 0, 4.0)

    _frame(export_path, 1.5, export_frame)
    _frame(preview_path, 1.5, preview_frame)
    visible_pixels = _changed_pixels(baseline_frame, export_frame)
    preview_export_difference = _mean_rgb_difference(export_frame, preview_frame)
    if visible_pixels < 1000:
        raise RuntimeError(f"{name}: stickers changed only {visible_pixels} pixels")
    if preview_export_difference >= 5:
        raise RuntimeError(f"{name}: preview/export mean RGB difference {preview_export_difference}")
    _decode(export_path)
    _decode(preview_path)
    probe = renderer.probe_media(str(export_path))
    if (probe.get("width"), probe.get("height")) != (width, height):
        raise RuntimeError(f"{name}: unexpected export dimensions {probe}")
    return {
        "name": name, "width": width, "height": height,
        "projectId": project_id, "projectDatabase": str(database),
        "projectJson": str(project_json),
        "aspectPreparedFootage": str(aspect_source),
        "aspectPreparedFootageSha256": _sha(aspect_source),
        "stickerTracks": [track.id for track in sticker_tracks],
        "stickerIds": [f"cutvoke.sticker.{stem}" for stem in STICKERS],
        "baseline": str(baseline_path), "export": str(export_path),
        "preview": str(preview_path), "frame": str(export_frame),
        "changedPixelsAgainstFootage": visible_pixels,
        "previewExportMeanRgbDifference": round(preview_export_difference, 4),
        "fullDecode": "passed",
    }


def main(source_path: str, output_path: str) -> None:
    source = Path(source_path).resolve()
    output_root = Path(output_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    renderer = RenderService()
    source_info = renderer.probe_media(str(source))
    source_duration = float(source_info.get("duration") or 0)
    if source_duration < SOURCE_START + DURATION:
        raise ValueError(f"source is too short for the requested sample: {source_duration}s")
    output_root.mkdir(parents=True, exist_ok=True)
    viewport = output_root / "botanical-scene-derived-from-recording.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(SOURCE_START), "-i", str(source), "-t", str(DURATION),
        "-vf", (f"crop={VIEWPORT_CROP['width']}:{VIEWPORT_CROP['height']}:"
                f"{VIEWPORT_CROP['x']}:{VIEWPORT_CROP['y']},scale=1280:720"),
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", str(viewport),
    ], check=True, capture_output=True)

    results = [_render_aspect(viewport, output_root, name, width, height, renderer)
               for name, width, height in ASPECTS]
    review = Image.new("RGB", (3 * 520, 340), (28, 31, 38))
    draw = ImageDraw.Draw(review)
    for index, result in enumerate(results):
        with Image.open(result["frame"]) as frame:
            frame = frame.convert("RGB")
            frame.thumbnail((500, 300))
            x = index * 520 + (500 - frame.width) // 2 + 10
            y = 28 + (300 - frame.height) // 2
            review.paste(frame, (x, y))
        draw.text((index * 520 + 12, 8),
                  f"{result['name']} {result['width']}x{result['height']}",
                  fill=(245, 245, 245))
    review_path = output_root / "botanical-real-footage-aspects-review.png"
    review.save(review_path)
    report = {
        "schemaVersion": 1,
        "sourceVideo": str(source), "sourceVideoSha256": _sha(source),
        "sourceDurationSeconds": round(source_duration, 4),
        "sourceViewportTimeSeconds": SOURCE_START,
        "viewportCrop": VIEWPORT_CROP,
        "derivedScene": str(viewport), "derivedSceneSha256": _sha(viewport),
        "note": "Scene was cropped from the user's Jianying screen recording; editor preview controls remain part of the captured source.",
        "stickerIds": [f"cutvoke.sticker.{stem}" for stem in STICKERS],
        "aspects": results, "visualReview": str(review_path),
    }
    report_path = output_root / "acceptance-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({
        "aspects": len(results), "independentStickerTracks": 2,
        "minChangedPixels": min(item["changedPixelsAgainstFootage"] for item in results),
        "maxPreviewExportMeanRgbDifference": max(
            item["previewExportMeanRgbDifference"] for item in results),
        "fullDecode": "passed", "report": str(report_path),
        "visualReview": str(review_path),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output-dir", default="output/real-footage-sticker-aspects-final-20260924")
    args = parser.parse_args()
    main(args.source, args.output_dir)
