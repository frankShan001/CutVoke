"""Check current effects against copies of existing local editing projects.

The source snapshots and media stay read-only. Evidence belongs to this run;
this script never changes the built-in catalogs or their approval records.
"""
from __future__ import annotations

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from PIL import Image, ImageChops, ImageDraw, ImageStat

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cutvoke.core.builtin_stickers import load_builtin_stickers
from cutvoke.core.effects import animation_slot, default_registry
from cutvoke.core.model import AssetReference, Clip, Project, Track
from cutvoke.core.preset_catalog import PresetCatalog
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService, QUALITY_PRESETS
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore
from audit_effect_presets import _editable_control

# A fresh preview starts a new GOP. Compare lossless encodes as a diagnostic
# when compression alone exceeds the RGB limit, without changing real presets.
QUALITY_PRESETS["qa-lossless"] = {"crf": "0", "preset": "veryfast"}


class LosslessDiagnosticRenderer(RenderService):
    def _run_ffmpeg(self, seq, graph, inputs, out_path, quality, cancel_event, has_audio, **kwargs):
        return super()._run_ffmpeg(seq, graph, inputs, out_path, "qa-lossless", cancel_event, has_audio, **kwargs)


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rat(value):
    return Rational.of(value)


def clip(project, cid):
    return next(c for t in project.sequence.tracks for c in t.clips if c.id == cid)


def command(service, pid, kind, payload):
    p = service.get_project(pid)
    service.execute(Command(type=kind, payload=payload, command_id=uuid.uuid4().hex,
                           project_id=pid, expected_revision=p.revision,
                           actor=Actor("agent", "real-project-effect-qa")))


def frame(video, t, destination):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss",
                    str(t), "-i", str(video), "-frames:v", "1", str(destination)],
                   check=True, capture_output=True, timeout=90)


def difference(a, b):
    with Image.open(a) as left, Image.open(b) as right:
        d = ImageChops.difference(left.convert("RGB"), right.convert("RGB"))
        return sum(ImageStat.Stat(d).mean) / 3


def excerpt(snapshot, *, width=320, height=180, seconds=2):
    p = Project.from_dict(json.loads(snapshot.read_text(encoding="utf-8-sig")))
    p.project_id = "qa-" + snapshot.stem.replace(".original", "")
    p.name += " | 特效验证副本"
    p.revision = "0"
    seq = p.sequence
    old_width = seq.width
    seq.width, seq.height = width, height
    seq.captions, seq.markers = [], []
    visual = next(t for t in seq.tracks if t.kind == "video" and len(t.clips) >= 2)
    visual.clips = visual.clips[:2]
    for i, c in enumerate(visual.clips):
        c.timeline_start, c.timeline_end = rat(i * seconds), rat((i + 1) * seconds)
        c.effects, c.keyframes = [], {}
        c.fade_in, c.fade_out = rat(0), rat(0)
    tracks = [visual]
    audio = next((t for t in seq.tracks if t.kind == "audio" and t.clips), None)
    if audio:
        audio.clips = audio.clips[:1]
        audio.clips[0].timeline_start, audio.clips[0].timeline_end = rat(0), rat(seconds * 2)
        tracks.append(audio)
    title = next((t for t in seq.tracks if t.kind == "text" and t.clips), None)
    if title:
        title.clips = title.clips[:1]
        c = title.clips[0]
        c.timeline_start, c.timeline_end = rat(0), rat(seconds * 2)
        c.keyframes = {}
        for e in c.effects:
            if e["effectId"] == "cutvoke.text":
                e["params"]["fontSize"] = max(12, round(e["params"].get("fontSize", 48) * width / old_width))
                e["params"]["animIn"] = e["params"]["animOut"] = 0
        tracks.append(title)
    seq.tracks = tracks
    p.sequences = [seq]
    for t in seq.tracks:
        for c in t.clips:
            if c.asset_ref.source_path and not Path(c.asset_ref.source_path).is_file():
                raise FileNotFoundError(c.asset_ref.source_path)
    return p


def make_bases(output):
    master = output / "tide-gallery-master-20260927.original.json"
    video = excerpt(master)
    image = copy.deepcopy(video)
    raw = Project.from_dict(json.loads((output / "tide-gallery-20260927.original.json").read_text(encoding="utf-8-sig")))
    originals = next(t for t in raw.sequence.tracks if t.id == "visual").clips
    for c, original in zip(image.sequence.tracks[0].clips, originals):
        c.asset_ref = copy.deepcopy(original.asset_ref)
    person = copy.deepcopy(video)
    person_path = ROOT / "output/acceptance/jy-r20-person-sam2-20260926/sam2-selected-person.mov"
    if not person_path.is_file():
        raise FileNotFoundError(person_path)
    person.sequence.tracks.insert(1, Track(id="qa-person-track", kind="video", clips=[
        Clip(id="qa-person", asset_ref=AssetReference("qa-person", str(person_path)),
             timeline_start=rat(0), timeline_end=rat(4), source_start=rat(0))]))
    # Keep all visual effect comparisons independent of the existing title.
    for p in (video, image, person):
        p.sequence.tracks = [t for t in p.sequence.tracks if t.kind != "text"]
    text = excerpt(master)
    return {"video": video, "image": image, "person": person, "text": text}


def audit_preset(spec, mode, base, output, renderer):
    folder = output / "presets" / (spec.id.replace(".", "_") + "-" + mode)
    folder.mkdir(parents=True, exist_ok=True)
    result_file = folder / "result.json"
    if result_file.is_file():
        result = json.loads(result_file.read_text(encoding="utf-8"))
        if result.get("passed") and result.get("rendererSourceSha256") == renderer.qa_source_sha:
            return result
    p = copy.deepcopy(base)
    p.project_id += "-" + folder.name
    target_id = ("qa-person" if mode == "person" else
                 next(t for t in p.sequence.tracks if t.kind == "text").clips[0].id if mode == "text" else
                 p.sequence.tracks[0].clips[1 if spec.family == "transition" else 0].id)
    db = folder / "project.sqlite"
    with ProjectStore(str(db)) as store:
        store.commit(p)
        service = EditService(store)
        before = copy.deepcopy(clip(p, target_id).effects)
        command(service, p.project_id, "builtinPreset.apply", {"clipId": target_id, "presetId": spec.id})
        applied = clip(service.get_project(p.project_id), target_id)
        primary = next(e for e in applied.effects if e["effectId"] == spec.effect_id)
        assert primary["presetId"] == spec.id
        command(service, p.project_id, "history.undo", {})
        assert clip(service.get_project(p.project_id), target_id).effects == before
        command(service, p.project_id, "history.redo", {})
        control = _editable_control(spec, primary.get("params") or {})
        if control:
            name, value = control
            original = primary["params"][name]
            command(service, p.project_id, "effect.update", {"clipId": target_id,
                    "effectId": spec.effect_id, "params": {name: value}})
            assert next(e for e in clip(service.get_project(p.project_id), target_id).effects if e["effectId"] == spec.effect_id)["params"][name] == value
            command(service, p.project_id, "history.undo", {})
            assert next(e for e in clip(service.get_project(p.project_id), target_id).effects if e["effectId"] == spec.effect_id)["params"][name] == original
            command(service, p.project_id, "history.redo", {})
        expected = service.get_project(p.project_id).to_dict()
    with ProjectStore(str(db)) as store:
        reopened = EditService(store).get_project(p.project_id)
        assert reopened.to_dict() == expected
        save(folder / "project.json", reopened.to_dict())
        exported, preview = folder / "export.mp4", folder / "preview.mp4"
        renderer.render(reopened, str(exported), quality="low", overwrite=True)
        sample = (2.25 if spec.family == "transition" else
                  1.65 if spec.family == "animation" and animation_slot(spec.effect_id) == "出场" else
                  0.35 if spec.family == "animation" else 1.0)
        fps = float(reopened.sequence.fps.to_fraction())
        target_frame = round(sample * fps)
        start = max(0, target_frame - 12) / fps
        sample = (target_frame + 0.5) / fps
        preview_report = renderer.render_preview_window(reopened, str(preview), start, 0.8)
        for name, path, t in (("export", exported, sample), ("preview", preview, sample - start),
                              ("baseline", output / f"baseline-{mode}.mp4", sample)):
            frame(path, t, folder / f"{name}.png")
        match = difference(folder / "export.png", folder / "preview.png")
        changed = difference(folder / "export.png", folder / "baseline.png")
        diagnostic = None
        if match >= 5:
            diagnostic_renderer = LosslessDiagnosticRenderer()
            diagnostic_renderer.render(reopened, str(folder / "lossless-export.mp4"), quality="low", overwrite=True)
            diagnostic_renderer.render_preview_window(reopened, str(folder / "lossless-preview.mp4"), start, 0.8)
            frame(folder / "lossless-export.mp4", sample, folder / "lossless-export.png")
            frame(folder / "lossless-preview.mp4", sample - start, folder / "lossless-preview.png")
            diagnostic = difference(folder / "lossless-export.png", folder / "lossless-preview.png")
            if diagnostic >= 0.1:
                raise RuntimeError(f"preview/export difference lossy={match:.3f}, lossless={diagnostic:.3f}")
        result = {"presetId": spec.id, "name": spec.name, "family": spec.family,
                  "subcategory": spec.subcategory, "sourceMode": mode,
                  "checks": ["apply", "undo", "redo", "saveReload", "preview", "export"],
                  "editedControl": dict(zip(("name", "value"), control)) if control else None,
                  "previewExportMeanRgbDifference": round(match, 4),
                  "losslessDiagnosticMeanRgbDifference": round(diagnostic, 4) if diagnostic is not None else None,
                  "previewRetryCount": preview_report.get("retryCount", 0),
                  "effectBaselineMeanRgbDifference": round(changed, 4),
                  "exportPath": str(exported), "exportSha256": sha(exported), "passed": True}
        result["rendererSourceSha256"] = renderer.qa_source_sha
        result["previewRecoveryMode"] = preview_report.get("recoveryMode")
        save(result_file, result)
        return result


def audit_sticker_commands(stickers, base, output):
    results = []
    folder = output / "stickers"
    folder.mkdir(exist_ok=True)
    db = folder / "commands.sqlite"
    with ProjectStore(str(db)) as store:
        for i, s in enumerate(stickers):
            try:
                p = copy.deepcopy(base)
                p.project_id = f"qa-sticker-{i}"
                store.commit(p)
                service = EditService(store)
                payload = {"trackId": "qa-stickers-track", "createTrackKind": "video", "createTrackRole": "sticker",
                           "role": "sticker", "clipId": "qa-sticker", "assetId": s["assetId"],
                           "sourcePath": s["path"], "timelineStart": rat(0).to_json(), "timelineEnd": rat(2).to_json()}
                if s["kind"] == "dynamic":
                    payload["stickerAnimation"] = {"effectId": s["effectId"], "params": s["params"]}
                command(service, p.project_id, "clip.insert", payload)
                command(service, p.project_id, "effect.update", {"clipId": "qa-sticker", "effectId": "cutvoke.transform",
                       "params": {"scale": 0.25, "position": {"x": 42, "y": 31}}})
                command(service, p.project_id, "history.undo", {})
                command(service, p.project_id, "history.redo", {})
                current = clip(service.get_project(p.project_id), "qa-sticker")
                assert current.role == "sticker" and current.transform()["scale"] == 0.25
                if s["kind"] == "dynamic":
                    assert any(e["effectId"] == s["effectId"] for e in current.effects)
                expected = service.get_project(p.project_id).to_dict()
                assert store.load(p.project_id).to_dict() == expected
                results.append({"stickerId": s["stickerId"], "kind": s["kind"], "subcategory": s["subcategory"],
                                "passed": True, "sourceSha256": sha(s["path"]), "projectId": p.project_id})
            except Exception as exc:
                results.append({"stickerId": s["stickerId"], "passed": False, "error": str(exc)})
    # Reopen the database independently, without relying on EditService's cache.
    with ProjectStore(str(db)) as store:
        for r in results:
            if r["passed"]:
                assert clip(store.load(r["projectId"]), "qa-sticker").transform()["scale"] == 0.25
                r["checks"] = ["apply", "edit", "undo", "redo", "saveReload"]
    save(folder / "commands.json", results)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--skip-sticker-commands", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    renderer = RenderService()
    renderer.qa_source_sha = sha(ROOT / "src/cutvoke/core/render.py")
    bases = make_bases(output)
    for mode, p in bases.items():
        if not (output / f"baseline-{mode}.mp4").is_file():
            renderer.render(p, str(output / f"baseline-{mode}.mp4"), quality="low")
        save(output / f"baseline-{mode}.project.json", p.to_dict())
    specs = PresetCatalog.builtin(default_registry()).all()
    jobs = []
    for spec in specs:
        mode = "text" if spec.family == "text" else "person" if spec.family == "personFx" else "video"
        jobs.append((spec, mode))
        if spec.family in {"animation", "filter", "fx"} and "image" in spec.applies_to:
            jobs.append((spec, "image"))
    jobs = jobs[:args.limit] if args.limit else jobs
    results = []
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(audit_preset, spec, mode, bases[mode], output, renderer): (spec, mode)
                   for spec, mode in jobs}
        for future in as_completed(pending):
            spec, mode = pending[future]
            try:
                r = future.result()
            except Exception as exc:
                r = {"presetId": spec.id, "family": spec.family, "sourceMode": mode,
                     "passed": False, "error": str(exc)}
            results.append(r)
            save(output / "preset-runtime.json", results)
            print(json.dumps({"completed": len(results), "total": len(jobs), "presetId": spec.id,
                              "mode": mode, "passed": r["passed"], "error": r.get("error"),
                              "elapsed": round(time.time() - started)}, ensure_ascii=True), flush=True)
    if not args.skip_sticker_commands:
        stickers = audit_sticker_commands(load_builtin_stickers(), bases["video"], output)
        print(f"Sticker command checks: {sum(r['passed'] for r in stickers)}/{len(stickers)}", flush=True)
    save(output / "preset-summary.json", {"cases": len(results), "passed": sum(r["passed"] for r in results),
         "failures": [r for r in results if not r["passed"]], "elapsedSeconds": round(time.time() - started),
         "sourceProject": "tide-gallery-master-20260927", "imageProject": "tide-gallery-20260927",
         "excerpt": "Two original scene clips, 2 seconds each, original music and title; 320x180, 30fps. Existing scene effects cleared to isolate the tested preset."})
    return 1 if any(not r["passed"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
