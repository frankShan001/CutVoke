"""Original-media Vlog effect comparisons over real stdio MCP.

Writes only a new isolated output folder. The comparison uses rendered pairs;
each of the four effect projects remains separately editable and packaged.
No downloaded footage, model, paid generation, user media or audio is used.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cutvoke.rendering.effects import default_registry
from cutvoke.rendering.vlog_fx import subject_lock_zoom

_spec = importlib.util.spec_from_file_location("editor_demo", ROOT / "skills/cutvoke-editor/scripts/demo.py")
_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_demo)
class MCP(_demo.MCP):
    """Handle normal MCP notifications without replaying an editing request."""
    def rpc(self, method, params):
        self.serial += 1
        request_id = self.serial
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id,
                                            "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + 180
        while True:
            line = self.responses.get(timeout=max(.01, deadline-time.monotonic()))
            if not line:
                raise RuntimeError("MCP closed; inspect mcp.log")
            response = json.loads(line)
            if "id" not in response:  # e.g. notifications/tools/list_changed
                continue
            if response["id"] != request_id:
                raise RuntimeError("Unexpected MCP response ID")
            if "error" in response:
                raise RuntimeError(response["error"])
            return response["result"]
W, H, FPS, SECONDS = 640, 360, 24, 4


def write_provenance(output):
    files = [p for p in sorted((output/"source-media").iterdir()) if p.suffix in (".png", ".mp4")]
    provenance = dict(creator="CutVoke demo_vlog_effects.py procedural illustration",
        license="MIT for original source illustrations and generator; bundled title fonts retain their OFL licenses.",
        externalMedia=False, audio=False,
        files=[dict(name=p.name, bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in files])
    (output/"source-media/provenance.json").write_text(json.dumps(provenance,indent=2)+"\n",encoding="utf-8")
    (output/"LICENSE.media.txt").write_text((ROOT/"LICENSE").read_text(encoding="utf-8"),encoding="utf-8")


def scene(variant=0):
    """Original flat illustration: layered mountains, lake and a coffee deck."""
    top, bottom = (((29, 55, 72), (233, 170, 115)) if variant == 0 else
                   ((34, 31, 69), (124, 135, 170)))
    pixels = np.empty((H, W, 3), np.uint8)
    for y in range(H):
        pixels[y] = [round(a+(b-a)*min(1,y/(H*.75))) for a,b in zip(top,bottom)]
    image = Image.fromarray(pixels)
    draw = ImageDraw.Draw(image)
    draw.ellipse((450, 45, 510, 105), fill=(255, 220, 156) if not variant else (240, 233, 206))
    draw.polygon([(0,210),(85,121),(174,191),(288,104),(405,197),(530,138),(640,209),
                  (640,360),(0,360)], fill=(70,97,113) if not variant else (66,70,112))
    draw.polygon([(0,244),(160,180),(302,239),(470,173),(640,244),(640,360),(0,360)], fill=(36,73,86))
    draw.rectangle((0,250,W,H), fill=(26,63,78))
    for i in range(17):
        y=259+i*5; x=(i*91+36)%W
        draw.line((x,y,min(W,x+95),y), fill=(72,102,112), width=1)
    draw.polygon([(0,302),(640,302),(640,360),(0,360)], fill=(165,111,78))
    for x in range(-80,700,85):
        draw.line((320+(x-320)*.84,302,x,360), fill=(122,80,59), width=2)
    draw.line((0,303,W,303),fill=(226,168,113),width=3)
    return image


def mug(image, x, y=.53, scale=1, angle=0, color=(245,201,122), steam=0):
    layer=Image.new("RGBA",(150,150))
    draw=ImageDraw.Draw(layer)
    draw.ellipse((25,110,130,133),fill=(12,26,35,55))
    draw.ellipse((92,45,128,89),outline=(*color,255),width=9)
    draw.rounded_rectangle((36,41,104,110),radius=13,fill=(*color,255))
    draw.ellipse((36,33,104,53),fill=(255,231,174))
    draw.ellipse((44,36,96,49),fill=(91,54,40))
    draw.rounded_rectangle((46,60,52,96),radius=3,fill=(255,233,187))
    for i in range(3):
        sx=55+i*15+3*math.sin(steam+i)
        draw.arc((sx,4,sx+12,32),30,180,fill=(255,240,207,130),width=2)
    layer=layer.rotate(angle,resample=Image.Resampling.BICUBIC)
    layer=layer.resize((round(150*scale),round(150*scale)),Image.Resampling.LANCZOS)
    # The tracked anchor is the cup body centre (70, 75), before rotation.
    px=round(x*(W-1)-layer.width/2); py=round(y*(H-1)-layer.height/2)
    result=image.convert("RGBA"); result.alpha_composite(layer,(px,py))
    return result.convert("RGB")


def encode(path, frame):
    command=["ffmpeg","-v","error","-nostdin","-f","rawvideo","-pix_fmt","rgb24",
             "-s",f"{W}x{H}","-r",str(FPS),"-i","pipe:0","-an","-c:v","libx264",
             "-crf","16","-pix_fmt","yuv420p",str(path)]
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for i in range(FPS*SECONDS):
            process.stdin.write(frame(i/FPS).tobytes())
        process.stdin.close()
        error=process.stderr.read().decode(errors="replace")
        if process.wait(timeout=60):
            raise RuntimeError(error)
    finally:
        if process.poll() is None: process.kill(); process.wait()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    sources=output/"source-media";sources.mkdir()
    pairs=output/"pairs";pairs.mkdir()
    projects=output/"projects";projects.mkdir()
    frames=output/"frames";frames.mkdir()
    (output/"data").mkdir()
    first,second=scene(),scene(1)
    encode(sources/"moving-cup.mp4",lambda t:mug(first,.35+.3*t/4,steam=t*2))
    encode(sources/"quiet-cup.mp4",lambda t:mug(first,.47,scale=1.05,steam=t*2))
    mug(first,.35,scale=.8,angle=-10).save(sources/"match-a.png")
    mug(second,.65,scale=1.12,angle=8,color=(149,220,215)).save(sources/"match-b.png")
    # The window creates real highlight structure; it is part of the source,
    # never a baked effect, transition matte or third-party texture.
    interior=first.copy();draw=ImageDraw.Draw(interior)
    draw.rectangle((38,20,243,255),fill=(245,222,174))
    draw.rectangle((48,30,233,245),fill=(245,236,204))
    draw.line((140,25,140,250),fill=(76,81,73),width=7)
    draw.line((43,145,238,145),fill=(76,81,73),width=7)
    mug(interior,.62,scale=1.1).save(sources/"luma-a.png")
    mug(second,.45,scale=1.1,color=(149,220,215)).save(sources/"luma-b.png")

    cases=[
      ("subject-lock","Manual subject lock","Two authored points / safe crop",["moving-cup.mp4"],
       "cutvoke.fx.subjectLock",{"fromX":.35,"fromY":.53,"toX":.65,"toY":.53,
          "targetX":.5,"targetY":.53,"duration":4,"zoom":1.6}),
      ("punch-in","Punch in, hold, return","A chosen point / one emphasis beat",["quiet-cup.mp4"],
       "cutvoke.fx.punchIn",{"anchorX":.47,"anchorY":.53,"start":.65,"enter":.25,
          "hold":1.25,"leave":.55,"zoom":1.5,"overshoot":.8}),
      ("match-align","Anchor match transition","Independent position / scale / angle",["match-a.png","match-b.png"],
       "cutvoke.transition.matchAlign",{"duration":.85,"aX":.35,"aY":.53,"bX":.65,"bY":.53,
          "aScale":1.5,"bScale":1.0714,"aAngle":-10,"bAngle":8,"mixWidth":.2}),
      ("luma-reveal","Content luma reveal","Actual source highlights / soft threshold",["luma-a.png","luma-b.png"],
       "cutvoke.transition.lumaReveal",{"duration":1,"softness":.09,"curve":"smooth"}),
    ]
    mcp=MCP([sys.executable,"-m","cutvoke"],output/"data/projects.sqlite",output,None)
    reports=[]; lease=None; project_id=None
    try:
        mcp.call("editor_help");runtime=mcp.call("runtime")
        assets=mcp.call("media_import",paths=[str(p) for p in sorted(sources.iterdir())])["assets"]
        imported={Path(a.get("name",a["path"])).name:a for a in assets}
        # media_import labels vary across API versions; match source hashes.
        imported={p.name:next(a for a in assets if hashlib.sha256(Path(a["path"]).read_bytes()).digest()==
                             hashlib.sha256(p.read_bytes()).digest()) for p in sources.iterdir()}
        r=lambda t:{"num":str(round(t*1000)),"den":"1000"}

        def apply(pid,kind,payload,lease_id=None):
            revision=mcp.call("project_summary",projectId=pid)["revision"]
            return mcp.call("command_apply",projectId=pid,expectedRevision=revision,type=kind,payload=payload,
                            commandId=f"{pid}-{revision}-{kind}",actorId="vlog-effects-demo",
                            **({"editLeaseId":lease_id} if lease_id else {}))

        for slug,title,note,media,effect_id,params in cases:
            project_id="vlog-"+slug
            mcp.call("create_project",projectId=project_id,name=title+" · editable effect",width=W,height=H,fps=FPS)
            lease=mcp.call("edit_lock",projectId=project_id,action="acquire",owner="vlog-effects-demo",ttlSeconds=120)["lease"]["leaseId"]
            edits=[]; length=4/len(media)
            for i,name in enumerate(media):
                edits.append({"type":"clip.insert","payload":{"trackId":"picture",
                    **({"createTrackKind":"video"} if i==0 else {}),"clipId":f"shot-{i}",
                    "assetId":imported[name]["assetId"],"timelineStart":r(i*length),"timelineEnd":r((i+1)*length)}})
            apply(project_id,"edit.batch",{"commands":edits},lease)
            revision=mcp.call("project_summary",projectId=project_id)["revision"]
            before_id=project_id+"-before"
            mcp.call("project_clone",projectId=project_id,expectedRevision=revision,newProjectId=before_id,name=title+" · original")
            kind="effect.setTransition" if len(media)==2 else "effect.add"
            payload={"clipId":f"shot-{len(media)-1}","effectId":effect_id,"params":params}
            mcp.call("editor_help",topic="command",command=kind,effectId=effect_id)
            result=apply(project_id,kind,payload,lease)
            revision=result["revision"]
            mcp.call("edit_lock",projectId=project_id,action="release",leaseId=lease);lease=None
            sample_dir=frames/slug;sample_dir.mkdir()
            mcp.call("preview_frames",projectId=project_id,expectedRevision=revision,
                     times=[.1,1,2.4,3.9],width=W,output_frames=sample_dir)
            preflight=apply(project_id,"project.preflight",{})["changedEntities"][0]["report"]
            if not preflight["readyToRender"]:raise RuntimeError(preflight)
            timings={}
            for pid,ending in ((before_id,"before"),(project_id,"after")):
                path=pairs/f"{slug}-{ending}.mp4"; began=time.monotonic()
                job=apply(pid,"export.enqueue",{"outPath":str(path),"quality":"high","overwrite":False})
                mcp.wait("export_job",pid,job["changedEntities"][0]["jobId"])
                timings[ending]=round(time.monotonic()-began,3)
                subprocess.run(["ffmpeg","-v","error","-i",str(path),"-f","null","-"],check=True,capture_output=True)
            mcp.call("project_package",projectId=project_id,expectedRevision=revision,
                     outPath=str(projects/(slug+".cvkpkg")))
            document=mcp.call("project_query",projectId=project_id)
            (projects/(slug+".json")).write_text(json.dumps(document,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            reports.append(dict(slug=slug,title=title,effectId=effect_id,params=default_registry().validate_params(effect_id,params),
                projectId=project_id,revision=revision,preflight=preflight,timingSeconds=timings))

        # A reusable comparison edit. Its four source effect projects above
        # preserve the editable algorithms; these presentation tiles use their
        # exported videos to keep each effect's coordinate system independent.
        bg=Image.new("RGB",(1280,720),(239,234,222));draw=ImageDraw.Draw(bg)
        draw.rectangle((0,0,1280,9),fill=(235,126,72))
        draw.rectangle((638,213,642,565),fill=(215,207,192))
        draw.rectangle((32,600,1248,602),fill=(207,200,186))
        bg.save(sources/"presentation.png")
        all_paths=[sources/"presentation.png"]+[pairs/f"{slug}-{ending}.mp4" for slug,*_ in cases for ending in ("before","after")]
        presentation_assets=mcp.call("media_import",paths=[str(p) for p in all_paths])["assets"]
        pid="vlog-comparison";project_id=pid
        mcp.call("create_project",projectId=pid,name="Vlog field notes · four editable effects",width=1280,height=720,fps=FPS)
        lease=mcp.call("edit_lock",projectId=pid,action="acquire",owner="vlog-effects-demo",ttlSeconds=120)["lease"]["leaseId"]
        edits=[{"type":"clip.insert","payload":{"trackId":"paper","createTrackKind":"video","clipId":"paper-clip",
                "assetId":presentation_assets[0]["assetId"],"timelineStart":r(0),"timelineEnd":r(16)}}]
        for index,(slug,title,note,*_) in enumerate(cases):
            for side,ending in enumerate(("before","after")):
                clip=f"{slug}-{ending}"
                edits.extend([
                    {"type":"clip.insert","payload":{"trackId":ending,**({"createTrackKind":"video"} if index==0 else {}),
                     "clipId":clip,"assetId":presentation_assets[1+index*2+side]["assetId"],
                     "timelineStart":r(index*4),"timelineEnd":r(index*4+4)}},
                    {"type":"effect.add","payload":{"clipId":clip,"effectId":"cutvoke.transform",
                     "params":{"scale":.465,"position":{"x":32+side*632,"y":222}}}},
                ])
            for lane,content,size,y,font in (("chapter",f"0{index+1}  /  {title}",54,.16,"Barlow Condensed"),
                 ("note",note,26,.875,"Space Grotesk")):
                edits.append({"type":"clip.insert","payload":{"trackId":lane,
                  **({"createTrackKind":"text"} if index==0 else {}),"clipId":f"{slug}-{lane}",
                  "timelineStart":r(index*4),"timelineEnd":r(index*4+4),"text":{"content":content,
                    "fontSize":size,"fontFamily":font,"color":"#233b47","strokeWidth":0,"shadow":0,"x":.5,"y":y}}})
        for lane,content,size,x,y in (("brand","CUTVOKE  /  VLOG FIELD NOTES",24,.5,.05),
               ("left-label","ORIGINAL",22,.258,.276),("right-label","EFFECT",22,.75,.276),
               ("foot","Original procedural media  /  24 fps  /  All four effect projects remain editable",18,.5,.96)):
            edits.append({"type":"clip.insert","payload":{"trackId":lane,"createTrackKind":"text","clipId":lane+"-text",
             "timelineStart":r(0),"timelineEnd":r(16),"text":{"content":content,"fontSize":size,
             "fontFamily":"Space Grotesk","color":"#233b47","strokeWidth":0,"shadow":0,"x":x,"y":y}}})
        for i in range(0,len(edits),25):apply(pid,"edit.batch",{"commands":edits[i:i+25]},lease)
        mcp.call("edit_lock",projectId=pid,action="release",leaseId=lease);lease=None
        revision=mcp.call("project_summary",projectId=pid)["revision"]
        preview=mcp.call("preview_prepare",projectId=pid,expectedRevision=revision)
        mcp.wait("preview_job",pid,preview["jobId"],timeout=300)
        job=apply(pid,"export.enqueue",{"outPath":str(output/"vlog-effects-comparison.mp4"),"quality":"high","overwrite":False})
        mcp.wait("export_job",pid,job["changedEntities"][0]["jobId"],timeout=300)
        mcp.call("project_package",projectId=pid,expectedRevision=revision,outPath=str(projects/"comparison.cvkpkg"))
        probe=json.loads(subprocess.check_output(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(output/"vlog-effects-comparison.mp4")]))
        subprocess.run(["ffmpeg","-v","error","-i",str(output/"vlog-effects-comparison.mp4"),"-f","null","-"],check=True,capture_output=True)
        contact=Image.new("RGB",(1280,4*240),(239,234,222))
        for index,at in enumerate((1.6,5.6,10.4,14.5)):
            image_path=frames/f"comparison-{index+1}.png"
            subprocess.run(["ffmpeg","-v","error","-ss",str(at),"-i",str(output/"vlog-effects-comparison.mp4"),
                "-frames:v","1",str(image_path)],check=True,capture_output=True)
            # Keep both pictures large enough to inspect in one contact sheet.
            image=Image.open(image_path).crop((20,175,1270,585)).resize((732,240),Image.Resampling.LANCZOS)
            contact.paste(image,(548,index*240))
            font=ImageFont.truetype(str(ROOT/"src/cutvoke/assets/fonts/BarlowCondensed-Regular.ttf"),31)
            d=ImageDraw.Draw(contact);d.text((24,index*240+66),f"0{index+1}  {cases[index][1]}",font=font,fill=(35,59,71))
            d.text((24,index*240+112),"ORIGINAL  /  EFFECT",font=font,fill=(185,101,65))
        contact.save(output/"contact.png")
        result=dict(ok=True,originalProceduralMedia=True,audio="none",runtime=runtime,cases=reports,
            comparison=dict(projectId=pid,revision=revision,probe=probe,fullDecode=True),
            effectiveSubjectLockZoom=subject_lock_zoom(cases[0][5]),
            limitations=["Reference films could not be played; source text only, no shot timestamps.",
                         "Manual two-point lock and manual shot matching; no automatic detection or tracking.",
                         "Synthetic illustrations verify behavior, not natural-footage creative acceptance."])
        (output/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        write_provenance(output)
        print(json.dumps({"ok":True,"output":str(output),"projects":[r["projectId"] for r in reports],"comparisonRevision":revision}))
    finally:
        if lease and project_id:
            try:mcp.call("edit_lock",projectId=project_id,action="release",leaseId=lease)
            except Exception:pass
        mcp.close(output)


if __name__=="__main__":main()
