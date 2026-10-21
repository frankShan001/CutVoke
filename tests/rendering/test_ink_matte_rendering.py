"""Vectorized ink must match coverage, including partial alpha and late seeks."""
from contextlib import contextmanager
import copy
import shutil
import subprocess
from fractions import Fraction

import numpy as np
import pytest
from PIL import Image

from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational as R
from cutvoke.rendering.render import RenderService, _fx_mask_expr
from cutvoke.editing.service import EditService
from cutvoke.rendering.vfx_matte import coverage_frames, supports


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
@pytest.mark.parametrize('curve', ['none', 'linear', 'minimum-jerk', 'inertia'])
@pytest.mark.parametrize('invert', [False, True])
def test_vectorized_field_matches_original_expression(tmp_path, curve, invert):
    params = dict(shape='ink', x=.68, y=.42, feather=.12, motion=curve,
                  motionStart=.125, motionDuration=.75, inkSeed=27, inkDetail=.85,
                  invert=invert)
    frames = np.array(list(coverage_frames(params, 160, 96, Fraction(24), 1.25)))
    graph = tmp_path/'reference.ffgraph'
    graph.write_text('[0:v]'+_fx_mask_expr(params)+'[v]', encoding='utf-8')
    result = subprocess.run(['ffmpeg', '-v', 'error', '-filter_complex_threads', '2',
                             '-f', 'lavfi', '-i', 'color=white:size=160x96:rate=24:duration=1.25',
                             '-/filter_complex', str(graph), '-map', '[v]',
                             '-pix_fmt', 'rgba', '-f', 'rawvideo', 'pipe:1'],
                            check=True, capture_output=True)
    original = np.frombuffer(result.stdout, np.uint8).reshape(-1,96,160,4)[:,:,:,3]
    assert np.max(np.abs(original.astype(float)-frames/257)) < 1.01


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
def test_partial_alpha_window_parity_and_request_isolation(tmp_path):
    source=tmp_path/'source.png'
    Image.new('RGBA',(160,96),(90,180,230,128)).save(source)
    params=dict(shape='ink',x=.55,y=.4,feather=.14,motion='minimum-jerk',motionDuration=1.25)
    project=EditService().create_project('ink',width=160,height=96,fps=R.of(24))
    clip=Clip('c',AssetReference('a',str(source)),R.of(3),R.of(5),R.of(0),
              effects=[dict(effectId='cutvoke.fx.mask',params=params)])
    project.sequence.tracks=[Track('v','video',[clip])]
    before=copy.deepcopy(project.to_dict())
    class Reference(RenderService):
        @contextmanager
        def _raster_mask_sources(self, project, *args, **kwargs):
            yield project
    alphas=[]
    for index,renderer in enumerate([RenderService(),Reference()]):
        target=tmp_path/f'frame-{index}.png'
        renderer.extract_still(project,3.625,str(target),alpha=True)
        alphas.append(np.asarray(Image.open(target).convert('RGBA'))[:,:,3].astype(int))
    assert np.abs(alphas[0]-alphas[1]).max() <= 1
    assert 125 <= alphas[0].max() <= 129
    assert project.to_dict() == before
    renderer=RenderService()
    with renderer._raster_mask_sources(project) as snapshot:
        path=snapshot.sequence.tracks[0].clips[0].effects[0]['params']['_renderedInkMattePath']
        from pathlib import Path
        assert Path(path).is_file()
    assert not Path(path).exists()
    assert not supports(dict(params,motionToX=.1))
    # Matroska stores millisecond ticks. The movie source must be restored to
    # rational frame ticks or 24/29.97 fps coverage alternates one frame late.
    alpha_frames=[]
    clip.timeline_start=R.of(0)
    clip.timeline_end=R.of(2)
    for index,renderer in enumerate([RenderService(),Reference()]):
        target=tmp_path/f'frames-{index}.mov'
        renderer.render(project,str(target),alpha=True)
        decoded=subprocess.run(['ffmpeg','-v','error','-i',str(target),'-vf','alphaextract',
                                 '-pix_fmt','gray','-f','rawvideo','pipe:1'],
                                check=True,capture_output=True).stdout
        alpha_frames.append(np.frombuffer(decoded,np.uint8).astype(int))
    assert np.abs(alpha_frames[0]-alpha_frames[1]).max() <= 2
