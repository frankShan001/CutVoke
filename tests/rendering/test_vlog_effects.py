"""Real pixels, clocks and public editing contracts for the Vlog effects."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest
from PIL import Image, ImageDraw

from cutvoke.rendering.effects import EffectParamInvalid, EffectRegistry, default_registry
from cutvoke.interfaces.httpapi import HttpApi
from cutvoke.interfaces.mcp_server import MCPServer
from cutvoke.domain.model import AssetReference, Clip, Project, Sequence, Track
from cutvoke.domain.rational import Rational
from cutvoke.rendering.render import RenderService
from cutvoke.editing.service import EditService
from cutvoke.storage.store import ProjectStore
from cutvoke.rendering.vlog_fx import preview_time, subject_lock_zoom, transition_expr


IDS = ("cutvoke.fx.subjectLock", "cutvoke.fx.punchIn",
       "cutvoke.transition.matchAlign", "cutvoke.transition.lumaReveal")
LOCALES = "zh-CN zh-TW en ja ko es fr de pt-BR ru ar".split()
W, H, FPS = 96, 64, 24


def rat(value):
    return Rational.from_float(value)


def decode(path):
    result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
                             "-pix_fmt", "rgb24", "-"], check=True, capture_output=True, timeout=30)
    return np.frombuffer(result.stdout, np.uint8).reshape(-1, H, W, 3).astype(int)


def project(sources, effects, *, fps=None, start=0):
    clips = [Clip(str(i), AssetReference(str(i), str(path)), rat(start+i), rat(start+i+1), rat(0),
                  effects=copy.deepcopy(effects[i])) for i, path in enumerate(sources)]
    return Project("1", "Vlog verification", "0", Sequence("s", W, H, fps or rat(FPS),
                   tracks=[Track("video", "video", clips=clips)]))


def still(tmp_path, name, color):
    path = tmp_path / (name + ".png")
    Image.new("RGB", (W, H), color).save(path)
    return path


def render(tmp_path, p, name):
    path = tmp_path / (name + ".mp4")
    RenderService().render(p, str(path), quality="high")
    return decode(path)


@pytest.mark.parametrize("effect_id", IDS)
def test_localized_public_schema_and_strict_numeric_bounds(effect_id):
    registry = default_registry(refresh=True)
    spec = registry.get(effect_id)
    assert registry.validate_params(effect_id, {}) == spec.default_params()
    assert spec.animatable_params() == []
    assert not spec.dependencies
    for locale in LOCALES:
        assert spec.to_dict(locale)["name"] == spec.name[locale]
        assert spec.description[locale]
    for name, schema in spec.parameters["properties"].items():
        assert set(schema["x-cutvoke-label"]) == set(LOCALES)
        if schema["type"] != "number":
            continue
        for good in (schema["minimum"], schema["maximum"]):
            registry.validate_params(effect_id, {name: good})
        for bad in (schema["minimum"]-.01, schema["maximum"]+.01,
                    float("nan"), float("inf"), -float("inf"), True, "0.5"):
            with pytest.raises(EffectParamInvalid):
                registry.validate_params(effect_id, {name: bad})
    with pytest.raises(EffectParamInvalid):
        registry.validate_params(effect_id, {"invented": 1})


def test_mcp_http_preview_history_idempotency_and_reload(tmp_path):
    database = str(tmp_path / "projects.sqlite")
    image = still(tmp_path, "source", "orange")
    blue = still(tmp_path, "second", "blue")
    with ProjectStore(database) as store:
        service = EditService(store)
        mcp = MCPServer(service, media_dir=str(tmp_path / "media"))
        api = HttpApi(service, media_dir=str(tmp_path / "media"))
        try:
            assert mcp.call_tool("create_project", {"projectId": "vlog", "width": W, "height": H})["ok"]
            lease = mcp.call_tool("edit_lock", {"projectId": "vlog", "action": "acquire", "owner": "vlog-test"})["lease"]["leaseId"]

            def apply(kind, payload, command_id=None):
                args = dict(projectId="vlog", type=kind, payload=payload,
                            expectedRevision=service.get_project("vlog").revision,
                            commandId=command_id or kind+service.get_project("vlog").revision,
                            actorId="vlog-test", editLeaseId=lease)
                result = mcp.call_tool("command_apply", args)
                assert result["ok"], result
                return args, result

            for i in range(2):
                apply("clip.insert", {"trackId": "v", **({"createTrackKind": "video"} if i == 0 else {}), "clipId": str(i),
                      "sourcePath": str(image if i == 0 else blue), "timelineStart": rat(i).to_json(),
                      "timelineEnd": rat(i+1).to_json()})
            for effect_id in IDS:
                assert effect_id in {e["effectId"] for e in mcp.call_tool("effects_list", {})["effects"]}
                assert mcp.call_tool("editor_help", {"topic": "command", "command": "effect.add", "effectId": effect_id})["ok"]
                kind = "effect.setTransition" if ".transition." in effect_id else "effect.add"
                payload = {"clipId": "1", "effectId": effect_id}
                revision = service.get_project("vlog").revision
                trial = mcp.call_tool("command_preview", dict(projectId="vlog", type=kind, payload=payload,
                                                             expectedRevision=revision))
                assert trial["ok"] and trial["valid"], trial
                assert service.get_project("vlog").revision == revision
                args, applied = apply(kind, payload)
                assert mcp.call_tool("command_apply", args)["revision"] == applied["revision"]
            before = service.get_project("vlog").to_dict()
            failed = mcp.call_tool("command_apply", dict(projectId="vlog", type="effect.update",
                payload={"clipId": "1", "effectId": IDS[1], "params": {"zoom": 99}},
                expectedRevision=before["revision"], editLeaseId=lease))
            assert not failed["ok"] and service.get_project("vlog").to_dict() == before
            apply("history.undo", {})
            apply("history.redo", {})
            assert service.get_project("vlog").sequence.to_dict() == before["sequence"]
            mcp.call_tool("edit_lock", {"projectId": "vlog", "action": "release", "leaseId": lease})
            for effect_id in IDS:
                snapshot = service.get_project("vlog").to_dict()
                status, body = api.handle("GET", "/api/v1/projects/vlog/resource-preview",
                                          {"effectId": effect_id, "clipId": "1"})
                assert status == 200, body
                assert Image.open(io.BytesIO(body["__png__"])).size == (270, 180)  # preserve 3:2 aspect
                assert service.get_project("vlog").to_dict() == snapshot
            pngs = []
            for reverse in (False, True):
                status, body = api.handle("GET", "/api/v1/projects/vlog/resource-preview", {
                    "effectId": IDS[3], "clipId": "1", "params": json.dumps({"reverse": reverse})})
                assert status == 200, body
                pngs.append(np.asarray(Image.open(io.BytesIO(body["__png__"]))).astype(int))
            assert np.mean(abs(pngs[0]-pngs[1])) > 20
            for params in ('[1]', '{invalid', '{"softness":99}', '{"invented":1}'):
                status, _ = api.handle("GET", "/api/v1/projects/vlog/resource-preview", {
                    "effectId": IDS[3], "clipId": "1", "params": params})
                assert status in (400, 422)
            assert service.get_project("vlog").to_dict() == snapshot
        finally:
            api.close()
            mcp.close()
    with ProjectStore(database) as reopened:
        effects = EditService(reopened).get_project("vlog").sequence.tracks[0].clips[1].effects
        assert {effect["effectId"] for effect in effects} == set(IDS[:2]) | {IDS[3]}


@pytest.mark.parametrize("effect_id,params,expected_time", [
    (IDS[0], {"start": 2.2, "duration": 1.2}, 2.8),
    (IDS[1], {"start": 2.6, "enter": .2, "hold": .6, "leave": .2}, 3.1),
])
def test_late_geometry_preview_samples_the_authored_action(effect_id, params, expected_time):
    spec = default_registry().get(effect_id)
    assert preview_time(spec, 4, params) == pytest.approx(expected_time)
    # A truncated action still needs a real, covered frame rather than the
    # end-exclusive clip boundary. Keep the NTSC frame rate exact at the call site.
    fps = 30000 / 1001
    assert preview_time(spec, 1, params, fps=fps) == pytest.approx(1 - 1 / fps)


@pytest.mark.parametrize("effect_id,params,expected_time", [
    (IDS[0], {"start": 2.2, "duration": 1.2, "fromX": .3, "toX": .7}, 2.8),
    (IDS[1], {"start": 2.6, "enter": .2, "hold": .6, "leave": .2, "zoom": 1.8}, 3.1),
    (IDS[2], {"duration": 1}, .625),
    (IDS[3], {"duration": 1}, .625),
])
def test_resource_preview_matches_actual_authored_geometry_or_short_transition(
        tmp_path, effect_id, params, expected_time):
    source = tmp_path / "pattern.png"
    image = Image.new("RGB", (W, H), (25, 42, 65))
    ImageDraw.Draw(image).rectangle((10, 15, 35, 50), fill="white")
    image.save(source)
    incoming = still(tmp_path, "incoming", "blue")
    transition = ".transition." in effect_id
    p = project([source, incoming] if transition else [source], [[], []] if transition else [[]])
    if transition:
        p.sequence.tracks[0].clips[0].timeline_end = rat(.5)
        p.sequence.tracks[0].clips[1].timeline_start = rat(.5)
        p.sequence.tracks[0].clips[1].timeline_end = rat(1)
    else:
        p.sequence.tracks[0].clips[0].timeline_end = rat(4)
    with ProjectStore(str(tmp_path / "project.sqlite")) as store:
        service = EditService(store)
        service.import_project(p, project_id="preview-audit")
        api = HttpApi(service, media_dir=str(tmp_path / "media"))
        try:
            before = service.get_project("preview-audit").to_dict()
            status, body = api.handle("GET", "/api/v1/projects/preview-audit/resource-preview", {
                "effectId": effect_id, "clipId": "1" if transition else "0",
                "params": json.dumps(params)})
            assert status == 200, body
            candidate = copy.deepcopy(service.get_project("preview-audit"))
            candidate.sequence.tracks[0].clips[1 if transition else 0].effects = [
                {"effectId": effect_id, "params": default_registry().validate_params(effect_id, params)}]
            expected_path = tmp_path / "expected.png"
            RenderService().extract_frame(candidate, expected_time, str(expected_path),
                                          size=(320, 180), include_captions=False)
            actual = np.asarray(Image.open(io.BytesIO(body["__png__"]))).astype(int)
            expected = np.asarray(Image.open(expected_path)).astype(int)
            assert np.mean(abs(actual - expected)) < 1
            assert service.get_project("preview-audit").to_dict() == before
        finally:
            api.close()


def test_punch_in_holds_anchor_and_returns_without_retiming(tmp_path):
    source = tmp_path / "target.png"
    im = Image.new("RGB", (W, H), (22, 40, 58))
    ImageDraw.Draw(im).rectangle((29, 26, 37, 38), fill="white")
    im.save(source)
    p = project([source], [[]])
    p.sequence.tracks[0].clips[0].timeline_end = rat(2)
    before = render(tmp_path, p, "punch-before")
    p.sequence.tracks[0].clips[0].effects = [{"effectId": IDS[1], "params": {
        "anchorX": 33/(W-1), "anchorY": 32/(H-1), "zoom": 1.8}}]
    after = render(tmp_path, p, "punch-after")
    assert len(after) == len(before) == 48
    assert np.mean(abs(after[:8]-before[:8])) < 1
    assert np.mean(abs(after[44:]-before[44:])) < 1
    baseline_area = (before[22].min(axis=2) > 210).sum()
    mask = after[22].min(axis=2) > 210
    y, x = np.where(mask)
    assert mask.sum() > baseline_area * 2.5
    assert abs(x.mean()-33) < 1 and abs(y.mean()-32) < 1


def test_two_point_lock_reduces_measured_target_travel(tmp_path):
    frames = np.zeros((48, H, W, 3), np.uint8)
    frames[:] = (25, 42, 65)
    for i, frame in enumerate(frames):
        x = round((.35+.3*i/48)*(W-1))
        frame[27:37, x-4:x+5] = (255, 230, 10)
    source = tmp_path / "moving.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                    "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0", "-c:v", "libx264",
                    "-crf", "12", str(source)], input=frames.tobytes(), check=True, capture_output=True)
    p = project([source], [[{"effectId": IDS[0], "params": {"fromX": .35, "toX": .65,
                 "fromY": .5, "toY": .5, "duration": 2, "zoom": 1.3}}]])
    p.sequence.tracks[0].clips[0].timeline_end = rat(2)
    locked = render(tmp_path, p, "locked")
    positions = []
    for frame in locked:
        y, x = np.where((frame[:,:,0] > 180) & (frame[:,:,1] > 160) & (frame[:,:,2] < 100))
        assert len(x) > 30
        positions.append(x.mean())
        assert np.min(frame.mean(axis=2)) > 10  # covered edges
    assert np.ptp(positions) < 2
    assert abs(np.mean(positions)-(W-1)/2) < 1
    assert subject_lock_zoom({"fromX": .2, "toX": .8, "targetX": .8}) == 4


@pytest.mark.parametrize("reverse", [False, True])
def test_luma_reveal_follows_content_instead_of_uniform_fade(tmp_path, reverse):
    source = tmp_path / "luma.png"
    image = Image.new("RGB", (W, H), "black")
    ImageDraw.Draw(image).rectangle((0, 0, W//2-1, H-1), fill="white")
    image.save(source)
    blue = still(tmp_path, "incoming", "blue")
    p = project([source, blue], [[], [{"effectId": IDS[3], "params": {"duration": .5, "reverse": reverse}}]])
    result = render(tmp_path, p, "luma-"+str(reverse))
    assert len(result) == 48
    left, right = result[30, H//2, W//4], result[30, H//2, 3*W//4]
    if reverse:
        assert left.min() > 230 and right[2] > 220 and right[0] < 20
    else:
        assert left[2] > 220 and left[0] < 20 and right.max() < 20
    assert result[12, H//2, W//4].min() > 230
    assert result[44, H//2, W//4, 2] > 220


@pytest.mark.parametrize("pattern,reverse", [("lumaReveal", False), ("lumaReveal", True),
                                           ("matchAlign", False)])
def test_custom_transition_is_identical_across_slice_threads(tmp_path, pattern, reverse):
    width, height = 192, 128
    x, y = np.arange(width)[None, :], np.arange(height)[:, None]
    source = tmp_path / "slice-pattern.png"
    Image.fromarray(((x*3+y*5) % 256).astype(np.uint8)).convert("RGB").save(source)
    expression = transition_expr(pattern, {"softness": .08, "reverse": reverse})

    def raw(threads):
        return subprocess.run(["ffmpeg", "-v", "error", "-filter_complex_threads", str(threads),
            "-loop", "1", "-framerate", "24", "-i", str(source), "-f", "lavfi", "-i",
            "color=c=blue:s=192x128:r=24:d=1,format=yuv444p", "-filter_complex",
            f"[0:v]format=yuv444p[a];[a][1:v]xfade=duration=0.5:offset=0.25:"
            f"transition=custom:expr='{expression}'[v]", "-map", "[v]", "-frames:v", "24",
            "-pix_fmt", "yuv444p", "-f", "rawvideo", "-"],
            check=True, capture_output=True, timeout=30).stdout

    expected = raw(1)
    assert len(expected) == 24*width*height*3
    for threads in (4, 8):
        assert raw(threads) == expected


def test_match_align_brings_different_subjects_to_the_same_handoff(tmp_path):
    paths = []
    for i, (x, radius, color) in enumerate(((.3, 5, "yellow"), (.7, 10, "cyan"))):
        path = tmp_path / f"match-{i}.png"
        image = Image.new("RGB", (W, H), (25, 42, 65))
        cx, cy = x*(W-1), .5*(H-1)
        ImageDraw.Draw(image).ellipse((cx-radius, cy-radius, cx+radius, cy+radius), fill=color)
        image.save(path); paths.append(path)
    p = project(paths, [[], [{"effectId": IDS[2], "params": {"duration": .5,
         "aX": .3, "bX": .7, "aScale": 2, "bScale": 1}}]])
    result = render(tmp_path, p, "match")
    y, x = np.where(result[30,:,:,1] > 170)
    assert abs(x.mean()-(W-1)/2) < 1
    assert 14 < np.ptp(x) < 23
    assert abs(np.where(result[12,:,:,1] > 170)[1].mean()-.3*(W-1)) < 1
    assert abs(np.where(result[44,:,:,1] > 170)[1].mean()-.7*(W-1)) < 1


@pytest.mark.parametrize("effect_id", IDS)
def test_window_matches_full_export_and_preserves_frame_count(tmp_path, effect_id):
    a, b = still(tmp_path, "a", "orange"), still(tmp_path, "b", "blue")
    # Textured source makes geometry changes visible in the window comparison.
    im = Image.open(a); ImageDraw.Draw(im).rectangle((8, 12, 32, 43), fill="white"); im.save(a)
    is_transition = ".transition." in effect_id
    effects = [[], [{"effectId": effect_id, "params": {}}]] if is_transition else [
        [{"effectId": effect_id, "params": {}}], []]
    p = project([a, b], effects, start=1)
    renderer = RenderService()
    full, bounded = tmp_path / "full.mp4", tmp_path / "window.mp4"
    renderer.render(p, str(full), quality="high")
    start = 1.75 if is_transition else 1.25
    renderer.render_preview_window(p, str(bounded), start, 1)
    original, window = decode(full), decode(bounded)
    assert len(original) == 72 and len(window) == 24
    assert np.mean(abs(window-original[round(start*FPS):round(start*FPS)+24])) < 3


@pytest.mark.parametrize("effect_id", IDS)
def test_transparency_reveals_lower_layer(tmp_path, effect_id):
    base = still(tmp_path, "base", "blue")
    overlay = tmp_path / "alpha.png"
    image = Image.new("RGBA", (W, H))
    ImageDraw.Draw(image).rectangle((23, 17, 73, 47), fill=(255, 0, 0, 128))
    image.save(overlay)
    if ".transition." in effect_id:
        upper = project([overlay, overlay], [[], [{"effectId": effect_id, "params": {"duration": .5}}]])
    else:
        upper = project([overlay], [[{"effectId": effect_id, "params": {}}]])
        upper.sequence.tracks[0].clips[0].timeline_end = rat(2)
    background = Clip("base", AssetReference("base", str(base)), rat(0), rat(2), rat(0))
    upper.sequence.tracks.insert(0, Track("background", "video", clips=[background]))
    frame = render(tmp_path, upper, "alpha")[30]
    assert frame[0, 0, 2] > 225 and frame[0, 0, 0] < 20
    assert 80 < frame[H//2, W//2, 0] < 180
    assert 80 < frame[H//2, W//2, 2] < 180


@pytest.mark.parametrize("pattern", ["lumaReveal", "matchAlign"])
def test_transition_minimum_and_maximum_parameters_execute(pattern):
    spec = default_registry().get("cutvoke.transition."+pattern)
    for bound in ("minimum", "maximum"):
        params = {key: schema.get(bound, schema.get("default"))
                  for key, schema in spec.parameters["properties"].items()}
        params = default_registry().validate_params(spec.id, params)
        expression = transition_expr(pattern, params, has_alpha=True)
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
            "testsrc2=s=48x32:r=24:d=1,format=yuva444p", "-f", "lavfi", "-i",
            "color=c=blue:s=48x32:r=24:d=1,format=yuva444p", "-filter_complex",
            f"[0:v][1:v]xfade=transition=custom:duration=0.5:expr='{expression}'[v]",
            "-map", "[v]", "-frames:v", "12", "-f", "null", "-"],
            check=True, capture_output=True, timeout=30)


@pytest.mark.parametrize("effect_id", IDS[:2])
def test_geometry_extremes_render_real_pixels_on_portrait_source(effect_id):
    spec = default_registry().get(effect_id)
    for bound in ("minimum", "maximum"):
        params = {key: schema.get(bound, schema.get("default"))
                  for key, schema in spec.parameters["properties"].items()}
        params = default_registry().validate_params(effect_id, params)
        parts = []
        label = RenderService()._fx_one(spec.implementation["filter"], params, "0:v", parts, [0], 0)
        result = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
            "color=c=white:s=48x80:r=24:d=0.2", "-filter_complex", ";".join(parts),
            "-map", f"[{label}]", "-frames:v", "4", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
            check=True, capture_output=True, timeout=30)
        pixels = np.frombuffer(result.stdout, np.uint8)
        assert len(pixels) == 4*48*80*3 and pixels.min() > 235


@pytest.mark.parametrize("effect_id", IDS[2:])
def test_transition_keeps_ntsc_frame_clock(tmp_path, effect_id):
    a, b = still(tmp_path, "a", "red"), still(tmp_path, "b", "blue")
    fps = Rational.of(30000, 1001)
    p = project([a, b], [[], [{"effectId": effect_id, "params": {"duration": .5}}]], fps=fps)
    path = tmp_path / "ntsc.mp4"
    RenderService().render(p, str(path), quality="high")
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)]))
    stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert stream["r_frame_rate"] == "30000/1001"
    assert int(stream["nb_frames"]) == 60


def test_optional_community_recipe_reuses_the_same_real_renderer(tmp_path):
    registry = EffectRegistry()
    root = Path(__file__).resolve().parents[2] / "effects/transitions"
    registry.discover([root], strict=True)
    alias = "cutvoke.transition.community.luma-ink"
    params = registry.validate_params(alias, {})
    assert params["reverse"] is True and params["duration"] == .8
    a, b = still(tmp_path, "a", "white"), still(tmp_path, "b", "blue")
    pictures = []
    for index, effect_id in enumerate((alias, IDS[3])):
        p = project([a, b], [[], [{"effectId": effect_id, "params": params}]])
        output = tmp_path / f"recipe-{index}.mp4"
        RenderService(registry=registry).render(p, str(output), quality="high")
        pictures.append(decode(output))
    assert np.array_equal(*pictures)
