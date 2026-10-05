"""Audit each sticker through real edit, history, persistence and render paths.

This records machine observations only; distinctness and artistic quality need
a separate visual decision before an item is counted as qualified content.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from cutvoke.core.builtin_stickers import PREVIEW_ROOT, load_builtin_stickers
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore


CHECKS = ("apply", "edit", "saveReload", "undo", "export")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seconds(value: int) -> dict[str, str]:
    return {"num": str(value), "den": "1"}


def _command(service: EditService, project_id: str, kind: str,
             payload: dict, serial: int) -> None:
    project = service.get_project(project_id)
    service.execute(Command(
        type=kind, payload=payload, command_id=f"sticker-audit-{serial}",
        project_id=project_id, expected_revision=project.revision,
        actor=Actor("agent", "sticker-audit"),
    ))


def _frame(video: Path, time: float, output: Path) -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(time), "-i", str(video), "-frames:v", "1", str(output),
    ], check=True, capture_output=True)


def _visible_pixels(left: Image.Image, right: Image.Image) -> int:
    return sum(1 for pixel in ImageChops.difference(left, right).get_flattened_data()
               if max(pixel) > 20)


def _audit(sticker: dict, temp: Path, renderer: RenderService) -> dict:
    project_id = "audit-sticker"
    background = temp / "background.png"
    background_mode = "light-solid"
    if sticker["subcategory"] in {
            "特效贴图", "能量光效", "电影叠加", "复古拼贴", "自然光影", "产品广告"}:
        base = Image.new("RGB", (320, 180))
        pixels = []
        for y in range(180):
            vertical = y / 179
            for x in range(320):
                horizontal = x / 319
                dark = (30 + round(7 * vertical), 38 + round(8 * vertical),
                        52 + round(10 * vertical))
                light = (238 - round(10 * vertical), 229 - round(9 * vertical),
                         210 - round(7 * vertical))
                pixels.append(tuple(round(dark[channel] * (1 - horizontal)
                                          + light[channel] * horizontal)
                                    for channel in range(3)))
        base.putdata(pixels)
        base.save(background)
        background_mode = "dark-to-light-gradient"
    else:
        Image.new("RGB", (320, 180), (229, 237, 245)).save(background)
    initial_scale = sticker.get("defaultScale", 0.46)
    scale_multiplier = 1.5 if background_mode == "dark-to-light-gradient" else 1.2
    edited_scale = round(initial_scale * scale_multiplier, 4)
    database = str(temp / "projects.sqlite")
    with ProjectStore(database) as store:
        service = EditService(store)
        service.create_project(project_id, width=320, height=180)
        _command(service, project_id, "clip.insert", {
            "trackId": "base", "createTrackKind": "video", "clipId": "background",
            "sourcePath": str(background), "timelineStart": _seconds(0),
            "timelineEnd": _seconds(2),
        }, 1)
        payload = {
            "trackId": "stickers", "createTrackKind": "video",
            "createTrackRole": "sticker", "role": "sticker", "clipId": "overlay",
            "assetId": sticker["assetId"], "sourcePath": sticker["path"],
            "timelineStart": _seconds(0), "timelineEnd": _seconds(2),
        }
        if sticker["kind"] == "dynamic":
            payload["stickerAnimation"] = {
                "effectId": sticker["effectId"], "params": sticker["params"]}
        _command(service, project_id, "clip.insert", payload, 2)
        project = service.get_project(project_id)
        overlay = project.sequence.tracks[1].clips[0]
        assert overlay.role == "sticker" and overlay.asset_ref.source_path == sticker["path"]
        if sticker["kind"] == "dynamic":
            assert any(effect["effectId"] == sticker["effectId"] for effect in overlay.effects)
        _command(service, project_id, "effect.update", {
            "clipId": "overlay", "effectId": "cutvoke.transform",
            "params": {"scale": initial_scale, "position": {"x": 119, "y": 49}},
        }, 3)
        baseline = temp / "baseline.png"
        renderer.extract_frame(service.get_project(project_id), 0.75, str(baseline))
        _command(service, project_id, "effect.update", {
            "clipId": "overlay", "effectId": "cutvoke.transform",
            "params": {"scale": edited_scale, "position": {"x": 82, "y": 33}},
        }, 4)
        edited = temp / "edited.png"
        renderer.extract_frame(service.get_project(project_id), 0.75, str(edited))
        with Image.open(baseline) as before, Image.open(edited) as after, \
                Image.open(background) as plain:
            edit_pixels = _visible_pixels(before.convert("RGB"), after.convert("RGB"))
            visible_pixels = _visible_pixels(after.convert("RGB"), plain.convert("RGB"))
        if edit_pixels < 100 or visible_pixels < 100:
            raise RuntimeError(f"{sticker['stickerId']}: edit={edit_pixels}, visible={visible_pixels}")
        _command(service, project_id, "history.undo", {}, 5)
        assert service.get_project(project_id).sequence.tracks[1].clips[0].effects[0]["params"]["scale"] == initial_scale
        _command(service, project_id, "history.redo", {}, 6)
        assert service.get_project(project_id).sequence.tracks[1].clips[0].effects[0]["params"]["scale"] == edited_scale
        for serial, effect_id in ((7, "cutvoke.anim.fadeIn"), (8, "cutvoke.anim.fadeOut")):
            _command(service, project_id, "effect.setAnimation", {
                "clipId": "overlay", "effectId": effect_id,
                "params": {"duration": 0.4},
            }, serial)
        overlay = service.get_project(project_id).sequence.tracks[1].clips[0]
        animation_ids = {item["effectId"] for item in overlay.effects}
        assert {"cutvoke.anim.fadeIn", "cutvoke.anim.fadeOut"} <= animation_ids
        _command(service, project_id, "history.undo", {}, 9)
        assert "cutvoke.anim.fadeOut" not in {
            item["effectId"] for item in service.get_project(project_id)
            .sequence.tracks[1].clips[0].effects}
        _command(service, project_id, "history.redo", {}, 10)

    with ProjectStore(database) as reopened:
        project = EditService(reopened).get_project(project_id)
        overlay = project.sequence.tracks[1].clips[0]
        assert overlay.role == "sticker"
        assert overlay.effects[0]["params"]["scale"] == edited_scale
        assert {"cutvoke.anim.fadeIn", "cutvoke.anim.fadeOut"} <= {
            item["effectId"] for item in overlay.effects}
        neutral_fades = copy.deepcopy(project)
        neutral_fade_overlay = next(
            clip for track in neutral_fades.sequence.tracks for clip in track.clips
            if clip.id == "overlay")
        # Keep the same entrance/exit lanes and frame boundaries so a separate
        # loop animation samples identical midpoints. Replace only opacity with
        # identity transforms; removing lanes changes loop sampling and can make
        # a pulsing sticker appear brighter despite its fade-out.
        for effect in neutral_fade_overlay.effects:
            if effect.get("effectId") == "cutvoke.anim.fadeIn":
                effect["effectId"] = "cutvoke.anim.zoomIn"
                effect["params"] = {"duration": 0.4, "fromScale": 1.0}
            elif effect.get("effectId") == "cutvoke.anim.fadeOut":
                effect["effectId"] = "cutvoke.anim.zoomOut"
                effect["params"] = {"duration": 0.4, "toScale": 1.0}
        animation_visibility = {}
        fade_visibility_comparison = {}
        for label, time in (("entry", 0.1), ("steady_entry", 0.55),
                            ("steady_exit", 1.45), ("exit", 1.9)):
            frame = temp / f"animation-{label}.png"
            reference = temp / f"animation-{label}-neutral-fade.png"
            renderer.extract_frame(project, time, str(frame))
            renderer.extract_frame(neutral_fades, time, str(reference))
            with (Image.open(frame) as rendered, Image.open(reference) as no_fade,
                  Image.open(background) as plain):
                animation_visibility[label] = _visible_pixels(
                    rendered.convert("RGB"), plain.convert("RGB"))
                no_fade_visibility = _visible_pixels(
                    no_fade.convert("RGB"), plain.convert("RGB"))
                fade_visibility_comparison[label] = {
                    "withFade": animation_visibility[label],
                    "withoutFade": no_fade_visibility,
                }
        if (sticker["kind"] == "static" and
                (animation_visibility["entry"] >= animation_visibility["steady_entry"] or
                 animation_visibility["exit"] >= animation_visibility["steady_exit"])):
            raise RuntimeError(
                f"{sticker['stickerId']}: entrance/exit fade did not change visibility "
                f"{animation_visibility}")
        if any(fade_visibility_comparison[label]["withFade"] >=
               fade_visibility_comparison[label]["withoutFade"]
               for label in ("entry", "exit")):
            raise RuntimeError(
                f"{sticker['stickerId']}: fades did not reduce same-frame visibility "
                f"{fade_visibility_comparison}")
        exported = temp / "export.mp4"
        preview = temp / "preview.mp4"
        renderer.render(project, str(exported), quality="low")
        renderer.render_preview_window(project, str(preview), 0, 1.7)
        differences = []
        for index, time in enumerate((0.55, 1.35)):
            export_frame, preview_frame = (temp / f"{kind}-{index}.png"
                                           for kind in ("export", "preview"))
            _frame(exported, time, export_frame)
            _frame(preview, time, preview_frame)
            with Image.open(export_frame) as left, Image.open(preview_frame) as right:
                differences.append(sum(ImageStat.Stat(ImageChops.difference(
                    left.convert("RGB"), right.convert("RGB"))).mean) / 3)
        if max(differences) >= 5:
            raise RuntimeError(f"{sticker['stickerId']}: preview/export mismatch {differences}")
    return {
        "schemaVersion": 1, "stickerId": sticker["stickerId"],
        "version": sticker["version"], "kind": sticker["kind"],
        "sourceSha256": _sha(Path(sticker["path"])),
        "previewSha256": _sha(Path(sticker["previewPath"])),
        "generator": "scripts/audit_sticker_library.py",
        "commands": ["clip.insert", "effect.update", "effect.setAnimation",
                     "history.undo", "history.redo"],
        "checks": list(CHECKS), "editedPixels": edit_pixels,
        "visiblePixels": visible_pixels,
        "animationIds": ["cutvoke.anim.fadeIn", "cutvoke.anim.fadeOut"],
        "animationDurationSeconds": 0.4, "auditBackgroundMode": background_mode,
        "animationVisibilityPixels": animation_visibility,
        "fadeVisibilityComparison": fade_visibility_comparison,
        "previewExportMeanRgbDifferences": [round(value, 3) for value in differences],
        "exportPath": exported,
    }


def main(sticker_id: str | list[str] | None = None,
         prefix: str | None = None) -> None:
    sticker_ids = ({sticker_id} if isinstance(sticker_id, str) else
                   set(sticker_id or ()))
    stickers = [item for item in load_builtin_stickers()
                if (not sticker_ids or item["stickerId"] in sticker_ids) and
                   (prefix is None or item["stickerId"].startswith(prefix))]
    if not stickers:
        raise ValueError(f"no sticker found: {sticker_id or prefix}")
    renderer = RenderService()
    for sticker in stickers:
        stem = sticker["stickerId"].rsplit(".", 1)[-1]
        with tempfile.TemporaryDirectory(prefix="cutvoke-sticker-audit-") as temporary:
            result = _audit(sticker, Path(temporary), renderer)
            exported = Path(result.pop("exportPath"))
            output_video = PREVIEW_ROOT / f"{stem}.audit.mp4"
            shutil.copyfile(exported, output_video)
            result["auditExport"] = output_video.name
            result["auditExportSha256"] = _sha(output_video)
            (PREVIEW_ROOT / f"{stem}.audit.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
        print(f"{sticker['stickerId']}: {result['visiblePixels']} visible pixels, "
              f"{result['editedPixels']} edited pixels")
    print(f"audited {len(stickers)} stickers; visual approval remains separate")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sticker-id", action="append",
                        help="Audit one sticker; repeat to audit a selected batch")
    parser.add_argument("--prefix")
    args = parser.parse_args()
    main(args.sticker_id, args.prefix)
