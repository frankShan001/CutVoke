import copy
import hashlib

import numpy as np
import pytest
from PIL import Image

from cutvoke.rendering.composition_operators import frame
from cutvoke.editing.effect_modules import EffectModuleLibrary
from tests.support.composition_fixtures import setup, frames


@pytest.mark.parametrize("effect,params", [
    ("fractalNoise", {"seed": 19, "mix": .7, "evolution": .5}),
    ("turbulentDisplace", {"seed": 19, "amount": 12, "evolution": .5}),
    ("waveWarp", {"amplitude": 10, "wavelength": 30, "frequency": .5}),
    ("vectorPath", {"trimFrames": [{"at": 0, "end": .1}, {"at": 2, "end": 1}], "repeaterCount": 3}),
])
def test_composable_operator_preview_matches_export_and_safe_module(tmp_path, effect, params):
    service, store, renderer, command = setup(tmp_path)
    try:
        source = tmp_path / "red.png"
        array = np.zeros((64, 96, 3), np.uint8)
        array[..., 0] = np.arange(96)[None, :] * 2
        array[::8, :, 1] = 200
        Image.fromarray(array).save(source)
        command("effect.add", clipId="base", effectId="cutvoke.fx." + effect, params=params)
        project = service.get_project("composition")
        before = copy.deepcopy(project.to_dict())
        output, preview = tmp_path / "full.mp4", tmp_path / "preview.mp4"
        renderer.render(project, str(output), quality="high")
        renderer.render_preview_window(project, str(preview), 1, .8)
        # Independently encoded H.264 windows have different reference frames.
        assert np.abs(frames(output)[10:18] - frames(preview)).mean() < 6
        assert project.to_dict() == before
        assert "cutvoke.fx." + effect in EffectModuleLibrary(tmp_path / "modules").supported()
    finally:
        service.close(); store.close()


def test_seeded_noise_and_editable_cubic_trim_geometry_are_deterministic():
    params = {"seed": 123, "_operator": "fractalNoise"}
    a, b = frame(params, 96, 64, .3), frame(params, 96, 64, .3)
    assert a.tobytes() == b.tobytes()
    assert a.tobytes() != frame({**params, "seed": 124}, 96, 64, .3).tobytes()
    params = {"_operator": "vectorPath", "points": [{"x": .1, "y": .5, "outY": -.5}, {"x": .9, "y": .5, "inY": -.5}],
              "closed": False, "strokeWidth": .05, "trimFrames": [{"at": 0, "end": 0}, {"at": 1, "end": 1}]}
    assert np.asarray(frame(params, 96, 64, 0))[..., 3].max() == 0
    curve = np.asarray(frame(params, 96, 64, 1))[..., 3]
    assert curve[:24].max() > 200
    assert curve.sum() > 10000


@pytest.mark.parametrize('operator',['fractalNoise','turbulentDisplace','waveWarp','trackedMask'])
def test_operator_handles_source_size_different_from_canvas_and_alpha(tmp_path,operator):
    service,store,renderer,command=setup(tmp_path)
    try:
        image=np.zeros((90,160,4),np.uint8)
        image[20:70,30:130]=[200,60,20,255]
        Image.fromarray(image).save(tmp_path/'red.png')
        command('effect.add',clipId='base',effectId='cutvoke.fx.'+operator,params={})
        full,preview=tmp_path/'full.mp4',tmp_path/'preview.mp4'
        renderer.render(service.get_project('composition'),str(full),quality='high')
        renderer.render_preview_window(service.get_project('composition'),str(preview),1,.4)
        pixels=frames(full);small=frames(preview)
        assert np.abs(pixels[10:14]-small).mean()<6
        assert small.mean()>4
        assert small[:,0,0].max()<16
    finally:
        service.close();store.close()
