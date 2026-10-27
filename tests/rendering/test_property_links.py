import copy

import numpy as np
import pytest

from cutvoke.domain.controllers import resolve
from cutvoke.domain.keyframes import evaluate, expression
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditError
from tests.support.composition_fixtures import setup, frames


def test_declarative_controller_pixels_preview_undo_and_reopen(tmp_path):
    service, store, renderer, command = setup(tmp_path)
    try:
        command("clip.bind", clipId="base", param="opacity", binding={"source": {"type": "sine", "amplitude": .5, "value": .5, "frequency": 1}})
        project = service.get_project("composition")
        snapshot = copy.deepcopy(project.to_dict())
        output, preview = tmp_path / "full.mp4", tmp_path / "preview.mp4"
        renderer.render(project, str(output), quality="high")
        renderer.render_preview_window(project, str(preview), 1, .8)
        rgb = frames(output)
        assert rgb[2, 20, 20, 0] > 150 and rgb[7, 20, 20, 0] < 15
        assert np.abs(rgb[10:18] - frames(preview)).mean() < 3
        assert project.to_dict() == snapshot
        assert store.load("composition").sequence.tracks[0].clips[0].property_links
        command("history.undo")
        assert not store.load("composition").sequence.tracks[0].clips[0].property_links
    finally:
        service.close(); store.close()


def test_binding_dag_clocks_clamping_and_cycle_rejection(tmp_path):
    service, store, renderer, command = setup(tmp_path)
    try:
        command("clip.bind", clipId="base", param="x", binding={"source": {"type": "time"}, "scale": 10})
        command("clip.bind", clipId="base", param="y", binding={"source": {"type": "property", "clipId": "base", "param": "x"}, "scale": 2, "offset": 5})
        project = service.get_project("composition")
        lane = resolve(project).sequence.tracks[0].clips[0].keyframes["y"]
        assert evaluate(lane, R.of(2)) == 45
        assert "clip(" in expression(lane, "t", 0)
        snapshot = copy.deepcopy(store.load("composition").to_dict())
        with pytest.raises(EditError):
            command("clip.bind", clipId="base", param="x", binding={"source": {"type": "property", "clipId": "base", "param": "y"}})
        assert store.load("composition").to_dict() == snapshot
        command("clip.bind", clipId="base", param="opacity", binding={"source": {"type": "constant", "value": 99}})
        assert evaluate(resolve(service.get_project("composition")).sequence.tracks[0].clips[0].keyframes["opacity"], R.of(2)) == 1
    finally:
        service.close(); store.close()
