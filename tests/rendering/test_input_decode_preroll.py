"""Input preroll trims retain media clocks in export, poster and playback."""
from __future__ import annotations

import copy
import os
from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

import pytest
from PIL import Image

from cutvoke.domain.keyframes import Keyframe
from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditService
from cutvoke.rendering.render import RenderService
from cutvoke.rendering.vfx_matte import regular_timestamps


def _clock_info(**changes):
    return {
        "has_video": True, "has_audio": True,
        "avg_frame_rate": "24", "r_frame_rate": "24",
        "video_start_time": 0, "audio_start_time": 0,
        **changes,
    }


def test_shared_video_and_audio_seek_uses_earliest_trim_and_keeps_residual(tmp_path):
    source = str(tmp_path / "shared.mp4")
    graph = (
        "[0:v]trim=start=9.25:duration=0.75,setpts=PTS-STARTPTS,tmix=frames=3[v1];"
        "[0:a]atrim=start=7.125:duration=0.875,asetpts=PTS-STARTPTS[a];"
        "[0:v]trim=start=10.5:duration=0.5,setpts=PTS-STARTPTS[v2]"
    )
    renderer = RenderService(gpu_effects=False)
    # This is deliberately not a bounded-preview context. The compiled graph
    # has already placed every temporal effect after the source trims.
    assert getattr(renderer._render_ctx, "bounded_origin", None) is None
    with patch.object(renderer, "probe_media", return_value=_clock_info()), \
            patch.object(renderer, "_regular_timestamps", return_value=True) as clock:
        result = renderer._preview_seek_inputs(graph, [source], Fraction(24))
    assert renderer._render_ctx.input_seeks == {str(Path(source).resolve()): 6}
    assert result == (
        "[0:v]trim=start=3.250000000000:duration=0.75,setpts=PTS-STARTPTS,tmix=frames=3[v1];"
        "[0:a]atrim=start=1.125000000000:duration=0.875,asetpts=PTS-STARTPTS[a];"
        "[0:v]trim=start=4.500000000000:duration=0.5,setpts=PTS-STARTPTS[v2]"
    )
    assert clock.called  # Equal advertised rates alone do not prove CFR.


def test_bounded_source_clock_is_not_scanned_twice(tmp_path):
    source = str(tmp_path / 'verified.mp4')
    renderer = RenderService(gpu_effects=False)
    renderer._render_ctx.verified_input_paths = {os.path.normcase(os.path.abspath(source))}
    graph = '[0:v]trim=start=9.25:duration=1,setpts=PTS-STARTPTS[v]'
    with patch.object(renderer, 'probe_media', return_value=_clock_info()), \
            patch.object(renderer, '_regular_timestamps', side_effect=AssertionError('duplicate scan')):
        result = renderer._preview_seek_inputs(graph, [source], Fraction(24))
    assert 'trim=start=1.250000000000:duration=1' in result


@pytest.mark.parametrize("extra_reference", [
    ";[0:v]scale=80:48[untrimmed]",
    ";[0:a]anull[untrimmed]",
])
def test_any_reference_without_direct_trim_prevents_seeking(tmp_path, extra_reference):
    source = str(tmp_path / "shared.mp4")
    graph = ("[0:v]trim=start=9.25:duration=1,setpts=PTS-STARTPTS[v];"
             "[0:a]atrim=start=8.5:duration=1,asetpts=PTS-STARTPTS[a]"
             + extra_reference)
    renderer = RenderService(gpu_effects=False)
    with patch.object(renderer, "probe_media", return_value=_clock_info()), \
            patch.object(renderer, "_regular_timestamps", return_value=True):
        assert renderer._preview_seek_inputs(graph, [source], Fraction(24)) == graph
    assert renderer._render_ctx.input_seeks == {}


@pytest.mark.parametrize("changes", [
    {"avg_frame_rate": "30"},
    {"r_frame_rate": "30"},
    {"video_start_time": .125},
    {"audio_start_time": -.125},
])
def test_mismatched_rates_or_nonzero_stream_origins_keep_original_graph(tmp_path, changes):
    source = str(tmp_path / "unusual.mp4")
    graph = ("[0:v]trim=start=9.25:duration=1,setpts=PTS-STARTPTS[v];"
             "[0:a]atrim=start=8.5:duration=1,asetpts=PTS-STARTPTS[a]")
    renderer = RenderService(gpu_effects=False)
    with patch.object(renderer, "probe_media", return_value=_clock_info(**changes)), \
            patch.object(renderer, "_regular_timestamps", return_value=True):
        assert renderer._preview_seek_inputs(graph, [source], Fraction(24)) == graph
    assert renderer._render_ctx.input_seeks == {}


def test_irregular_timestamps_are_rejected_even_when_rates_claim_cfr(tmp_path):
    source = str(tmp_path / "variable.mp4")
    graph = "[0:v]trim=start=9.25:duration=1,setpts=PTS-STARTPTS[v]"
    renderer = RenderService(gpu_effects=False)
    with patch.object(renderer, "probe_media", return_value=_clock_info()), \
            patch.object(renderer, "_regular_timestamps", return_value=False) as clock:
        assert renderer._preview_seek_inputs(graph, [source], Fraction(24)) == graph
    assert clock.called
    assert renderer._render_ctx.input_seeks == {}


@pytest.mark.parametrize("times,time_base,expected", [
    (["0.000", "0.033", "0.067", "0.100"], "1/1000", True),
    # Each step is close to 1/30s, but the errors accumulate off the lattice.
    (["0.000", "0.034", "0.068", "0.102"], "1/1000", False),
    (["0.000", "0.033", "0.100", "0.133"], "1/1000", False),
    # A coarse container clock cannot establish frame-accurate seek safety.
    (["0.000", "0.033", "0.067", "0.100"], "1/120", False),
    (["0.000", "0.033", "0.067", "0.100"], "1/100", False),
])
def test_frame_lattice_accounts_for_webm_quantization_without_accepting_vfr(
        times, time_base, expected):
    result = subprocess.CompletedProcess([], 0, json.dumps({
        "streams": [{"time_base": time_base}],
        "frames": [{"best_effort_timestamp_time": t} for t in times],
    }).encode("utf-8"), b"")
    with patch("cutvoke.rendering.vfx_matte.subprocess.run", return_value=result):
        assert regular_timestamps("ffprobe", "quantized.webm", 0, .2,
                                  Fraction(30), exact_interval=True) is expected


@pytest.fixture(scope="module")
def long_gop_source(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe required")
    source = tmp_path_factory.mktemp("decode-preroll") / "long-gop.mp4"
    renderer = RenderService(gpu_effects=False)
    subprocess.run([
        renderer.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
        "testsrc2=s=160x96:r=24:d=9", "-f", "lavfi", "-i",
        "aevalsrc=0.2*sin(2*PI*(200*t+80*t*t)):s=48000:d=9",
        "-c:v", "libx264", "-g", "216", "-keyint_min", "216",
        "-sc_threshold", "0", "-c:a", "aac", "-shortest", str(source),
    ], check=True, capture_output=True)
    return source


class _WithoutInputSeek(RenderService):
    def _preview_seek_inputs(self, graph, paths, fps):
        self._render_ctx.input_seeks = {}
        return graph


class _RecordInputSeek(RenderService):
    def __init__(self):
        super().__init__(gpu_effects=False)
        self.seek_plans = []

    def _preview_seek_inputs(self, graph, paths, fps):
        result = super()._preview_seek_inputs(graph, paths, fps)
        self.seek_plans.append(dict(self._render_ctx.input_seeks))
        return result


def _decoded(renderer, path, media):
    output = ["-map", "0:v:0", "-an", "-pix_fmt", "rgb24", "-f", "rawvideo"]
    if media == "audio":
        output = ["-map", "0:a:0", "-vn", "-ac", "1", "-ar", "48000",
                  "-acodec", "pcm_s16le", "-f", "s16le"]
    return subprocess.run([
        renderer.ffmpeg, "-v", "error", "-i", str(path), *output, "pipe:1",
    ], check=True, capture_output=True).stdout


@pytest.mark.parametrize("operation", ["export", "range-export", "poster", "preview"])
def test_long_gop_media_and_effect_history_match_without_input_seek(
        tmp_path, long_gop_source, operation):
    service = EditService()
    try:
        project = service.create_project("decode-preroll", width=160, height=96, fps=R.of(24))
        video = Clip("v", AssetReference("source", str(long_gop_source)),
                     R.of(0), R.of(1), R.of(25, 4), effects=[
                         {"effectId": "cutvoke.fx.trail", "params": {"frames": 3, "decay": .36}},
                     ])
        video.keyframes = {
            "x": [Keyframe("x0", R.of(0), 0), Keyframe("x1", R.of(1), -8)],
        }
        audio = Clip("a", AssetReference("source", str(long_gop_source)),
                     R.of(0), R.of(1), R.of(17, 4))
        project.sequence.tracks = [Track("v", "video", [video], muted=True),
                                   Track("a", "audio", [audio])]
        authority = copy.deepcopy(project.to_dict())
        outputs, plans = [], []
        for renderer in [_WithoutInputSeek(gpu_effects=False), _RecordInputSeek()]:
            output = tmp_path / f"{type(renderer).__name__}{'.png' if operation == 'poster' else '.mp4'}"
            if operation == "export":
                renderer.render(project, str(output), quality="preview")
            elif operation == "range-export":
                renderer.render(project, str(output), quality="preview",
                                output_start=.25, output_duration=.5)
            elif operation == "poster":
                renderer.extract_frame(project, .5, str(output))
            else:
                renderer.render_preview_window(project, str(output), .25, .5)
            if operation == "poster":
                with Image.open(output) as image:
                    outputs.append(image.convert("RGB").tobytes())
            else:
                pixels = _decoded(renderer, output, "video")
                samples = _decoded(renderer, output, "audio")
                assert pixels and samples
                outputs.append((pixels, samples))
            if isinstance(renderer, _RecordInputSeek):
                plans = renderer.seek_plans
        assert outputs[0] == outputs[1]
        assert any(plan.get(str(long_gop_source.resolve()), 0) > 0 for plan in plans)
        assert project.to_dict() == authority
    finally:
        service.close()


def test_late_bounded_export_preserves_independent_wav_audio_origin(
        tmp_path, long_gop_source):
    from cutvoke.rendering.preview_window import bounded

    renderer = RenderService(gpu_effects=False)
    sound = tmp_path / "changing-tone.wav"
    subprocess.run([
        renderer.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
        "aevalsrc=0.2*sin(2*PI*(200*t+80*t*t)):s=48000:d=9",
        "-acodec", "pcm_s16le", str(sound),
    ], check=True, capture_output=True)
    service = EditService()
    try:
        project = service.create_project("audio-origin", width=160, height=96, fps=R.of(24))
        video = Clip("v", AssetReference("video", str(long_gop_source)),
                     R.of(0), R.of(5), R.of(0))
        audio = Clip("a", AssetReference("audio", str(sound)),
                     R.of(0), R.of(5), R.of(9, 8))
        project.sequence.tracks = [Track("v", "video", [video], muted=True),
                                   Track("a", "audio", [audio])]
        authority = copy.deepcopy(project.to_dict())
        outputs, origins = [], []
        for optimized in (False, True):
            def choose_window(project, window, active_renderer):
                result = (bounded(project, window, active_renderer) if optimized
                          else (project, {}, None))
                origins.append((optimized, result[2]))
                return result

            output = tmp_path / f"audio-origin-{optimized}.mp4"
            # Both use the same CPU encoder and original audio graph. Only the
            # bounded video optimization differs; re-trimming audio before AAC
            # encoding changes sample history even when timestamps look right.
            with patch("cutvoke.rendering.preview_window.bounded", side_effect=choose_window):
                renderer.render(project, str(output), quality="preview",
                                output_start=3.375, output_duration=.5)
            pixels = _decoded(renderer, output, "video")
            samples = _decoded(renderer, output, "audio")
            assert len(pixels) == 12 * 160 * 96 * 3
            assert len(samples) >= 24000 * 2
            assert any(samples)
            outputs.append((pixels, samples))
        assert outputs[0] == outputs[1]
        assert (False, None) in origins
        assert any(enabled and origin is not None and origin > 0
                   for enabled, origin in origins)
        assert project.to_dict() == authority
    finally:
        service.close()
