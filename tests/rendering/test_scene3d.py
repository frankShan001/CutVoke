import copy
import numpy as np
import pytest
from PIL import Image

from cutvoke.rendering.scene3d import frame,availability
from cutvoke.rendering.scene_geometry import import_obj
from cutvoke.editing.service import EditError
from tests.support.composition_fixtures import setup,frames


def scene(renderer='software'):
    return {'renderer':renderer,'objects':[
        {'id':'front','shape':'plane','color':'#ff0000','z':.5,'scaleX':.65,'scaleY':.65},
        {'id':'back','shape':'plane','color':'#0000ff','z':-.5,'scaleX':1.2,'scaleY':1.2}],
        'camera':{'z':5,'fov':45},'light':{'ambient':1,'intensity':0},'depthLayers':4}


@pytest.mark.parametrize('renderer',['software','opengl'])
def test_pixel_depth_is_independent_of_object_order_and_handles_alpha_layers(renderer):
    if renderer=='opengl' and availability()['defaultRenderer']!='opengl':pytest.skip('OpenGL 3.3 unavailable; software path tested')
    params=scene(renderer)
    before=copy.deepcopy(params);a=np.asarray(frame(params,96,64,0))
    params['objects'].reverse();b=np.asarray(frame(params,96,64,0))
    assert np.array_equal(a,b)
    assert a[32,48,0]>240 and a[32,48,2]<10
    assert a[32,63,2]>240
    params=before;params['objects'][0]['opacity']=.5
    rgba=np.asarray(frame(params,96,64,0));assert 115<rgba[32,48,0]<140 and 115<rgba[32,48,2]<140
    # Transparent texture pixels must not write an opaque occluder into depth.
    assert rgba[0,0,3]==0 and rgba[32,48,3]==255


def test_near_plane_clipping_and_hardware_software_agree():
    params=scene();params['objects'][0].update(rotationY=55,z=4.8,scaleX=.5)
    software=np.asarray(frame(params,96,64,.2))
    assert np.isfinite(software).all()
    if availability()['defaultRenderer']=='opengl':
        params['renderer']='opengl';hardware=np.asarray(frame(params,96,64,.2))
        assert np.abs(software.astype(float)-hardware).mean()<2


def test_scene_layer_preview_export_mesh_import_and_undo_are_editable(tmp_path):
    service,store,renderer,command=setup(tmp_path)
    try:
        command('composition.scene3d',clipId='scene',timelineEnd=2)
        project=service.get_project('composition');clip=project.sequence.tracks[-1].clips[0];instance=clip.effects[0]['instanceId']
        params=scene();params['objects'][0]['poseFrames']=[{'at':0,'x':-.3},{'at':2,'x':.3}]
        command('effect.update',clipId='scene',instanceId=instance,params=params,replaceParams=True)
        project=service.get_project('composition');before=copy.deepcopy(project.to_dict())
        full,preview=tmp_path/'full.mp4',tmp_path/'preview.mp4'
        renderer.render(project,str(full),quality='high');renderer.render_preview_window(project,str(preview),1,.5)
        assert np.abs(frames(full)[10:15]-frames(preview)).mean()<4
        png=tmp_path/'frame.png';renderer.extract_frame(project,1,str(png))
        still=np.asarray(Image.open(png).convert('RGB'));encoded=frames(full)[10]
        # YUV420 blurs the small fixture's hard colored edges. Check the
        # geometry clock separately from that expected compression error.
        assert np.abs(encoded-still).mean()<8
        def center(image):return np.argwhere((image[:,:,0]>230)&(image[:,:,1]<30)&(image[:,:,2]<30)).mean(axis=0)
        assert np.abs(center(encoded)-center(still)).max()<1
        assert project.to_dict()==before
        command('scene3d.import',clipId='scene',instanceId=instance,objText='v 0 1 0\nv -1 -1 0\nv 1 -1 0\nf 1 2 3',objectId='triangle')
        assert store.load('composition').sequence.tracks[-1].clips[0].effects[0]['params']['objects'][-1]['triangles']==[{'a':0,'b':1,'c':2}]
        command('history.undo')
        assert len(store.load('composition').sequence.tracks[-1].clips[0].effects[0]['params']['objects'])==2
        with pytest.raises(EditError):command('scene3d.import',clipId='scene',instanceId=instance,objText='v 0 1 0\nf 1 2 3')
    finally:service.close();store.close()


def test_obj_import_is_bounded_and_does_not_fetch_material_paths():
    mesh,warnings=import_obj('mtllib https://example.invalid/a.mtl\nv -1 -1 0\nv 1 -1 0\nv 1 1 0\nv -1 1 0\nf -4 -3 -2 -1')
    assert len(mesh['triangles'])==2 and warnings
    with pytest.raises(ValueError):import_obj('v nan 0 1\nf 1 1 1')


@pytest.mark.parametrize('renderer',['software','opengl'])
def test_transparent_material_holes_reveal_deeper_geometry_and_texture_cache_invalidates(tmp_path,renderer):
    if renderer=='opengl' and availability()['defaultRenderer']!='opengl':pytest.skip('OpenGL unavailable')
    path=tmp_path/'texture.png';image=Image.new('RGBA',(16,16),(255,255,255,255));image.paste((255,255,255,0),(5,5,11,11));image.save(path)
    params=scene(renderer);params['objects'][0]['texturePath']=str(path)
    first=np.asarray(frame(params,96,64,0));assert first[32,48,2]>240 and first[32,48,0]<10
    image.paste((255,255,255,255),(0,0,16,16));image.save(path)
    import os
    stat=path.stat();os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))
    second=np.asarray(frame(params,96,64,0));assert second[32,48,0]>240 and second[32,48,2]<10


def test_invalid_scene_material_is_rejected_before_commit(tmp_path):
    service,store,renderer,command=setup(tmp_path)
    try:
        before=service.get_project('composition').revision
        params=scene();params['objects'][0]['color']='#ff000080'
        with pytest.raises(EditError):command('effect.add',clipId='base',effectId='cutvoke.fx.scene3d',params=params)
        assert service.get_project('composition').revision==before
    finally:service.close();store.close()


def test_missing_material_blocks_preflight_and_export(tmp_path):
    from cutvoke.domain.project_health import project_preflight
    from cutvoke.rendering.render import RenderError
    service,store,renderer,command=setup(tmp_path)
    try:
        path=tmp_path/'material.png';Image.new('RGB',(16,16),'white').save(path)
        params=scene();params['objects'][0]['texturePath']=str(path)
        command('effect.add',clipId='base',effectId='cutvoke.fx.scene3d',params=params)
        path.unlink();project=service.get_project('composition');report=project_preflight(project)
        assert not report['readyToRender'] and any(error.get('resourceKind')=='texture' for error in report['errors'])
        with pytest.raises(RenderError):renderer.render(project,str(tmp_path/'missing.mp4'),quality='high')
    finally:service.close();store.close()
