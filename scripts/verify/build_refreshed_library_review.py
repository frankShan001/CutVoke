"""Extract actual refreshed library sample frames; no approval is granted."""

# Shared tooling paths; supports direct execution from any working directory.
import sys as _tool_sys
from pathlib import Path as _ToolPath
_TOOL_ROOT = next(p for p in _ToolPath(__file__).resolve().parents if (p / "pyproject.toml").is_file())
_tool_sys.path.insert(0, str(_TOOL_ROOT))
from scripts._paths import ARTIFACTS as _TOOL_ARTIFACTS
_tool_sys.path.insert(0, str(_TOOL_ROOT / "src"))
from pathlib import Path
import json
from scripts.verify.prepare_effect_visual_review import OUT,ROOT,sheet,save,sha
from cutvoke.core.preset_catalog import PresetCatalog

def main():
    ids=set(json.loads((OUT / "library-media-refresh.json").read_text(encoding="utf-8"))["presetIds"])
    records=[]
    directory=OUT / "library-review"
    directory.mkdir(exist_ok=True)
    for spec in PresetCatalog.builtin().all():
        if spec.id not in ids: continue
        video=(spec.asset_root / spec.motion_preview).resolve()
        record={"caseId":spec.id,"name":spec.name,"sourceMode":"library-preview",
                "effects":list(spec.effects),"videoPath":str(video),"family":spec.family,
                "videoSha256":sha(video),"coverSha256":sha(spec.asset_root / spec.cover)}
        dest=directory / (spec.id.replace(".","_")+".png")
        sheet(record,[0,1,3,5,8,10,12,15,18,21,25,30],dest)
        record["overviewSheet"]=str(dest)
        records.append(record)
    save(directory / "manifest.json",records)
    print(f"library review sheets: {len(records)}")

if __name__=="__main__": main()
