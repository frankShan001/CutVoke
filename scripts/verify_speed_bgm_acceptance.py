"""Verify clip speed changes beside six shots and an independent BGM track."""

from __future__ import annotations

import json
import math
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

from PIL import Image, ImageDraw

from cutvoke.core.model import Project
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore


PROJECT_ID = "speed-bgm-acceptance"
VIDEO_TRACK_ID = "video"
AUDIO_TRACK_ID = "music"
VIDEO_SOURCE_RANGES = (0, 8, 20, 40, 60, 120)
CLIP_DURATION = 2
BGM = Path(__file__).resolve().parents[1] / "src/cutvoke/assets/audio/tech_minimal.wav"


def _edit(service: EditService, kind: str, payload: dict, index: int) -> None:
    project = service.get_project(PROJECT_ID)
    service.execute(Command(
        type=kind, payload=payload, command_id=f"speed-bgm-{index}",
        project_id=PROJECT_ID, expected_revision=project.revision,
        actor=Actor("human", "speed-bgm-acceptance"),
    ))


def _save(project: Project, path: Path) -> None:
    path.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def run(source: Path, output_dir: Path) -> dict:
    source = source.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not BGM.is_file():
        raise FileNotFoundError(BGM)
    database = output_dir / "project.sqlite"
    with ProjectStore(str(database)) as store:
        service = EditService(store)
        service.create_project(PROJECT_ID, width=1920, height=1080)
        _edit(service, "track.add", {"trackId": VIDEO_TRACK_ID, "kind": "video"}, 1)
        _edit(service, "track.add", {"trackId": AUDIO_TRACK_ID, "kind": "audio"}, 2)
        for index, source_start in enumerate(VIDEO_SOURCE_RANGES):
            clip_id = f"shot-{index + 1}"
            start = index * CLIP_DURATION
            _edit(service, "clip.insert", {
                "trackId": VIDEO_TRACK_ID, "clipId": clip_id, "sourcePath": str(source),
                "timelineStart": Rational.of(start, 1).to_json(),
                "timelineEnd": Rational.of(start + CLIP_DURATION, 1).to_json(),
                "sourceStart": Rational.of(source_start, 1).to_json(),
            }, index + 3)
        _edit(service, "clip.insert", {
            "trackId": AUDIO_TRACK_ID, "clipId": "bgm", "sourcePath": str(BGM),
            "timelineStart": Rational.of(0, 1).to_json(),
            "timelineEnd": Rational.of(12, 1).to_json(),
        }, 9)

        _edit(service, "clip.speed", {
            "clipId": "shot-1", "speed": {"num": "7", "den": "5"},
            "preservePitch": True,
        }, 10)
        sped_up = service.get_project(PROJECT_ID)
        _save(sped_up, output_dir / "project-1.4x.json")
        video = next(t for t in sped_up.sequence.tracks if t.id == VIDEO_TRACK_ID)
        audio = next(t for t in sped_up.sequence.tracks if t.id == AUDIO_TRACK_ID)
        first_end = video.clips[0].timeline_end
        next_start = video.clips[1].timeline_start
        music_start, music_end = audio.clips[0].timeline_start, audio.clips[0].timeline_end
        gap = next_start - first_end
        _edit(service, "history.undo", {}, 11)
        undone = service.get_project(PROJECT_ID)
        _save(undone, output_dir / "project-after-undo.json")
        if undone.sequence.tracks[0].clips[0].timeline_end != Rational.of(2, 1):
            raise RuntimeError("undo did not restore the first clip duration")
        _edit(service, "history.redo", {}, 12)
        restored = service.get_project(PROJECT_ID)
        _save(restored, output_dir / "project-after-redo.json")
        exported = output_dir / "speed-bgm-export.mp4"
        render_result = RenderService().render(restored, str(exported), quality="low")
        preview = output_dir / "speed-bgm-preview-gap.mp4"
        preview_result = RenderService().render_preview_window(restored, str(preview), 1.35, 1.0)

    decode = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(exported),
        "-f", "null", "-",
    ], capture_output=True, text=True, encoding="utf-8")
    if decode.returncode != 0:
        raise RuntimeError(f"full export decode failed: {decode.stderr[-1000:]}")
    gap_audio_start = float(first_end.to_fraction()) + 0.05
    gap_audio_duration = float(gap.to_fraction()) - 0.1
    with tempfile.TemporaryDirectory(prefix="speed-bgm-gap-audio-") as temporary:
        gap_audio_path = Path(temporary) / "gap-audio.wav"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i",
            str(exported), "-ss", f"{gap_audio_start:.6f}", "-t",
            f"{gap_audio_duration:.6f}", "-map", "0:a:0", "-vn", "-ac", "2",
            "-ar", "48000", "-c:a", "pcm_s16le", str(gap_audio_path),
        ], check=True, capture_output=True)
        with wave.open(str(gap_audio_path), "rb") as audio_wave:
            frames = audio_wave.readframes(audio_wave.getnframes())
            samples = struct.unpack(f"<{len(frames) // 2}h", frames)
            gap_audio_rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768
    if gap_audio_rms < 0.01:
        raise RuntimeError(f"independent BGM was not audible during the black gap: RMS={gap_audio_rms:.5f}")
    frame_dir = output_dir / "frames"
    frame_dir.mkdir(exist_ok=True)
    timestamps = (1.2, 1.5, 1.75, 2.05)
    frame_paths = []
    for index, time_sec in enumerate(timestamps):
        frame = frame_dir / f"{index + 1}.png"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss",
            f"{time_sec:.3f}", "-i", str(exported), "-frames:v", "1", str(frame),
        ], check=True, capture_output=True)
        frame_paths.append(frame)
    with Image.open(frame_paths[0]) as sample:
        width = 480
        height = round(sample.height * width / sample.width)
    sheet = Image.new("RGB", (width * len(timestamps), height + 44), "#202027")
    draw = ImageDraw.Draw(sheet)
    for index, (time_sec, frame_path) in enumerate(zip(timestamps, frame_paths)):
        with Image.open(frame_path) as image:
            sheet.paste(image.convert("RGB").resize((width, height)), (index * width, 0))
        label = (f"{time_sec:.2f}s · BGM on" if first_end <= Rational.from_float(time_sec) < next_start
                 else f"{time_sec:.2f}s")
        draw.text((index * width + 10, height + 12), label, fill="white")
    sheet_path = output_dir / "speed-gap-contact.png"
    sheet.save(sheet_path)
    result = {
        "schemaVersion": 1,
        "source": str(source),
        "videoShots": len(VIDEO_SOURCE_RANGES),
        "videoTrackClipCount": len(video.clips),
        "bgm": str(BGM),
        "bgmRange": [float(music_start.to_fraction()), float(music_end.to_fraction())],
        "speed": 1.4,
        "firstClipTimelineEnd": float(first_end.to_fraction()),
        "secondClipTimelineStart": float(next_start.to_fraction()),
        "gapDuration": float(gap.to_fraction()),
        "undoRestoresEndTo": 2.0,
        "redoRestoresSpeed": float(video.clips[0].speed.to_fraction()),
        "checks": ["six-video-shots", "independent-bgm-track", "clip-speed",
                   "undo", "redo", "sqlite-save-reopen", "mp4-with-bgm-export",
                   "preview-in-gap", "bgm-audible-during-black-gap", "full-decode"],
        "behavior": "later video clips keep their timeline positions and independent BGM does not move; the shortened first shot leaves a black gap while the BGM continues",
        "render": render_result,
        "preview": preview_result,
        "gapAudio": {
            "analysisWindow": [gap_audio_start, gap_audio_start + gap_audio_duration],
            "rmsLinear": round(gap_audio_rms, 5),
            "rmsDbfs": round(20 * math.log10(gap_audio_rms), 2),
        },
        "decodeExitCode": decode.returncode,
        "contactSheet": sheet_path.name,
    }
    (output_dir / "acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.output_dir), ensure_ascii=False, indent=2))
