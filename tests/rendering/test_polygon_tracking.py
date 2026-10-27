import subprocess

import numpy as np

from cutvoke.rendering.polygon_tracking import track_polygon
from tests.support.composition_fixtures import setup, frames


def video(path, moving=True):
    images=[]
    for index in range(16):
        pixels=np.full((64,96),30,np.uint8)
        if moving:
            yy,xx=np.mgrid[:24,:24]
            pixels[16:40,20+index*2:44+index*2]=((xx//3+yy//3)%2)*140+80
        images.append(pixels)
    subprocess.run(['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','gray','-s','96x64','-r','8','-i','pipe:0',
                    '-an','-c:v','libx264','-crf','8','-pix_fmt','yuv420p',str(path)],input=b''.join(image.tobytes() for image in images),capture_output=True,check=True)


def test_dense_polygon_flow_tracks_vertices_and_reports_lost_signal(tmp_path):
    path=tmp_path/'moving.mp4';video(path)
    points=[{'x':x/96,'y':y/64} for x,y in [(21,17),(42,17),(42,38),(21,38)]]
    result=track_polygon(str(path),points,seconds=2,sample_fps=8)
    assert result['status']=='completed'
    assert result['sampleCount']==16
    assert result['pathFrames'][-1]['points'][0]['x']-points[0]['x']>.2
    assert result['minimumConfidence']>.35
    path=tmp_path/'blank.mp4';video(path,False)
    lost=track_polygon(str(path),points,seconds=2,sample_fps=8)
    assert lost['status']=='lost' and lost['lostAt']==.125
    assert len(lost['pathFrames'])==1


def test_editable_roto_keyframes_use_same_mask_in_preview_and_export(tmp_path):
    service,store,renderer,command=setup(tmp_path)
    try:
        first=[{'x':x,'y':y} for x,y in [(0,.2),(.4,.2),(.4,.8),(0,.8)]]
        last=[{'x':point['x']+.5,'y':point['y']} for point in first]
        command('effect.add',clipId='base',effectId='cutvoke.fx.trackedMask',params={'points':first,'pathFrames':[{'at':0,'points':first},{'at':2,'points':last}],'feather':0})
        project=service.get_project('composition')
        full,part=tmp_path/'full.mp4',tmp_path/'part.mp4'
        renderer.render(project,str(full),quality='high')
        renderer.render_preview_window(project,str(part),1,.8)
        rgb=frames(full)
        assert rgb[0,30,10,0]>150 and rgb[0,30,80].max()<10
        assert rgb[20,30,10].max()<10 and rgb[20,30,70,0]>150
        assert np.abs(rgb[10:18]-frames(part)).mean()<4
    finally:
        service.close();store.close()
