from pathlib import Path
import numpy as np
from PIL import Image

from cutvoke.core.model import AssetReference,Clip,Track
from cutvoke.core.service import EditService
from cutvoke.core.rational import Rational as R
from cutvoke.core.render import RenderService


def test_animated_title_writes_glyph_alpha_to_a_transparent_canvas(tmp_path):
    source=tmp_path/'transparent.png';Image.new('RGBA',(320,180),(0,0,0,0)).save(source)
    service=EditService();p=service.create_project('alpha-title',width=320,height=180)
    text=dict(content='LIGHT',fontFamily='Barlow Condensed',fontSize=130,bold=True,
        x=.5,y=.5,strokeWidth=0,animInStyle='stagger',animIn=200,animGlow=5)
    p.sequence.tracks=[Track('v','video',[Clip('blank',AssetReference('a',str(source)),R.of(0),R.of(1),R.of(0))]),
        Track('t','text',[Clip('title',AssetReference('text',''),R.of(0),R.of(1),R.of(0),effects=[dict(effectId='cutvoke.text',params=text)])])]
    output=tmp_path/'title.png';RenderService().extract_still(p,.5,str(output),alpha=True)
    a=np.asarray(Image.open(output).convert('RGBA'))[:,:,3]
    assert a.max()>200
    assert np.count_nonzero(a>0)>100
    assert np.count_nonzero(a==0)>a.size*.7
