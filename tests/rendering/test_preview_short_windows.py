"""Production windows start sooner without shifting cuts, audio, or seek clocks."""
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from cutvoke.interfaces.httpapi import HttpApi, PREVIEW_WINDOW_SECONDS
from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational as R
from cutvoke.rendering.render import RenderService
from cutvoke.editing.service import EditService


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
def test_two_second_boundary_matches_full_export_with_continuous_audio(tmp_path):
    assert PREVIEW_WINDOW_SECONDS == 2
    images=[]
    for color in ['red','blue']:
        source=tmp_path/f'{color}.png'
        Image.new('RGB',(160,90),color).save(source)
        images.append(str(source))
    tone=tmp_path/'tone.wav'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',
                    'sine=frequency=440:sample_rate=48000:duration=4',str(tone)],
                   check=True,capture_output=True)
    service=EditService()
    project=service.create_project('short-windows',width=160,height=90,fps=R.of(24))
    a=Clip('a',AssetReference('a',images[0]),R.of(0),R.of(2),R.of(0))
    b=Clip('b',AssetReference('b',images[1]),R.of(2),R.of(4),R.of(0),
           effects=[dict(effectId='cutvoke.transition.crossfade',params=dict(duration=.5))])
    sound=Clip('sound',AssetReference('sound',str(tone)),R.of(0),R.of(4),R.of(0))
    project.sequence.tracks=[Track('v','video',[a,b]),Track('a','audio',[sound])]
    renderer=RenderService()
    full=tmp_path/'full.mp4'
    renderer.render(project,str(full),quality='low')
    api=HttpApi(service,renderer,media_dir=str(tmp_path/'cache'))
    try:
        first,_,first_duration=api._preview_window_file(project,0)
        second,_,second_duration=api._preview_window_file(project,1)
        assert first_duration == second_duration == 2
        def frame(path,at):
            return subprocess.run(['ffmpeg','-v','error','-ss',str(at),'-i',str(path),
                                   '-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],
                                  check=True,capture_output=True).stdout
        for time in [.125,.25,.5,1.0,1.75]:
            x=np.frombuffer(frame(full,2+time),np.uint8).astype(float)
            y=np.frombuffer(frame(second,time),np.uint8).astype(float)
            assert np.abs(x-y).mean() < 3
        for window in [first,second]:
            raw=subprocess.run(['ffmpeg','-v','error','-i',window,'-map','0:a:0',
                                 '-f','s16le','-ac','1','-ar','48000','pipe:1'],
                                check=True,capture_output=True).stdout
            pcm=np.frombuffer(raw,np.int16).astype(float)
            assert len(pcm) >= 2*48000
            assert np.sqrt(np.mean(pcm[1200:4800]**2)) > 100
            assert np.sqrt(np.mean(pcm[48000:48000+4800]**2)) > 100
    finally:
        api.close()
