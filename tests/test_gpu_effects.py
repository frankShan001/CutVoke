"""Dispatch real GPU shaders and compare their clocks/coverage to CPU frames."""
from fractions import Fraction
import copy
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from cutvoke.core import gpu_effects
from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.rational import Rational as R
from cutvoke.core.render import RenderService, _fx_mask_expr
from cutvoke.core.service import EditService
from cutvoke.core.vfx_fragments import build


@pytest.fixture(scope='module')
def device():
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg required')
    selected=gpu_effects.device('ffmpeg')
    if not selected:
        pytest.skip('OpenCL GPU unavailable')
    return selected


def decode(source, graph, path, selected=None):
    path.write_text(graph,encoding='utf-8')
    command=['ffmpeg','-v','error','-nostdin','-filter_complex_threads','2']
    if selected:
        command+=['-init_hw_device',selected['specification'],'-filter_hw_device','cutvoke']
    command+=['-loop','1','-framerate','24','-i',str(source),'-/filter_complex',str(path),
              '-map','[v]','-frames:v','24','-pix_fmt','rgba','-f','rawvideo','pipe:1']
    result=subprocess.run(command,capture_output=True,timeout=25)
    assert result.returncode==0,result.stderr.decode(errors='replace')
    return np.frombuffer(result.stdout,np.uint8).reshape(-1,96,160,4).astype(float)


@pytest.mark.parametrize('shape',['rect','circle','wipe','ink','freehand'])
@pytest.mark.parametrize('invert',[False,True])
def test_gpu_matte_matches_cpu_partial_alpha_and_moving_geometry(tmp_path,device,shape,invert):
    source=tmp_path/'source.png'
    Image.new('RGBA',(160,96),(90,180,230,128)).save(source)
    params=dict(shape=shape,x=.42,y=.45,w=.6,h=.5,feather=.12,invert=invert,
                motion='inertia',motionFromRotation=-12,motionToRotation=23,
                motionFromX=-.1,motionToX=.15,motionDuration=.8,
                points=[dict(x=.2,y=.2),dict(x=.8,y=.3),dict(x=.7,y=.8),dict(x=.3,y=.7)])
    cpu=_fx_mask_expr(params)
    expected=decode(source,'[0:v]'+cpu+'[v]',tmp_path/'cpu.ffgraph')
    with gpu_effects.compiler('ffmpeg',Fraction(24)) as gpu:
        formula=cpu.split("a='alpha(X,Y)*(",1)[1][:-2]
        actual=decode(source,'[0:v]'+gpu.matte(formula)+'[v]',tmp_path/'gpu.ffgraph',device)
    delta=np.abs(expected[:,:,:,3]-actual[:,:,:,3])
    assert delta.mean()<.03
    assert delta.max()<=1
    assert actual[:,:,:,3].max()<=128


@pytest.mark.parametrize('mode',['assemble','disperse'])
def test_gpu_fragments_match_cpu_with_alpha_and_radial_scatter(tmp_path,device,mode):
    source=tmp_path/'source.png'
    image=np.zeros((96,160,4),np.uint8)
    image[:,:,0]=np.arange(160)[None,:]+40
    image[:,:,1:3]=160
    image[:,:,3]=128
    Image.fromarray(image).save(source)
    params=dict(rows=6,columns=3,mode=mode,scatterMode='radial',duration=.7,start=.125,seed=71)
    parts=[]
    output=build(params,'0:v',parts,[0],160,96)
    expected=decode(source,';'.join(parts)+f';[{output}]null[v]',tmp_path/'cpu.ffgraph')
    with gpu_effects.compiler('ffmpeg',Fraction(24)) as gpu:
        actual=decode(source,'[0:v]'+gpu.fragments(params,160,96)+'[v]',tmp_path/'gpu.ffgraph',device)
    # Compare visible energy, not arbitrary RGB stored under a transparent pixel.
    a=expected[:,:,:,:3]*expected[:,:,:,3:]/255
    b=actual[:,:,:,:3]*actual[:,:,:,3:]/255
    assert np.abs(a-b).mean()<.5
    assert np.abs(expected[:,:,:,3]-actual[:,:,:,3]).mean()<.5


def test_device_failure_recompiles_cpu_graph_and_preserves_project(tmp_path,device,monkeypatch):
    source=tmp_path/'source.png'
    Image.new('RGBA',(160,96),(90,180,230,128)).save(source)
    project=EditService().create_project('fallback',width=160,height=96,fps=R.of(24))
    project.sequence.tracks=[Track('v','video',[Clip('c',AssetReference('a',str(source)),
        R.of(0),R.of(1),R.of(0),effects=[dict(effectId='cutvoke.fx.mask',
            params=dict(shape='freehand',motion='linear',motionDuration=.8,
                points=[dict(x=.1,y=.1),dict(x=.9,y=.1),dict(x=.8,y=.9)]))])])]
    original=copy.deepcopy(project.to_dict())
    renderer=RenderService()
    original_program=gpu_effects.Compiler.program
    def broken(self,*args,**kwargs):
        original_program(self,*args,**kwargs)
        return 'format=rgba,hwupload,program_opencl=source=missing.cl:kernel=cv_effect,hwdownload,format=rgba'
    monkeypatch.setattr(gpu_effects.Compiler,'program',broken)
    result=renderer.render_preview_window(project,str(tmp_path/'fallback.mp4'),0,.5)
    assert result['effectProcessor']=='cpu'
    assert result['gpuEffects']==[]
    assert renderer._gpu_failed
    assert project.to_dict()==original
    assert getattr(renderer._render_ctx,'gpu',None) is None
