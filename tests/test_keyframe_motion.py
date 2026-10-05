"""Measure actual frame motion, including slow fractional camera movement."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from cutvoke.core.keyframes import Keyframe, evaluate
from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")


def motion_project(root: Path, *, fps: Rational = Rational.of(30)):
    source = root / "marker.png"
    image = Image.new("RGB", (128, 96))
    ImageDraw.Draw(image).rectangle((40, 30, 55, 45), fill="white")
    image.save(source)
    project = EditService().create_project("motion", width=128, height=96, fps=fps)
    clip = Clip("marker", AssetReference("marker", str(source)),
                Rational.of(0), Rational.of(2), Rational.of(0))
    project.sequence.tracks = [Track("v1", "video", [clip])]
    return project, clip


def lane(first: float, last: float, easing: str = "linear"):
    return [Keyframe("a", Rational.of(0), first, easing),
            Keyframe("b", Rational.of(2), last)]


def frames(path: Path):
    raw = subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
        "-pix_fmt", "gray", "pipe:1"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, 96, 128).astype(float)


def centroids(images):
    weights = np.maximum(images - 12, 0)
    yy, xx = np.indices((96, 128))
    total = weights.sum(axis=(1, 2))
    return np.column_stack(((weights * xx).sum(axis=(1, 2)) / total,
                            (weights * yy).sum(axis=(1, 2)) / total))


@pytest.mark.parametrize("easing", ["linear", "ease-in", "ease-out"])
def test_fractional_pan_updates_every_frame_and_preserves_easing(tmp_path, easing):
    project, clip = motion_project(tmp_path)
    clip.keyframes = {"x": lane(0, 12, easing), "y": lane(0, 6, easing)}
    output = tmp_path / "pan.mp4"
    RenderService().render(project, str(output), quality="high")
    images = frames(output)
    assert len(images) == 60
    positions = centroids(images)
    expected = np.array([
        [47.5 + evaluate(clip.keyframes["x"], Rational.of(index, 30)),
         37.5 + evaluate(clip.keyframes["y"], Rational.of(index, 30))]
        for index in range(60)])
    assert np.max(np.abs(positions - expected)) < .15
    if easing == "linear":
        steps = np.diff(positions, axis=0)
        # Old 15 fps sampling + integer positions alternated holds and jumps.
        assert np.all((steps[:, 0] > .1) & (steps[:, 0] < .3))
        assert np.max(np.abs(np.diff(steps[:, 0]))) < .12


def test_zoom_and_opacity_are_continuous_and_window_matches_export(tmp_path):
    project, clip = motion_project(tmp_path, fps=Rational.of(30000, 1001))
    clip.keyframes = {"scale": lane(1.0, 1.06), "x": lane(-1, -4),
                      "y": lane(-1, -3), "opacity": lane(.5, 1)}
    renderer = RenderService()
    output = tmp_path / "zoom.mp4"
    renderer.render(project, str(output), quality="low")
    images = frames(output)
    positions = centroids(images)
    times = np.arange(len(images)) * 1001 / 30000
    expected = np.column_stack((48 * (1 + .03 * times) - .5 - 1 - 1.5 * times,
                                38 * (1 + .03 * times) - .5 - 1 - times))
    assert np.max(np.abs(positions - expected)) < .2
    window = tmp_path / "window.mp4"
    renderer.render_preview_window(project, str(window), 1001 / 30000 * 30,
                                   1001 / 30000 * 20)
    window_images = frames(window)
    # Independent lossy encodes can differ at sharp edges; compare geometry and
    # overall pixels, rather than requiring bit-identical H.264 output.
    assert np.mean(np.abs(window_images - images[30:50])) < .5
    np.testing.assert_allclose(centroids(window_images), positions[30:50], atol=.15)


def test_keyframed_overlay_does_not_extend_edges_over_lower_track(tmp_path):
    project, clip = motion_project(tmp_path)
    red = tmp_path / "red.png"
    Image.new("RGB", (128, 96), "red").save(red)
    lower = Clip("lower", AssetReference("lower", str(red)),
                 Rational.of(0), Rational.of(2), Rational.of(0))
    clip.keyframes = {"scale": lane(.5, .55), "x": lane(20, 25), "y": lane(20, 22)}
    project.sequence.tracks = [Track("lower", "video", [lower]),
                               Track("upper", "video", [clip])]
    frame = tmp_path / "overlay.png"
    RenderService().extract_frame(project, 1, str(frame))
    with Image.open(frame) as image:
        for point in ((0, 0), (127, 95), (115, 40)):
            pixel = image.convert("RGB").getpixel(point)
            assert pixel[0] > 200 and pixel[1] < 20


def test_long_motion_uses_bounded_graph(tmp_path):
    project, clip = motion_project(tmp_path)
    clip.timeline_end = Rational.of(120)
    clip.keyframes = {"x": lane(0, 12), "scale": lane(1, 1.06)}
    graph, *_ = RenderService()._compile(project.sequence, (0, 0))
    assert len(graph) < 10_000
    assert graph.count("perspective=") == 1
