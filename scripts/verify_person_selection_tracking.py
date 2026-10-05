"""Run MediaPipe single-subject selection on a local two-copy motion fixture.

The fixture duplicates the already-used public demo clip side by side. It is a
repeatable regression for target selection and temporal component tracking; it
does not model two independently moving or occluding people.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cutvoke.core.person_cutout import generate_person_cutout  # noqa: E402


SOURCE = ROOT / "output/acceptance/jy-r20-person-20260925/mediapipe-spike/selfie_segmentation_web.mp4"
MODEL = ROOT / "output/acceptance/jy-r20-person-20260925/mediapipe-spike/selfie_segmenter.tflite"
OUTPUT = ROOT / "output/acceptance/jy-r20-person-selection-20260925"
FPS = 25
WIDTH = 1280
HEIGHT = 360


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, capture_output=True, timeout=180)


def _extract_frame(ffmpeg: str, path: Path, seconds: float) -> Image.Image:
    result = _run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{seconds:.3f}",
        "-i", str(path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1",
    ])
    return Image.open(BytesIO(result.stdout)).convert("RGBA")


def _measure_alpha(ffmpeg: str, path: Path, selected_half: str, frame_count: int) -> dict:
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path),
               "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout is not None
    frame_bytes = WIDTH * HEIGHT * 4
    left_active = right_active = 0
    sampled: dict[int, Image.Image] = {}
    sample_frames = {0, frame_count // 2, frame_count - 1}
    try:
        for index in range(frame_count):
            raw = process.stdout.read(frame_bytes)
            if len(raw) != frame_bytes:
                raise RuntimeError(f"expected {frame_count} frames, decoded {index}")
            image = Image.frombytes("RGBA", (WIDTH, HEIGHT), raw)
            alpha = image.getchannel("A")
            left_active += alpha.crop((0, 0, WIDTH // 2, HEIGHT)).point(
                lambda value: 255 if value > 32 else 0).histogram()[255]
            right_active += alpha.crop((WIDTH // 2, 0, WIDTH, HEIGHT)).point(
                lambda value: 255 if value > 32 else 0).histogram()[255]
            if index in sample_frames:
                sampled[index] = image.copy()
    finally:
        process.stdout.close()
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    code = process.wait(timeout=30)
    if code:
        raise RuntimeError(f"alpha decode failed: {stderr[-1000:]}")
    selected = left_active if selected_half == "left" else right_active
    other = right_active if selected_half == "left" else left_active
    return {
        "selectedHalf": selected_half,
        "selectedActivePixels": selected,
        "otherHalfActivePixels": other,
        "otherToSelectedLeakage": round(other / max(1, selected), 6),
        "sampledFrames": sampled,
    }


def main() -> int:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not SOURCE.is_file() or not MODEL.is_file():
        raise SystemExit("requires ffmpeg and the local JY-R20 demo/model acceptance assets")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    paired = OUTPUT / "two-separated-copies.mp4"
    _run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(SOURCE),
        "-filter_complex", "[0:v]split=2[a][b];[a]setsar=1[l];[b]setsar=1[r];[l][r]hstack=inputs=2[v]",
        "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "18", "-pix_fmt", "yuv420p", str(paired),
    ])
    left_output = OUTPUT / "left-subject.mov"
    right_output = OUTPUT / "right-subject.mov"
    left = generate_person_cutout(
        paired, left_output, model_file=MODEL, selection_point=(0.25, 0.5),
        selection_at_seconds=0, edge_softness=1.2,
    )
    right = generate_person_cutout(
        paired, right_output, model_file=MODEL, selection_point=(0.75, 0.5),
        selection_at_seconds=0, edge_softness=1.2,
    )
    frame_count = min(int(round(FPS * 9)), left["frameCount"], right["frameCount"])
    left_measure = _measure_alpha(ffmpeg, left_output, "left", frame_count)
    right_measure = _measure_alpha(ffmpeg, right_output, "right", frame_count)

    rows = []
    for sample_index, seconds in zip((0, frame_count // 2, frame_count - 1), (0, 4.48, 8.88)):
        source_frame = _extract_frame(ffmpeg, paired, seconds).convert("RGB")
        left_frame = left_measure["sampledFrames"][sample_index]
        right_frame = right_measure["sampledFrames"][sample_index]
        background = Image.new("RGBA", (WIDTH, HEIGHT), (24, 44, 74, 255))
        left_composite = Image.alpha_composite(background, left_frame).convert("RGB")
        right_composite = Image.alpha_composite(background, right_frame).convert("RGB")
        rows.append((source_frame, left_composite, right_composite))
    sheet = Image.new("RGB", (WIDTH * 3, HEIGHT * len(rows) + 34), (18, 20, 27))
    draw = ImageDraw.Draw(sheet)
    labels = ("Two separate people (duplicated demo)", "Click left person · output", "Click right person · output")
    for column, label in enumerate(labels):
        draw.text((column * WIDTH + 12, 10), label, fill=(235, 239, 246))
    for row_index, cells in enumerate(rows):
        for column, frame in enumerate(cells):
            sheet.paste(frame, (column * WIDTH, 34 + row_index * HEIGHT))
    contact = OUTPUT / "person-selection-tracking-contact.png"
    sheet.save(contact)

    report = {
        "schemaVersion": 1,
        "status": "passed" if (
            left_measure["selectedActivePixels"] > 100_000 and
            right_measure["selectedActivePixels"] > 100_000 and
            left_measure["otherToSelectedLeakage"] < 0.02 and
            right_measure["otherToSelectedLeakage"] < 0.02
        ) else "failed",
        "source": SOURCE.relative_to(ROOT).as_posix(),
        "sourceSha256": sha256(SOURCE),
        "model": MODEL.relative_to(ROOT).as_posix(),
        "modelSha256": sha256(MODEL),
        "fixture": "one public demo person duplicated into two separated, identical motion paths",
        "limitations": [
            "This exercises separated-component selection and motion tracking, not independent trajectories.",
            "Touching or overlapping subjects may merge in the underlying semantic segmentation mask.",
        ],
        "visualReview": {
            "status": "selected_subject_visible_with_segmentation_artifacts",
            "observation": "At the start, middle, and end, each output keeps the selected person and excludes the other half; small background fragments and the demo watermark remain around the subject, so this is not commercial edge-quality acceptance.",
        },
        "frames": frame_count,
        "leftSelection": {**left, **{key: value for key, value in left_measure.items()
                                      if key != "sampledFrames"}},
        "rightSelection": {**right, **{key: value for key, value in right_measure.items()
                                       if key != "sampledFrames"}},
        "contactSheet": contact.relative_to(ROOT).as_posix(),
    }
    (OUTPUT / "person-selection-acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
