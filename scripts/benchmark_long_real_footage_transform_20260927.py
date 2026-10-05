"""Benchmark a long, real-footage timeline with animated transform keyframes.

The 96-second timeline is assembled from repeated, openly licensed real footage.
It measures the local render engine only; it is not a browser/device UX benchmark.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from cutvoke.core.keyframes import Keyframe
from cutvoke.core.model import AssetReference, Clip, Project, Sequence, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/acceptance/jy-r06-long-real-footage-transform-20260927"
VISUAL_QA = OUTPUT / "visual-qa.json"
BASE_FOOTAGE = ROOT / "output/acceptance/jy-camera-commons-pack-20260926/web-workflow/cutvoke-six-clip-sample.mp4"
OVERLAY_FOOTAGE = ROOT / "output/acceptance/jy-open-license-samples-20260926/shibuya-crossing-15s-18-33.mp4"
DURATION_PER_CLIP = 12
CLIP_COUNT_PER_TRACK = 8
TIMELINE_DURATION = DURATION_PER_CLIP * CLIP_COUNT_PER_TRACK
PREVIEW_START = TIMELINE_DURATION - 8
CANVAS = (960, 540)
FPS = 24


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def keyframes(param: str, first: float, last: float) -> list[Keyframe]:
    return [
        Keyframe(f"{param}-start", Rational.of(0), first, "ease-in"),
        Keyframe(f"{param}-end", Rational.of(11, 1) + Rational.of(9, 10), last, "ease-out"),
    ]


def make_track(track_id: str, source: Path, overlay: bool) -> Track:
    clips = []
    for index in range(CLIP_COUNT_PER_TRACK):
        start = index * DURATION_PER_CLIP
        if overlay:
            scale_start, scale_end = 0.72, 0.82
            x_start, x_end = 80, -80
            y_start, y_end = -20, 20
            rotation_start, rotation_end = -3, 3
        else:
            scale_start, scale_end = 1.0, 1.04
            x_start, x_end = -8, 8
            y_start, y_end = 0, -6
            rotation_start, rotation_end = 0, 1
        transform = {
            "position": {"x": x_start, "y": y_start},
            "scale": scale_start,
            "rotation": rotation_start,
            "opacity": 1.0,
        }
        clips.append(Clip(
            id=f"{track_id}-clip-{index + 1:02d}",
            asset_ref=AssetReference(
                asset_id=f"{track_id}-asset-{index + 1:02d}",
                source_path=str(source),
                fingerprint=sha256(source),
            ),
            timeline_start=Rational.of(start),
            timeline_end=Rational.of(start + DURATION_PER_CLIP),
            source_start=Rational.of(0),
            effects=[{
                "effectId": "cutvoke.transform",
                "version": "1.0.0",
                "params": transform,
            }],
            keyframes={
                "x": keyframes("x", x_start, x_end),
                "y": keyframes("y", y_start, y_end),
                "scale": keyframes("scale", scale_start, scale_end),
                "rotation": keyframes("rotation", rotation_start, rotation_end),
            },
        ))
    return Track(id=track_id, kind="video", clips=clips)


def extract_frame(source: Path, timestamp: float, output: Path) -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{timestamp:.6f}", "-i", str(source), "-frames:v", "1",
        str(output),
    ], check=True, capture_output=True)


def decode_check(source: Path) -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-v", "error", "-i", str(source),
        "-f", "null", "-",
    ], check=True, capture_output=True)


def run() -> dict:
    for source in (BASE_FOOTAGE, OVERLAY_FOOTAGE):
        if not source.is_file():
            raise FileNotFoundError(source)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    base = make_track("base", BASE_FOOTAGE, overlay=False)
    overlay = make_track("overlay", OVERLAY_FOOTAGE, overlay=True)
    sequence = Sequence(
        id="jy-r06-long-transform-sequence",
        width=CANVAS[0],
        height=CANVAS[1],
        fps=Rational.of(FPS),
        tracks=[base, overlay],
    )
    project = Project(
        schema_version="1.0",
        project_id="jy-r06-long-transform-benchmark",
        revision="benchmark",
        sequence=sequence,
        name="Long real-footage transform keyframe benchmark",
    )
    keyframe_count = sum(
        len(values)
        for track in sequence.tracks
        for clip in track.clips
        for values in clip.keyframes.values()
    )
    report: dict = {
        "schemaVersion": 1,
        "benchmark": "JY-R06 long real-footage transform keyframes",
        "createdAtLocal": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "scope": "Local RenderService/FFmpeg engine only; no browser interaction or cross-device claim.",
        "timeline": {
            "durationSeconds": TIMELINE_DURATION,
            "width": CANVAS[0],
            "height": CANVAS[1],
            "fps": FPS,
            "videoTracks": 2,
            "clips": sum(len(track.clips) for track in sequence.tracks),
            "transformKeyframes": keyframe_count,
            "composition": "Two real-footage sources repeated in 12-second blocks to create a long, repeatable stress timeline.",
        },
        "sources": [
            {"path": str(path.relative_to(ROOT)), "sha256": sha256(path)}
            for path in (BASE_FOOTAGE, OVERLAY_FOOTAGE)
        ],
        "environment": {
            "os": platform.platform(),
            "python": platform.python_version(),
        },
        "stages": [],
    }
    report_path = OUTPUT / "benchmark-report.json"

    def save_report() -> None:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    renderer = RenderService()
    export_path = OUTPUT / "long-keyframe-export.mp4"
    started = time.perf_counter()
    print("Rendering full 96-second two-track export…", flush=True)
    export = renderer.render(project, str(export_path), quality="low", overwrite=True)
    export_elapsed = time.perf_counter() - started
    decode_started = time.perf_counter()
    decode_check(export_path)
    export_decode_elapsed = time.perf_counter() - decode_started
    report["stages"].append({
        "name": "full-export",
        "elapsedSeconds": round(export_elapsed, 3),
        "fullDecodeSeconds": round(export_decode_elapsed, 3),
        "metadata": {key: export[key] for key in ("duration", "width", "height", "has_audio")},
        "output": {"path": str(export_path.relative_to(ROOT)), "sizeBytes": export_path.stat().st_size, "sha256": sha256(export_path)},
    })
    save_report()
    print(f"Full export and decode complete ({export_elapsed:.1f}s + {export_decode_elapsed:.1f}s).", flush=True)

    preview_path = OUTPUT / "late-preview-window.mp4"
    started = time.perf_counter()
    print(f"Rendering final 8-second preview window from {PREVIEW_START}s…", flush=True)
    preview = renderer.render_preview_window(project, str(preview_path), PREVIEW_START, 8)
    preview_elapsed = time.perf_counter() - started
    decode_started = time.perf_counter()
    decode_check(preview_path)
    preview_decode_elapsed = time.perf_counter() - decode_started
    export_frame = OUTPUT / "export-frame-92s.png"
    preview_frame = OUTPUT / "preview-frame-4s.png"
    extract_frame(export_path, PREVIEW_START + 4, export_frame)
    extract_frame(preview_path, 4, preview_frame)
    with Image.open(export_frame) as full, Image.open(preview_frame) as window:
        difference = ImageChops.difference(full.convert("RGB"), window.convert("RGB"))
        mean_rgb_difference = max(ImageStat.Stat(difference).mean)
    preview_stage = {
        "name": "late-preview-window",
        "windowStartSeconds": PREVIEW_START,
        "elapsedSeconds": round(preview_elapsed, 3),
        "fullDecodeSeconds": round(preview_decode_elapsed, 3),
        "metadata": {key: preview[key] for key in ("duration", "windowStart", "has_audio")},
        "output": {"path": str(preview_path.relative_to(ROOT)), "sizeBytes": preview_path.stat().st_size, "sha256": sha256(preview_path)},
        "fullExportFrameComparison": {
            "timelineTimeSeconds": PREVIEW_START + 4,
            "meanAbsoluteRgbDifference": round(mean_rgb_difference, 4),
            "threshold": 8.0,
            "pass": mean_rgb_difference < 8.0,
        },
    }
    report["stages"].append(preview_stage)
    performance_pass = (
        abs(export["duration"] - TIMELINE_DURATION) < 0.12
        and abs(preview["duration"] - 8) < 0.12
        and mean_rgb_difference < 8.0
    )
    visual_review = {"status": "pending", "reason": "visual review record is missing"}
    if VISUAL_QA.is_file():
        visual_review = json.loads(VISUAL_QA.read_text(encoding="utf-8"))
        if visual_review.get("exportSha256") != sha256(export_path):
            visual_review = {
                "status": "pending",
                "reason": "visual review is bound to a different export hash",
            }
    report["performanceChecksPass"] = performance_pass
    report["visualAcceptance"] = visual_review
    report["overallPass"] = (
        performance_pass and visual_review.get("status") == "passed"
    )
    report["limitations"] = [
        "The 96-second duration is synthetic: two 12-second real-footage sources are repeated to exercise late-timeline keyframes.",
        "This run measures one Windows machine and the local RenderService/FFmpeg path; browser scrub responsiveness, continuous playback, other machines, and independent editor review remain open.",
    ]
    save_report()
    print(json.dumps({
        "report": str(report_path.relative_to(ROOT)),
        "overallPass": report["overallPass"],
        "exportSeconds": export_elapsed,
        "latePreviewSeconds": preview_elapsed,
        "previewExportMeanRgbDifference": mean_rgb_difference,
        "visualAcceptance": visual_review.get("status", "pending"),
    }, ensure_ascii=False, indent=2), flush=True)
    if not report["overallPass"]:
        raise SystemExit("long real-footage transform benchmark failed acceptance")
    return report


if __name__ == "__main__":
    run()
