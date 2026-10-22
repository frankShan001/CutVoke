"""Draft computation changes resolution, never editable positions or export."""
import copy
import subprocess

import numpy as np
from PIL import Image,ImageDraw

from cutvoke.domain.keyframes import Keyframe
from cutvoke.domain.model import AssetReference,Clip,Track
from cutvoke.domain.rational import Rational as R
from cutvoke.rendering.render import RenderService
from cutvoke.editing.service import EditService
from cutvoke.rendering.preview_quality import snapshot
from cutvoke.interfaces.httpapi import HttpApi


def test_preview_resamples_before_effects_and_preserves_canvas_geometry(tmp_path):
    source=tmp_path/'source.png'
    image=Image.new('RGBA',(512,288),(0,0,0,0))
    draw=ImageDraw.Draw(image)
    draw.rectangle((130,65,370,230),fill=(220,90,40,180))
    image.save(source)
    project=EditService().create_project('draft',width=1024,height=576,fps=R.of(24))
    clip=Clip('c',AssetReference('a',str(source)),R.of(0),R.of(1),R.of(0),effects=[
        dict(effectId='cutvoke.transform',params=dict(scale=.8,position=dict(x=71,y=39))),
        dict(effectId='cutvoke.fx.mask',params=dict(shape='circle',x=.5,y=.5,w=.75,h=.8,feather=.12))])
    clip.keyframes={'x':[Keyframe('a',R.of(0),71),Keyframe('b',R.of(1),81)]}
    project.sequence.tracks=[Track('v','video',[clip])]
    original=copy.deepcopy(project.to_dict())
    preview,scale=snapshot(project,maximum=640)
    assert scale==.625
    assert preview.sequence.width==640
    assert preview.sequence.tracks[0].clips[0].keyframes['x'][0].value==44.375
    renderer=RenderService(gpu_effects=False)
    result=renderer.render_preview_window(project,str(tmp_path/'draft.mp4'),0,1)
    assert (result['width'],result['height'])==(1024,576)
    full=renderer.render(project,str(tmp_path/'full.mp4'),quality='high')
    assert (full['width'],full['height'])==(1024,576)
    frames=[]
    for name in ['draft','full']:
        raw=subprocess.run(['ffmpeg','-v','error','-ss','0.5','-i',str(tmp_path/(name+'.mp4')),
            '-vf','scale=640:360','-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],
            capture_output=True,check=True).stdout
        frames.append(np.frombuffer(raw,np.uint8).reshape(360,640,3).astype(float))
    assert np.abs(frames[0]-frames[1]).mean()<2
    # The fast seek poster must use the same draft geometry as playback, while
    # the explicit still export below keeps the original pixel dimensions.
    poster=tmp_path/'poster.png'
    renderer.extract_frame(project,.5,str(poster),size=(640,360))
    image=np.asarray(Image.open(poster).convert('RGB')).astype(float)
    assert image.shape==(360,640,3)
    assert np.abs(image-frames[0]).mean()<2
    still=tmp_path/'full-still.png'
    renderer.extract_still(project,.5,str(still))
    assert Image.open(still).size==(1024,576)
    assert project.to_dict()==original
    assert getattr(renderer._render_ctx,'preview_scale',1)==1


def test_draft_preserves_title_size_and_pixel_effects_without_mutating_authority(tmp_path):
    project=EditService().create_project('scale',width=1920,height=1080)
    clip=Clip('c',AssetReference('a','source.png'),R.of(0),R.of(2),R.of(0),effects=[
        dict(effectId='cutvoke.text',params=dict(fontSize=90,strokeWidth=3,x=.2,y=.4)),
        dict(effectId='cutvoke.fx.chromatic',params=dict(offset=6)),
        dict(effectId='cutvoke.fx.glow',params=dict(intensity=.8))])
    project.sequence.tracks=[Track('v','video',[clip])]
    original=project.to_dict()
    scaled,ratio=snapshot(project)
    effects=scaled.sequence.tracks[0].clips[0].effects
    # Title units are defined on a 1080-high design canvas. The title renderer
    # already converts them to pixels; rescaling them here would shrink twice.
    assert effects[0]['params']['fontSize']==90
    assert effects[0]['params']['x']==.2
    assert effects[1]['params']['offset']==4
    assert effects[2]['params']['_previewScale']==ratio
    assert project.to_dict()==original


def test_actual_title_glyph_bounds_match_full_frame_after_downsampling(tmp_path):
    project=EditService().create_project('title-size',width=1024,height=576,fps=R.of(24))
    title=Clip('title',AssetReference('',''),R.of(0),R.of(1),R.of(0),effects=[
        dict(effectId='cutvoke.text',params=dict(content='CHASE THE LIGHT',fontFamily='Barlow Condensed',
            fontSize=150,bold=True,color='#FFFFFF',x=.5,y=.5,animInStyle='none',
            animOutStyle='none',animLoopStyle='none',strokeWidth=0,shadow=0,animGlow=0))])
    project.sequence.tracks=[Track('t','text',[title])]
    renderer=RenderService(gpu_effects=False)
    renderer.render_preview_window(project,str(tmp_path/'draft.mp4'),0,.5)
    renderer.render(project,str(tmp_path/'full.mp4'),quality='high')
    bounds=[]
    for name in ['draft','full']:
        raw=subprocess.run(['ffmpeg','-v','error','-ss','0.25','-i',str(tmp_path/(name+'.mp4')),
            '-vf','scale=640:360','-frames:v','1','-f','rawvideo','-pix_fmt','gray','pipe:1'],
            capture_output=True,check=True).stdout
        y,x=np.where(np.frombuffer(raw,np.uint8).reshape(360,640)>100)
        bounds.append(np.array([x.min(),y.min(),x.max(),y.max()]))
    assert np.abs(bounds[0]-bounds[1]).max()<=2


def test_large_single_clip_late_window_uses_its_rebased_source_interval(tmp_path):
    source=tmp_path/'red-blue.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=red:s=1024x576:r=24:d=3',
        '-f','lavfi','-i','color=blue:s=1024x576:r=24:d=3','-filter_complex',
        '[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]','-c:v','libx264',
        '-preset','ultrafast',str(source)],capture_output=True,check=True)
    service=EditService()
    project=service.create_project('late-proxy',width=1024,height=576,fps=R.of(24))
    project.sequence.tracks=[Track('v','video',[Clip('c',AssetReference('a',str(source)),
        R.of(0),R.of(6),R.of(0))])]
    renderer=RenderService(gpu_effects=False)
    api=HttpApi(service,renderer,media_dir=str(tmp_path/'cache'))
    try:
        path,_,duration=api._preview_window_file(project,1)
        info=renderer.probe_media(path)
        assert duration==2 and (info['width'],info['height'])==(1024,576)
        for at,channel in [('0.5',0),('1.5',2)]:
            raw=subprocess.run(['ffmpeg','-v','error','-ss',at,'-i',path,'-frames:v','1',
                '-f','rawvideo','-pix_fmt','rgb24','pipe:1'],capture_output=True,check=True).stdout
            color=np.frombuffer(raw,np.uint8).reshape(-1,3).mean(axis=0)
            assert color[channel]>240 and np.delete(color,channel).max()<10
    finally:
        api.close()


def test_browser_title_snapshot_preserves_duration_and_export_authority(tmp_path):
    from cutvoke.rendering.editor_preview import browser_titles
    from cutvoke.rendering.preview_prepare import preview_identity
    service = EditService()
    project = service.create_project('live-title', width=1920, height=1080)
    title = Clip('title', AssetReference('', ''), R.of(0), R.of(1), R.of(0), effects=[
        dict(effectId='cutvoke.text', params=dict(content='LIVE', fontSize=120, bold=True,
            fontFamily='Barlow Condensed', color='#ffffff', strokeWidth=0, shadow=0))])
    project.sequence.tracks = [Track('t', 'text', [title])]
    original = copy.deepcopy(project.to_dict())
    base = browser_titles(project)
    assert base.sequence.tracks[0].clips[0].timeline_end == R.of(1)
    assert base.sequence.tracks[0].clips[0].effects[0]['params']['content'] == ''
    from cutvoke.interfaces.httpapi import PREVIEW_RENDER_CACHE_VERSION
    first_key = preview_identity(base, PREVIEW_RENDER_CACHE_VERSION)
    title.effects[0]['params']['content'] = 'EDITED'
    assert preview_identity(browser_titles(project), PREVIEW_RENDER_CACHE_VERSION) == first_key
    title.effects[0]['params']['content'] = 'LIVE'
    renderer = RenderService(gpu_effects=False)
    api = HttpApi(service, renderer, media_dir=str(tmp_path/'cache'))
    try:
        api._require_current_preview(base, first_key)
        renderer.extract_still(base, .5, str(tmp_path/'base.png'))
        renderer.extract_still(project, .5, str(tmp_path/'export.png'))
        assert np.asarray(Image.open(tmp_path/'base.png').convert('RGB')).max() == 0
        assert np.count_nonzero(np.asarray(Image.open(tmp_path/'export.png').convert('RGB')) > 200) > 500
        assert project.to_dict() == original
        result = renderer.render_preview_window(base, str(tmp_path/'base.mp4'), 0, .5)
        assert (result['width'], result['height']) == (1280, 720)
    finally:
        api.close()
