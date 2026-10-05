"""Bind actual review records to retained media and check full scope coverage."""
from pathlib import Path
import json,hashlib,collections,urllib.request
from prepare_effect_visual_review import ROOT,OUT,OLD,save,sha,PresetCatalog,default_registry

def read(p): return json.loads(p.read_text(encoding="utf-8-sig"))
def canonical(v): return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()

def main():
    manifest=read(OUT / "manifest.json")
    expected={r["caseId"]:r for r in manifest}
    assert len(expected)==281,len(expected)
    entries=[]
    for name in ("animation-text","fx-filter","transition-person"):
        data=read(OUT / "reviews" / (name+".json"))
        entries.extend(data.get("cases",data.get("records",[])))
    actual={r["caseId"]:r for r in entries}
    specs={s.id:s for s in PresetCatalog.builtin(default_registry()).all()}
    assert set(actual)==set(expected),{"missing":sorted(set(expected)-set(actual)),"extra":sorted(set(actual)-set(expected))}
    for item in entries:
        render=expected[item["caseId"]]
        assert item.get("status") not in {"pending","fail","error"},item
        video_sha=sha(render["videoPath"])
        recorded_sha=item.get("videoSha256",item.get("currentVideoSha256"))
        if recorded_sha:
            assert recorded_sha==video_sha,(item["caseId"],"reviewed video changed")
        for path,digest in item.get("evidenceSha256",{}).items():
            assert sha(path)==digest,(item["caseId"],"reviewed sheet changed",path)
        item.update(name=specs[render["presetId"]].name,family=render["family"],sourceMode=render["sourceMode"],
                    videoPath=render["videoPath"],videoSha256=video_sha,
                    rendererSha256=render["rendererSha256"],defaultParameters=render["effects"])
    save(OUT / "visual-default-ledger.json",entries)
    pages={r["page"]:r for r in read(OUT / "reviews/sticker-pages.json")["pages"]}
    stickers=read(OUT / "sticker-manifest.json")
    motion={r["stickerId"]:r for r in read(OUT / "reviews/dynamic-stickers.json")["records"]}
    motion_media={r["caseId"]:r for r in read(OUT / "dynamic-stickers/manifest.json")}
    notes={"cutvoke.sticker.pet_poodle":"主体外有孤立小色点", "cutvoke.sticker.pet_paw_ball":"主体外有孤立小色点",
           "cutvoke.sticker.printfx_curled_paper_corner":"源图底部有零碎小片", "cutvoke.sticker.printfx_vintage_label_frame":"源图底部有额外零碎线片"}
    source_results={r["stickerId"]:r for r in read(OLD / "sticker-renders/results.json")}
    for index,item in enumerate(stickers):
        page=pages[index//16]
        item.update(status="pass_with_note" if item["stickerId"] in notes else "pass",
                    visualConclusion=notes.get(item["stickerId"],page["notes"]),
                    sourceSha256=sha(item["source"]),reviewer="Codex",reviewedAt="2026-10-03")
        assert item["sourceSha256"]==source_results[item["stickerId"]]["sourceSha256"]
        if item["kind"]=="dynamic":
            item.update(motion[item["stickerId"]])
            item.update(motionEvidence=motion_media[item["stickerId"]])
        if item["stickerId"] in {"cutvoke.sticker.gift","cutvoke.sticker.gift_float"}:
            item.update(status="source_artwork_clipped",qualified=False,
                        visualConclusion="源 gift.png 的蝴蝶结顶部被截断，静态和动态版本均待修；运动功能正常。",
                        correction="放大源图及新库连续帧后纠正此前边缘完整的错误观察。")
    assert len(stickers)==612 and len(motion)==8
    save(OUT / "visual-sticker-ledger.json",stickers)
    originals=[]
    for path in sorted(OLD.glob("*.original.json")):
        before=read(path)
        with urllib.request.urlopen("http://127.0.0.1:8787/api/v1/projects/"+before["projectId"],timeout=10) as response:
            after=json.load(response)
        originals.append({"projectId":before["projectId"],"revisionBefore":before["revision"],"revisionAfter":after["revision"],
                          "beforeSha256":canonical(before),"afterSha256":canonical(after),"unchanged":before==after})
    save(OUT / "original-projects-unchanged.json",originals)
    assert all(r["unchanged"] for r in originals)
    decode=read(OUT / "strict-decode.json")
    assert decode["count"]==335 and decode["failed"]==0,decode
    for item in decode["results"]:
        assert sha(item["path"])==item["sha256"],("decoded media changed",item["path"])
    summary={"reviewedAt":"2026-10-03","presetCount":len({r["presetId"] for r in entries}),"defaultCases":len(entries),
             "defaultStatus":dict(collections.Counter(r["status"] for r in entries)),"stickerCount":len(stickers),
             "stickerStatus":dict(collections.Counter(r["status"] for r in stickers)),"dynamicMotionCases":len(motion),
             "sourceArtworkPending":[r["stickerId"] for r in stickers if r["status"]=="source_artwork_clipped"],
             "strictDecodedVideos":decode["count"],
             "originalProjectsUnchanged":True,"sourceRendererSha256":sha(ROOT / "src/cutvoke/core/render.py"),
             "retainedCaseRendererHashes":dict(collections.Counter(r["rendererSha256"] for r in entries))}
    save(OUT / "visual-summary.json",summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
