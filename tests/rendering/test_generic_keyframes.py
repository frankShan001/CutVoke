"""Time curves retain their identity through editing, storage and real rendering."""
import copy
import math
from pathlib import Path
import subprocess

import numpy as np
import pytest
from PIL import Image

from cutvoke.domain.keyframes import Keyframe, crop_keyframes, evaluate
from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.protocol import Actor, Command
from cutvoke.domain.rational import Rational
from cutvoke.editing.service import EditService, EditError
from cutvoke.rendering.render import RenderService
from cutvoke.storage.store import ProjectStore


def rat(value):
    return Rational.from_float(value)


def command(service, kind, payload):
    project=service.get_project("curve")
    return service.execute(Command(kind,payload,"command-"+__import__('uuid').uuid4().hex,
        "curve",project.revision,Actor("human","test")))


def setup(tmp_path):
    store=ProjectStore(str(tmp_path/"projects.sqlite"));service=EditService(store)
    source=tmp_path/"source.png";Image.new("RGB",(160,90),(128,128,128)).save(source)
    project=service.create_project("curve",width=160,height=90,fps=Rational.of(10))
    project.sequence.tracks=[Track("v","video",[Clip("c",AssetReference("source",str(source)),rat(0),rat(3),rat(0))])]
    store.save(project)
    return store,service


def decoded(path):
    raw=subprocess.run(["ffmpeg","-v","error","-i",str(path),"-an","-f","rawvideo","-pix_fmt","rgb24","pipe:1"],capture_output=True,check=True).stdout
    return np.frombuffer(raw,dtype=np.uint8).reshape(-1,90,160,3)


def renderer():
    result=RenderService(gpu_effects=False);result.gpu_acceleration=False;return result


def test_bezier_inverts_time_and_exact_crop_preserves_all_easing():
    curve=[Keyframe("a",rat(0),0,"cubic-bezier",(.8,.1,.9,.2)),Keyframe("b",rat(2),1)]
    assert evaluate(curve,rat(1.525)) == pytest.approx(.2375,abs=1e-10)
    for mode in ("linear","ease-in","ease-out","cubic-bezier"):
        curve[0].interpolation=mode
        cropped=crop_keyframes(curve,rat(.3),rat(1.7))
        restored=[Keyframe.from_dict(key.to_dict()) for key in cropped]
        for index in range(101):
            assert evaluate(restored,rat(index*.014)) == pytest.approx(evaluate(curve,rat(.3+index*.014)),abs=1e-9)


def test_commands_retime_update_undo_roundtrip_and_reject_partial_invalid_changes(tmp_path):
    store,service=setup(tmp_path)
    try:
        command(service,"clip.keyframe",{"clipId":"c","action":"batch","keyframes":[
            {"param":"volume","time":0,"value":.2,"interpolation":"cubic-bezier","bezier":[.8,.1,.9,.2]},
            {"param":"volume","time":2,"value":1}]})
        before=service.get_project("curve").sequence.tracks[0].clips[0].to_dict()
        ids=[key["id"] for key in before["keyframes"]["volume"]]
        command(service,"clip.keyframe",{"clipId":"c","action":"retime","keyframeIds":ids,"timeScale":.5,"offset":.5})
        lane=service.get_project("curve").sequence.tracks[0].clips[0].keyframes["volume"]
        assert [float(key.time.to_fraction()) for key in lane] == [.5,1.5]
        command(service,"history.undo",{})
        assert service.get_project("curve").sequence.tracks[0].clips[0].to_dict()==before
        command(service,"history.redo",{})
        command(service,"clip.keyframe",{"clipId":"c","param":"volume","action":"update","keyframeId":ids[1],"value":.7})
        project=store.load("curve");lane=project.sequence.tracks[0].clips[0].keyframes["volume"]
        assert lane[0].bezier == (.8,.1,.9,.2) and lane[1].value == .7
        frozen=project.to_dict()
        for payload in ({"action":"retime","keyframeIds":ids,"offset":10},
                        {"action":"update","param":"volume","keyframeId":ids[1],"time":.5},
                        {"param":"volume","time":0,"value":1,"interpolation":"cubic-bezier","bezier":[-1,0,1,1]}):
            with pytest.raises(EditError):command(service,"clip.keyframe",{"clipId":"c",**payload})
            assert store.load("curve").to_dict()==frozen
    finally:store.close()


def test_effect_instances_remain_bound_after_reorder_and_remove_cleans_lanes(tmp_path):
    store,service=setup(tmp_path)
    try:
        command(service,"effect.add",{"clipId":"c","effectId":"cutvoke.fx.blur","params":{"sigma":2}})
        command(service,"effect.add",{"clipId":"c","effectId":"cutvoke.fx.blur","params":{"sigma":8}})
        clip=service.get_project("curve").sequence.tracks[0].clips[0]
        first,second=clip.effects
        assert first["instanceId"] != second["instanceId"]
        lane=f"effect:{first['instanceId']}:sigma"
        command(service,"clip.keyframe",{"clipId":"c","param":lane,"time":0,"value":3})
        clip=store.load("curve").sequence.tracks[0].clips[0]
        clip.effects.reverse();assert clip.keyframes[lane][0].value==3
        command(service,"effect.remove",{"clipId":"c","effectId":"cutvoke.fx.blur"})
        assert not store.load("curve").sequence.tracks[0].clips[0].keyframes
    finally:store.close()


def test_real_color_curve_matches_window_preview_and_bezier_midpoint(tmp_path):
    store,service=setup(tmp_path)
    try:
        command(service,"effect.add",{"clipId":"c","effectId":"cutvoke.color","params":{}})
        clip=service.get_project("curve").sequence.tracks[0].clips[0]
        param=f"effect:{clip.effects[0]['instanceId']}:brightness"
        command(service,"clip.keyframe",{"clipId":"c","action":"batch","keyframes":[
            {"param":param,"time":0,"value":-.4,"interpolation":"cubic-bezier","bezier":[.8,.1,.9,.2]},
            {"param":param,"time":2,"value":.4}]})
        project=service.get_project("curve");full=tmp_path/"full.mp4";window=tmp_path/"window.mp4"
        renderer().render(project,str(full),quality="high")
        renderer().render_preview_window(project,str(window),start=1,duration=1,preview_max_dimension=None)
        a,b=decoded(full),decoded(window)
        assert a[2].mean()+40 < a[18].mean()
        assert np.abs(a[10:20].astype(float)-b[:10].astype(float)).mean()<3
        # A late-moving time curve stays substantially darker than a linear midpoint.
        assert a[10].mean()<100
    finally:store.close()


def test_real_blur_curve_changes_detail_over_time(tmp_path):
    store,service=setup(tmp_path)
    try:
        image=np.indices((90,160)).sum(axis=0)%2*255
        Image.fromarray(np.repeat(image[:,:,None],3,axis=2).astype(np.uint8)).save(tmp_path/"source.png")
        command(service,"effect.add",{"clipId":"c","effectId":"cutvoke.fx.blur","params":{"sigma":1}})
        clip=service.get_project("curve").sequence.tracks[0].clips[0]
        param=f"effect:{clip.effects[0]['instanceId']}:sigma"
        command(service,"clip.keyframe",{"clipId":"c","action":"batch","keyframes":[
            {"param":param,"time":0,"value":1}, {"param":param,"time":2,"value":20}]})
        output=tmp_path/"blur.mp4";renderer().render(service.get_project("curve"),str(output),quality="high")
        frames=decoded(output)
        assert frames[0].var()>frames[20].var()+.1
        window=tmp_path/"blur-window.mp4"
        renderer().render_preview_window(service.get_project("curve"),str(window),start=1,duration=1,preview_max_dimension=None)
        assert np.abs(frames[10:20].astype(float)-decoded(window)[:10].astype(float)).mean()<3
    finally:store.close()
