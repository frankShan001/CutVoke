"""Real MCP preview buffers and exports stay separate from a review-only PNG."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import base64
import shutil
import subprocess
import threading
import time
import unittest

from PIL import Image, ImageDraw
from cutvoke.editing.service import EditService
from cutvoke.interfaces.mcp_server import MCPServer
from cutvoke.storage.store import ProjectStore

from cutvoke.interfaces.httpapi import HttpApi
from cutvoke.rendering.render import RenderService
def fixture(color):
    png = BytesIO()
    image = Image.new('RGBA', (1080, 1920))
    ImageDraw.Draw(image).rectangle((790,1516,830,1556), fill=color)
    image.save(png, 'PNG')
    return png.getvalue()


def call(server, tool, **params):
    result = server.call_tool(tool, params)
    assert result["ok"], (tool, result)
    return result


def _verify_real_preview_and_exports():
    with TemporaryDirectory(prefix="cv-guide-real-frame-") as directory:
        root = Path(directory)
        store = ProjectStore(str(root / "projects.sqlite"))
        service = EditService(store)
        server = MCPServer(service)
        try:
            call(server, "create_project", projectId="guide", width=180,height=320,fps=12)
            picture = root / "blue.png"
            Image.new("RGB", (180,320), "blue").save(picture)
            asset = call(server, "media_import", paths=[str(picture)], projectId="guide")["assets"][0]["assetId"]
            call(server, "command_apply", projectId="guide", expectedRevision=service.get_project("guide").revision,
                type="clip.insert",payload={"trackId":"v","createTrackKind":"video","clipId":"c","assetId":asset,
                   "timelineStart":{"num":"0","den":"1"},"timelineEnd":{"num":"2","den":"1"}})
            audio = root / "tone.wav"
            subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i","sine=frequency=440:sample_rate=48000:duration=2",str(audio)],check=True)
            tone = call(server,"media_import",paths=[str(audio)],projectId="guide")["assets"][0]["assetId"]
            for kind,payload in (("clip.insert", {"trackId":"a","createTrackKind":"audio",
                    "clipId":"tone","assetId":tone,"timelineStart":{"num":"0","den":"1"},"timelineEnd":{"num":"2","den":"1"}}),
                    ("caption.add",{"captionId":"title","text":"REVIEW TEST ONLY","fontSize":12,
                    "start":{"num":"0","den":"1"},"end":{"num":"2","den":"1"}})):
                call(server,"command_apply",projectId="guide",expectedRevision=service.get_project("guide").revision,
                     type=kind,payload=payload)
            snapshot = service.get_project("guide").to_dict()
            revision = snapshot["revision"]
            normal = call(server,"preview_frames",projectId="guide",expectedRevision=revision,times=[.5],width=540)
            frame = normal["frames"][0]
            preview_path = Path(frame["path"])
            clean = preview_path.read_bytes()
            second_store = ProjectStore(str(root / "projects.sqlite"))
            gate=threading.Event()
            started=threading.Event()

            class GateRender(RenderService):
                def render(self, project, output, *args, **kwargs):
                    if Path(output).name == "queued.mp4":
                        started.set()
                        assert gate.wait(10)
                    return super().render(project, output, *args, **kwargs)

            second_api = HttpApi(EditService(second_store), render=GateRender(gpu_effects=False))
            second_api.render.gpu_acceleration=False
            baseline = second_api.handle("POST","/api/v1/projects/guide/export",{"outPath":str(root/"base.mp4")})
            assert baseline[0]==200, baseline
            try:
                route = "/api/v1/projects/guide/review-guide/" + service.get_project("guide").sequence.id
                first, second = None, None
                for color, pixel in (("magenta",(255,0,255)),("yellow",(255,255,0))):
                    status, receipt = second_api.handle("POST", route, {"expectedRevision":revision,
                        "pngBase64": base64.b64encode(fixture(color)).decode("ascii")})
                    assert status==200, (status, receipt if "error" in receipt else receipt.keys())
                    assert second_api.handle("GET",route,{})==(200,receipt)
                    result=call(server,"preview_frames",projectId="guide",expectedRevision=revision,
                                times=[.5],width=540,includeReviewGuide=True)
                    measured=result["frames"][0]
                    raw=Path(measured["path"]).read_bytes()
                    assert raw==base64.b64decode(result["_images"][0]["data"])
                    assert hashlib.sha256(raw).hexdigest()==measured["sha256"]
                    assert len(raw)==measured["sizeBytes"]
                    assert measured["path"]!=frame["path"]
                    decoded=Image.open(BytesIO(raw)).convert("RGB")
                    assert decoded.getpixel((405,768))==pixel
                    if first is None: first=measured
                    else: second=measured
                assert first["path"]!=second["path"]
                sync=second_api.handle("POST","/api/v1/projects/guide/export",{"outPath":str(root/"sync.mp4")})
                assert sync[0]==200, sync
                code, job=second_api.handle("POST","/api/v1/projects/guide/exports",{"outPath":str(root/"queued.mp4")})
                assert code==202, (code,job)
                assert started.wait(10),job
                replaced = second_api.handle("POST",route,{"expectedRevision":revision,
                    "pngBase64":base64.b64encode(fixture("lime")).decode("ascii")})
                assert replaced[0]==200, replaced
                gate.set()
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    code, outcome=second_api.handle("GET","/api/v1/jobs/"+job["jobId"],{})
                    assert code==200,(code,outcome)
                    if outcome["status"] in ("succeeded","cancelled","failed"):
                        break
                    time.sleep(.05)
                assert outcome["status"]=="succeeded",outcome
                def decode(file,args):
                    return subprocess.run(["ffmpeg","-v","error","-i",str(root/file),*args,"pipe:1"],
                        check=True,capture_output=True).stdout
                rgb=['-an','-pix_fmt','rgb24','-f','rawvideo']
                pcm=['-vn','-acodec','pcm_s16le','-f','s16le']
                for spec in (rgb,pcm):
                    original=decode("base.mp4",spec)
                    assert original and original==decode("sync.mp4",spec)==decode("queued.mp4",spec)


                bad=second_api.handle("POST",route,{"expectedRevision":"stale","pngBase64":None})
                assert bad[0]==409
                assert second_api.handle("POST",route,{"expectedRevision":revision,"pngBase64":None})[0]==200
                cleared=call(server,"preview_frames",projectId="guide",times=[.5],width=540,includeReviewGuide=True)
                assert cleared["frames"][0]==frame
            finally:
                gate.set()
                second_api.close()
                second_store.close()
            assert hashlib.sha256(preview_path.read_bytes()).hexdigest()==frame["sha256"]
            assert call(server,"preview_frames",projectId="guide",times=[.5],width=540)["frames"][0]==frame
            assert service.get_project("guide").to_dict()==snapshot

        finally:
            server.close()
            store.close()

class ReviewGuideAcceptance(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and FFprobe required")
    def test_real_preview_and_exports(self):
        _verify_real_preview_and_exports()

