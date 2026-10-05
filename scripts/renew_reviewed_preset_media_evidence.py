"""Renew only approvals supported by actual refreshed-library visual records."""
import json
from prepare_effect_visual_review import ROOT,OUT,save,sha
from cutvoke.core.preset_catalog import PresetCatalog

def main():
    media={r["caseId"]:r for r in json.loads((OUT / "library-review/manifest.json").read_text(encoding="utf-8"))}
    reviews=[]
    for name in ("animation-library.json","fx-filter-library.json","transition-library-review.json"):
        value=json.loads((OUT / "reviews" / name).read_text(encoding="utf-8"))
        reviews.extend(value.get("records",value.get("cases",[])))
    by_id={r.get("presetId",r.get("caseId")):r for r in reviews}
    assert set(by_id)==set(media),(len(by_id),len(media))
    catalog_path=ROOT / "src/cutvoke/core/builtin_presets.json"
    document=json.loads(catalog_path.read_text(encoding="utf-8"))
    specs={s.id:s for s in PresetCatalog.builtin().all()}
    updates=[]
    for entry in document["presets"]:
        preset_id=entry["presetId"]
        if preset_id not in media: continue
        review=by_id[preset_id]
        assert review["status"]=="pass",review
        spec=specs[preset_id]
        video=spec.asset_root / spec.motion_preview
        cover=spec.asset_root / spec.cover
        assert sha(video)==media[preset_id]["videoSha256"]
        assert sha(cover)==media[preset_id]["coverSha256"]
        if review.get("videoSha256"):
            assert review["videoSha256"]==sha(video),(preset_id,"reviewed video changed")
        if review.get("coverSha256"):
            assert review["coverSha256"]==sha(cover),(preset_id,"reviewed cover changed")
        for path,digest in review.get("evidenceSha256",{}).items():
            assert sha(path)==digest,(preset_id,"reviewed sheet changed",path)
        observation=review.get("visualConclusion",review.get("visualJudgment",review.get("observation","")))
        assert len(observation)>=8,review
        audit=spec.asset_root / spec.evidence["export"]
        record={"schemaVersion":1,"decision":"approved","presetId":preset_id,"version":spec.version,
                "effectId":spec.effect_id,"params":spec.params,"subcategory":spec.subcategory,
                "reviewer":"Codex actual image visual inspection","reviewedAt":"2026-10-03",
                "observation":observation,"method":"Actual view_image review of chronological regenerated library frames and corresponding real-project default cases",
                "coverSha256":sha(cover),"motionPreviewSha256":sha(video),"auditSha256":sha(audit),
                "realProjectReviewDirectory":str(OUT / "reviews"),"libraryReviewSheet":media[preset_id]["overviewSheet"],
                "libraryReviewSheetSha256":sha(media[preset_id]["overviewSheet"])}
        save(spec.asset_root / spec.evidence["visualDistinct"],record)
        entry["checks"]["visualDistinct"]=True
        entry["status"]="approved"
        updates.append(preset_id)
    save(catalog_path,document)
    save(OUT / "library-approval-renewal.json",{"updatedCount":len(updates),"presetIds":updates})
    print(f"renewed {len(updates)} actually reviewed library approvals")

if __name__=="__main__":main()
