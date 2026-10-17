"""Actual moving alpha, physical velocity, persistence, and seek parity."""
import copy
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from cutvoke.core.mask_motion import progress
from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational as R
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore


def test_rest_to_rest_velocity_and_acceleration():
    for curve in ['minimum-jerk', 'inertia']:
        p = lambda t: progress(t, .2, 1, curve)
        dt = 1e-5
        assert p(0) == 0 and p(2) == 1
        assert abs((p(.2+dt)-p(.2))/dt) < .001
        assert abs((p(1.2)-p(1.2-dt))/dt) < .001
        assert (p(.7+dt)-p(.7))/dt > 1
        samples = [p(t) for t in np.linspace(0, 2, 100)]
        assert all(b >= a for a, b in zip(samples, samples[1:]))
    assert progress(.3, 0, 1, 'inertia') == pytest.approx(.2333333333)


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
@pytest.mark.parametrize('shape', ['rect', 'circle', 'freehand', 'text', 'wipe'])
def test_moving_mask_is_not_baked_and_seeks_same_as_export(tmp_path, shape):
    origin = 3 if shape == 'wipe' else 0
    source = tmp_path/'white.png'
    # Spatial gradient proves that it is the mask, not the image, that moves.
    rgb = np.zeros((96, 160, 3), dtype=np.uint8)
    rgb[:, :, 0] = np.arange(160)[None, :] + 60
    rgb[:, :, 1:] = 220
    Image.fromarray(rgb).save(source)
    params = dict(shape=shape, x=.3, y=.2, w=.3, h=.6, feather=0,
                  motion='minimum-jerk', motionDuration=1.5,
                  motionFromX=-.5, motionToX=.6)
    if shape == 'freehand':
        params['points'] = [dict(x=.3,y=.2),dict(x=.6,y=.2),dict(x=.6,y=.8),dict(x=.3,y=.8)]
    if shape == 'text':
        params.update(content='M', fontSize=.5)
    if shape == 'wipe':
        params.update(x=.5, y=.5, angle=0)
    db = tmp_path/'project.sqlite'
    with ProjectStore(str(db)) as store:
        service = EditService(store)
        project = service.create_project('mask', width=160, height=96, fps=R.of(24))
        def edit(name,payload,index):
            service.execute(Command(type=name,payload=payload,project_id='mask',
                expected_revision=service.get_project('mask').revision,command_id=f'test-{index}',
                actor=Actor('test','mask-motion')))
        edit('clip.insert',dict(trackId='v',createTrackKind='video',clipId='c',sourcePath=str(source),
             timelineStart=R.of(origin).to_json(),timelineEnd=R.of(origin+2).to_json()),1)
        edit('effect.add',dict(clipId='c',effectId='cutvoke.fx.mask',params=params),2)
        edit('history.undo',{},3)
        assert not service.get_project('mask').sequence.tracks[0].clips[0].effects
        edit('history.redo',{},4)
    with ProjectStore(str(db)) as store:
        project = EditService(store).get_project('mask')
        assert project.sequence.tracks[0].clips[0].effects[0]['params']['motion'] == 'minimum-jerk'
    renderer = RenderService()
    with renderer._static_mask_sources(project) as prepared:
        assert prepared.sequence.tracks[0].clips[0].asset_ref.source_path == str(source)
    renderer.render(project,str(tmp_path/'full.mp4'),quality='high')
    renderer.render_preview_window(project,str(tmp_path/'window.mp4'),origin+.5,1)
    frames=[]
    for at in [.25,.75,1.25]:
        path=tmp_path/f'alpha-{at}.png'
        renderer.extract_still(project,origin+at,str(path),alpha=True)
        frames.append(np.array(Image.open(path).convert('RGBA')))
    assert np.count_nonzero(frames[0][:,:,3] != frames[2][:,:,3]) > 500
    assert frames[1][:,:,3].max() >= 250
    assert np.count_nonzero(frames[1][:,:,3] >= 250) > 150
    # The visible source pixels retain the same gradient despite matte motion.
    for frame in frames:
        visible = frame[:,:,3] > 240
        if visible.any():
            assert np.abs(frame[:,:,:3].astype(float)[visible]-rgb[visible]).mean() < 5
    for filename,at in [('full',origin+.75),('window',.25)]:
        subprocess.run(['ffmpeg','-v','error','-y','-ss',str(at),'-i',str(tmp_path/f'{filename}.mp4'),
                        '-frames:v','1',str(tmp_path/f'{filename}.png')],check=True,capture_output=True)
    a=np.array(Image.open(tmp_path/'full.png')).astype(float)
    b=np.array(Image.open(tmp_path/'window.png')).astype(float)
    assert np.abs(a-b).mean() < 3


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
def test_original_partial_alpha_and_inertial_rotating_matte(tmp_path):
    source=tmp_path/'half-alpha.png'
    Image.new('RGBA',(160,96),(90,180,230,128)).save(source)
    project=EditService().create_project('partial-alpha',width=160,height=96,fps=R.of(24))
    effect=dict(effectId='cutvoke.fx.mask',params=dict(shape='rect',x=.3,y=.2,w=.4,h=.6,
                feather=0,motion='inertia',motionDuration=1,motionFromX=-.3,motionToX=.3,
                motionFromScale=.8,motionToScale=1.3,motionFromRotation=-30,motionToRotation=30))
    clip=Clip('c',AssetReference('a',str(source)),R.of(0),R.of(2),R.of(0),effects=[effect])
    project.sequence.tracks=[Track('v','video',[clip])]
    renderer=RenderService()
    bounds=[]
    for at in [.1,.5,.9]:
        path=tmp_path/f'{at}.png'
        renderer.extract_still(project,at,str(path),alpha=True)
        alpha=Image.open(path).convert('RGBA').getchannel('A')
        assert 120<=alpha.getextrema()[1]<=135
        bounds.append(alpha.getbbox())
    assert bounds[0][0]<bounds[1][0]<bounds[2][0]
