"""Real procedural pixels must not depend on seek order or another request.

This is same-process CPU coverage, not an external renderer/GPU/codec guarantee.
"""
from concurrent.futures import ThreadPoolExecutor
import copy

import pytest

from cutvoke.rendering.composition_operators import frame as operator_frame
from cutvoke.rendering.vfx_light import frame as light_frame


@pytest.mark.parametrize("render,params,variant", [
    (operator_frame, {"_operator": "fractalNoise", "seed": 123, "evolution": .5},
     {"seed": 124}),
    (operator_frame, {"_operator": "vectorPath", "closed": False,
                     "points": [{"x": .1, "y": .5}, {"x": .9, "y": .5}],
                     "trimFrames": [{"at": 0, "end": 0}, {"at": 1, "end": 1}]},
     {"points": [{"x": .1, "y": .25}, {"x": .9, "y": .75}]}),
    (light_frame, {"seed": 17, "intensity": .6, "duration": 1.7}, {"seed": 18}),
], ids=["fractal-noise", "vector-path", "light-weave"])
def test_real_frames_are_seek_safe_and_isolated(render, params, variant):
    variants = [copy.deepcopy(params), {**copy.deepcopy(params), **copy.deepcopy(variant)}]
    original = copy.deepcopy(variants)
    times = (0., .1, .3, .7, 1.1, 1.8)

    def pixels(request):
        which, time = request
        # Share the exact parameter object across calls to expose mutation.
        image = render(variants[which], 96, 64, time)
        assert image.mode == "RGBA" and image.size == (96, 64)
        return image.tobytes()

    baseline = {(which, time): pixels((which, time))
                for which in range(2) for time in times}
    for which in range(2):
        assert len({baseline[which, time] for time in times}) > 1, "fixture must animate"
    assert any(baseline[0, time] != baseline[1, time] for time in times), "variant must matter"
    assert variants == original

    shuffled = (1.1, .1, 1.8, 0., .7, .3, .1, 1.1)
    schedules = [list(reversed(times)), list(shuffled)]
    for schedule in schedules:
        requests = [(which, time) for time in schedule for which in (1, 0)]
        assert [pixels(request) for request in requests] == [baseline[r] for r in requests]
        assert variants == original

    requests = [(which, time) for time in shuffled for which in (1, 0)] * 3
    with ThreadPoolExecutor(max_workers=4) as pool:
        actual = list(pool.map(pixels, requests))
    assert actual == [baseline[request] for request in requests]
    assert variants == original
    # A concurrent batch must not poison a later ordinary sequential request.
    assert {request: pixels(request) for request in baseline} == baseline
