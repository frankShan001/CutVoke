import copy
import subprocess

import numpy as np
import pytest
from PIL import Image

from cutvoke.domain.keyframes import Keyframe, crop_keyframes, evaluate
from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.protocol import Command
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditService, EditError
from cutvoke.rendering.render import RenderService
from cutvoke.storage.store import ProjectStore


def test_hold_values_and_crop_retain_the_discontinuity_at_exact_key_time():
    keys=[Keyframe('a',R.of(0),.2,'hold'),Keyframe('b',R.of(2),.8,'hold'),Keyframe('c',R.of(3),1)]
    assert evaluate(keys,R.of(1999,1000))==.2
    assert evaluate(keys,R.of(2))==.8
    for start,end in ((.3,1.7),(.3,2.7),(2,3)):
        cropped=crop_keyframes(keys,R.from_float(start),R.from_float(end))
        assert any(key.interpolation=='hold' for key in cropped)
        for at in np.linspace(0,end-start,51):
            assert evaluate(cropped,R.from_float(at))==evaluate(keys,R.from_float(start+at))


def test_hold_preview_matches_export_pixels_across_a_discontinuous_key(tmp_path):
    source=tmp_path/'image.png';Image.new('RGB',(160,90),(150,100,50)).save(source)
    project=EditService().create_project('hold',width=160,height=90,fps=R.of(10))
    clip=Clip('c',AssetReference('image',str(source)),R.of(0),R.of(3),R.of(0))
    clip.keyframes={'opacity':[Keyframe('a',R.of(0),0,'hold'),Keyframe('b',R.of(1),1,'hold')]}
    project.sequence.tracks=[Track('v','video',[clip])]
    renderer=RenderService(gpu_effects=False);renderer.gpu_acceleration=False
    full=tmp_path/'full.mp4';preview=tmp_path/'preview.mp4'
    renderer.render(project,str(full),quality='high')
    renderer.render_preview_window(project,str(preview),.6,1.4)
    def frames(path):
        result=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-an','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],capture_output=True,check=True)
        return np.frombuffer(result.stdout,np.uint8).reshape(-1,90,160,3).astype(float)
    whole,part=frames(full),frames(preview)
    assert whole[:10].max()<5
    assert whole[10:20].mean()>60
    assert np.abs(whole[6:20]-part).mean()<3


def test_bulk_interpolation_is_one_undo_and_rejects_partial_invalid_updates(tmp_path):
    store=ProjectStore(str(tmp_path/'library.sqlite'));service=EditService(store)
    project=service.create_project('p',width=160,height=90)
    source=tmp_path/'source.png';Image.new('RGB',(160,90),'blue').save(source)
    def command(kind,**payload):
        return service.execute(Command(kind,payload,project_id='p',expected_revision=service.get_project('p').revision))
    try:
        command('clip.insert',clipId='c',trackId='v',createTrackKind='video',sourcePath=str(source),
                timelineEnd={'num':'3','den':'1'})
        command('clip.keyframe',clipId='c',action='batch',keyframes=[{'param':'opacity','time':i/20,'value':i%2} for i in range(40)])
        before=copy.deepcopy(service.get_project('p').sequence.tracks[0].clips[0].keyframes['opacity'])
        ids=[key.id for key in before]
        command('clip.keyframe',clipId='c',param='opacity',action='interpolate',keyframeIds=ids,interpolation='cubic-bezier',bezier=[1/3,0,2/3,1])
        lane=store.load('p').sequence.tracks[0].clips[0].keyframes['opacity']
        assert all(key.interpolation=='cubic-bezier' for key in lane)
        assert [(key.id,key.time,key.value) for key in before]==[(key.id,key.time,key.value) for key in lane]
        command('history.undo')
        assert store.load('p').sequence.tracks[0].clips[0].keyframes['opacity']==before
        snapshot=store.load('p').to_dict()
        with pytest.raises(EditError):
            command('clip.keyframe',clipId='c',param='opacity',action='interpolate',keyframeIds=[ids[0],'missing'],interpolation='hold')
        assert store.load('p').to_dict()==snapshot
    finally:service.close();store.close()


def test_replacing_one_effect_parameter_object_removes_optional_geometry_and_preserves_sibling(tmp_path):
    service=EditService();project=service.create_project('p');source=tmp_path/'source.png';Image.new('RGB',(32,32),'blue').save(source)
    def command(kind,**payload):
        return service.execute(Command(kind,payload,project_id='p',expected_revision=service.get_project('p').revision))
    try:
        command('clip.insert',clipId='c',trackId='v',createTrackKind='video',sourcePath=str(source))
        for intensity in [.3,.8]:
            command('effect.add',clipId='c',effectId='cutvoke.fx.opticalField',params={'intensity':intensity,'reflectionRegion':{'y':.6,'height':.4}})
        clip=service.get_project('p').sequence.tracks[0].clips[0];first,second=copy.deepcopy(clip.effects)
        command('effect.update',clipId='c',instanceId=second['instanceId'],params={'intensity':.6},replaceParams=True)
        effects=service.get_project('p').sequence.tracks[0].clips[0].effects
        assert effects[0]==first and 'reflectionRegion' not in effects[1]['params']
        command('effect.update',clipId='c',instanceId=second['instanceId'],params={},replaceParams=True)
        assert service.get_project('p').sequence.tracks[0].clips[0].effects[1]['params']['intensity']==1
        command('history.undo')
        assert service.get_project('p').sequence.tracks[0].clips[0].effects[1]['params']['intensity']==.6
    finally:service.close()
