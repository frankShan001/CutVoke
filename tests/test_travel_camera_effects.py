"""Actual native render checks for the travel camera effects (no fixture film edits)."""
import subprocess
import numpy as np
import pytest
from PIL import Image
from cutvoke.core.effects import default_registry
from cutvoke.core.model import AssetReference, Clip, Project, Sequence, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService

def decode(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True, timeout=30).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 144, 96, 3)

@pytest.mark.parametrize("leaf", ["whipPan", "zoomOut", "lightLeak"])
def test_camera_transition_is_visible_and_window_matches_full(tmp_path, leaf):
    clips = []
    for i, color in enumerate(["red", "blue"]):
        p = tmp_path / f"{color}.png"
        Image.new("RGB", (96, 144), color).save(p)
        effects = [{"effectId": "cutvoke.transition."+leaf, "params": {"duration": .5}}] if i else []
        clips.append(Clip(str(i), AssetReference(str(i), str(p)), Rational.of(i),
                          Rational.of(i+1), Rational.of(0), effects=effects))
    project = Project("1", "travel-camera", "0", Sequence("s",96,144,Rational.of(24),
                      tracks=[Track("v","video",clips=clips)]))
    renderer=RenderService()
    out=tmp_path/"full.mp4"
    renderer.render(project,str(out),quality="high")
    frames=decode(out).astype(int)
    assert len(frames)==48
    assert np.max(abs(frames[12,72,48]-[255,0,0]))<16
    assert np.max(abs(frames[43,72,48]-[0,0,255]))<16
    mid=frames[30]
    if leaf=="whipPan":
        assert mid[72,10,0]>180 and mid[72,85,2]>180
    elif leaf=="lightLeak":
        assert mid[:,:,1].mean()>15  # a warm colored exposure, not plain red/blue fade
    else:
        assert mid[:,:,0].mean()>20 and mid[:,:,2].mean()>20
    bounded=tmp_path/"bounded.mp4"
    renderer.render_preview_window(project,str(bounded),.75,1.0)
    view=decode(bounded).astype(int)
    assert len(view)==24
    assert np.mean(abs(view-frames[18:42]))<3

def test_motion_blur_changes_only_bounded_frames(tmp_path):
    spec=default_registry().get("cutvoke.fx.motionBlur")
    assert spec.default_params()["strength"]==.5
    src=tmp_path/"moving.mp4"; out=tmp_path/"blur.mp4"
    moving=np.zeros((24,144,96,3),np.uint8)
    for i in range(24):
        moving[i,40:100,4+2*i:14+2*i]=255
    subprocess.run(["ffmpeg","-v","error","-f","rawvideo","-pix_fmt","rgb24",
                    "-s","96x144","-r","24","-i","pipe:0",
                    "-c:v","libx264","-y",str(src)],input=moving.tobytes(),check=True,capture_output=True)
    clip=Clip("m",AssetReference("m",str(src)),Rational.of(0),Rational.of(1),Rational.of(0),
              effects=[{"effectId":"cutvoke.fx.motionBlur","params":{"strength":.5,"start":.25,"end":.5}}])
    project=Project("1","shutter","0",Sequence("s",96,144,Rational.of(24),tracks=[Track("v","video",clips=[clip])]))
    RenderService().render(project,str(out),quality="high")
    before=decode(src).astype(int); after=decode(out).astype(int)
    assert len(after)==24
    assert np.mean(abs(before[8:12]-after[8:12]))>.5
    assert np.mean(abs(before[:5]-after[:5]))<1
    assert np.mean(abs(before[16:]-after[16:]))<1

def test_real_sidechain_reduces_music_and_recovers_after_key(tmp_path):
    import wave
    rate=48000; duration=3
    times=np.arange(rate*duration)/rate
    music=(.2*np.sin(2*np.pi*440*times)).astype(np.float32)
    key=(.25*np.sin(2*np.pi*1500*times)*((times>.8)&(times<1.4))).astype(np.float32)
    def wav(p,arr):
        with wave.open(str(p),"wb") as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate)
            w.writeframes((arr*32767).astype("<i2").tobytes())
    m=tmp_path/"music.wav"; k=tmp_path/"key.wav";wav(m,music);wav(k,key)
    still=tmp_path/"bg.png";Image.new("RGB",(96,144),"navy").save(still)
    bg=Clip("v",AssetReference("v",str(still)),Rational.of(0),Rational.of(3),Rational.of(0))
    mc=Clip("music",AssetReference("m",str(m)),Rational.of(0),Rational.of(3),Rational.of(0),
            effects=[{"effectId":"cutvoke.fx.sidechainDuck","params":{"keyClipIds":["voice"],
             "reductionDb":-14,"thresholdDb":-40,"attackMs":10,"releaseMs":300}}])
    kc=Clip("voice",AssetReference("k",str(k)),Rational.of(0),Rational.of(3),Rational.of(0))
    p=Project("1","duck","0",Sequence("s",96,144,Rational.of(24),tracks=[
       Track("v","video",clips=[bg]),Track("m","audio",clips=[mc]),Track("k","audio",clips=[kc])]))
    out=tmp_path/"duck.mp4";renderer=RenderService();renderer.render(p,str(out),quality="high")
    def audio(path):
        raw=subprocess.run(["ffmpeg","-v","error","-i",str(path),"-f","f32le","-ac","1","-ar",str(rate),"-"],
                           check=True,capture_output=True).stdout
        return np.frombuffer(raw,"<f4")
    mixed=audio(out)
    def amplitude(y,start,end):
        a=int(start*rate);b=int(end*rate);t=np.arange(a,b)/rate
        return 2*abs(np.sum(y[a:b]*np.exp(-2j*np.pi*440*t)))/(b-a)
    before=amplitude(mixed,.2,.6); during=amplitude(mixed,1.0,1.3); after=amplitude(mixed,2.2,2.7)
    cut=20*np.log10(during/before)
    assert -15.5<cut<-11
    assert abs(after/before-1)<.08
    assert np.max(abs(mixed))<.80
    bounded=tmp_path/"duck-window.mp4"
    renderer.render_preview_window(p,str(bounded),.5,2)
    view=audio(bounded)
    # AAC blocks may carry a small tail; compare stable tone energy, not packet padding.
    assert abs(amplitude(view,.5,.8)/during-1)<.08

def test_cached_path_mask_keeps_cutout_and_frame_clock(tmp_path):
    p=tmp_path/"source.png";Image.new("RGB",(96,144),"white").save(p)
    points=[{"x":.1,"y":.04},{"x":.9,"y":.04},{"x":.96,"y":.075},{"x":.96,"y":.925},
            {"x":.9,"y":.96},{"x":.1,"y":.96},{"x":.04,"y":.925},{"x":.04,"y":.075}]
    c=Clip("m",AssetReference("m",str(p)),Rational.of(0),Rational.of(1),Rational.of(0),
           effects=[{"effectId":"cutvoke.fx.mask","params":{"shape":"freehand","pathMode":"smooth","points":points,"feather":.003}}])
    project=Project("1","cached-path","0",Sequence("s",96,144,Rational.of(24),
                    tracks=[Track("v","video",clips=[c])]))
    out=tmp_path/"path.mp4";RenderService().render(project,str(out),quality="high")
    pixels=decode(out)
    assert len(pixels)==24
    assert pixels[:,72,48].mean()>235
    assert pixels[:,0,0].mean()<8
    assert pixels[:,143,95].mean()<8
