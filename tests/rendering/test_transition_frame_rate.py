"""A plain concat followed by xfade must have a valid sequence frame clock."""
import json
import subprocess

import numpy as np
import pytest
from PIL import Image

from cutvoke.domain.model import AssetReference, Clip, Project, Sequence, Track
from cutvoke.domain.rational import Rational
from cutvoke.rendering.render import RenderService


@pytest.mark.parametrize("fps", [Rational.of(30), Rational.of(30000, 1001), Rational.of(24000, 1001)])
def test_mixed_plain_joins_and_transitions_render_at_sequence_rate(tmp_path, fps):
    clips = []
    for index, color in enumerate(("red", "green", "blue", "yellow")):
        source = tmp_path / f"{color}.png"
        Image.new("RGB", (64, 48), color).save(source)
        effects = ([{"effectId": "cutvoke.transition.crossfade",
                     "params": {"duration": .4}}] if index >= 2 else [])
        clips.append(Clip(color, AssetReference(color, str(source)),
                          Rational.of(index * 2), Rational.of((index + 1) * 2),
                          Rational.of(0), effects=effects))
    project = Project("1", "mixed-joins", "0", Sequence(
        "seq", 64, 48, fps, tracks=[Track("video", "video", clips=clips)]))
    renderer = RenderService()
    full = tmp_path / "export.mp4"
    renderer.render(project, str(full), quality="low")
    rate = fps.to_fraction()

    def decode(path):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path),
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             check=True, capture_output=True, timeout=30).stdout
        return np.frombuffer(raw, dtype=np.uint8).reshape(-1, 48, 64, 3)

    probe = json.loads(subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams",
        "-of", "json", str(full)], check=True, capture_output=True, text=True,
        timeout=15).stdout)["streams"][0]
    assert probe["r_frame_rate"] == f"{rate.numerator}/{rate.denominator}"
    pictures = decode(full)
    assert len(pictures) == round(8 * float(rate))
    assert abs(float(probe["duration"]) - 8) <= 1 / float(rate)
    for at, expected in ((1, (255, 0, 0)), (3, (0, 128, 0)),
                         (5, (0, 0, 255)), (7, (255, 255, 0))):
        pixel = pictures[round(at * float(rate)), 24, 32].astype(int)
        assert np.max(np.abs(pixel - expected)) < 12
    midpoint = pictures[round(4.2 * float(rate)), 24, 32].astype(int)
    assert midpoint[1] > 25 and midpoint[2] > 50 and midpoint[0] < 15

    # A bounded preview crosses the concat/transition seam and must keep the
    # same pictures and frame count as that portion of the export.
    start_frame = round(3.5 * float(rate))
    frame_count = round(1.5 * float(rate))
    preview = tmp_path / "preview.mp4"
    renderer.render_preview_window(project, str(preview),
                                   float(start_frame / rate), float(frame_count / rate))
    window = decode(preview)
    assert len(window) == frame_count
    assert np.max(np.abs(window.astype(int) -
                         pictures[start_frame:start_frame + frame_count].astype(int))) < 15
