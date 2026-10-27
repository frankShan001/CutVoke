import copy

import numpy as np
import pytest
from PIL import Image

from cutvoke.editing.service import EditError
from cutvoke.rendering.camera_plane import project_corners
from tests.support.composition_fixtures import setup, frames


def test_camera_projection_window_clock_alpha_and_actual_geometry(tmp_path):
    service, store, renderer, command=setup(tmp_path)
    try:
        image=np.zeros((64,96,4),np.uint8)
        image[8:56,8:88]=[220,60,20,255]
        image[16:48,24:72]=[20,210,60,255]
        Image.fromarray(image).save(tmp_path/'red.png')
        command('effect.add',clipId='base',effectId='cutvoke.fx.cameraPlane',params={
            'poseFrames':[{'at':0,'rotationY':-35,'rotationX':15},{'at':2,'rotationY':35,'rotationX':-15}]})
        project=service.get_project('composition');before=copy.deepcopy(project.to_dict())
        output,preview=tmp_path/'full.mp4',tmp_path/'preview.mp4'
        renderer.render(project,str(output),quality='high')
        renderer.render_preview_window(project,str(preview),1,.8)
        full=frames(output);short=frames(preview)
        assert len(short)==8
        assert np.abs(full[10:18]-short).mean()<4
        assert np.abs(full[0]-full[20]).mean()>6
        assert full[:,0,0].max()<12
        assert project.to_dict()==before
        assert project_corners({},96,64)==[(0.,0.),(96.,0.),(0.,64.),(96.,64.)]
        corners=project_corners({'rotationY':35},96,64)
        assert corners[0][1]!=corners[1][1]
        assert project_corners({'positionZ':-4},96,64) is None
        command('history.undo')
        assert not service.get_project('composition').sequence.tracks[0].clips[0].effects
        with pytest.raises(EditError):
            command('effect.add',clipId='base',effectId='cutvoke.fx.cameraPlane',params={'poseFrames':[{'at':1},{'at':0}]})
    finally:
        service.close();store.close()


def test_camera_near_plane_and_large_source_are_bounded(tmp_path):
    service,store,renderer,command=setup(tmp_path)
    try:
        Image.new('RGBA',(160,90),(210,60,20,255)).save(tmp_path/'red.png')
        command('effect.add',clipId='base',effectId='cutvoke.fx.cameraPlane',params={'positionZ':-4})
        output=tmp_path/'hidden.mp4';renderer.render_preview_window(service.get_project('composition'),str(output),0,.2)
        assert frames(output).max()<12
    finally:
        service.close();store.close()
