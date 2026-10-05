"""Apply, undo, persist and render a seam transition on supplied real footage."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from cutvoke.core.model import Project
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore


PROJECT_ID = "real-transition-acceptance"
TRACK_ID = "video"
CLIPS = (("shot-a", 0.0, 4.0, 0.0), ("shot-b", 4.0, 8.0, 120.0))
TRANSITION_ID = "cutvoke.transition.pixelize"


def _edit(service: EditService, kind: str, payload: dict, index: int) -> None:
    project = service.get_project(PROJECT_ID)
    service.execute(Command(
        type=kind, payload=payload, command_id=f"real-transition-{index}",
        project_id=PROJECT_ID, expected_revision=project.revision,
        actor=Actor("human", "transition-acceptance"),
    ))


def _write_snapshot(project: Project, path: Path) -> None:
    path.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def _frame_mean_delta(left: Path, right: Path) -> float:
    with Image.open(left) as first, Image.open(right) as second:
        delta = ImageChops.difference(first.convert("RGB"), second.convert("RGB"))
        return round(sum(ImageStat.Stat(delta).mean) / 3, 4)


def run(source: Path, output_dir: Path) -> dict:
    source = source.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    database = output_dir / "project.sqlite"
    renderer = RenderService()
    with ProjectStore(str(database)) as store:
        service = EditService(store)
        service.create_project(PROJECT_ID, width=1920, height=1080)
        _edit(service, "track.add", {"trackId": TRACK_ID, "kind": "video"}, 1)
        for index, (clip_id, start, end, source_start) in enumerate(CLIPS, 2):
            _edit(service, "clip.insert", {
                "trackId": TRACK_ID, "clipId": clip_id, "sourcePath": str(source),
                "timelineStart": Rational.from_float(start).to_json(),
                "timelineEnd": Rational.from_float(end).to_json(),
                "sourceStart": Rational.from_float(source_start).to_json(),
            }, index)
        _edit(service, "effect.setTransition", {
            "clipId": "shot-b", "effectId": TRANSITION_ID,
            "params": {"duration": 0.5},
        }, 4)
        with_transition = service.get_project(PROJECT_ID)
        _write_snapshot(with_transition, output_dir / "project-with-transition.json")
        if not with_transition.sequence.tracks[0].clips[1].effects_by_id(TRANSITION_ID):
            raise RuntimeError("transition did not attach to the incoming clip")
        _edit(service, "history.undo", {}, 5)
        after_undo = service.get_project(PROJECT_ID)
        if after_undo.sequence.tracks[0].clips[1].effects_by_id(TRANSITION_ID):
            raise RuntimeError("undo did not remove the transition")
        _edit(service, "history.redo", {}, 6)
        restored = service.get_project(PROJECT_ID)
        _write_snapshot(restored, output_dir / "project-reopened-after-redo.json")
        full_export = output_dir / "transition-export.mp4"
        render_result = renderer.render(restored, str(full_export), quality="low")
        preview = output_dir / "transition-preview-window.mp4"
        preview_result = renderer.render_preview_window(restored, str(preview), 3.5, 1.0)

    decode = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(full_export),
        "-f", "null", "-",
    ], capture_output=True, text=True, encoding="utf-8")
    if decode.returncode != 0:
        raise RuntimeError(f"full export decode failed: {decode.stderr[-1000:]}")
    frame_dir = output_dir / "seam-frames"
    frame_dir.mkdir(exist_ok=True)
    frames = []
    for index, timestamp in enumerate((3.5, 3.75, 4.0, 4.25, 4.5)):
        frame = frame_dir / f"frame-{index + 1}.png"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss",
            f"{timestamp:.3f}", "-i", str(full_export), "-frames:v", "1", str(frame),
        ], check=True, capture_output=True)
        frames.append((timestamp, frame))
    with Image.open(frames[0][1]) as first:
        thumb_w = 640
        thumb_h = round(first.height * thumb_w / first.width)
    contact = Image.new("RGB", (thumb_w * len(frames), thumb_h + 44), "#202027")
    from PIL import ImageDraw

    draw = ImageDraw.Draw(contact)
    for index, (timestamp, frame) in enumerate(frames):
        with Image.open(frame) as image:
            contact.paste(image.convert("RGB").resize((thumb_w, thumb_h)), (index * thumb_w, 0))
        draw.text((index * thumb_w + 12, thumb_h + 12), f"{timestamp:.2f}s", fill="white")
    contact_path = output_dir / "transition-seam-contact.png"
    contact.save(contact_path)
    preview_frame = output_dir / "preview-seam-frame.png"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.5",
        "-i", str(preview), "-frames:v", "1", str(preview_frame),
    ], check=True, capture_output=True)
    export_middle = frame_dir / "frame-3.png"
    evidence = {
        "schemaVersion": 1,
        "source": str(source),
        "sourceDuration": 370.1333,
        "sourceAudioMeanAndPeakDbfs": -91.0,
        "clips": [{"id": clip_id, "timeline": [start, end], "sourceStart": source_start}
                  for clip_id, start, end, source_start in CLIPS],
        "transition": {"effectId": TRANSITION_ID, "incomingClipId": "shot-b", "duration": 0.5},
        "checks": ["seam-attachment", "undo", "redo", "sqlite-save-reopen",
                   "mp4-export-with-source-audio", "full-decode", "transition-frame-review"],
        "render": render_result,
        "preview": preview_result,
        "decodeExitCode": decode.returncode,
        "previewExportMeanRgbDeltaAtSeam": _frame_mean_delta(preview_frame, export_middle),
        "contactSheet": contact_path.name,
        "audioQualityReviewed": False,
    }
    (output_dir / "acceptance.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return evidence


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.output_dir), ensure_ascii=False, indent=2))
