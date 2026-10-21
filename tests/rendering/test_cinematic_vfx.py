"""Real alpha, assembly endpoints, seek clocks, and disposable optical caches."""
import copy
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from cutvoke.domain.model import AssetReference,Clip,Track
from cutvoke.domain.rational import Rational as R
from cutvoke.rendering.render import RenderService
from cutvoke.editing.service import EditService
from cutvoke.rendering.effects import default_registry,EffectParamInvalid
from cutvoke.rendering.vfx_light import anchor,frame

pytestmark=pytest.mark.skipif(not shutil.which('ffmpeg'),reason='FFmpeg required')


def project(tmp_path,effect,size=(160,96)):
    array=np.zeros((size[1],size[0],4),dtype='uint8')
    array[18:-18,28:-28]=[50,180,230,255]
    source=tmp_path/'source.png';Image.fromarray(array).save(source)
    p=EditService().create_project('vfx',width=size[0],height=size[1],fps=R.of(24))
    p.sequence.tracks=[Track('v','video',[Clip('c',AssetReference('a',str(source)),R.of(0),R.of(2),R.of(0),effects=[effect])])]
    return p


def test_fragment_endpoint_and_independent_source_alpha(tmp_path):
    p=project(tmp_path,dict(effectId='cutvoke.fx.shatter',params=dict(duration=1,rows=7,columns=3,spread=.8)))
    r=RenderService();images=[]
    for at in [0,.45,1.5]:
        path=tmp_path/f'{at}.png';r.extract_still(p,at,str(path),alpha=True)
        images.append(np.array(Image.open(path).convert('RGBA')))
    assert np.count_nonzero(images[0][:,:,3])<30
    assert np.count_nonzero(images[1][:,:,3])>50
    assert np.count_nonzero(images[1][:,:,3])<np.count_nonzero(images[2][:,:,3])
    assert images[2][40,60,3]>250 and images[2][0,0,3]==0
    baseline=copy.deepcopy(p);baseline.sequence.tracks[0].clips[0].effects=[]
    r.extract_still(baseline,1.5,str(tmp_path/'base.png'),alpha=True)
    assert np.abs(images[2].astype(float)-np.array(Image.open(tmp_path/'base.png').convert('RGBA'))).mean()<1
    p.sequence.tracks[0].clips[0].effects[0]['params'].update(scatterMode='radial',originX=.4,spread=.3,fadeFragments=False)
    r.extract_still(p,0,str(tmp_path/'radial.png'),alpha=True)
    assert np.count_nonzero(np.array(Image.open(tmp_path/'radial.png').convert('RGBA'))[:,:,3])>300


def test_light_native_render_is_seek_safe_and_does_not_modify_project(tmp_path):
    p=project(tmp_path,dict(effectId='cutvoke.fx.lightWeave',params=dict(layer='all',duration=1.3,amplitude=.2)))
    original=json.dumps(p.to_dict(),sort_keys=True)
    r=RenderService();r.render(p,str(tmp_path/'full.mp4'))
    r.render_preview_window(p,str(tmp_path/'window.mp4'),.5,1)
    arrays=[]
    for name,at in [('full',.75),('window',.25)]:
        out=tmp_path/f'{name}.png'
        subprocess.run(['ffmpeg','-v','error','-y','-ss',str(at),'-i',str(tmp_path/f'{name}.mp4'),'-frames:v','1',str(out)],check=True,capture_output=True)
        arrays.append(np.array(Image.open(out)).astype(float))
    assert np.abs(arrays[0]-arrays[1]).mean()<3
    assert original==json.dumps(p.to_dict(),sort_keys=True)
    image=np.array(frame(dict(layer='all',duration=1.3),160,96,.6))
    assert image[:,:,3].max()>100 and np.count_nonzero(image[:,:,3]==0)>200
    assert np.array_equal(image,np.array(frame(dict(layer='all',duration=1.3),160,96,.6)))
    assert anchor(dict(anchorFrames=[dict(time=0,x=.2,y=.3),dict(time=1,x=.6,y=.7)]),.5)==pytest.approx((.4,.5))


def test_stalled_fragment_export_recovers_without_resetting_motion_clock(tmp_path):
    from cutvoke.rendering.render import RenderError
    class FirstWindowStalls(RenderService):
        stalled = False
        def _run_ffmpeg(self, *args, **kwargs):
            if kwargs.get('lossless') and not self.stalled:
                self.stalled = True
                raise RenderError('preview encoder stopped making progress after one retry')
            return super()._run_ffmpeg(*args, **kwargs)
    p = project(tmp_path, dict(effectId='cutvoke.fx.shatter', params=dict(
        start=1.25, duration=1.5, rows=7, columns=2, spread=.4)))
    p.sequence.tracks[0].clips[0].timeline_end = R.of(5)
    RenderService().render(p, str(tmp_path/'reference.mp4'))
    result = FirstWindowStalls()._render_in_windows(p.sequence,
        str(tmp_path/'recovered.mp4'), 'high', (0, 0), None, False,
        '', None, None, None, 0, 5, [])
    assert result['windowCount'] == 3
    assert any('reduced export windows' in w for w in result['warnings'])
    for at in [1.8, 2.1, 2.5, 4.5]:
        arrays = []
        for name in ['reference', 'recovered']:
            target = tmp_path/f'{name}-{at}.png'
            subprocess.run(['ffmpeg','-v','error','-y','-ss',str(at),'-i',
                str(tmp_path/f'{name}.mp4'),'-frames:v','1',str(target)],
                check=True,capture_output=True)
            arrays.append(np.asarray(Image.open(target),dtype=float))
        assert np.abs(arrays[0]-arrays[1]).mean() < 3


def test_organic_and_tiled_text_masks_preserve_subject_alpha(tmp_path):
    r=RenderService()
    p=project(tmp_path,dict(effectId='cutvoke.fx.mask',params=dict(shape='ink',x=.5,y=.5,feather=.08,motion='minimum-jerk',motionDuration=1)))
    counts=[]
    for at in [0,.35,.7,1.25]:
        path=tmp_path/f'ink-{at}.png';r.extract_still(p,at,str(path),alpha=True)
        alpha=np.array(Image.open(path).convert('RGBA'))[:,:,3]
        counts.append(np.count_nonzero(alpha>128));assert alpha[0,0]==0
    assert counts[0]==0 and counts[-1]>500 and counts[0]<counts[1]<counts[2]<=counts[3]
    p.sequence.tracks[0].clips[0].effects=[dict(effectId='cutvoke.fx.mask',params=dict(shape='text',content='CUTVOKE',fontSize=.09,tileText=True,tileSpacing=1.15,tileDrift=.08,feather=0))]
    r.extract_still(p,.5,str(tmp_path/'text.png'),alpha=True)
    alpha=np.array(Image.open(tmp_path/'text.png').convert('RGBA'))[:,:,3]
    assert np.count_nonzero(alpha)>100 and alpha[0,0]==0
    assert np.count_nonzero(np.any(alpha>100,axis=1))>20


def test_vfx_params_validate_and_legacy_masks_do_not_gain_defaults():
    registry=default_registry()
    with pytest.raises(EffectParamInvalid):registry.validate_params('cutvoke.fx.lightWeave',{'anchorFrames':[dict(time=1,x=.2,y=.4),dict(time=0,x=.5,y=.5)]})
    with pytest.raises(EffectParamInvalid):registry.validate_params('cutvoke.fx.shatter',{'rows':1000})
    params=dict(shape='rect',x=.1,y=.1,w=.7,h=.7,feather=.05,invert=False)
    assert registry.validate_params('cutvoke.fx.mask',params)==params
