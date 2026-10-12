"""Render all dynamic stickers over actual Tide project footage for visual review."""
from __future__ import annotations

# Shared tooling paths; supports direct execution from any working directory.
import sys as _tool_sys
from pathlib import Path as _ToolPath
_TOOL_ROOT = next(p for p in _ToolPath(__file__).resolve().parents if (p / "pyproject.toml").is_file())
_tool_sys.path.insert(0, str(_TOOL_ROOT))
from scripts._paths import ARTIFACTS as _TOOL_ARTIFACTS
_tool_sys.path.insert(0, str(_TOOL_ROOT / "src"))
import copy
from pathlib import Path
import json
from scripts.verify.prepare_effect_visual_review import OUT, ROOT, prepare_inputs, sheet, sha, save
from scripts.verify.verify_effects_real_projects import Clip, AssetReference, Track, rat, load_builtin_stickers
from cutvoke.core.render import RenderService

def main():
    base = prepare_inputs()["video"]
    results = []
    folder = OUT / "dynamic-stickers"
    folder.mkdir(parents=True, exist_ok=True)
    for item in load_builtin_stickers():
        if item["kind"] != "dynamic":
            continue
        directory = folder / item["stickerId"].replace(".", "_")
        directory.mkdir(exist_ok=True)
        project = copy.deepcopy(base)
        project.project_id = "visual-motion-" + item["stickerId"]
        effects = [{"effectId":"cutvoke.transform", "params":{"scale":0.5,
                   "position":{"x":240,"y":95}}},
                   {"effectId":item["effectId"], "params":item["params"]}]
        project.sequence.tracks.append(Track(id="sticker",kind="video",role="sticker",clips=[
            Clip(id=item["stickerId"],asset_ref=AssetReference(item["assetId"],item["path"]),
                 timeline_start=rat(0),timeline_end=rat(4),source_start=rat(0),
                 role="sticker",effects=effects)]))
        save(directory / "project.json",project.to_dict())
        video = directory / "motion.mp4"
        cache=directory / "render.json"
        previous=json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else {}
        renderer_sha=sha(ROOT / "src/cutvoke/core/render.py")
        rendered=previous.get("render") if video.is_file() and previous.get("rendererSha256")==renderer_sha else None
        if not rendered:
            rendered = RenderService().render(project,str(video),quality="medium",overwrite=True)
        record = {"caseId":item["stickerId"],"name":item["name"],"sourceMode":"dynamic-sticker",
                  "effects":effects,"videoPath":str(video),"render":rendered,
                  "sourceSha256":sha(item["path"]),"videoSha256":sha(video),
                  "rendererSha256":sha(ROOT / "src/cutvoke/core/render.py")}
        overview=directory / "overview.png"
        dense=directory / "dense.png"
        sheet(record,[0,5,10,15,20,30,45,60,75,90,105,119],overview)
        sheet(record,list(range(120)),dense,dense=True)
        record.update(overviewSheet=str(overview),denseSheet=str(dense))
        save(directory / "render.json",record)
        results.append(record)
        save(folder / "manifest.json",results)
        print(f"rendered {len(results)}/8: {item['name']}",flush=True)

if __name__=="__main__":
    main()
