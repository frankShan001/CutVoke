"""Audit non-text preset candidates through command, history, reload and render.

This records observed machine checks. It does not decide artistic quality or
approve a preset. Fixed operators without a declared value control do not
pass the edit check merely because they can be disabled or removed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cutvoke.core.effects import animation_slot, default_registry  # noqa: E402
from cutvoke.core.preset_catalog import PresetCatalog, PresetSpec  # noqa: E402
from cutvoke.core.protocol import Actor, Command  # noqa: E402
from cutvoke.core.render import RenderService  # noqa: E402
from cutvoke.core.service import EditService  # noqa: E402
from cutvoke.core.store import ProjectStore  # noqa: E402
from generate_preset_previews import _art, _frame, _title_art  # noqa: E402
from person_fx_preview_source import make_person_alpha_video  # noqa: E402

CATALOG = REPO / "src/cutvoke/core/builtin_presets.json"
ASSETS = REPO / "src/cutvoke/assets/preset_previews"
FAMILIES = ("transition", "animation", "fx", "filter", "personFx")


def _rat(value: int) -> dict[str, str]:
    return {"num": str(value), "den": "1"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command(service: EditService, project_id: str, name: str,
             payload: dict, serial: int) -> None:
    project = service.get_project(project_id)
    service.execute(Command(type=name, payload=payload,
        command_id=f"preset-audit-{serial}", project_id=project_id,
        expected_revision=project.revision, actor=Actor("agent", "preset-audit")))


def _target(service: EditService, project_id: str, clip_id: str):
    return next(clip for track in service.get_project(project_id).sequence.tracks
                for clip in track.clips if clip.id == clip_id)


def _different_value(value: object, schema: dict) -> int | float | str | None:
    choices = schema.get("enum")
    if isinstance(choices, list) and len(choices) > 1:
        return next((choice for choice in choices if choice != value), None)
    kind = schema.get("type")
    if kind not in ("number", "integer") or isinstance(value, bool) \
            or not isinstance(value, (int, float)):
        return None
    minimum, maximum = schema.get("minimum"), schema.get("maximum")
    if kind == "integer":
        if maximum is None or value + 1 <= maximum:
            return int(value + 1)
        if minimum is None or value - 1 >= minimum:
            return int(value - 1)
        return None
    if minimum is not None and value > minimum:
        return round((value + minimum) / 2, 4)
    if maximum is not None and value < maximum:
        return round((value + maximum) / 2, 4)
    return None


def _editable_control(spec: PresetSpec, params: dict) -> tuple[str, int | float | str] | None:
    effect = default_registry().find(spec.effect_id)
    assert effect is not None
    properties = effect.parameters.get("properties") or {}
    for name in spec.adjustable:
        if name in params and name in properties:
            changed = _different_value(params[name], properties[name])
            if changed is not None and changed != params[name]:
                return name, changed
    return None


def _audit(spec: PresetSpec, temp: Path, renderer: RenderService) -> dict:
    project_id = "audit-effect"
    first, second = temp / "source-a.png", temp / "source-b.png"
    _art(first, 0)
    _art(second, 1)
    is_transition = spec.family == "transition"
    is_person_fx = spec.family == "personFx"
    background_source = first
    person_source = temp / "person-alpha.mov"
    if is_person_fx:
        make_person_alpha_video(person_source, ffmpeg=renderer.ffmpeg, frames=60)
        background_source = temp / "person-dark-stage.png"
        _title_art(background_source)
    target_id = "person-layer" if is_person_fx else "second" if is_transition else "first"
    database = str(temp / "projects.sqlite")
    with ProjectStore(database) as store:
        service = EditService(store)
        service.create_project(project_id, width=320, height=180)
        _command(service, project_id, "clip.insert", {
            "trackId": "video", "createTrackKind": "video", "clipId": "first",
            "sourcePath": str(background_source), "timelineStart": _rat(0),
            "timelineEnd": _rat(2),
        }, 1)
        if is_person_fx:
            _command(service, project_id, "clip.insert", {
                "trackId": "person", "createTrackKind": "video", "clipId": target_id,
                "sourcePath": str(person_source), "timelineStart": _rat(0),
                "timelineEnd": _rat(2),
            }, 2)
        elif is_transition:
            _command(service, project_id, "clip.insert", {
                "trackId": "video", "clipId": "second",
                "sourcePath": str(second), "timelineStart": _rat(2),
                "timelineEnd": _rat(4),
            }, 2)
        _command(service, project_id, "builtinPreset.apply", {
            "clipId": target_id, "presetId": spec.id,
        }, 3)
        applied = _target(service, project_id, target_id)
        primary = next(effect for effect in applied.effects
                       if effect.get("effectId") == spec.effect_id)
        assert primary.get("presetId") == spec.id
        assert primary.get("presetVersion") == spec.version
        _command(service, project_id, "history.undo", {}, 4)
        assert not _target(service, project_id, target_id).effects
        _command(service, project_id, "history.redo", {}, 5)
        assert next(effect for effect in _target(service, project_id, target_id).effects
                    if effect.get("effectId") == spec.effect_id)["presetId"] == spec.id
        control = _editable_control(spec, primary.get("params") or {})
        if control is not None:
            name, value = control
            original = primary["params"][name]
            _command(service, project_id, "effect.update", {
                "clipId": target_id, "effectId": spec.effect_id,
                "params": {name: value},
            }, 6)
            updated = next(effect for effect in _target(service, project_id, target_id).effects
                           if effect.get("effectId") == spec.effect_id)
            assert updated["params"][name] == value
            _command(service, project_id, "history.undo", {}, 7)
            restored = next(effect for effect in _target(service, project_id, target_id).effects
                            if effect.get("effectId") == spec.effect_id)
            assert restored["params"][name] == original
            _command(service, project_id, "history.redo", {}, 8)
        saved_revision = service.get_project(project_id).revision

    with ProjectStore(database) as reopened:
        service = EditService(reopened)
        project = service.get_project(project_id)
        assert project.revision == saved_revision
        primary = next(effect for effect in _target(service, project_id, target_id).effects
                       if effect.get("effectId") == spec.effect_id)
        assert primary["presetId"] == spec.id
        if control is not None:
            assert primary["params"][control[0]] == control[1]
        if is_transition:
            sample_at = 2.25
        elif spec.family == "animation":
            sample_at = 1.65 if animation_slot(spec.effect_id) == "出场" else 0.35
        else:
            sample_at = 1.0
        # Compare the same project frame in both encodes. A half-frame preview
        # start (e.g. 0.65 s at 30 fps) can make FFmpeg choose adjacent frames;
        # this looks like a large visual mismatch for genuine motion effects.
        fps = float(project.sequence.fps.to_fraction())
        target_frame = round(sample_at * fps)
        start_frame = max(0, target_frame - round(0.4 * fps))
        preview_start = start_frame / fps
        sample_at = (target_frame + 0.5) / fps
        exported, preview = temp / "export.mp4", temp / "preview.mp4"
        export_report = renderer.render(project, str(exported), quality="low")
        preview_report = renderer.render_preview_window(
            project, str(preview), preview_start, 0.9)
        export_frame, preview_frame = temp / "export.png", temp / "preview.png"
        _frame(exported, sample_at, export_frame)
        _frame(preview, sample_at - preview_start, preview_frame)
        with Image.open(export_frame) as a, Image.open(preview_frame) as b:
            diff = ImageStat.Stat(ImageChops.difference(a.convert("RGB"), b.convert("RGB"))).mean
            preview_difference = sum(diff) / len(diff)
        if preview_difference >= 5:
            raise RuntimeError(f"{spec.id}: preview/export diff={preview_difference:.2f}")
        return {
            "presetId": spec.id,
            "generator": "scripts/audit_effect_presets.py",
            "source": ("generated transparent person-shaped video over a dark neutral stage"
                       if is_person_fx else "generated geometric artwork; no external media"),
            "commands": ["builtinPreset.apply", "history.undo", "history.redo"]
                        + (["effect.update"] if control else []),
            "savedRevision": saved_revision,
            "editableControl": {"name": control[0], "value": control[1]} if control else None,
            "exportDurationSeconds": export_report["duration"],
            "previewDurationSeconds": preview_report["duration"],
            "previewExportMeanRgbDifference": round(preview_difference, 3),
            "exportSha256": _sha(exported),
            "checks": ["apply", "saveReload", "undo", "export"]
                      + (["edit"] if control else []),
            "exportPath": exported,
        }


def main(family: str | None = None, preset_id: str | None = None,
         *, include_approved: bool = False, preset_ids: list[str] | None = None) -> None:
    manifest = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = {entry["presetId"]: entry for entry in manifest["presets"]}
    specs = [item for item in PresetCatalog.builtin(default_registry()).all()
             if item.family in FAMILIES and (item.status == "candidate" or include_approved)
             and (not preset_ids or item.id in preset_ids)
             and (family is None or item.family == family)
             and (preset_id is None or item.id == preset_id)]
    if not specs:
        raise SystemExit("no matching candidates")
    renderer = RenderService()
    failures: list[tuple[str, str]] = []
    for spec in specs:
        slug = spec.id.removeprefix("cutvoke.preset.").replace(".", "-")
        stem = f"{slug}-v{spec.version.replace('.', '-')}"
        audit_file = ASSETS / f"{stem}.audit.json"
        audit_video = ASSETS / f"{stem}.audit.mp4"
        try:
            with tempfile.TemporaryDirectory(prefix="cutvoke-effect-audit-") as temporary:
                result = _audit(spec, Path(temporary), renderer)
                exported = Path(result.pop("exportPath"))
                shutil.copyfile(exported, audit_video)
                result["auditExportSha256"] = _sha(audit_video)
                result["auditExport"] = audit_video.name
                audit_file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
            relative = "../assets/preset_previews/" + audit_file.name
            entry = entries[spec.id]
            for check in ("apply", "edit", "saveReload", "undo", "export"):
                entry.setdefault("checks", {})[check] = check in result["checks"]
                if check in result["checks"]:
                    entry.setdefault("evidence", {})[check] = relative
                else:
                    entry.setdefault("evidence", {}).pop(check, None)
            print(f"{spec.id}: export/preview diff={result['previewExportMeanRgbDifference']:.2f}, "
                  f"editable={bool(result['editableControl'])}")
        except Exception as error:
            # A new failed audit invalidates an older successful report for the
            # same preset; the catalog must describe the current renderer.
            entry = entries[spec.id]
            for check in ("apply", "edit", "saveReload", "undo", "export"):
                entry.setdefault("checks", {})[check] = False
                entry.setdefault("evidence", {}).pop(check, None)
            failures.append((spec.id, str(error)))
            print(f"FAILED {spec.id}: {error}", file=sys.stderr)
    CATALOG.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Audited {len(specs) - len(failures)}/{len(specs)} candidates; "
          "visualDistinct and approval unchanged")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=FAMILIES)
    parser.add_argument("--preset-id")
    parser.add_argument("--preset-ids", nargs="+")
    parser.add_argument("--include-approved", action="store_true",
                        help="Reaudit existing presets; does not grant visual approval")
    args = parser.parse_args()
    main(args.family, args.preset_id, include_approved=args.include_approved,
         preset_ids=args.preset_ids)
