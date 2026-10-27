import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational
from cutvoke.editing.service import EditService
from cutvoke.rendering.render import RenderService
from cutvoke.rendering.effects import EffectRegistry, EffectParamInvalid
from cutvoke.rendering.vfx_fusion import constrain


def test_reflection_and_occlusion_masks_have_real_pixel_constraints_and_follow_anchor():
    image=Image.new('RGBA',(100,100),(255,255,255,255))
    points=[{'x':.2,'y':.2},{'x':.4,'y':.2},{'x':.4,'y':.4},{'x':.2,'y':.4}]
    params={'occlusionPolygon':points,'occlusionFollowAnchor':True,
            'anchorFrames':[{'time':0,'x':.1,'y':.1},{'time':1,'x':.4,'y':.1}]}
    first=constrain(image,params,0); moved=constrain(image,params,1)
    assert first.getpixel((30,30))[3]==0 and moved.getpixel((60,30))[3]==0
    assert moved.getpixel((30,30))[3]==255
    water=constrain(image,{'reflectionRegion':{'y':.6,'height':.4}},0)
    assert water.getpixel((50,30))[3]==0 and water.getpixel((50,80))[3]==255


@pytest.mark.parametrize('mode',['normal','screen','add'])
def test_scene_fusion_blends_render_and_keep_sdr_tags(tmp_path, mode):
    source=tmp_path/'source.png'; Image.new('RGB',(160,90),(20,40,80)).save(source)
    service=EditService(); project=service.create_project('fusion',width=160,height=90)
    clip=Clip('c',AssetReference('source',str(source)),Rational.of(0),Rational.of(2),Rational.of(0))
    registry=EffectRegistry()
    params=registry.validate_params('cutvoke.fx.opticalField',{'layer':'flare','duration':1.5,'blendMode':mode,
        'highlightProtect':.8,'occlusionPolygon':[{'x':0,'y':0},{'x':.4,'y':0},{'x':.4,'y':1},{'x':0,'y':1}],
        'angleFrames':[{'time':0,'angle':-10},{'time':1,'angle':20}]})
    clip.effects=[{'effectId':'cutvoke.fx.opticalField','params':params}]
    project.sequence.tracks=[Track('v','video',[clip])]
    renderer=RenderService(); renderer.gpu_acceleration=False
    out=tmp_path/f'{mode}.mp4'; result=renderer.render(project,str(out),quality='medium')
    assert result['color_space']=='bt709' and result['color_primaries']=='bt709' and result['color_transfer']=='bt709'
    frame=tmp_path/'frame.png'; renderer.extract_frame(project,.6,str(frame))
    pixels=np.asarray(Image.open(frame).convert('RGB'))
    # The occluded left strip retains the scene's original color.
    assert np.abs(pixels[20:70,5:40].mean(axis=(0,1))-np.array([20,40,80])).max()<4


def test_direction_points_reject_duplicate_times():
    with pytest.raises(EffectParamInvalid):
        EffectRegistry().validate_params('cutvoke.fx.opticalField',{'angleFrames':[{'time':1,'angle':0},{'time':1,'angle':30}]})


def test_project_light_cache_survives_non_visual_edits_and_composite_tuning(tmp_path):
    from cutvoke.storage.project_directory import DirectoryProjectStore
    store=DirectoryProjectStore(tmp_path/'project',create_id='cache')
    source=tmp_path/'source.png'; Image.new('RGB',(160,90),(20,40,80)).save(source)
    project=EditService().create_project('cache',width=160,height=90)
    clip=Clip('c',AssetReference('source',str(source)),Rational.of(0),Rational.of(1),Rational.of(0))
    clip.effects=[{'effectId':'cutvoke.fx.opticalField','params':{'seed':int.from_bytes(str(tmp_path).encode()[-2:],'big'),'duration':.8}}]
    project.sequence.tracks=[Track('v','video',[clip])];store.save(project)
    project=store.load('cache');renderer=RenderService();renderer.gpu_acceleration=False
    try:
        cold=renderer.render(project,str(store.root/'exports'/'cold.mp4'),quality='medium')
        assert cold['timings']['lightGeneratedFrames']>0
        assert list((store.root/'cache'/'effects').glob('*.mkv'))
        project.name='renamed'
        warm=renderer.render(project,str(store.root/'exports'/'warm.mp4'),quality='medium')
        assert warm['timings']['lightGeneratedFrames']==0 and warm['timings']['lightMemoryCacheHits']==1
        project.sequence.tracks[0].clips[0].effects[0]['params']['highlightProtect']=.5
        tuned=renderer.render(project,str(store.root/'exports'/'tuned.mp4'),quality='medium')
        assert tuned['timings']['lightGeneratedFrames']==0 and tuned['timings']['lightDiskCacheHits']==1
    finally: store.close()
