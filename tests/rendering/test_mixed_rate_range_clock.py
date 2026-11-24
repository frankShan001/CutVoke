"""A 25 Hz media clock must not change frames when a 30 fps export is ranged."""

import json
import shutil
import subprocess

import pytest

from cutvoke.domain.model import AssetReference, Clip, Project, Sequence, Track
from cutvoke.domain.rational import Rational
from cutvoke.rendering.render import RenderService


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="FFmpeg and ffprobe required")
def test_mixed_rate_range_starts_on_same_picture_as_full_export(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTVOKE_GPU_ACCELERATION", "0")
    width, height, source_count = 160, 96, 75

    def run(tool, args, input_bytes=None):
        return subprocess.run([tool, "-v", "error", *args], input=input_bytes,
                              check=True, capture_output=True, timeout=30).stdout

    def marker(index):
        row = b"".join(bytes([220 if index & (1 << bit) else 32]) * 20 for bit in range(7))
        return (row + bytes([128]) * 20) * height

    def frame_ids(path):
        raw = run("ffmpeg", ["-i", str(path), "-map", "0:v:0", "-fps_mode", "passthrough",
                             "-threads", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"])
        frame_size = width * height
        assert raw and len(raw) % frame_size == 0
        codes = []
        for base in range(0, len(raw), frame_size):
            levels = [sum(raw[base + y * width + 20 * bit + x] for y in range(46, 50)
                          for x in range(8, 12)) / 16 for bit in range(7)]
            assert all(level < 64 or level > 192 for level in levels), levels
            codes.append(sum((level > 192) << bit for bit, level in enumerate(levels)))
        return codes

    source = tmp_path / "numbered-25hz.mp4"
    run("ffmpeg", ["-f", "rawvideo", "-pixel_format", "gray", "-video_size", f"{width}x{height}",
                   "-framerate", "25", "-i", "pipe:0", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-threads", "1", "-g", "25", "-bf", "2", "-sc_threshold", "0",
                   "-video_track_timescale", "25", str(source)],
        b"".join(marker(index) for index in range(source_count)))
    probe = json.loads(run("ffprobe", [
        "-select_streams", "v:0", "-show_streams", "-show_frames", "-show_entries",
        "stream=codec_name,time_base,r_frame_rate,avg_frame_rate,start_pts:frame=key_frame,pict_type,best_effort_timestamp",
        "-of", "json", str(source),
    ]))
    stream = probe["streams"][0]
    assert stream["codec_name"] == "h264"
    assert stream["time_base"] == "1/25" and stream["start_pts"] == 0
    assert stream["avg_frame_rate"] == stream["r_frame_rate"] == "25/1"
    assert [i for i, frame in enumerate(probe["frames"]) if frame["key_frame"]] == [0, 25, 50]
    assert any(frame["pict_type"] == "B" for frame in probe["frames"])
    assert [frame["best_effort_timestamp"] for frame in probe["frames"]] == list(range(source_count))
    assert frame_ids(source) == list(range(source_count))

    clip = Clip("camera", AssetReference("asset", str(source)),
                Rational.of(0), Rational.of(3), Rational.of(0))
    project = Project("mixed-rate", "Mixed rate", "0", Sequence(
        "sequence", width, height, Rational.of(30), tracks=[Track("v", "video", clips=[clip])]))
    full = tmp_path / "full.mp4"
    RenderService(gpu_effects=False).render(project, str(full), quality="low")
    whole = frame_ids(full)
    assert len(whole) == 90 and set(whole) == set(range(source_count))
    # At this exact grid intersection neither nearest nor floor policy is ambiguous.
    anchors = {6: 5, 30: 25, 36: 30}
    assert {i: whole[i] for i in anchors} == anchors

    count = 12
    # Two starts inside the first source-frame intervals and two at/after the next GOP.
    for first in (1, 2, 30, 31):
        ranged = tmp_path / f"range-{first}.mp4"
        RenderService(gpu_effects=False).render(project, str(ranged), quality="low",
                                                output_start=first / 30, output_duration=count / 30)
        ids = frame_ids(ranged)
        assert len(ids) == count
        assert ids == whole[first:first + count], f"30 fps range starts at frame {first}"
        assert all(ids[frame - first] == code for frame, code in anchors.items()
                   if first <= frame < first + count)
