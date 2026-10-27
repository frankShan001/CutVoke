import json
import subprocess

import numpy as np
import pytest
from PIL import Image, ImageDraw

from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational
from cutvoke.editing.service import EditService
from cutvoke.rendering.effects import EffectRegistry, EffectParamInvalid
from cutvoke.rendering.render import RenderService


@pytest.mark.parametrize('alpha',[False,True])
def test_filtered_luma_key_preserves_duration_endpoints_and_real_blend(tmp_path,alpha):
    a=tmp_path/'a.png'; b=tmp_path/'b.png'
    first=Image.new('RGBA',(160,90),(12,20,30,180));ImageDraw.Draw(first).rectangle((50,5,108,85),fill=(245,245,245,180));first.save(a)
    Image.new('RGBA',(160,90),(35,100,170,200)).save(b)
    project=EditService().create_project('luma',width=160,height=90,fps=Rational.of(48))
    left=Clip('a',AssetReference('a',str(a)),Rational.of(0),Rational.of(1),Rational.of(0))
    right=Clip('b',AssetReference('b',str(b)),Rational.of(1),Rational.of(2),Rational.of(0))
    right.effects=[{'effectId':'cutvoke.transition.lumaReveal','params':EffectRegistry().validate_params('cutvoke.transition.lumaReveal',{
        'duration':.6,'spatialBlur':8,'temporalFrames':3,'dissolveMix':.4,
        'thresholdCurve':[{'at':0,'value':1},{'at':.5,'value':.55},{'at':1,'value':0}],
        'localRegion':{'x':.1,'y':.1,'width':.8,'height':.8,'feather':.1}})}]
    project.sequence.tracks=[Track('v','video',[left,right])]
    renderer=RenderService(); renderer.gpu_acceleration=False
    out=tmp_path/('filtered.mov' if alpha else 'filtered.mp4')
    result=renderer.render(project,str(out),quality='medium',alpha=alpha)
    assert abs(result['duration']-2)<.03 and result['has_alpha']==alpha
    raw=subprocess.run(['ffmpeg','-v','error','-i',str(out),'-f','rawvideo','-pix_fmt','rgb24','pipe:1'],check=True,capture_output=True).stdout
    frames=np.frombuffer(raw,np.uint8).reshape(-1,90,160,3)
    assert len(frames)==96
    # Outside the transition, neither the spatial nor temporal key blurs pictures.
    assert np.abs(frames[0].astype(float)-frames[24].astype(float)).mean()<1
    assert np.abs(frames[-1].astype(float)-frames[85].astype(float)).mean()<1
    assert np.abs(frames[62].astype(float)-frames[-1].astype(float)).mean()>5


def test_threshold_curve_rejects_unordered_progress():
    with pytest.raises(EffectParamInvalid):
        EffectRegistry().validate_params('cutvoke.transition.lumaReveal',{'thresholdCurve':[{'at':.5,'value':1},{'at':.5,'value':0}]})
