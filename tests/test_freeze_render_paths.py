"""Regression: a real moving source must remain frozen through canvas and animations."""
import subprocess
import numpy as np
import pytest
from cutvoke.core.model import AssetReference, Clip, Project, Sequence, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService

def decode(path):
    result=subprocess.run(["ffmpeg","-v","error","-i",str(path),"-f","rawvideo",
        "-pix_fmt","rgb24","-"],capture_output=True,check=True,timeout=30)
    return np.frombuffer(result.stdout,np.uint8).reshape(-1,144,96,3)

@pytest.mark.parametrize("source_fps",[24,60])
@pytest.mark.parametrize("mode",["plain","canvas","animated"])
def test_real_freeze_is_same_source_frame_in_all_paths(tmp_path,source_fps,mode):
    source=tmp_path/"moving.mkv"
    pixels=np.empty((source_fps*3,144,96,3),np.uint8)
    for i in range(len(pixels)):
        pixels[i]=[(i*17)%200+30,(i*41)%200+30,(i*79)%200+30]
        pixels[i,4:14,(i*3)%80:(i*3)%80+12]=255
    subprocess.run(["ffmpeg","-v","error","-f","rawvideo","-pix_fmt","rgb24",
        "-s","96x144","-r",str(source_fps),"-i","pipe:0","-c:v","ffv1",
        "-y",str(source)],input=pixels.tobytes(),check=True,capture_output=True,timeout=30)
    effects=[]
    if mode!="plain":
        effects=[{"effectId":"cutvoke.transform","params":{"scale":.8,"position":{"x":10,"y":14}}},
                 {"effectId":"cutvoke.fx.mask","params":{"shape":"rect","x":.1,"y":.1,"w":.8,"h":.8,"feather":0}}]
    if mode=="animated":
        effects.append({"effectId":"cutvoke.anim.fadeIn","params":{"duration":.25}})
    clip=Clip("f",AssetReference("f",str(source)),Rational.of(1,2),Rational.of(2),
              Rational.of(1,4),effects=effects,freeze_at=Rational.of(5,4))
    project=Project("1","freeze-paths","0",Sequence("s",96,144,Rational.of(24),
        tracks=[Track("v","video",clips=[clip])]))
    renderer=RenderService();out=tmp_path/"full.mp4"
    renderer.render(project,str(out),quality="high")
    frames=decode(out).astype(int)
    assert len(frames)==48
    # Check after entrance animation settles, away from the transformed edges.
    centers=frames[20:46,60:84,38:58]
    assert np.mean(np.abs(centers-centers[0]))<1
    expected=decode(source)[round(1.25*source_fps),72,48].astype(int)
    assert np.max(np.abs(centers.mean(axis=(0,1,2))-expected))<8
    bounded=tmp_path/"bounded.mp4"
    renderer.render_preview_window(project,str(bounded),1,.5)
    view=decode(bounded).astype(int)
    assert len(view)==12
    assert np.mean(np.abs(view-frames[24:36]))<3
