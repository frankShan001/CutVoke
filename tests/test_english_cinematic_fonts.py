import json
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image

from cutvoke.core.caption_render import resolve_title_font, _caption_font_instance
from cutvoke.core.effects import default_registry
from cutvoke.core.font_catalog import FONT_FILES
from cutvoke.core.httpapi import HttpApi
from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.rational import Rational as R
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService


def test_new_packaged_fonts_are_distinct_exportable_and_available_to_web(tmp_path):
    registry = default_registry()
    service = EditService(); api = HttpApi(service, RenderService(), media_dir=str(tmp_path/'media'))
    source = tmp_path/'black.png';Image.new('RGB',(320,180),'black').save(source)
    pictures = []
    for family in list(FONT_FILES)[2:]:
        assert Path(resolve_title_font(family)).is_file()
        alias, instance = _caption_font_instance(family, True)
        assert alias and Path(instance).is_file()
        params = registry.validate_params('cutvoke.text',dict(content='CHASE',fontFamily=family,
            fontSize=150,bold=True,x=.5,y=.5,strokeWidth=0,animInStyle='impact',animIn=100))
        p = service.create_project(family.replace(' ','-'),width=320,height=180)
        p.sequence.tracks=[Track('v','video',[Clip('bg',AssetReference('a',str(source)),R.of(0),R.of(1),R.of(0))]),
            Track('t','text',[Clip('title',AssetReference('text',''),R.of(0),R.of(1),R.of(0),effects=[dict(effectId='cutvoke.text',params=params)])])]
        out=tmp_path/(family+'.png');RenderService().extract_frame(p,.5,str(out))
        pictures.append(np.asarray(Image.open(out)))
        status,body=api.handle('GET','/api/v1/fonts/'+FONT_FILES[family],{})
        assert status == 200 and Path(body['__media_path__']).is_file()
    api.close()
    assert all(np.abs(a.astype(float)-b).mean()>1 for i,a in enumerate(pictures) for b in pictures[i+1:])
    root=Path(resolve_title_font('Barlow Condensed')).parent
    for entry in json.loads((root/'latin-font-provenance.json').read_text()):
        assert hashlib.sha256((root/entry['file']).read_bytes()).hexdigest()==entry['sha256']


def test_native_optics_has_distinct_layers_and_transparent_endpoints():
    from cutvoke.core.vfx_optics import frame
    images=[]
    for layer in ['flare','rays','sparks']:
        p=dict(layer=layer,start=0,duration=1.5,particles=100,anchorX=.3,anchorY=.5)
        a=np.asarray(frame(p,160,96,.65));images.append(a)
        assert a[:,:,3].max()>0 and np.count_nonzero(a[:,:,3]==0)>200
        assert np.array_equal(a,np.asarray(frame(p,160,96,.65)))
        assert np.asarray(frame(p,160,96,0))[:,:,3].max()==0
        assert np.asarray(frame(p,160,96,1.5))[:,:,3].max()==0
    assert all(not np.array_equal(a,b) for i,a in enumerate(images) for b in images[i+1:])
