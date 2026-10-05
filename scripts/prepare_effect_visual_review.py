"""Build default-preset footage and explicit temporal sheets for visual review."""
from __future__ import annotations
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from verify_effects_real_projects import make_bases, command, save, sha
from cutvoke.core.preset_catalog import PresetCatalog
from cutvoke.core.effects import default_registry, animation_slot
from cutvoke.core.model import Project
from cutvoke.core.store import ProjectStore
from cutvoke.core.service import EditService
from cutvoke.core.render import RenderService

OLD = ROOT / "output/acceptance/effects-real-project-20261002"
OUT = ROOT / "output/acceptance/effects-visual-review-20261003"
FONT = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 18)
SMALL = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 13)

def ffmpeg(args):
    return subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *args],
                          capture_output=True, check=True, timeout=180)

def prepare_inputs():
    inputs = OUT / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    bases = make_bases(OLD)
    provenance = []
    paths = {}
    for p in bases.values():
        p.sequence.width, p.sequence.height = 640, 360
        for t in p.sequence.tracks:
            for c in t.clips:
                src = c.asset_ref.source_path
                if not src or t.kind != "video":
                    continue
                if src not in paths:
                    original = Path(src)
                    suffix = ".mov" if "selected-person" in src else ".png" if original.suffix.lower() == ".png" else ".mp4"
                    proxy = inputs / (original.stem + "-640" + suffix)
                    if not proxy.is_file():
                        if suffix == ".png":
                            ffmpeg(["-i", src, "-vf", "scale=640:360,setsar=1", "-frames:v", "1", str(proxy)])
                        elif suffix == ".mov":
                            ffmpeg(["-i", src, "-t", "4", "-vf", "scale=640:360,setsar=1", "-an",
                                    "-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le", str(proxy)])
                        else:
                            ffmpeg(["-i", src, "-t", "2", "-vf", "scale=640:360,setsar=1", "-an",
                                    "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", str(proxy)])
                    paths[src] = str(proxy)
                    provenance.append({"original": src, "sha256": sha(src), "proxy": str(proxy), "proxySha256": sha(proxy)})
                c.asset_ref.source_path = paths[src]
    save(OUT / "source-provenance.json", provenance)
    return bases

def read_frames(path, indices, size=(640, 360)):
    selected = sorted(set(indices))
    runs = []
    for n in selected:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    expression = "+".join(f"eq(n\\,{a})" if a == b else f"between(n\\,{a}\\,{b})" for a,b in runs)
    raw = ffmpeg(["-i", str(path), "-vf", f"select={expression},scale={size[0]}:{size[1]}",
                  "-an", "-fps_mode", "vfr", "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]).stdout
    stride = size[0] * size[1] * 3
    if len(raw) != stride * len(selected):
        raise RuntimeError(f"Frame count mismatch: {path}: {len(raw) // stride}/{len(selected)}")
    return {n: Image.frombytes("RGB", size, raw[k * stride:(k + 1) * stride]) for k,n in enumerate(selected)}

def sheet(record, indices, destination, *, dense=False, crop=False):
    frames = read_frames(record["videoPath"], indices)
    tw, th, columns = (160, 90, 8) if dense else (208, 117, 6)
    rows = (len(indices) + columns - 1) // columns
    header = 90
    im = Image.new("RGB", (columns * (tw + 4) + 8, header + rows * (th + 23) + 8), "#17202b")
    d = ImageDraw.Draw(im)
    d.text((8, 5), f'{record["name"]} | {record["sourceMode"]} | {record["caseId"]}', font=FONT, fill="white")
    d.text((8, 31), "默认预设参数 · 实际工程素材 · 时间从左到右，再从上到下", font=SMALL, fill="#c5d3e1")
    params = json.dumps(record["effects"], ensure_ascii=False, separators=(",", ":"))
    d.text((8, 53), params[:160], font=SMALL, fill="#b8c9de")
    for j,n in enumerate(indices):
        x, y = 4 + j % columns * (tw + 4), header + j // columns * (th + 23)
        frame = frames[n]
        if crop:
            frame = frame.crop((240, 95, 465, 360))
            frame.thumbnail((tw, th), Image.Resampling.LANCZOS)
            cell = Image.new("RGB", (tw, th), "#17202b")
            cell.paste(frame, ((tw - frame.width) // 2, (th - frame.height) // 2))
            frame = cell
        else:
            frame = frame.resize((tw, th), Image.Resampling.LANCZOS)
        im.paste(frame, (x, y))
        d.text((x, y + th + 1), f"f{n:03}  {n / 30:.3f}s", font=SMALL, fill="#d9e4ef")
    im.save(destination)

def build_case(spec, mode, base, renderer_hash):
    group = "transition-person" if spec.family in {"transition", "personFx"} else "fx-filter" if spec.family in {"fx", "filter"} else "animation-text"
    case_id = spec.id.replace(".", "_") + "-" + mode
    directory = OUT / "cases" / case_id
    directory.mkdir(parents=True, exist_ok=True)
    p = copy.deepcopy(base)
    p.project_id = "visual-default-" + case_id
    p.name = spec.name + " | 默认效果视觉检查"
    cid = "qa-person" if mode == "person" else next(t for t in p.sequence.tracks if t.kind == "text").clips[0].id if mode == "text" else p.sequence.tracks[0].clips[1 if spec.family == "transition" else 0].id
    with ProjectStore(str(directory / "project.sqlite")) as store:
        store.commit(p)
        service = EditService(store)
        command(service, p.project_id, "builtinPreset.apply", {"clipId": cid, "presetId": spec.id})
        p = service.get_project(p.project_id)
        save(directory / "project.json", p.to_dict())
    effects = next(c for t in p.sequence.tracks for c in t.clips if c.id == cid).effects
    path = directory / "default.mp4"
    record = {"caseId": case_id, "presetId": spec.id, "name": spec.name, "family": spec.family,
              "subcategory": spec.subcategory, "sourceMode": mode, "effects": effects,
              "group": group, "videoPath": str(path), "projectPath": str(directory / "project.json"),
              "rendererSha256": renderer_hash}
    record["parametersSha256"] = hashlib.sha256(json.dumps(effects, sort_keys=True).encode()).hexdigest()
    old = directory / "render.json"
    cached = json.loads(old.read_text(encoding="utf-8")) if old.is_file() else {}
    if not (path.is_file() and cached.get("rendererSha256") == renderer_hash
            and cached.get("parametersSha256") == record["parametersSha256"]):
        record["render"] = RenderService().render(p, str(path), quality="medium", overwrite=True)
        record["videoSha256"] = sha(path)
        save(old, record)
    else:
        record = json.loads(old.read_text(encoding="utf-8"))
    sheets = OUT / "sheets" / group
    sheets.mkdir(parents=True, exist_ok=True)
    overview = sheets / (case_id + "-overview.png")
    if spec.family == "transition":
        indices = [0, 15, 38, 42, 46, 50, 54, 57, 59, 61, 66, 75]
        dense_indices = list(range(37, 78))
    elif spec.family == "animation":
        indices = [0, 1, 2, 4, 8, 12, 18, 25, 35, 45, 54, 59]
        dense_indices = list(range(60))
    else:
        indices = [0, 2, 6, 12, 20, 30, 45, 60, 80, 100, 112, 119] if mode in {"person", "text"} else [0, 1, 3, 6, 12, 18, 25, 35, 45, 52, 57, 59]
        dense_indices = list(range(0, 30)) + list(range(90, 120)) if mode == "text" else list(range(12, 72)) if mode == "person" else list(range(60))
    sheet(record, indices, overview)
    record["overviewSheet"] = str(overview)
    if spec.family in {"animation", "transition", "personFx"} or spec.family == "fx" and spec.subcategory == "动感" or spec.family == "text" and spec.subcategory == "文字动画":
        dense = sheets / (case_id + "-dense.png")
        sheet(record, dense_indices, dense, dense=True)
        record["denseSheet"] = str(dense)
    if mode == "person":
        close = sheets / (case_id + "-person-detail.png")
        sheet(record, indices, close, crop=True)
        record["detailSheet"] = str(close)
    save(directory / "review-input.json", record)
    return record

def cleanup_inventory():
    OUT.mkdir(parents=True, exist_ok=True)
    redundant = {"check_alignment.py", "check_lossless.py", "check_overlay_threads.py", "check_preview_audio.py",
                 "check_preview_trim.py", "check_ui_history.py", "ui-evidence.raw.txt", "runtime-final-accepted.log",
                 "runtime-final.log", "runtime-rerun.log", "runtime-verified.log", "runtime.log",
                 "stickers-final-accepted.log", "stickers-runtime-final.log", "stickers-runtime-verified.log", "stickers-runtime.log"}
    items = []
    for path in OLD.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        reason = "未完成编码临时文件" if path.name.endswith(".tmp.mp4") else "一次性编码诊断视频" if path.name.startswith("preview-encoder2-") or path.name in {"preview-filter-trim.mp4", "debug.log"} else "已被最终结果替代的调试文件" if path.parent == OLD and path.name in redundant else None
        if reason:
            items.append({"path": str(path.resolve()), "bytes": path.stat().st_size, "reason": reason})
    files = [x for x in OLD.rglob("*") if x.is_file()]
    save(OUT / "cleanup-manifest.json", {"root": str(OLD.resolve()), "candidates": items,
         "filesBefore": len(files), "bytesBefore": sum(x.stat().st_size for x in files),
         "candidateCount": len(items), "candidateBytes": sum(x["bytes"] for x in items)})
    print(json.dumps({"cleanupCandidates": len(items), "candidateMiB": round(sum(x["bytes"] for x in items) / 2**20, 2)}), flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--families", nargs="*")
    args = parser.parse_args()
    if args.inventory_only or not (OUT / "cleanup-manifest.json").is_file():
        cleanup_inventory()
    if args.inventory_only:
        return
    bases = prepare_inputs()
    specs = PresetCatalog.builtin(default_registry()).all()
    jobs = []
    for spec in specs:
        if args.families and spec.family not in args.families:
            continue
        mode = "text" if spec.family == "text" else "person" if spec.family == "personFx" else "video"
        jobs.append((spec, mode))
        if spec.family in {"animation", "filter", "fx"} and "image" in spec.applies_to:
            jobs.append((spec, "image"))
    renderer_hash = sha(ROOT / "src/cutvoke/core/render.py")
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(build_case, spec, mode, bases[mode], renderer_hash):(spec,mode) for spec,mode in jobs}
        for future in as_completed(futures):
            spec,mode = futures[future]
            try:
                record = future.result()
                results.append(record)
                save(OUT / "manifest.json", results)
                print(json.dumps({"completed": len(results), "total":len(jobs), "caseId": record["caseId"]}), flush=True)
            except Exception as exc:
                print(json.dumps({"error":str(exc), "presetId":spec.id,"mode":mode}), flush=True)
                errors = OUT / "render-errors.jsonl"
                with errors.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"error":str(exc), "presetId":spec.id,"mode":mode}) + "\n")

if __name__ == "__main__":
    main()
