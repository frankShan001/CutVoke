"""Case-derived lettering stays one editable word with synchronized sublayers."""
from fractions import Fraction

import pytest

from cutvoke.core.effects import EffectParamInvalid, default_registry
from cutvoke.core.model import Caption
from cutvoke.core.rational import Rational
from cutvoke.core.title_motion import _at, needs_title_motion, title_motion_events
from cutvoke.core.caption_render import _ass_escape, _ass_color_hex_to_bgr


def test_legacy_text_does_not_acquire_new_style_defaults():
    params = default_registry().validate_params("cutvoke.text", {"content": "Hello"})
    assert not {"outerStrokeWidth", "outerStrokeColor", "blockShadowX",
                "blockShadowY", "blockShadowColor"} & params.keys()
    assert not needs_title_motion(params, {})


@pytest.mark.parametrize("patch", [{"outerStrokeWidth": 1}, {"blockShadowX": -3},
                                  {"blockShadowY": 6}, {"animLoopStyle": "float"}])
def test_style_alone_opts_into_shared_native_renderer(patch):
    assert needs_title_motion(patch, {})


def test_zero_width_and_zero_offset_retain_plain_text_path():
    assert not needs_title_motion({"outerStrokeWidth": 0, "blockShadowX": 0,
                                   "blockShadowY": 0}, {})


@pytest.mark.parametrize("field,value", [("outerStrokeWidth", 17), ("outerStrokeWidth", -1),
    ("blockShadowX", 25), ("blockShadowY", -25), ("outerStrokeColor", "bad"),
    ("blockShadowColor", "#badcolor")])
def test_invalid_controls_are_rejected(field, value):
    with pytest.raises(EffectParamInvalid):
        default_registry().validate_params("cutvoke.text", {"content": "Hello", field: value})


def test_float_preserves_whole_word_shaping_and_has_bounded_amplitude():
    cap = Caption("word", "AVATAR", Rational.of(0), Rational.of(3),
                  fontSize=84, animLoopStyle="float", animLoopMs=2400)
    spec = {"params": {"animStrength": .35}}
    a = _at(cap, spec, Fraction(0), 1080, 1920, None)[0]
    b = _at(cap, spec, Fraction(3, 5), 1080, 1920, None)[0]
    assert len(a) == len(b) == 1 and b[0].text == "AVATAR"
    assert 0 < b[0].y - a[0].y < 5
    assert abs(b[0].rotation - a[0].rotation) < 1


def test_each_frame_shares_text_timing_geometry_and_literal_escaping():
    cap = Caption("word", r"A{V}\!", Rational.of(0), Rational.of(2),
                  fontSize=84, strokeWidth=4.5, shadow=0, animInStyle="impact",
                  animIn=280, animLoopStyle="float", animLoopMs=2400)
    params = dict(outerStrokeWidth=4, outerStrokeColor="#4B9FAD",
                  blockShadowX=4, blockShadowY=6, blockShadowColor="#2B7183",
                  animStrength=.35)
    def compile(window=None):
        return title_motion_events(cap, {"params": params, "fps": Rational.of(24),
                "window": window}, "C0", 360, 640, font_path=None,
                escape=_ass_escape, color=_ass_color_hex_to_bgr)
    whole, later = compile(), compile((1, 2))
    assert later == [s for s in whole if s.split(",")[1] >= "0:00:01.00"]
    groups = {}
    for event in whole:
        layer, begin, end, *rest = event.split(",")
        groups.setdefault((begin, end), set()).add(layer)
        assert r"A\{V\}\\!" in event
    assert groups and all(layers == {"Dialogue: 0", "Dialogue: 1", "Dialogue: 3"}
                          for layers in groups.values())
