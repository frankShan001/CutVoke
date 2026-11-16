"""Real product rendering of retained signed-local manual animation."""
from copy import deepcopy
import shutil,subprocess
import numpy as np
import pytest
from PIL import Image,ImageDraw
from cutvoke.domain.model import Clip,AssetReference,Track
from cutvoke.domain.keyframes import Keyframe
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditService
from cutvoke.rendering.render import RenderService

@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='FFmpeg required')
def test_signed_crop_retains_real_rendered_positions(tmp_path):
    path=tmp_path/'marker.png';im=Image.new('RGB',(128,96));ImageDraw.Draw(im).rectangle((40,30,55,45),fill='white');im.save(path)
    p=EditService().create_project('p',width=128,height=96,fps=R.of(24));p.schema_version='2'
    c=Clip('c',AssetReference('a',str(path)),R.of(0),R.of(2),R.of(0),keyframe_time_domain='signed-local')
    c.keyframes={'x':[Keyframe('a',R.of(0),0,'ease-in'),Keyframe('b',R.of(2),12)]}
    p.sequence.tracks=[Track('v','video',[c])]
    renderer=RenderService();original=tmp_path/'original.mp4';renderer.render(p,str(original),quality='high')
    changed=deepcopy(p);EditService()._h_clip_trim(changed,{'clipId':'c','timelineStart':R.of(1,2).to_json(),'timelineEnd':R.of(3,2).to_json(),'sourceStart':R.of(1,2).to_json()})
    cropped=tmp_path/'cropped.mp4';renderer.render(changed,str(cropped),quality='high')
    def positions(path):
        data=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-f','rawvideo','-pix_fmt','gray','pipe:1'],capture_output=True,check=True).stdout
        a=np.frombuffer(data,dtype=np.uint8).reshape(-1,96,128).astype(float);weights=np.maximum(a-12,0);_,xx=np.indices((96,128));total=weights.sum(axis=(1,2))
        return (weights*xx).sum(axis=(1,2))/np.maximum(total,1)
    assert np.max(np.abs(positions(original)[12:36]-positions(cropped)[12:36]))<.15
