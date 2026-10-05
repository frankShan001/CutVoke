"""Verify embedded audio detachment through real media, edits, preview and export."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "output/acceptance/jy-camera-commons-pack-20260926"
SOURCE_MANIFEST = SOURCE_ROOT / "source-manifest.json"
SOURCE_FILENAME = "steam-train-at-station.webm"
OUT = ROOT / "output/acceptance/jy-r15-real-footage-audio-detachment-20260927-v3"
PROJECT_ID = "jy-r15-real-footage-audio-detachment"
CLIP_ID = "street-scene"
WIDTH, HEIGHT, FPS = 640, 360, 24
DURATION = 4
SAMPLE_RATE = 48_000
MAX_RELATIVE_RMS_DIFFERENCE = 0.03


def rat(numerator: int, denominator: int = 1) -> dict[str, str]:
    return {"num": str(numerator), "den": str(denominator)}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clip_by_id(project, clip_id: str):
    return next(clip for track in project.sequence.tracks for clip in track.clips
                if clip.id == clip_id)


def apply(service: EditService, name: str, payload: dict, serial: int) -> dict:
    current = service.get_project(PROJECT_ID)
    result = service.execute(Command(
        type=name,
        payload=payload,
        command_id=f"jy-r15-audio-{serial:02d}-{name.replace('.', '-')}",
        project_id=PROJECT_ID,
        expected_revision=current.revision,
        actor=Actor("human", "jy-r15-real-footage-audio-acceptance"),
    ))
    return {
        "command": name,
        "revision": service.get_project(PROJECT_ID).revision,
        "changedEntities": result.changed_entities,
    }


def full_decode(ffmpeg: str, media: Path) -> None:
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-v", "error", "-i", str(media), "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode:
        raise RuntimeError(f"full decode failed for {media.name}: {result.stderr[-800:]}")


def decode_audio(ffmpeg: str, media: Path) -> np.ndarray:
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(media),
         "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
         "-f", "f32le", "-"],
        capture_output=True, timeout=60,
    )
    if result.returncode:
        raise RuntimeError(f"audio decode failed for {media.name}: {result.stderr[-800:]}")
    samples = np.frombuffer(result.stdout, dtype="<f4").copy()
    if len(samples) < SAMPLE_RATE * (DURATION - 1):
        raise AssertionError(f"audio is missing or too short in {media.name}: {len(samples)} samples")
    return samples


def rms(samples: np.ndarray) -> float:
    margin = SAMPLE_RATE // 10
    usable = samples[margin:len(samples) - margin]
    return float(math.sqrt(float(np.mean(usable * usable))))


def compare_rms(reference: np.ndarray, candidate: np.ndarray, label: str) -> dict:
    reference_rms = rms(reference)
    candidate_rms = rms(candidate)
    if reference_rms <= 1e-5:
        raise AssertionError(f"reference audio is unexpectedly silent for {label}: {reference_rms}")
    relative = abs(candidate_rms / reference_rms - 1.0)
    if relative > MAX_RELATIVE_RMS_DIFFERENCE:
        raise AssertionError(
            f"audio level changed by {relative:.4%} for {label}; "
            f"before={reference_rms:.6f}, after={candidate_rms:.6f}"
        )
    return {
        "comparison": label,
        "referenceRms": round(reference_rms, 7),
        "candidateRms": round(candidate_rms, 7),
        "relativeRmsDifference": round(relative, 7),
        "maximumRelativeRmsDifference": MAX_RELATIVE_RMS_DIFFERENCE,
    }


def main() -> int:
    if OUT.exists():
        raise FileExistsError(f"refusing to overwrite acceptance output: {OUT}")
    if not SOURCE_MANIFEST.is_file():
        raise FileNotFoundError(f"real-footage source manifest is missing: {SOURCE_MANIFEST}")

    manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    if not manifest.get("localOnly") or manifest.get("errors"):
        raise ValueError("the local source pack is not a clean local-only fixture")
    source = next((item for item in manifest["files"]
                   if item["filename"] == SOURCE_FILENAME), None)
    if source is None:
        raise FileNotFoundError(f"source not listed in manifest: {SOURCE_FILENAME}")
    source_path = SOURCE_ROOT / source["localPath"]
    if not source_path.is_file() or sha256(source_path) != source["sha256"]:
        raise ValueError("real-footage source hash does not match the local manifest")

    OUT.mkdir(parents=True)
    database = OUT / "audio-detachment-project.sqlite"
    before_path = OUT / "embedded-audio-before-detach.mp4"
    preview_path = OUT / "detached-audio-preview.mp4"
    export_path = OUT / "detached-audio-export.mp4"
    commands = []

    with ProjectStore(str(database)) as store:
        service = EditService(store)
        service.create_project(PROJECT_ID, width=WIDTH, height=HEIGHT,
                               fps=Rational.of(FPS))
        commands.append(apply(service, "clip.insert", {
            "trackId": "footage", "createTrackKind": "video", "clipId": CLIP_ID,
            "sourcePath": str(source_path), "sourceStart": rat(8),
            "timelineStart": rat(0), "timelineEnd": rat(DURATION),
        }, len(commands) + 1))
        commands.append(apply(service, "clip.audio", {
            "clipId": CLIP_ID, "volume": 0.7, "fadeIn": 0.15, "fadeOut": 0.35,
        }, len(commands) + 1))

        renderer = RenderService()
        embedded_project = service.get_project(PROJECT_ID)
        embedded_probe = renderer.probe_media(str(source_path))
        if not embedded_probe.get("has_video") or not embedded_probe.get("has_audio"):
            raise AssertionError("the selected Commons source must contain video and embedded audio")
        renderer.render(embedded_project, str(before_path), quality="low", overwrite=True)
        before_probe = renderer.probe_media(str(before_path))
        if not before_probe.get("has_audio"):
            raise AssertionError("the source clip's embedded audio was not present before detachment")

        commands.append(apply(service, "clip.detachAudio", {"clipId": CLIP_ID}, len(commands) + 1))
        detached = service.get_project(PROJECT_ID)
        video = clip_by_id(detached, CLIP_ID)
        audio_tracks = [track for track in detached.sequence.tracks if track.kind == "audio"]
        if len(audio_tracks) != 1 or len(audio_tracks[0].clips) != 1:
            raise AssertionError("detaching must create one editable audio lane and clip")
        audio = audio_tracks[0].clips[0]
        if audio.attached_to_clip_id != CLIP_ID or audio.asset_ref.source_path != str(source_path):
            raise AssertionError("detached audio must retain its source and parent anchor")
        if audio.timeline_start != video.timeline_start or audio.timeline_end != video.timeline_end:
            raise AssertionError("detached audio must begin aligned with its video parent")
        if audio.source_start != video.source_start:
            raise AssertionError("detached audio must retain the parent's source in-point")
        if audio.volume.to_json() != rat(7, 10) or audio.fade_in.to_json() != rat(3, 20):
            raise AssertionError("detached audio did not preserve source volume/fade-in")
        if audio.fade_out.to_json() != rat(7, 20) or video.volume.to_json() != rat(0):
            raise AssertionError("detached audio did not preserve fade-out or mute the video copy")

        commands.append(apply(service, "history.undo", {}, len(commands) + 1))
        undone = service.get_project(PROJECT_ID)
        if len(undone.sequence.tracks) != 1 or clip_by_id(undone, CLIP_ID).volume.to_json() != rat(7, 10):
            raise AssertionError("undo of audio detachment did not restore embedded audio state")
        commands.append(apply(service, "history.redo", {}, len(commands) + 1))
        redone = service.get_project(PROJECT_ID)
        detached_audio_track = next(track for track in redone.sequence.tracks
                                    if track.kind == "audio")
        commands.append(apply(service, "clip.move", {
            "clipId": CLIP_ID, "timelineStart": rat(1, 2),
        }, len(commands) + 1))
        moved = service.get_project(PROJECT_ID)
        moved_audio = next(track for track in moved.sequence.tracks
                           if track.kind == "audio").clips[0]
        if moved_audio.timeline_start != Rational.of(1, 2):
            raise AssertionError("detached source audio did not follow its video parent")
        commands.append(apply(service, "history.undo", {}, len(commands) + 1))
        commands.append(apply(service, "history.redo", {}, len(commands) + 1))
        commands.append(apply(service, "history.undo", {}, len(commands) + 1))
        restored = service.get_project(PROJECT_ID)
        final_audio_track = next(track for track in restored.sequence.tracks
                                 if track.kind == "audio")
        final_audio = final_audio_track.clips[0]
        if final_audio.timeline_start != Rational.of(0) or final_audio.attached_to_clip_id != CLIP_ID:
            raise AssertionError("undo did not return the detached lane to the original aligned position")

    with ProjectStore(str(database)) as reopened:
        service = EditService(reopened)
        project = service.get_project(PROJECT_ID)
        reopened_audio = next(track for track in project.sequence.tracks
                              if track.kind == "audio").clips[0]
        if reopened_audio.attached_to_clip_id != CLIP_ID:
            raise AssertionError("SQLite reopen lost the detached audio relationship")
        renderer.render_preview_window(project, str(preview_path), 0, DURATION)
        renderer.render(project, str(export_path), quality="low", overwrite=True)
        preview_probe = renderer.probe_media(str(preview_path))
        export_probe = renderer.probe_media(str(export_path))
        if not preview_probe.get("has_audio") or not export_probe.get("has_audio"):
            raise AssertionError("detached audio is missing from preview or export")
        full_decode(renderer.ffmpeg, preview_path)
        full_decode(renderer.ffmpeg, export_path)

    before_samples = decode_audio(renderer.ffmpeg, before_path)
    preview_samples = decode_audio(renderer.ffmpeg, preview_path)
    export_samples = decode_audio(renderer.ffmpeg, export_path)
    audio_comparisons = [
        compare_rms(before_samples, export_samples, "embedded audio vs detached-audio export"),
        compare_rms(export_samples, preview_samples, "detached-audio preview vs export"),
    ]
    if rms(export_samples) <= 1e-5:
        raise AssertionError("detached export audio is silent")

    evidence = [database, before_path, preview_path, export_path]
    report = {
        "schemaVersion": 1,
        "status": "local_real_footage_audio_detachment_preview_export_sweep",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "project": {"projectId": PROJECT_ID, "width": WIDTH, "height": HEIGHT,
                    "fps": FPS, "duration": DURATION},
        "source": {"filename": SOURCE_FILENAME, "title": source["title"],
                   "license": source["license"], "author": source["author"],
                   "sha256": source["sha256"], "sourceStartSeconds": 8,
                   "videoProbe": embedded_probe},
        "operations": commands,
        "detachedAudio": {
            "trackId": detached_audio_track.id,
            "clipId": audio.id,
            "attachedToClipId": audio.attached_to_clip_id,
            "sourcePathSharedWithVideo": audio.asset_ref.source_path == str(source_path),
            "volume": audio.volume.to_json(),
            "fadeIn": audio.fade_in.to_json(),
            "fadeOut": audio.fade_out.to_json(),
            "undoRedo": "undo removed the lane and restored embedded volume; redo restored the lane",
            "moveFollow": "detached audio followed the parent video; undo restored alignment",
            "saveAndReopen": "SQLite reopen retained the attachment and source reference",
        },
        "render": {
            "embeddedBaseline": {"path": before_path.name, "probe": before_probe,
                                 "fullDecode": "passed"},
            "preview": {"path": preview_path.name, "probe": preview_probe,
                        "fullDecode": "passed"},
            "export": {"path": export_path.name, "probe": export_probe,
                        "fullDecode": "passed"},
            "audioRmsComparisons": audio_comparisons,
        },
        "evidenceFiles": {
            path.name: {"sizeBytes": path.stat().st_size, "sha256": sha256(path)}
            for path in evidence
        },
        "limitation": "local functional and audio-level comparison only; Commons source audio is open test footage and does not establish owner-supplied commercial audio quality or independent editor UX",
    }
    report_path = OUT / "acceptance.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"detached original audio accepted across {len(commands)} edit/history commands")
    print("maximum relative RMS difference: " +
          f"{max(item['relativeRmsDifference'] for item in audio_comparisons):.4%}")
    print(f"evidence: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
