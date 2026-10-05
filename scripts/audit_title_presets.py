"""Exercise every title candidate through edit, history, persistence and render.

This script records observed machine checks only. It never asserts visual
distinctiveness, approves a candidate, or treats a passing render as a product
quality decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cutvoke.core.model import Project  # noqa: E402
from cutvoke.core.preset_catalog import PresetCatalog  # noqa: E402
from cutvoke.core.protocol import Actor, Command  # noqa: E402
from cutvoke.core.render import RenderService  # noqa: E402
from cutvoke.core.service import EditService  # noqa: E402
from cutvoke.core.store import ProjectStore  # noqa: E402

CATALOG = REPO / "src/cutvoke/core/builtin_presets.json"
ASSETS = REPO / "src/cutvoke/assets/preset_previews"
CHECKS = ("apply", "edit", "saveReload", "undo", "export")


def _seconds(value: int) -> dict[str, str]:
    return {"num": str(value), "den": "1"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command(service: EditService, project_id: str, kind: str,
             payload: dict, serial: int) -> None:
    project = service.get_project(project_id)
    service.execute(Command(
        type=kind, payload=payload, command_id=f"title-audit-{serial}",
        project_id=project_id, expected_revision=project.revision,
        actor=Actor("agent", "title-audit"),
    ))


def _frame(video: Path, time: float, output: Path) -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(time), "-i", str(video), "-frames:v", "1", str(output),
    ], check=True, capture_output=True)


def _audit(preset_id: str, temp: Path, renderer: RenderService) -> dict:
    project_id = "audit-title"
    background = temp / "background.png"
    Image.new("RGB", (320, 180), (23, 31, 49)).save(background)
    database = str(temp / "projects.sqlite")
    with ProjectStore(database) as store:
        service = EditService(store)
        service.create_project(project_id, width=320, height=180)
        _command(service, project_id, "clip.insert", {
            "trackId": "video", "createTrackKind": "video", "clipId": "picture",
            "sourcePath": str(background), "timelineStart": _seconds(0),
            "timelineEnd": _seconds(3),
        }, 1)
        _command(service, project_id, "clip.insert", {
            "trackId": "titles", "createTrackKind": "text", "clipId": "headline",
            "textPresetId": preset_id, "text": {"content": "验收标题"},
            "timelineStart": _seconds(0), "timelineEnd": _seconds(2),
        }, 2)
        project = service.get_project(project_id)
        title = project.sequence.tracks[1].clips[0]
        assert title.effects[0]["presetId"] == preset_id
        assert title.effects[0]["params"]["content"] == "验收标题"
        assert len(project.sequence.captions) == 0
        applied_revision = project.revision
        _command(service, project_id, "history.undo", {}, 3)
        assert len(service.get_project(project_id).sequence.tracks) == 1
        _command(service, project_id, "history.redo", {}, 4)
        assert service.get_project(project_id).sequence.tracks[1].clips[0].effects[0]["presetId"] == preset_id
        _command(service, project_id, "effect.update", {
            "clipId": "headline", "effectId": "cutvoke.text",
            "params": {"content": "验收改字", "x": 0.52},
        }, 5)
        assert service.get_project(project_id).sequence.tracks[1].clips[0].effects[0]["params"]["content"] == "验收改字"
        _command(service, project_id, "history.undo", {}, 6)
        assert service.get_project(project_id).sequence.tracks[1].clips[0].effects[0]["params"]["content"] == "验收标题"
        _command(service, project_id, "history.redo", {}, 7)

    with ProjectStore(database) as reopened:
        service = EditService(reopened)
        project: Project = service.get_project(project_id)
        title = project.sequence.tracks[1].clips[0]
        assert title.effects[0]["presetId"] == preset_id
        assert title.effects[0]["params"]["content"] == "验收改字"
        assert title.effects[0]["params"]["x"] == 0.52
        exported = temp / "export.mp4"
        preview = temp / "preview.mp4"
        export_report = renderer.render(project, str(exported), quality="low")
        preview_report = renderer.render_preview_window(project, str(preview), 0, 2)
        export_frame, preview_frame, plain_frame = (temp / name for name in
                                                     ("export.png", "preview.png", "plain.png"))
        _frame(exported, 1, export_frame)
        _frame(preview, 1, preview_frame)
        _frame(exported, 2.5, plain_frame)
        with Image.open(export_frame) as export_image, Image.open(preview_frame) as preview_image, \
                Image.open(plain_frame) as plain_image:
            export_rgb = export_image.convert("RGB")
            preview_rgb = preview_image.convert("RGB")
            plain_rgb = plain_image.convert("RGB")
            preview_difference = sum(ImageStat.Stat(
                ImageChops.difference(export_rgb, preview_rgb)).mean) / 3
            diff = ImageChops.difference(export_rgb, plain_rgb).convert("RGB")
            visible_pixels = sum(1 for pixel in diff.get_flattened_data() if max(pixel) > 20)
        if preview_difference >= 5 or visible_pixels < 80:
            raise RuntimeError(f"{preset_id}: preview/export diff={preview_difference:.2f}, "
                               f"visible pixels={visible_pixels}")
        return {
            "presetId": preset_id,
            "generator": "scripts/audit_title_presets.py",
            "source": "generated solid-color picture and editable title clip",
            "commands": ["clip.insert", "history.undo", "history.redo", "effect.update"],
            "appliedRevision": applied_revision,
            "savedRevision": project.revision,
            "contentAfterReload": title.effects[0]["params"]["content"],
            "exportDurationSeconds": export_report["duration"],
            "previewDurationSeconds": preview_report["duration"],
            "previewExportMeanRgbDifference": round(preview_difference, 3),
            "visibleTitlePixels": visible_pixels,
            "exportSha256": _sha(exported),
            "checks": list(CHECKS),
            "exportPath": exported,
        }


def main(preset_id: str | None) -> None:
    manifest = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = {entry["presetId"]: entry for entry in manifest["presets"]}
    candidates = [spec for spec in PresetCatalog.builtin().all()
                  if spec.family == "text" and spec.status == "candidate"
                  and (preset_id is None or spec.id == preset_id)]
    if not candidates:
        raise SystemExit("no matching title candidates")
    renderer = RenderService()
    completed = 0
    for spec in candidates:
        slug = spec.id.removeprefix("cutvoke.preset.").replace(".", "-")
        stem = f"{slug}-v{spec.version.replace('.', '-')}"
        audit_file = ASSETS / f"{stem}.audit.json"
        audit_video = ASSETS / f"{stem}.audit.mp4"
        with tempfile.TemporaryDirectory(prefix="cutvoke-title-audit-") as temporary:
            result = _audit(spec.id, Path(temporary), renderer)
            exported = Path(result.pop("exportPath"))
            shutil.copyfile(exported, audit_video)
            result["auditExportSha256"] = _sha(audit_video)
            result["auditExport"] = audit_video.name
            audit_file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
        entry = entries[spec.id]
        relative = "../assets/preset_previews/" + audit_file.name
        entry.setdefault("checks", {}).update({key: True for key in CHECKS})
        entry.setdefault("evidence", {}).update({key: relative for key in CHECKS})
        completed += 1
        print(f"{spec.id}: edit/reload/undo/export observed, {result['visibleTitlePixels']} visible pixels")
    CATALOG.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Audited {completed} title candidates; visualDistinct stays unapproved")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset-id")
    main(parser.parse_args().preset_id)
