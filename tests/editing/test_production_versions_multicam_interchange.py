"""Acceptance: saved editorial versions, timed camera pixels, actual waveform sync and standard interchange."""
from pathlib import Path
import json
import subprocess
import time
import uuid
import wave
import xml.etree.ElementTree as ET
import numpy as np
import pytest
from PIL import Image

from cutvoke.domain.protocol import Actor, Command
from cutvoke.domain.rational import Rational
from cutvoke.editing.service import EditService, EditError
from cutvoke.editing.interchange import export_timeline, import_timeline
from cutvoke.rendering.render import RenderService
from cutvoke.rendering.multicam_sync import correlate
from cutvoke.storage.store import ProjectStore


def command(service, kind, payload=None):
    project = service.get_project("production")
    return service.execute(Command(type=kind, command_id=uuid.uuid4().hex, project_id="production",
        expected_revision=project.revision, payload=payload or {}, actor=Actor("human", "production-acceptance")))


@pytest.fixture
def editing(tmp_path):
    store = ProjectStore(str(tmp_path / "project.sqlite"))
    service = EditService(store)
    service.create_project("production", width=160, height=90, fps=Rational.of(10))
    try: yield service, store
    finally:
        service.close()
        store.close()


def add_clip(service, track_id, source, clip_id, duration=3):
    command(service, "track.add", {"trackId": track_id, "kind": "video"})
    command(service, "clip.insert", {"trackId": track_id, "clipId": clip_id, "sourcePath": str(source),
        "timelineStart": {"num": "0", "den": "1"}, "timelineEnd": {"num": str(duration), "den": "1"}})


def video(path, color, audio=None):
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color={color}:s=160x90:r=10:d=6"]
    if audio: args += ["-i", str(audio), "-shortest", "-c:a", "aac"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True, capture_output=True)


def test_canvas_versions_save_undo_render_and_queue_real_dimensions(editing, tmp_path):
    service, store = editing
    source = tmp_path / "wide.png"
    image = Image.new("RGB", (160,90), "red")
    for x in range(60,100):
        for y in range(90): image.putpixel((x,y), (0,0,255))
    image.save(source)
    add_clip(service, "main", source, "picture", 1)
    original = service.get_project("production").to_dict()
    command(service, "sequence.duplicate", {"sequenceId": "portrait", "width": 72, "height": 128,
        "fps": 24, "reframe": "cover", "name": "Portrait"})
    project = service.get_project("production")
    assert (project.sequence.width, project.sequence.height, project.sequence.fps.num) == (72,128,24)
    assert project.sequence.tracks[0].clips[0].id != "picture"
    assert store.load("production").sequence.reframe == "cover"
    render = RenderService()
    frame = render.extract_frame(project, 0.5, str(tmp_path / "portrait.png"))
    with Image.open(frame) as result:
        assert result.size == (72,128)
        assert result.convert("RGB").getpixel((36,64))[2] > 240
    command(service, "history.undo")
    assert service.get_project("production").to_dict()["sequence"] == original["sequence"]
    command(service, "history.redo")
    queue = service._get_export_queue(render_factory=lambda: render)
    first = queue.submit(service.get_project("production"), {"sequenceId": original["sequence"]["id"], "outPath": str(tmp_path/"landscape.mp4")})
    second = queue.submit(service.get_project("production"), {"sequenceId": "portrait", "outPath": str(tmp_path/"portrait.mp4")})
    end = time.monotonic()+45
    while time.monotonic()<end and any(queue.get(key["jobId"])["status"] in ("queued","running") for key in (first,second)):
        time.sleep(.05)
    for job, dimensions, rate in ((first,(160,90),10),(second,(72,128),24)):
        state = queue.get(job["jobId"])
        assert state["status"] == "succeeded", state
        metadata = render.probe_media(job["outPath"])
        assert (metadata["width"],metadata["height"]) == dimensions
        raw = subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=avg_frame_rate","-of","json",job["outPath"]], capture_output=True, check=True)
        assert json.loads(raw.stdout)["streams"][0]["avg_frame_rate"] == f"{rate}/1"
    assert service.get_project("production").sequence.id == "portrait"


def test_timed_multicam_preview_export_and_overlay_preservation(editing, tmp_path):
    service, _store = editing
    red, green = tmp_path/"red.mp4", tmp_path/"green.mp4"
    video(red,"red"); video(green,"green")
    add_clip(service,"camera-a",red,"a")
    add_clip(service,"camera-b",green,"b")
    command(service,"multicam.create",{"trackIds":["camera-a","camera-b"]})
    command(service,"multicam.switch",{"trackId":"camera-b","time":1})
    command(service,"multicam.switch",{"trackId":"camera-a","time":2})
    overlay = tmp_path/"overlay.png"; Image.new("RGB",(160,90),"blue").save(overlay)
    add_clip(service,"overlay",overlay,"overlay-clip")
    command(service,"effect.add",{"clipId":"overlay-clip","effectId":"cutvoke.transform","params":{"scale":.2,"position":{"x":0,"y":0}}})
    project = service.get_project("production")
    assert len(project.sequence.multicam["cuts"]) == 2
    render = RenderService()
    for index, (at, channel) in enumerate(((.5,0),(1.5,1),(2.5,0))):
        output = render.extract_frame(project,at,str(tmp_path/f"camera-{index}.png"))
        pixel = Image.open(output).convert("RGB").getpixel((80,45))
        assert pixel[channel] > 100 and pixel[1-channel] < 20, pixel
        assert Image.open(output).convert("RGB").getpixel((10,10))[2] > 200
    render.render(project,str(tmp_path/"program.mp4"))
    for at, channel in ((.5,0),(1.5,1),(2.5,0)):
        result = subprocess.run(["ffmpeg","-v","error","-ss",str(at),"-i",str(tmp_path/"program.mp4"),
            "-frames:v","1","-f","rawvideo","-pix_fmt","rgb24","pipe:1"],capture_output=True,check=True)
        pixel = np.frombuffer(result.stdout,dtype=np.uint8).reshape(90,160,3)[45,80]
        assert pixel[channel] > 100 and pixel[1-channel] < 20
    command(service,"history.undo")
    command(service,"history.undo")
    command(service,"history.undo")
    command(service,"history.undo")
    assert len(service.get_project("production").sequence.multicam["cuts"]) == 1


def test_waveform_alignment_measures_actual_media_and_rejects_ambiguity(editing,tmp_path):
    service,_store = editing
    rng = np.random.default_rng(28)
    envelope = rng.uniform(.05,.9,600)
    samples = (np.sin(np.arange(48000)*2*np.pi*440/8000)*np.repeat(envelope,80)*24000).astype("<i2")
    delayed = np.concatenate([np.zeros(3200,dtype="<i2"),samples])[:48000]
    paths=[]
    for index, values in enumerate((samples,delayed)):
        audio=tmp_path/f"wave{index}.wav"
        with wave.open(str(audio),"wb") as out:
            out.setnchannels(1);out.setsampwidth(2);out.setframerate(8000);out.writeframes(values.tobytes())
        path=tmp_path/f"camera{index}.mp4";video(path,"red" if index==0 else "green",audio);paths.append(path)
    add_clip(service,"ref",paths[0],"ref-clip",6);add_clip(service,"target",paths[1],"target-clip",6)
    command(service,"multicam.create",{"trackIds":["ref","target"]})
    outcome=command(service,"multicam.sync",{"referenceTrackId":"ref","targetTrackId":"target","maxOffsetSeconds":1})
    report=outcome.changed_entities[0]["report"]
    assert report["lagSeconds"] == pytest.approx(.4,abs=.02)
    assert report["confidence"]>.8
    target=service.get_project("production").sequence.tracks[1].clips[0]
    assert float(target.source_start.to_fraction()) == pytest.approx(.4,abs=.02)
    with pytest.raises(ValueError,match="Ambiguous|insufficient"):
        correlate(np.ones(300),np.ones(300),1)


@pytest.mark.parametrize("format",["otio","edl","fcpxml"])
def test_interchange_standard_cut_subsets_and_undo(editing,tmp_path,format):
    service,_store=editing
    source=tmp_path/"source.png";Image.new("RGB",(160,90),"red").save(source)
    add_clip(service,"main",source,"clip",1)
    original=service.get_project("production").sequence
    result=export_timeline(original,format)
    assert result["content"],result
    if format=="otio":
        otio=pytest.importorskip("opentimelineio")
        external=otio.adapters.read_from_string(result["content"],"otio_json")
        assert len(list(external.find_clips())) == 1
        assert external.duration().to_seconds() == 1
    imported,diagnostics=import_timeline(result["content"],format)
    assert len(imported.tracks[0].clips)==1
    assert imported.tracks[0].clips[0].asset_ref.source_path == str(source)
    assert imported.tracks[0].clips[0].duration == Rational.of(1)
    command(service,"project.interchangeImport",{"format":format,"content":result["content"]})
    assert len(service.get_project("production").sequences)==2
    command(service,"history.undo")
    assert len(service.get_project("production").sequences)==1
    original.captions.append(__import__("cutvoke.domain.model",fromlist=["Caption"]).Caption("caption","words",Rational.of(0),Rational.of(1)))
    rejected=export_timeline(original,format)
    assert rejected["content"] is None
    assert any(item["code"]=="CAPTIONS_UNMAPPED" for item in rejected["diagnostics"])
    assert export_timeline(original,"aaf")["content"] is None


def test_interchange_unknown_otio_item_returns_diagnostics_without_silent_commit(editing,tmp_path):
    service,_store=editing
    source=tmp_path/"source.png";Image.new("RGB",(160,90),"red").save(source);add_clip(service,"main",source,"clip",1)
    doc=json.loads(export_timeline(service.get_project("production").sequence)["content"])
    doc["tracks"]["children"][0]["children"].append({"OTIO_SCHEMA":"Transition.1","name":"unmapped dissolve"})
    before=service.get_project("production").to_dict()
    with pytest.raises(EditError,match="Unsupported interchange"):
        command(service,"project.interchangeImport",{"format":"otio","content":json.dumps(doc)})
    assert service.get_project("production").to_dict()==before


def test_fcpxml_embedded_audio_declares_real_stereo_and_preserves_audible_roundtrip(editing,tmp_path):
    service,_store=editing
    source=tmp_path/"stereo.mp4"
    subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i","color=red:s=160x90:r=10:d=1",
        "-f","lavfi","-i","sine=frequency=440:sample_rate=48000:duration=1","-ac","2",
        "-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-shortest",str(source)],check=True,capture_output=True)
    add_clip(service,"main",source,"clip",1)
    result=export_timeline(service.get_project("production").sequence,"fcpxml")
    assert result["content"],result
    asset=ET.fromstring(result["content"]).find("resources/asset")
    assert {key:asset.get(key) for key in ("hasAudio","audioSources","audioChannels","audioRate")} == {
        "hasAudio":"1","audioSources":"1","audioChannels":"2","audioRate":"48000"}
    command(service,"project.interchangeImport",{"format":"fcpxml","content":result["content"]})
    output=tmp_path/"roundtrip.mp4"
    renderer=RenderService()
    renderer.render(service.get_project("production"),str(output))
    assert renderer.probe_media(str(output))["has_audio"]
    decoded=subprocess.run(["ffmpeg","-v","error","-i",str(output),"-vn","-ac","2","-ar","48000",
        "-f","f32le","pipe:1"],check=True,capture_output=True)
    samples=np.frombuffer(decoded.stdout,dtype="<f4").reshape(-1,2)
    assert len(samples)>=48000
    assert np.all(np.sqrt(np.mean(samples[:48000]**2,axis=0))>.01)


def test_fcpxml_unverified_or_complex_source_audio_is_rejected_explicitly(editing,tmp_path,monkeypatch):
    from cutvoke.editing import interchange
    service,_store=editing
    source=tmp_path/"two-audio-streams.mp4"
    subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i","color=red:s=160x90:r=10:d=1",
        "-f","lavfi","-i","sine=frequency=440:sample_rate=48000:duration=1",
        "-f","lavfi","-i","sine=frequency=880:sample_rate=48000:duration=1",
        "-map","0:v","-map","1:a","-map","2:a","-c:v","libx264","-pix_fmt","yuv420p",
        "-c:a","aac","-shortest",str(source)],check=True,capture_output=True)
    add_clip(service,"main",source,"clip",1)
    sequence=service.get_project("production").sequence
    result=export_timeline(sequence,"fcpxml")
    assert result["content"] is None
    assert any(item["code"]=="FCPXML_EMBEDDED_AUDIO_UNVERIFIED" and "multiple source streams" in item["message"]
        for item in result["diagnostics"])
    def unavailable_probe(_source): raise subprocess.TimeoutExpired("ffprobe",15)
    monkeypatch.setattr(interchange,"_fcpxml_asset_audio",unavailable_probe)
    for strict in (True,False):
        result=export_timeline(sequence,"fcpxml",strict=strict)
        assert result["content"] is None
        assert result["diagnostics"][-1]["code"]=="FCPXML_EMBEDDED_AUDIO_UNVERIFIED"
    source.unlink()
    monkeypatch.undo()
    result=export_timeline(sequence,"fcpxml")
    assert result["content"] is None
    assert result["diagnostics"][-1]["code"]=="FCPXML_EMBEDDED_AUDIO_UNVERIFIED"


def test_canvas_resize_preserves_auto_caption_and_scales_title_and_transform(editing,tmp_path):
    from cutvoke.domain.model import Caption
    service,store=editing
    source=tmp_path/"picture.png";Image.new("RGB",(160,90),(20,30,50)).save(source)
    add_clip(service,"main",source,"picture",1)
    command(service,"effect.add",{"clipId":"picture","effectId":"cutvoke.transform",
        "params":{"position":{"x":20,"y":10},"scale":.5}})
    command(service,"track.add",{"trackId":"titles","kind":"text"})
    command(service,"clip.insert",{"trackId":"titles","clipId":"title","timelineStart":{"num":"0","den":"1"},
        "timelineEnd":{"num":"1","den":"1"},"text":{"content":"Title","fontSize":20,"x":.3,"y":.4,
            "strokeWidth":2,"panelWidth":.8,"panelHeight":.2}})
    project=service.get_project("production")
    project.sequence.captions=[Caption("automatic","Auto",Rational.of(0),Rational.of(1),fontSize=0),
        Caption("explicit","Sized",Rational.of(0),Rational.of(1),fontSize=20)]
    store.commit(project)
    command(service,"sequence.configure",{"width":320,"height":180,"reframe":"contain"})
    project=service.get_project("production")
    assert [caption.fontSize for caption in project.sequence.captions] == [0,40]
    assert project.sequence.tracks[0].clips[0].transform()["position"] == {"x":40,"y":20}
    text=project.sequence.tracks[1].clips[0].effects_by_id("cutvoke.text")[0]["params"]
    assert text["fontSize"]==40 and text["strokeWidth"]==4
    assert (text["x"],text["y"],text["panelWidth"],text["panelHeight"]) == (.3,.4,.8,.2)
    output=RenderService().extract_frame(project,.5,str(tmp_path/"text-resized.png"))
    assert Image.open(output).size==(320,180)
    command(service,"history.undo")
    assert [caption.fontSize for caption in service.get_project("production").sequence.captions] == [0,20]


def test_nested_sequence_version_rebuilds_ids_layout_references_and_reframes_baked_pixels(editing,tmp_path):
    service,_store=editing
    source=tmp_path/"wide.png"
    image=Image.new("RGB",(160,90),"red")
    for x in range(60,100):
        for y in range(90): image.putpixel((x,y),(0,0,255))
    image.save(source);add_clip(service,"main",source,"child",1)
    command(service,"clip.group",{"clipIds":["child"],"groupId":"compound"})
    before=service.get_project("production").sequence.tracks[0].clips[0].nested
    original_ids={before.id,before.tracks[0].id,before.tracks[0].clips[0].id}
    command(service,"sequence.duplicate",{"width":72,"height":128,"reframe":"cover","name":"Nested portrait"})
    project=service.get_project("production")
    compound=next(clip for track in project.sequence.tracks for clip in track.clips if clip.nested)
    nested=compound.nested
    assert original_ids.isdisjoint({nested.id,nested.tracks[0].id,nested.tracks[0].clips[0].id})
    assert nested.compound_layout[0]["nestedTrackId"]==nested.tracks[0].id
    frame=RenderService().extract_frame(project,.5,str(tmp_path/"nested-portrait.png"))
    result=Image.open(frame).convert("RGB")
    assert result.size==(72,128) and result.getpixel((36,64))[2]>200
