"""Exercise attached titles and stickers through edits, preview, save and export."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat

from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "output/acceptance/jy-camera-commons-pack-20260926"
SOURCE_MANIFEST = SOURCE_ROOT / "source-manifest.json"
STICKER = ROOT / "src/cutvoke/assets/stickers/guide_pointer_arrow.png"
OUT = ROOT / "output/acceptance/jy-r04-real-footage-attachments-20260927-v4"
PROJECT_ID = "jy-r04-real-footage-attachments"
WIDTH, HEIGHT, FPS = 640, 360, 24
PREVIEW_EXPORT_LIMIT = 8.0


def rat(numerator: int, denominator: int = 1) -> dict[str, str]:
    return {"num": str(numerator), "den": str(denominator)}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clip_by_id(project, clip_id: str):
    return next(clip for track in project.sequence.tracks for clip in track.clips
                if clip.id == clip_id)


def position(project, clip_id: str) -> dict[str, str]:
    return clip_by_id(project, clip_id).timeline_start.to_json()


def apply(service: EditService, name: str, payload: dict, serial: int) -> dict:
    current = service.get_project(PROJECT_ID)
    result = service.execute(Command(
        type=name,
        payload=payload,
        command_id=f"jy-r04-{serial:02d}-{name.replace('.', '-')}",
        project_id=PROJECT_ID,
        expected_revision=current.revision,
        actor=Actor("human", "jy-r04-real-footage-acceptance"),
    ))
    return {
        "command": name,
        "revision": service.get_project(PROJECT_ID).revision,
        "changedEntityCount": len(result.changed_entities),
        "changedEntities": result.changed_entities,
    }


def extract_video_frame(ffmpeg: str, video: Path, time_s: float, output: Path) -> None:
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss",
         f"{time_s:.6f}", "-i", str(video), "-frames:v", "1", str(output)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode or not output.is_file():
        raise RuntimeError(f"frame extraction failed at {time_s}s: {result.stderr[-500:]}")


def full_decode(ffmpeg: str, media: Path) -> None:
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-v", "error", "-i", str(media), "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode:
        raise RuntimeError(f"full decode failed for {media.name}: {result.stderr[-800:]}")


def mean_rgb_difference(first: Path, second: Path) -> float:
    with Image.open(first) as left, Image.open(second) as right:
        difference = ImageChops.difference(left.convert("RGB"), right.convert("RGB"))
        return sum(ImageStat.Stat(difference).mean) / 3


def make_contact_sheet(frames: list[tuple[str, Path]], out_path: Path) -> None:
    thumb_size = (512, 288)
    header = 30
    sheet = Image.new("RGB", (len(frames) * thumb_size[0], thumb_size[1] + header),
                      (24, 27, 34))
    draw = ImageDraw.Draw(sheet)
    for index, (label, path) in enumerate(frames):
        with Image.open(path) as source:
            thumb = source.convert("RGB").resize(thumb_size, Image.Resampling.LANCZOS)
        left = index * thumb_size[0]
        sheet.paste(thumb, (left, header))
        draw.text((left + 10, 8), label, fill=(240, 243, 248))
    sheet.save(out_path, format="JPEG", quality=94, optimize=True)


def main() -> int:
    if OUT.exists():
        raise FileExistsError(f"refusing to overwrite acceptance output: {OUT}")
    if not SOURCE_MANIFEST.is_file() or not STICKER.is_file():
        raise FileNotFoundError("the local footage pack or built-in sticker is missing")
    OUT.mkdir(parents=True)

    manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    if not manifest.get("localOnly") or manifest.get("errors"):
        raise ValueError("the local source pack is not a clean local-only fixture")
    source_by_name = {item["filename"]: item for item in manifest["files"]}
    video_names = ["street-traffic.webm", "waves-of-the-sea.webm",
                   "small-waterfall.webm", "rain-in-kenwood.webm"]
    sources = {}
    for filename in video_names:
        item = source_by_name[filename]
        path = SOURCE_ROOT / item["localPath"]
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise ValueError(f"local-only source hash mismatch: {filename}")
        sources[filename] = {**item, "path": path}

    database = OUT / "attachment-project.sqlite"
    command_log = []
    with ProjectStore(str(database)) as store:
        service = EditService(store)
        service.create_project(PROJECT_ID, width=WIDTH, height=HEIGHT,
                               fps=Rational.of(FPS))

        base_clips = [
            ("parent-a", "street-traffic.webm", 0, 2, 2),
            ("parent-b", "waves-of-the-sea.webm", 4, 6, 3),
            ("parent-c", "small-waterfall.webm", 8, 10, 5),
        ]
        for index, (clip_id, filename, start, end, source_start) in enumerate(base_clips):
            payload = {
                "trackId": "footage", "clipId": clip_id,
                "sourcePath": str(sources[filename]["path"]),
                "sourceStart": rat(source_start),
                "timelineStart": rat(start), "timelineEnd": rat(end),
            }
            if index == 0:
                payload["createTrackKind"] = "video"
            command_log.append(apply(service, "clip.insert", payload, len(command_log) + 1))

        sticker_payloads = [
            ("sticker-b", "parent-b", rat(17, 4), rat(21, 4)),
            ("sticker-c", "parent-c", rat(33, 4), rat(37, 4)),
        ]
        for index, (clip_id, parent_id, start, end) in enumerate(sticker_payloads):
            payload = {
                "trackId": "stickers", "clipId": clip_id,
                "sourcePath": str(STICKER), "timelineStart": start, "timelineEnd": end,
                "role": "sticker", "stickerScale": 0.5,
            }
            if index == 0:
                payload.update({"createTrackKind": "video", "createTrackRole": "sticker"})
            command_log.append(apply(service, "clip.insert", payload, len(command_log) + 1))
            command_log.append(apply(service, "clip.attach", {
                "clipId": clip_id, "attachedToClipId": parent_id,
            }, len(command_log) + 1))

        command_log.append(apply(service, "clip.insert", {
            "trackId": "titles", "createTrackKind": "text", "clipId": "title-b",
            "text": {"content": "WAVES B", "fontSize": 56, "bold": True,
                     "color": "#ffffff", "strokeColor": "#102238",
                     "strokeWidth": 3, "x": 0.82, "y": 0.18},
            "timelineStart": rat(21, 5),
            "timelineEnd": rat(53, 10),
        }, len(command_log) + 1))
        command_log.append(apply(service, "clip.attach", {
            "clipId": "title-b", "attachedToClipId": "parent-b",
        }, len(command_log) + 1))

        moved = apply(service, "clip.move", {
            "clipId": "parent-b", "timelineStart": rat(3),
        }, len(command_log) + 1)
        command_log.append(moved)
        moved_state = service.get_project(PROJECT_ID)
        move_positions = {clip_id: position(moved_state, clip_id)
                          for clip_id in ("parent-b", "sticker-b", "title-b")}
        assert move_positions == {
            "parent-b": rat(3), "sticker-b": rat(13, 4), "title-b": rat(16, 5),
        }, move_positions
        command_log.append(apply(service, "history.undo", {}, len(command_log) + 1))
        undone_move = service.get_project(PROJECT_ID)
        assert position(undone_move, "parent-b") == rat(4)
        assert position(undone_move, "sticker-b") == rat(17, 4)
        command_log.append(apply(service, "history.redo", {}, len(command_log) + 1))
        redone_move = service.get_project(PROJECT_ID)
        assert position(redone_move, "parent-b") == rat(3)
        assert position(redone_move, "sticker-b") == rat(13, 4)

        inserted = apply(service, "clip.insert", {
            "trackId": "footage", "clipId": "inserted-temp",
            "sourcePath": str(sources["rain-in-kenwood.webm"]["path"]),
            "sourceStart": rat(1), "timelineStart": rat(2), "timelineEnd": rat(3),
            "mode": "insert",
        }, len(command_log) + 1)
        command_log.append(inserted)
        inserted_state = service.get_project(PROJECT_ID)
        insert_positions = {clip_id: position(inserted_state, clip_id)
                            for clip_id in ("parent-b", "sticker-b", "title-b", "parent-c", "sticker-c")}
        assert insert_positions == {
            "parent-b": rat(4), "sticker-b": rat(17, 4), "title-b": rat(21, 5),
            "parent-c": rat(9), "sticker-c": rat(37, 4),
        }, insert_positions

        command_log.append(apply(service, "clip.rippleDelete", {
            "clipId": "inserted-temp",
        }, len(command_log) + 1))
        ripple_state = service.get_project(PROJECT_ID)
        ripple_positions = {clip_id: position(ripple_state, clip_id)
                            for clip_id in ("parent-b", "sticker-b", "title-b", "parent-c", "sticker-c")}
        assert ripple_positions == {
            "parent-b": rat(3), "sticker-b": rat(13, 4), "title-b": rat(16, 5),
            "parent-c": rat(8), "sticker-c": rat(33, 4),
        }, ripple_positions

        command_log.append(apply(service, "clip.closeGap", {
            "trackId": "footage", "afterClipId": "parent-a",
        }, len(command_log) + 1))
        compacted = service.get_project(PROJECT_ID)
        compact_positions = {clip_id: position(compacted, clip_id)
                             for clip_id in ("parent-b", "sticker-b", "title-b", "parent-c", "sticker-c")}
        assert compact_positions == {
            "parent-b": rat(2), "sticker-b": rat(9, 4), "title-b": rat(11, 5),
            "parent-c": rat(4), "sticker-c": rat(17, 4),
        }, compact_positions
        command_log.append(apply(service, "history.undo", {}, len(command_log) + 1))
        undo_close_gap = service.get_project(PROJECT_ID)
        assert position(undo_close_gap, "sticker-c") == rat(33, 4)
        command_log.append(apply(service, "history.redo", {}, len(command_log) + 1))
        compacted = service.get_project(PROJECT_ID)

        command_log.append(apply(service, "clip.insert", {
            "trackId": "footage", "clipId": "overwrite-parent-b",
            "sourcePath": str(sources["rain-in-kenwood.webm"]["path"]),
            "sourceStart": rat(1), "timelineStart": rat(2), "timelineEnd": rat(4),
            "mode": "overwrite",
        }, len(command_log) + 1))
        overwritten = service.get_project(PROJECT_ID)
        assert not any(clip.id == "parent-b" for track in overwritten.sequence.tracks
                       for clip in track.clips)
        assert clip_by_id(overwritten, "sticker-b").attached_to_clip_id is None
        assert clip_by_id(overwritten, "title-b").attached_to_clip_id is None
        command_log.append(apply(service, "history.undo", {}, len(command_log) + 1))
        restored = service.get_project(PROJECT_ID)
        assert clip_by_id(restored, "sticker-b").attached_to_clip_id == "parent-b"
        assert clip_by_id(restored, "title-b").attached_to_clip_id == "parent-b"

    with ProjectStore(str(database)) as reopened:
        service = EditService(reopened)
        project = service.get_project(PROJECT_ID)
        attachments = {
            clip_id: clip_by_id(project, clip_id).attached_to_clip_id
            for clip_id in ("sticker-b", "title-b", "sticker-c")
        }
        assert attachments == {
            "sticker-b": "parent-b", "title-b": "parent-b", "sticker-c": "parent-c",
        }, attachments
        positions = {clip_id: position(project, clip_id) for clip_id in (
            "parent-a", "parent-b", "parent-c", "sticker-b", "title-b", "sticker-c")}
        assert positions == {
            "parent-a": rat(0), "parent-b": rat(2), "parent-c": rat(4),
            "sticker-b": rat(9, 4), "title-b": rat(11, 5), "sticker-c": rat(17, 4),
        }, positions

        renderer = RenderService()
        preview = OUT / "attached-preview.mp4"
        export = OUT / "attached-export.mp4"
        renderer.render_preview_window(project, str(preview), 0, 6)
        renderer.render(project, str(export), quality="low", overwrite=True)
        preview_probe = renderer.probe_media(str(preview))
        export_probe = renderer.probe_media(str(export))
        full_decode(renderer.ffmpeg, preview)
        full_decode(renderer.ffmpeg, export)

        comparisons = []
        contact_frames = []
        for time_s, label in ((0.5, "A street"), (2.5, "B waves + attached title/sticker"),
                              (4.5, "C waterfall + attached sticker")):
            preview_frame = OUT / f"preview-{time_s:.1f}.png"
            export_frame = OUT / f"export-{time_s:.1f}.png"
            extract_video_frame(renderer.ffmpeg, preview, time_s, preview_frame)
            extract_video_frame(renderer.ffmpeg, export, time_s, export_frame)
            difference = mean_rgb_difference(preview_frame, export_frame)
            comparisons.append({"time": time_s, "label": label,
                                "meanAbsoluteRgbDifference": round(difference, 4)})
            contact_frames.append((label, export_frame))

        make_contact_sheet(contact_frames, OUT / "attachment-real-footage-contact.jpg")

        control = copy.deepcopy(project)
        control.sequence.tracks = [track for track in control.sequence.tracks
                                   if track.id not in {"stickers", "titles"}]
        attached_frame = OUT / "attached-b-scene.png"
        plain_frame = OUT / "plain-b-scene.png"
        renderer.extract_frame(project, 2.5, str(attached_frame))
        renderer.extract_frame(control, 2.5, str(plain_frame))
        with Image.open(attached_frame) as with_overlay, Image.open(plain_frame) as without_overlay:
            delta = ImageChops.difference(with_overlay.convert("RGB"),
                                          without_overlay.convert("RGB"))
            changed_pixels = sum(1 for pixel in delta.get_flattened_data()
                                 if max(pixel) > 8)
            mean_overlay_difference = sum(ImageStat.Stat(delta).mean) / 3
        if changed_pixels < 500 or mean_overlay_difference < 0.2:
            raise AssertionError("attached sticker/title did not visibly affect real footage")

        individual_object_effects = []
        for clip_id in ("sticker-b", "title-b"):
            without_one = copy.deepcopy(project)
            for track in without_one.sequence.tracks:
                track.clips = [clip for clip in track.clips if clip.id != clip_id]
            control_frame = OUT / f"control-without-{clip_id}.png"
            renderer.extract_frame(without_one, 2.5, str(control_frame))
            with Image.open(attached_frame) as with_object, Image.open(control_frame) as without_object:
                object_delta = ImageChops.difference(with_object.convert("RGB"),
                                                     without_object.convert("RGB"))
                object_pixels = sum(1 for pixel in object_delta.get_flattened_data()
                                    if max(pixel) > 8)
                object_mean = sum(ImageStat.Stat(object_delta).mean) / 3
                object_report = {
                    "clipId": clip_id,
                    "controlFrame": control_frame.name,
                    "changedPixelsAbove8": object_pixels,
                    "meanAbsoluteRgbDifference": round(object_mean, 4),
                }
                if clip_id == "title-b":
                    # Text occupies only a small part of the full video frame, so
                    # judge it in its known upper-right placement instead of using
                    # the sticker's full-frame mean-difference threshold.
                    title_region = object_delta.crop((
                        int(object_delta.width * 0.60), 0,
                        object_delta.width, int(object_delta.height * 0.45),
                    ))
                    title_pixels = sum(1 for pixel in title_region.get_flattened_data()
                                       if max(pixel) > 8)
                    title_mean = sum(ImageStat.Stat(title_region).mean) / 3
                    if title_pixels < 500 or title_mean < 0.5:
                        raise AssertionError(
                            "attached title is not visibly rendered in its upper-right region: "
                            f"{title_pixels} changed pixels, mean {title_mean:.4f}"
                        )
                    object_report["visibilityCheck"] = {
                        "region": "x >= 60% frame width, y < 45% frame height",
                        "changedPixelsAbove8": title_pixels,
                        "meanAbsoluteRgbDifference": round(title_mean, 4),
                        "minimumChangedPixels": 500,
                        "minimumMeanAbsoluteRgbDifference": 0.5,
                    }
                elif object_pixels < 500 or object_mean < 0.2:
                    raise AssertionError(f"attached object is not visible in footage: {clip_id}")
            individual_object_effects.append(object_report)

    max_difference = max(item["meanAbsoluteRgbDifference"] for item in comparisons)
    if max_difference >= PREVIEW_EXPORT_LIMIT:
        raise AssertionError(f"preview/export difference {max_difference} exceeds limit")

    evidence_files = [database, preview, export, OUT / "attachment-real-footage-contact.jpg",
                      attached_frame, plain_frame,
                      *(OUT / f"preview-{time_s:.1f}.png" for time_s in (0.5, 2.5, 4.5)),
                      *(OUT / f"export-{time_s:.1f}.png" for time_s in (0.5, 2.5, 4.5)),
                      *(OUT / f"control-without-{clip_id}.png"
                        for clip_id in ("sticker-b", "title-b"))]
    report = {
        "schemaVersion": 1,
        "status": "local_real_footage_attachment_preview_export_sweep",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "project": {"projectId": PROJECT_ID, "width": WIDTH, "height": HEIGHT,
                    "fps": FPS, "duration": 6, "revisionAfterRestore": project.revision},
        "sourcePack": {
            "path": str(SOURCE_ROOT.relative_to(ROOT)).replace("\\", "/"),
            "localOnly": True,
            "files": [{"filename": filename, "title": sources[filename]["title"],
                       "license": sources[filename]["license"],
                       "sha256": sources[filename]["sha256"]}
                      for filename in video_names],
        },
        "sticker": {"path": str(STICKER.relative_to(ROOT)).replace("\\", "/"),
                    "sha256": sha256(STICKER), "role": "sticker", "scale": 0.5},
        "operations": command_log,
        "finalAttachments": attachments,
        "finalPositions": positions,
        "overwriteBehavior": "fully overwritten parent is removed; its title and sticker detach; undo restores both anchors",
        "saveAndReopen": "SQLite project reopened with all three attachments and expected timeline positions",
        "preview": {"path": preview.name, "probe": preview_probe, "fullDecode": "passed"},
        "export": {"path": export.name, "probe": export_probe, "fullDecode": "passed"},
        "previewExportComparison": {
            "metric": "mean absolute RGB difference per channel (0-255)",
            "strictLessThan": PREVIEW_EXPORT_LIMIT,
            "comparisons": comparisons,
            "maxObserved": round(max_difference, 4),
        },
        "overlayVisualEffect": {"frame": attached_frame.name,
                                "controlFrame": plain_frame.name,
                                "changedPixelsAbove8": changed_pixels,
                                "meanAbsoluteRgbDifference": round(mean_overlay_difference, 4),
                                "individualObjects": individual_object_effects},
        "evidenceFiles": {
            path.name: {"sizeBytes": path.stat().st_size, "sha256": sha256(path)}
            for path in evidence_files
        },
        "contactSheet": "attachment-real-footage-contact.jpg",
        "limitation": "local functional and render acceptance only; source footage is synthetic test input from a local Commons pack, not owner-supplied commercial footage or independent editor UX evidence",
    }
    (OUT / "acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"accepted attachments across real footage: {len(command_log)} edit/history commands")
    print(f"preview/export max mean RGB difference: {max_difference:.4f} (< {PREVIEW_EXPORT_LIMIT})")
    print(f"overlay changed pixels: {changed_pixels}; mean difference: {mean_overlay_difference:.4f}")
    print(f"evidence: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
