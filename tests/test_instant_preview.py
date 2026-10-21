"""Cold seeks preserve local clocks, finite history and editable authority."""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image, ImageDraw

from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.keyframes import Keyframe
from cutvoke.core.rational import Rational as R
from cutvoke.core.service import EditService
from cutvoke.core.render import RenderService, RenderCancelled
from cutvoke.core.httpapi import HttpApi
from cutvoke.core.preview_window import bounded


def scene(tmp_path):
    source=tmp_path/'source.png'
    image=Image.new('RGBA',(160,90),(20,70,160,190))
    ImageDraw.Draw(image).rectangle((20,10,65,60),fill=(230,60,30,230))
    image.save(source)
    p=EditService().create_project('instant',width=160,height=90,fps=R.of(24))
    c=Clip('c',AssetReference('a',str(source)),R.of(0),R.of(12),R.of(0),effects=[
        dict(effectId='cutvoke.fx.mask',params=dict(shape='ink',motion='minimum-jerk',
             motionStart=5.,motionDuration=2.,inkSeed=27,x=.5,y=.5,feather=.1)),
        dict(effectId='cutvoke.fx.trail',params=dict(frames=4,decay=.6)),
        dict(effectId='cutvoke.transform',params=dict(scale=.85,position=dict(x=9,y=3)))])
    c.keyframes={'x':[Keyframe('x0',R.of(0),5),Keyframe('x1',R.of(12),18)],
                'opacity':[Keyframe('o0',R.of(0),.1),Keyframe('o1',R.of(12),.9)]}
    p.sequence.tracks=[Track('v','video',[c])]
    return p


@pytest.mark.parametrize('gpu',[False,True])
def test_late_unrendered_matte_geometry_and_trail_match_unsliced_renderer(tmp_path,gpu):
    p=scene(tmp_path); renderer=RenderService(gpu_effects=gpu)
    authority=copy.deepcopy(p.to_dict())
    for at in (5.5,6.25,8.5):
        paths=[tmp_path/f'{at}-{kind}.png' for kind in ('bounded','reference')]
        renderer.extract_frame(p,at,str(paths[0]))
        with patch('cutvoke.core.preview_window.bounded',lambda p,w,r:(p,{},None)):
            renderer.extract_frame(p,at,str(paths[1]))
        a,b=[np.asarray(Image.open(f).convert('RGB')).astype(float) for f in paths]
        assert np.abs(a-b).mean()<1.5
    assert p.to_dict()==authority
    assert getattr(renderer._render_ctx,'clip_clocks',{})=={}


def test_unknown_temporal_effect_and_transition_keep_authority(tmp_path):
    p=scene(tmp_path);renderer=RenderService()
    for eid in ('cutvoke.fx.filmGrain','cutvoke.transition.fade','cutvoke.anim.fadeIn'):
        p.sequence.tracks[0].clips[0].effects.append(dict(effectId=eid,params={}))
        prepared,clocks,origin=bounded(p,(5,6),renderer)
        assert prepared is p and not clocks and origin is None
        p.sequence.tracks[0].clips[0].effects.pop()


def test_cpu_gpu_multitrack_composition_keeps_alpha_order(tmp_path):
    p=scene(tmp_path); clip=p.sequence.tracks[0].clips[0]
    clip.effects=[dict(effectId='cutvoke.transform',params=dict(opacity=.65))]
    clip.keyframes={}
    top=copy.deepcopy(clip);top.id='top';top.timeline_end=R.of(49,8);top.effects=[dict(effectId='cutvoke.transform',
        params=dict(scale=.5,opacity=.35,position=dict(x=50,y=18)))]
    p.sequence.tracks.append(Track('top','video',[top]))
    frames=[]
    for enabled in (False,True):
        r=RenderService(gpu_effects=enabled)
        with patch.object(r,'_run_ffmpeg',wraps=r._run_ffmpeg) as run:
            result=r.render_preview_window(p,str(tmp_path/f'{enabled}.mp4'),6,0.25)
        assert run.call_count==1
        if enabled and not r._gpu_failed and result['effectProcessor'].startswith('opencl:'):
            assert 'multi-track-composite' in result['gpuEffects']
        png=tmp_path/f'{enabled}.png'
        import subprocess
        subprocess.run([r.ffmpeg,'-v','error','-y','-i',result['output_path'],
                        '-ss','0.166666667','-frames:v','1',str(png)],check=True)
        frames.append(np.asarray(Image.open(png).convert('RGB')).astype(float))
    assert np.abs(frames[0]-frames[1]).mean()<2


def test_stills_do_not_wait_for_background_video_slot(tmp_path):
    p=scene(tmp_path);service=EditService();service._projects[p.project_id]=p
    class Fake:
        def extract_frame(self,project,at,path,**kwargs):
            Path(path).write_bytes(b'frame');return path
    api=HttpApi(service,Fake(),media_dir=str(tmp_path/'media'))
    try:
        with api._preview_scheduler.slot(threading.Event()):
            with ThreadPoolExecutor(1) as pool:
                assert Path(pool.submit(api._preview_frame_file,p,5,None).result(timeout=2)).read_bytes()==b'frame'
    finally:api.close()


def test_new_frame_cancels_older_position_before_publication(tmp_path):
    p=scene(tmp_path);service=EditService();service._projects[p.project_id]=p
    started=threading.Event()
    class BlockingRenderer(RenderService):
        def extract_frame(self,project,at,path,**kwargs):
            event=kwargs['cancel_event']
            if at==5:
                started.set();assert event.wait(2)
                raise RenderCancelled('superseded')
            Path(path).write_bytes(b'latest');return path
    api=HttpApi(service,BlockingRenderer(),media_dir=str(tmp_path/'media'))
    try:
        with ThreadPoolExecutor(2) as pool:
            first=pool.submit(api._preview_frame_file,p,5,None)
            assert started.wait(1)
            second=pool.submit(api._preview_frame_file,p,6,None)
            with pytest.raises(RenderCancelled):first.result(timeout=2)
            assert Path(second.result(timeout=2)).read_bytes()==b'latest'
        assert not api._frame_active_jobs
    finally:api.close()
