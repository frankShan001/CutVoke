"""Render every current sticker over real project footage in labelled batches."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
from PIL import Image, ImageChops, ImageStat
from verify_effects_real_projects import (ROOT, LosslessDiagnosticRenderer, RenderService,
    AssetReference, Clip, Track, rat, save, sha, frame, make_bases, load_builtin_stickers)


def run(output):
    base = make_bases(output)["video"]
    base.sequence.width, base.sequence.height = 640, 360
    base.sequence.tracks[0].clips = base.sequence.tracks[0].clips[:1]
    for t in base.sequence.tracks:
        for c in t.clips:
            c.timeline_end = rat(2)
    folder = output / "sticker-renders"
    folder.mkdir(exist_ok=True)
    r = RenderService()
    renderer_sha = sha(ROOT / "src/cutvoke/core/render.py")
    baseline = folder / "baseline.mp4"
    r.render(base, str(baseline), quality="low", overwrite=True)
    frame(baseline, 0.55, folder / "baseline.png")
    plain = Image.open(folder / "baseline.png").convert("RGB")
    stickers = load_builtin_stickers()
    results = []
    for index in range(0, len(stickers), 8):
        batch = stickers[index:index + 8]
        directory = folder / f"batch-{index // 8:03}"
        directory.mkdir(exist_ok=True)
        result_file = directory / "result.json"
        if result_file.is_file():
            old = json.loads(result_file.read_text(encoding="utf-8"))
            if all(x.get("passed") and x.get("rendererSourceSha256") == renderer_sha for x in old):
                results.extend(old)
                continue
        p = copy.deepcopy(base)
        p.project_id = f"qa-sticker-render-{index // 8}"
        for slot, s in enumerate(batch):
            x, y = slot % 4 * 160 + 20, slot // 4 * 180 + 25
            effects = [{"effectId": "cutvoke.transform", "params":
                        {"scale": 0.32, "position": {"x": x, "y": y}}}]
            if s["kind"] == "dynamic":
                effects.append({"effectId": s["effectId"], "params": s["params"]})
            p.sequence.tracks.append(Track(id=f"stickers-{slot}", kind="video", role="sticker", clips=[
                Clip(id=s["stickerId"], asset_ref=AssetReference(s["assetId"], s["path"]),
                     timeline_start=rat(0), timeline_end=rat(2), source_start=rat(0),
                     effects=effects, role="sticker")]))
        save(directory / "project.json", p.to_dict())
        try:
            export, preview = directory / "export.mp4", directory / "preview.mp4"
            r.render(p, str(export), quality="low", overwrite=True)
            preview_report = r.render_preview_window(p, str(preview), 0.4, 0.8)
            frame(export, 0.55, directory / "export.png")
            frame(preview, 0.15, directory / "preview.png")
            a = Image.open(directory / "export.png").convert("RGB")
            b = Image.open(directory / "preview.png").convert("RGB")
            diff = sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3
            lossless = None
            if diff >= 5:
                d = LosslessDiagnosticRenderer()
                d.render(p, str(directory / "lossless-export.mp4"), quality="low", overwrite=True)
                d.render_preview_window(p, str(directory / "lossless-preview.mp4"), 0.4, 0.8)
                frame(directory / "lossless-export.mp4", 0.55, directory / "lossless-export.png")
                frame(directory / "lossless-preview.mp4", 0.15, directory / "lossless-preview.png")
                lossless = sum(ImageStat.Stat(ImageChops.difference(
                    Image.open(directory / "lossless-export.png").convert("RGB"),
                    Image.open(directory / "lossless-preview.png").convert("RGB"))).mean) / 3
                if lossless >= 0.1:
                    raise RuntimeError(f"Preview mismatch lossy={diff}, lossless={lossless}")
            group_results = []
            for slot, s in enumerate(batch):
                x, y = slot % 4 * 160, slot // 4 * 180
                region = (x + 15, y + 20, x + 140, y + 150)
                pixels = list(ImageChops.difference(a.crop(region), plain.crop(region)).getdata())
                visible = sum(max(pixel) > 20 for pixel in pixels)
                if visible < 40:
                    raise RuntimeError(f"Sticker not visible: {s['stickerId']}, pixels={visible}")
                group_results.append({"stickerId": s["stickerId"], "kind": s["kind"],
                    "subcategory": s["subcategory"], "sourceSha256": sha(s["path"]),
                    "visiblePixels": visible, "batch": index // 8, "slot": slot,
                    "previewExportMeanRgbDifference": round(diff, 4),
                    "losslessDiagnosticMeanRgbDifference": round(lossless, 4) if lossless is not None else None,
                    "previewRetryCount": preview_report.get("retryCount", 0),
                    "previewRecoveryMode": preview_report.get("recoveryMode"),
                    "exportPath": str(export), "exportSha256": sha(export), "passed": True})
                group_results[-1]["rendererSourceSha256"] = renderer_sha
            save(result_file, group_results)
            results.extend(group_results)
        except Exception as exc:
            group_results = [{"stickerId": s["stickerId"], "passed": False, "error": str(exc),
                              "batch": index // 8} for s in batch]
            save(result_file, group_results)
            results.extend(group_results)
        save(folder / "results.json", results)
        print(json.dumps({"completed": len(results), "total": len(stickers), "batch": index // 8,
                          "passed": all(x["passed"] for x in group_results),
                          "error": next((x.get("error") for x in group_results if not x["passed"]), None)}), flush=True)
    save(folder / "summary.json", {"total": len(results), "passed": sum(x["passed"] for x in results),
                                   "failures": [x for x in results if not x["passed"]],
                                   "sourceProject": "tide-gallery-master-20260927",
                                   "canvas": "640x360/30fps", "batchSize": 8, "durationSeconds": 2})
    return int(any(not x["passed"] for x in results))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    raise SystemExit(run(p.parse_args().output.resolve()))
