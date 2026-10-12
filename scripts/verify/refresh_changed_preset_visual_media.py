"""Rebuild affected library previews and machine evidence after renderer fixes.

This does not grant visual approval. Updated samples must actually be viewed
before their approval records are renewed.
"""

# Shared tooling paths; supports direct execution from any working directory.
import sys as _tool_sys
from pathlib import Path as _ToolPath
_TOOL_ROOT = next(p for p in _ToolPath(__file__).resolve().parents if (p / "pyproject.toml").is_file())
_tool_sys.path.insert(0, str(_TOOL_ROOT))
from scripts._paths import ARTIFACTS as _TOOL_ARTIFACTS
_tool_sys.path.insert(0, str(_TOOL_ROOT / "src"))
from pathlib import Path
import json
from scripts.assets.generate_preset_previews import main as generate
from scripts.verify.audit_effect_presets import main as audit

ROOT=_TOOL_ROOT
OUT=ROOT / ".artifacts/acceptance/effects-visual-review-20261003"
def main():
    presets=json.loads((ROOT / "src/cutvoke/core/builtin_presets.json").read_text(encoding="utf-8"))["presets"]
    extra={"cutvoke.preset.transition.squeeze","cutvoke.preset.transition.squeezev",
           "cutvoke.preset.fx.circleMask","cutvoke.preset.fx.centerCutout",
           "cutvoke.preset.fx.circleAperture","cutvoke.preset.fx.boldPoster"}
    ids=[p["presetId"] for p in presets if p["presetId"].startswith("cutvoke.preset.animation.")
         or p["presetId"] in extra]
    assert len(ids)==38,len(ids)
    (OUT / "library-media-refresh.json").write_text(json.dumps({"presetIds":ids,"visualApprovalRenewed":False},indent=2),encoding="utf-8")
    generate(include_approved=True,preset_ids=ids)
    audit(include_approved=True,preset_ids=ids)

if __name__=="__main__":
    main()
