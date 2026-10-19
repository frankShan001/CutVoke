"""Render and audit the travel title additions; never grants visual approval.

Uses EditService and RenderService for all edits and video rendering. PIL only
authors a plain test background and assembles decoded proof stills.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
import numpy as np
from PIL import Image, ImageDraw
from cutvoke.core.preset_catalog import PresetCatalog
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore
from scripts.assets.add_travel_title_presets import IDS

ASSETS = ROOT / "src/cutvoke/assets/preset_previews"
CATALOG = ROOT / "src/cutvoke/core/builtin_presets.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frame(renderer, path, at, output):
    subprocess.run([renderer.ffmpeg, "-v", "error", "-y", "-ss", str(at),
                    "-i", str(path), "-frames:v", "1", str(output)],
                   capture_output=True, check=True, timeout=30)
    return np.array(Image.open(output).convert("RGB")).astype(float)


def main():
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = {p["presetId"]: p for p in data["presets"]}
    catalog = {p.id: p for p in PresetCatalog.builtin().all()}
    renderer = RenderService()
    sheet = Image.new("RGB", (360 * 3, 640 * 3))
    ASSETS.mkdir(exist_ok=True)
    for column, pid in enumerate(IDS):
        spec = catalog[pid]
        if spec.status != "candidate":
            raise ValueError(f"refusing to invalidate already reviewed media: {pid}")
        stem = pid.removeprefix("cutvoke.preset.").replace(".", "-") + "-v1-0-0"
        audit_export = ASSETS / (stem + ".audit.mp4")
        preview = ASSETS / (stem + ".mp4")
        cover = ASSETS / (stem + ".png")
        with tempfile.TemporaryDirectory(prefix="cutvoke-travel-title-") as td:
            temp = Path(td)
            bg = temp / "stage.png"
            image = Image.new("RGB", (360, 640), (30, 52, 65))
            ImageDraw.Draw(image).rectangle((0, 360, 360, 640), fill=(84, 137, 143))
            image.save(bg)
            database = str(temp / "project.sqlite")
            serial = 0
            with ProjectStore(database) as store:
                service = EditService(store)
                service.create_project("proof", width=360, height=640)
                def command(kind, payload):
                    nonlocal serial
                    serial += 1
                    service.execute(Command(type=kind, payload=payload,
                        command_id=f"proof-{serial}", project_id="proof",
                        expected_revision=service.get_project("proof").revision,
                        actor=Actor("agent", "travel-title-audit")))
                def seconds(n):
                    return {"num": str(n), "den": "1"}
                command("clip.insert", dict(trackId="v", createTrackKind="video", clipId="bg",
                    sourcePath=str(bg), timelineStart=seconds(0), timelineEnd=seconds(3)))
                command("clip.insert", dict(trackId="t", createTrackKind="text", clipId="word",
                    textPresetId=pid, text={"content": spec.params["content"]},
                    timelineStart=seconds(0), timelineEnd=seconds(2)))
                applied = service.get_project("proof").sequence.tracks[1].clips[0]
                assert len(applied.effects) == 1
                original = dict(applied.effects[0]["params"])
                # One word change controls every rendered sublayer. Change a
                # real depth parameter as well, then verify persisted state.
                patch = {"content": "SEA YOU!" if column < 2 else "COFFEE FIRST!"}
                if column < 2:
                    patch.update(blockShadowX=-5, outerStrokeWidth=5)
                else:
                    patch.update(panelWidth=.8)
                command("effect.update", dict(clipId="word", effectId="cutvoke.text", params=patch))
                edited = dict(service.get_project("proof").sequence.tracks[1].clips[0].effects[0]["params"])
                command("history.undo", {})
                assert service.get_project("proof").sequence.tracks[1].clips[0].effects[0]["params"] == original
                command("history.redo", {})
            with ProjectStore(database) as store:
                project = EditService(store).get_project("proof")
                title = project.sequence.tracks[1].clips[0]
                assert title.effects[0]["params"] == edited
                edited_out = temp / "edited.mp4"
                renderer.render(project, str(edited_out), quality="high")
                edited_pixels = frame(renderer, edited_out, .75, temp / "edited.png")
                # Restore via normal history for a preview of the default
                # catalog style; do not alter the renderer's snapshot in place.
                service = EditService(store)
                serial += 1
                service.execute(Command(type="history.undo", payload={}, command_id=f"proof-{serial}",
                    project_id="proof", expected_revision=project.revision,
                    actor=Actor("agent", "travel-title-audit")))
                project = service.get_project("proof")
                report = renderer.render(project, str(audit_export), quality="high", overwrite=True)
                renderer.render_preview_window(project, str(preview), .5, 1.5)
                renderer.extract_frame(project, .75, str(cover))
                exported = frame(renderer, audit_export, .75, temp / "export.png")
                previewed = frame(renderer, preview, .25, temp / "preview.png")
                sought = np.array(Image.open(cover).convert("RGB")).astype(float)
                plain = frame(renderer, audit_export, 2.5, temp / "plain.png")
                diff = float(np.abs(exported - previewed).mean())
                seek_diff = float(np.abs(exported - sought).mean())
                pixels = int((np.abs(exported - plain).max(axis=2) > 20).sum())
                edit_pixels = int((np.abs(exported - edited_pixels).max(axis=2) > 20).sum())
                assert diff < 5 and seek_diff < 5 and pixels >= 80 and edit_pixels >= 80
                # A catalog card needs a readable landscape cover. Crop only
                # the actual seek-rendered still; keep the motion sample 9:16.
                yy, xx = np.where(np.abs(exported - plain).max(axis=2) > 20)
                cx, cy = (xx.min() + xx.max()) / 2, (yy.min() + yy.max()) / 2
                cw = min(360, max(xx.max() - xx.min() + 40, (yy.max() - yy.min() + 60) * 16 / 9))
                ch = cw * 9 / 16
                cx, cy = max(cw / 2, min(360 - cw / 2, cx)), max(ch / 2, min(640 - ch / 2, cy))
                with Image.open(cover) as rendered_cover:
                    rendered_cover.crop((round(cx - cw / 2), round(cy - ch / 2),
                        round(cx + cw / 2), round(cy + ch / 2))).resize(
                            (320, 180), Image.Resampling.LANCZOS).save(cover)
                sample_motion = []
                for row, at in enumerate((.125, .75, 1.5)):
                    sampled = frame(renderer, audit_export, at, temp / f"sample-{row}.png")
                    sheet.paste(Image.fromarray(sampled.astype("uint8")), (column * 360, row * 640))
                    sample_motion.append(sampled)
                motion_diff = float(np.abs(sample_motion[0] - sample_motion[1]).mean())
                assert motion_diff > .25
                if column < 2:
                    # Settled motion is small but measurable, not a still
                    # treatment with only an entrance animation.
                    assert float(np.abs(sample_motion[1] - sample_motion[2]).mean()) > .05
                audit = dict(presetId=pid, generator="scripts/verify/verify_travel_title_presets.py",
                    source="original generated two-tone stage and editable native title",
                    checks=["apply", "edit", "saveReload", "undo", "export"],
                    contentAfterReload=edited["content"], savedParams=edited,
                    previewExportMeanRgbDifference=round(diff, 4),
                    seekExportMeanRgbDifference=round(seek_diff, 4),
                    visibleTitlePixels=pixels, editedPixels=edit_pixels,
                    exportDurationSeconds=report["duration"],
                    auditExport=audit_export.name, auditExportSha256=sha(audit_export))
                audit_file = ASSETS / (stem + ".audit.json")
                audit_file.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                qa_file = ASSETS / (stem + ".qa.json")
                qa_file.write_text(json.dumps(dict(presetId=pid,
                    generator="scripts/verify/verify_travel_title_presets.py", renderPath="RenderService",
                    coverSha256=sha(cover), previewSha256=sha(preview),
                    firstLastMeanRgbDifference=round(motion_diff, 4),
                    checks=["cover", "motionPreview"]), indent=2) + "\n", encoding="utf-8")
                entry = entries[pid]
                rel = "../assets/preset_previews/"
                entry.update(cover=rel + cover.name, motionPreview=rel + preview.name)
                for check in audit["checks"]:
                    entry["checks"][check] = True
                    entry["evidence"][check] = rel + audit_file.name
                for check in ("cover", "motionPreview"):
                    entry["checks"][check] = True
                    entry["evidence"][check] = rel + qa_file.name
                print(f"{pid}: visible={pixels}, edit={edit_pixels}, preview diff={diff:.4f}, seek diff={seek_diff:.4f}")
    proof = ROOT / "output/travel-title-library-20261019"
    proof.mkdir(parents=True, exist_ok=True)
    sheet.save(proof / "contact.png")
    CATALOG.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\r\n")


if __name__ == "__main__":
    main()
