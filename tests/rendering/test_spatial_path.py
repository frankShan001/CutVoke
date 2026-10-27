import copy

import numpy as np
import pytest

from cutvoke.domain.spatial_path import resolved_points, resolve
from cutvoke.domain.keyframes import evaluate
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditError
from tests.support.composition_fixtures import setup, frames


def test_roving_points_use_spatial_arc_length_and_reject_invalid_endpoints():
    points = resolved_points({"points": [{"at": 0, "x": 0, "y": 0}, {"roving": True, "x": 10, "y": 0}, {"at": 4, "x": 40, "y": 0}]})
    assert points[1]["at"] == pytest.approx(1)
    with pytest.raises(ValueError):
        resolved_points({"points": [{"roving": True, "x": 0, "y": 0}, {"at": 1, "x": 10, "y": 0}]})


def test_spatial_cubic_curve_preview_export_trim_clock_and_undo(tmp_path):
    service, store, renderer, command = setup(tmp_path)
    try:
        command("clip.motionPath", clipId="base", points=[{"at": 0, "x": 0, "y": 0, "outY": 30}, {"at": 3, "x": 30, "y": 0, "inY": 30}])
        project = service.get_project("composition")
        resolved = resolve(project).sequence.tracks[0].clips[0]
        assert evaluate(resolved.keyframes["y"], R.of(3,2)) == pytest.approx(22.5)
        output, preview = tmp_path / "full.mp4", tmp_path / "preview.mp4"
        renderer.render(project, str(output), quality="high")
        renderer.render_preview_window(project, str(preview), 1, 1)
        full, part = frames(output), frames(preview)
        assert full[15, 0, 40].max() < 10
        assert full[15, 40, 40, 0] > 100
        assert np.abs(full[10:20] - part).mean() < 4
        expected = evaluate(resolved.keyframes["y"], R.of(1))
        command("clip.trim", clipId="base", timelineStart=R.of(1).to_json(), sourceStart=R.of(1).to_json())
        cropped = resolve(service.get_project("composition")).sequence.tracks[0].clips[0]
        assert evaluate(cropped.keyframes["y"], R.of(0)) == pytest.approx(expected)
        command("history.undo")
        assert store.load("composition").sequence.tracks[0].clips[0].timeline_start == R.of(0)
    finally:
        service.close(); store.close()
