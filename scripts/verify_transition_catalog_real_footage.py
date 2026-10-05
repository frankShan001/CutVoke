#!/usr/bin/env python3
"""Render every built-in transition preset over licensed real camera footage.

The report is local QA evidence. It does not claim independent user or
commercial-quality acceptance.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore
from cutvoke.core.effects import default_registry
from cutvoke.core.preset_catalog import PresetCatalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACK = ROOT / "output/acceptance/jy-camera-commons-pack-20260926"
SOURCE_MANIFEST = SOURCE_PACK / "source-manifest.json"
OUTPUT = ROOT / "output/acceptance/jy-transition-catalog-real-footage-all-exports-20260927"
CANVAS = (320, 180)
CLIP_LENGTH = 2
TRANSITION_DURATION = 0.5
PREVIEW_START = 1.5
PREVIEW_LENGTH = 1.0
PREVIEW_EXPORT_RGB_LIMIT = 8.0
THUMB = (288, 162)
COLUMNS = 4


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def mean_rgb_delta(left: Path, right: Path) -> float:
    with Image.open(left) as first, Image.open(right) as second:
        if first.size != second.size:
            raise ValueError(f"frame dimensions differ: {first.size} != {second.size}")
        diff = ImageChops.difference(first.convert("RGB"), second.convert("RGB"))
        return round(sum(ImageStat.Stat(diff).mean) / 3, 4)


def edit(service: EditService, project_id: str, kind: str, payload: dict, serial: int) -> None:
    project = service.get_project(project_id)
    service.execute(Command(
        type=kind,
        payload=payload,
        command_id=f"transition-gallery-{serial}",
        project_id=project_id,
        expected_revision=project.revision,
        actor=Actor("human", "transition-catalog-real-footage"),
    ))


def extract_frame(ffmpeg: str, video: Path, timestamp: float, target: Path) -> None:
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
         "-ss", f"{timestamp:.6f}", "-frames:v", "1", str(target)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    if proc.returncode or not target.is_file():
        raise RuntimeError(f"frame extraction failed at {timestamp}s: {proc.stderr[-1000:]}")


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite existing acceptance output: {OUTPUT}")
    source_manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    if not source_manifest.get("localOnly") or not source_manifest.get("allStreamsFullDecoded"):
        raise ValueError("source pack is not marked as decoded local-only acceptance footage")
    sources = [item for item in source_manifest["files"]
               if item["filename"].lower().endswith((".webm", ".mp4"))]
    if len(sources) != 6:
        raise ValueError(f"expected six real-footage sources, got {len(sources)}")

    presets = [preset for preset in PresetCatalog.builtin(default_registry()).all()
               if preset.family == "transition" and preset.qualified]
    if len(presets) != 41 or len({preset.id for preset in presets}) != len(presets):
        raise ValueError(f"expected 41 unique qualified transitions, found {len(presets)}")

    source_rows = []
    for item in sources:
        path = SOURCE_PACK / item["localPath"]
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise ValueError(f"real-footage source hash mismatch: {path}")
        duration = float(item["probe"]["format"]["duration"])
        if duration < CLIP_LENGTH:
            raise ValueError(f"source too short for transition sample: {path.name}")
        source_rows.append({**item, "path": path, "duration": duration})

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    renderer = RenderService()
    font_path = Path("C:/Windows/Fonts/segoeui.ttf")
    font = ImageFont.truetype(str(font_path), 14) if font_path.is_file() else ImageFont.load_default()
    built = []
    serial = 0

    with tempfile.TemporaryDirectory(
        prefix=f".{OUTPUT.name}-", dir=str(OUTPUT.parent)
    ) as temporary:
        work = Path(temporary)
        project_db = work / "transition-gallery.sqlite"
        # Each preset gets a fresh two-shot project so the acceptance path is
        # the same as inserting the preset onto one incoming clip in the UI.
        with ProjectStore(str(project_db)) as store:
            service = EditService(store)
            for index, preset in enumerate(presets):
                project_id = f"transition-gallery-{index:02d}"
                first = source_rows[index % len(source_rows)]
                second = source_rows[(index + 1) % len(source_rows)]
                first_start = max(0.0, (first["duration"] - CLIP_LENGTH) / 2)
                second_start = max(0.0, (second["duration"] - CLIP_LENGTH) / 2)
                service.create_project(project_id, width=CANVAS[0], height=CANVAS[1])
                serial += 1
                edit(service, project_id, "track.add", {"trackId": "video", "kind": "video"}, serial)
                for clip_index, (clip_id, path, source_start) in enumerate((
                    ("outgoing", first["path"], first_start),
                    ("incoming", second["path"], second_start),
                )):
                    serial += 1
                    start = clip_index * CLIP_LENGTH
                    edit(service, project_id, "clip.insert", {
                        "trackId": "video",
                        "clipId": clip_id,
                        "sourcePath": str(path),
                        "timelineStart": Rational.of(start).to_json(),
                        "timelineEnd": Rational.of(start + CLIP_LENGTH).to_json(),
                        "sourceStart": Rational.from_float(source_start).to_json(),
                    }, serial)

                serial += 1
                edit(service, project_id, "builtinPreset.apply", {
                    "clipId": "incoming",
                    "presetId": preset.id,
                    "duration": TRANSITION_DURATION,
                }, serial)
                applied = service.get_project(project_id)
                incoming = next(clip for clip in applied.sequence.tracks[0].clips
                                if clip.id == "incoming")
                transition = next((effect for effect in incoming.effects
                                   if effect.get("effectId") == preset.effects[0]["effectId"]), None)
                if (transition is None or transition.get("params", {}).get("duration") != TRANSITION_DURATION
                        or transition.get("presetId") != preset.id):
                    raise RuntimeError(f"preset provenance/duration did not apply: {preset.id}")

                serial += 1
                edit(service, project_id, "history.undo", {}, serial)
                undone = service.get_project(project_id)
                incoming = next(clip for clip in undone.sequence.tracks[0].clips
                                if clip.id == "incoming")
                if any(item.get("effectId") == preset.effects[0]["effectId"]
                       for item in incoming.effects):
                    raise RuntimeError(f"undo left transition applied: {preset.id}")

                serial += 1
                edit(service, project_id, "history.redo", {}, serial)
                redone = service.get_project(project_id)
                incoming = next(clip for clip in redone.sequence.tracks[0].clips
                                if clip.id == "incoming")
                if not any(item.get("effectId") == preset.effects[0]["effectId"]
                           and item.get("presetId") == preset.id for item in incoming.effects):
                    raise RuntimeError(f"redo did not restore transition: {preset.id}")

                built.append({
                    "presetId": preset.id,
                    "presetVersion": preset.version,
                    "name": preset.name,
                    "subcategory": preset.subcategory,
                    "effectId": preset.effects[0]["effectId"],
                    "effectVersion": preset.effects[0]["version"],
                    "params": {**preset.effects[0]["params"], "duration": TRANSITION_DURATION},
                    "projectId": project_id,
                    "outgoingSource": first,
                    "outgoingSourceStart": round(first_start, 3),
                    "incomingSource": second,
                    "incomingSourceStart": round(second_start, 3),
                })

        # Reopen the project database before preview/export checks.
        with ProjectStore(str(project_db)) as reopened:
            service = EditService(reopened)
            for index, record in enumerate(built):
                project = service.get_project(record["projectId"])
                incoming = next(clip for clip in project.sequence.tracks[0].clips
                                if clip.id == "incoming")
                if not any(effect.get("presetId") == record["presetId"]
                           and effect.get("presetVersion") == record["presetVersion"]
                           for effect in incoming.effects):
                    raise RuntimeError(f"transition did not survive SQLite reopen: {record['presetId']}")

                slug = record["presetId"].rsplit(".", 1)[-1]
                preview_path = work / "previews" / f"{index + 1:02d}-{slug}.mp4"
                preview_path.parent.mkdir(parents=True, exist_ok=True)
                preview_result = renderer.render_preview_window(
                    project, str(preview_path), PREVIEW_START, PREVIEW_LENGTH
                )
                decode = subprocess.run(
                    [renderer.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(preview_path),
                     "-f", "null", "-"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                )
                if decode.returncode:
                    raise RuntimeError(f"preview decode failed for {record['presetId']}: {decode.stderr[-1000:]}")

                frame_path = work / "midframes" / f"{index + 1:02d}-{slug}.png"
                frame_path.parent.mkdir(parents=True, exist_ok=True)
                extract_frame(renderer.ffmpeg, preview_path, PREVIEW_LENGTH / 2, frame_path)
                record.update({
                    "preview": str(preview_path.relative_to(work).as_posix()),
                    "previewSha256": sha256(preview_path),
                    "previewFrame": str(frame_path.relative_to(work).as_posix()),
                    "previewFrameSha256": sha256(frame_path),
                    "previewDurationSeconds": preview_result["duration"],
                    "previewDecoded": True,
                    "workflowChecks": ["presetApply", "undo", "redo", "sqliteSaveReopen",
                                       "realFootagePreview", "fullPreviewDecode"],
                })
                print(f"[{index + 1}/{len(built)}] {record['presetId']} preview OK", flush=True)

            # Compare every preset's real-footage preview with the full export.
            export_dir = work / "exports"
            frame_dir = work / "export-frames"
            export_dir.mkdir()
            frame_dir.mkdir()
            for index, record in enumerate(built):
                project = service.get_project(record["projectId"])
                slug = record["presetId"].rsplit(".", 1)[-1]
                export_path = export_dir / f"{slug}.mp4"
                export_result = renderer.render(project, str(export_path), quality="low")
                frame_path = frame_dir / f"{slug}.png"
                extract_frame(renderer.ffmpeg, export_path, 2.0, frame_path)
                preview_frame = work / record["previewFrame"]
                delta = mean_rgb_delta(preview_frame, frame_path)
                if delta >= PREVIEW_EXPORT_RGB_LIMIT:
                    raise RuntimeError(
                        f"preview/export frame mismatch for {record['presetId']}: {delta:.4f}"
                    )
                decode = subprocess.run(
                    [renderer.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(export_path),
                     "-f", "null", "-"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                )
                if decode.returncode:
                    raise RuntimeError(f"export decode failed for {record['presetId']}: {decode.stderr[-1000:]}")
                record["fullExport"] = {
                    "path": str(export_path.relative_to(work).as_posix()),
                    "sha256": sha256(export_path),
                    "durationSeconds": export_result["duration"],
                    "decoded": True,
                    "comparisonFrame": str(frame_path.relative_to(work).as_posix()),
                    "previewExportMeanRgbDelta": delta,
                }
                print(f"[{index + 1}/{len(built)}] {record['presetId']} export OK (RGB Δ {delta:.2f})",
                      flush=True)

        # Create category-sorted contact sheets of actual renderer output.
        ordered = sorted(built, key=lambda item: (item["subcategory"], item["presetId"]))
        sheet_entries = []
        for category in sorted({item["subcategory"] for item in ordered}):
            items = [item for item in ordered if item["subcategory"] == category]
            cell_width, cell_height, label_height = THUMB[0], THUMB[1], 30
            columns = min(COLUMNS, len(items))
            rows = (len(items) + columns - 1) // columns
            sheet = Image.new("RGB", (columns * cell_width, rows * (cell_height + label_height)), "#202632")
            draw = ImageDraw.Draw(sheet)
            for item_index, item in enumerate(items):
                row, column = divmod(item_index, columns)
                x, y = column * cell_width, row * (cell_height + label_height)
                exported_frame = work / item["fullExport"]["comparisonFrame"]
                with Image.open(exported_frame) as opened:
                    frame = opened.convert("RGB").resize(THUMB, Image.Resampling.LANCZOS)
                sheet.paste(frame, (x, y))
                label = item["effectId"].rsplit(".", 1)[-1]
                draw.text((x + 5, y + cell_height + 6), label, font=font, fill="white")
            path = work / "contact-sheets" / f"transition-{len(sheet_entries) + 1:02d}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            sheet.save(path, quality=90, optimize=True)
            sheet_entries.append({
                "subcategory": category,
                "count": len(items),
                "presetIds": [item["presetId"] for item in items],
                "image": path.relative_to(work).as_posix(),
                "sha256": sha256(path),
            })

        # Retain only evidence artifacts; the transient project database and
        # oversized source-independent intermediates are excluded from final output.
        (work / "transition-gallery.sqlite").unlink(missing_ok=True)
        report = {
            "schemaVersion": 1,
            "status": "local_real_footage_preview_export_sweep",
            "limitations": [
                "Uses six Commons videos retained for local QA under their individual licenses.",
                "320x180 and one-second preview windows are functional coverage, not commercial image-quality acceptance.",
                "All 41 presets are applied, undone, redone, persisted/reopened, previewed, full-exported, decoded, and preview/export frame-compared.",
                "No external editor blind test or target-footage approval is implied.",
            ],
            "canvas": list(CANVAS),
            "transitionDurationSeconds": TRANSITION_DURATION,
            "previewWindow": {"start": PREVIEW_START, "duration": PREVIEW_LENGTH},
            "previewExportComparison": {
                "metric": "mean absolute RGB difference per channel (0-255)",
                "strictLessThan": PREVIEW_EXPORT_RGB_LIMIT,
                "comparisonCount": len(built),
                "minObserved": min(item["fullExport"]["previewExportMeanRgbDelta"] for item in built),
                "maxObserved": max(item["fullExport"]["previewExportMeanRgbDelta"] for item in built),
            },
            "presetCount": len(built),
            "subcategoryCount": len({item["subcategory"] for item in built}),
            "sourcePack": {
                "manifest": str(SOURCE_MANIFEST.relative_to(ROOT).as_posix()),
                "manifestSha256": sha256(SOURCE_MANIFEST),
                "localOnly": True,
                "sources": [{
                    "filename": item["filename"], "page": item["page"],
                    "author": item["author"], "license": item["license"],
                    "description": item["description"], "sha256": item["sha256"],
                    "durationSeconds": item["duration"],
                } for item in source_rows],
            },
            "contactSheets": sheet_entries,
            "fullExportCount": len(built),
            "items": [{
                key: value for key, value in item.items()
                if key not in ("outgoingSource", "incomingSource")
            } | {
                "outgoingSource": {key: item["outgoingSource"][key] for key in
                                   ("filename", "page", "license", "sha256")},
                "incomingSource": {key: item["incomingSource"][key] for key in
                                   ("filename", "page", "license", "sha256")},
            } for item in built],
        }
        (work / "acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        readme = """# Real-footage transition catalog sweep

All 41 qualified built-in transition presets were applied through `builtinPreset.apply` to incoming clips in two-shot projects built from the six locally retained Wikimedia Commons videos. Each case exercised apply, undo, redo, SQLite save/reopen, one-second real-footage preview, a full MP4 export, full-file decode, and preview/export seam-frame comparison.

This is 320x180 local functional coverage. It does not assess final commercial quality, longer timelines, user preference, or independent editor usability. Each source retains its own Commons attribution and license; this pack remains local QA material.

See `acceptance.json` for source and output hashes, exact preset/source mappings, checks, and per-subcategory contact sheets.
"""
        (work / "README.md").write_text(readme, encoding="utf-8")
        os.replace(work, OUTPUT)

    print(f"Verified {len(built)} real-footage transition previews and exports; {len(sheet_entries)} subcategory sheets; {len(built)} preview/export comparisons.")
    print(OUTPUT.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
