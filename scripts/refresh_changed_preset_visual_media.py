"""Rebuild affected library previews and machine evidence after renderer fixes.

This does not grant visual approval. Updated samples must actually be viewed
before their approval records are renewed.
"""
from pathlib import Path
import json
from generate_preset_previews import main as generate
from audit_effect_presets import main as audit

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT / "output/acceptance/effects-visual-review-20261003"
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
