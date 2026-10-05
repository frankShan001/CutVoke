"""Verify media and handoff artifacts produced by the JY-R19 browser recording."""

from __future__ import annotations

import argparse
import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "output" / "acceptance" / "jy-r19-20260925"


def probe(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate",
            "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def mean_rgb_difference(left: Path, right: Path) -> float:
    with Image.open(left) as first, Image.open(right) as second:
        a = first.convert("RGB")
        b = second.convert("RGB")
        if a.size != b.size:
            raise AssertionError(f"preview/export size mismatch: {a.size} != {b.size}")
        pixels_a = a.tobytes()
        pixels_b = b.tobytes()
        return sum(abs(x - y) for x, y in zip(pixels_a, pixels_b)) / len(pixels_a)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    movie = output_dir / "cutvoke-six-clip-sample.mp4"
    cover = output_dir / "cutvoke-six-clip-cover.png"
    package = output_dir / "cutvoke-six-clip-sample.cutvokepack.zip"
    project_path = output_dir / "six-clip-project.json"
    report_path = output_dir / "acceptance-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    project = json.loads(project_path.read_text(encoding="utf-8"))

    movie_probe = probe(movie)
    video = next(stream for stream in movie_probe["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in movie_probe["streams"] if stream["codec_type"] == "audio")
    duration = float(movie_probe["format"]["duration"])
    assert video["codec_name"] == "h264"
    assert audio["codec_name"] == "aac"
    assert (video["width"], video["height"], video["r_frame_rate"]) == (640, 360, "15/1")
    assert abs(duration - 12.0) < 0.02
    cover_probe = probe(cover)
    cover_stream = cover_probe["streams"][0]
    assert cover_stream["codec_name"] == "png"
    assert (cover_stream["width"], cover_stream["height"]) == (640, 360)

    subprocess.run(["ffmpeg", "-v", "error", "-i", str(movie), "-f", "null", "-"], check=True)

    tracks = project["sequence"]["tracks"]
    video_track = next(track for track in tracks if track["id"] == "v1")
    assert len(video_track["clips"]) == 6
    assert sum(track["kind"] == "audio" and len(track["clips"]) == 1 for track in tracks) == 1
    assert any(track["kind"] == "text" and len(track["clips"]) == 1 for track in tracks)
    assert any(track.get("role") == "sticker" and len(track["clips"]) == 1 for track in tracks)
    assert len(project["sequence"]["captions"]) == 1
    assert any(effect["effectId"].startswith("cutvoke.fx.") for effect in video_track["clips"][0]["effects"])
    assert any(effect["effectId"].startswith("cutvoke.anim.") for effect in video_track["clips"][1]["effects"])
    assert any(effect["effectId"].startswith("cutvoke.transition.") for effect in video_track["clips"][1]["effects"])

    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        assert "manifest.json" in names and "project.json" in names
        manifest = json.loads(archive.read("manifest.json"))
        packaged_project = json.loads(archive.read("project.json"))
        assert packaged_project["projectId"] == project["projectId"]
        assert len(manifest["assets"]) == 5
        assert sum(name.startswith("media/") for name in names) == 5

    frame_differences: dict[str, float] = {}
    contact = Image.new("RGB", (640 * 3, 392), "#111827")
    draw = ImageDraw.Draw(contact)
    font = ImageFont.load_default()
    for index, time in enumerate((1.75, 2.25, 3.25)):
        preview = output_dir / f"preview-{time:.2f}s.png"
        exported = output_dir / f"export-frame-{time:.2f}s.png"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(movie), "-ss", str(time),
             "-frames:v", "1", "-y", str(exported)],
            check=True,
        )
        with Image.open(preview) as image:
            contact.paste(image.convert("RGB"), (index * 640, 32))
        draw.text((index * 640 + 8, 10), f"Preview at {time:.2f}s", fill="white", font=font)
        if time in (2.25, 3.25):
            frame_differences[f"{time:.2f}"] = round(mean_rgb_difference(preview, exported), 4)
    contact.save(output_dir / "preview-contact-sheet.png")

    result = {
        "movie": {
            "durationSeconds": duration,
            "width": video["width"],
            "height": video["height"],
            "fps": video["r_frame_rate"],
            "videoCodec": video["codec_name"],
            "audioCodec": audio["codec_name"],
            "fullDecode": "passed",
        },
        "cover": {"width": cover_stream["width"], "height": cover_stream["height"], "codec": cover_stream["codec_name"]},
        "package": {"format": manifest["packageFormat"], "assets": len(manifest["assets"]), "mediaFiles": 5},
        "previewExportMeanAbsoluteRgbDifference": frame_differences,
        "contactSheet": str(output_dir / "preview-contact-sheet.png"),
        "assessment": "Machine-verified synthetic browser sample; human review on complex live footage remains open.",
    }
    (output_dir / "media-verification.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report["mediaVerification"] = result
    report["outputs"]["contactSheet"] = result["contactSheet"]
    report["outputs"]["mediaVerification"] = str(output_dir / "media-verification.json")
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
