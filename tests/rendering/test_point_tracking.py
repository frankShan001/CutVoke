import json

import cv2
import numpy as np
import pytest

from cutvoke.rendering.point_tracking import track_point
from cutvoke.domain.protocol import Command
from cutvoke.editing.service import EditService


def test_native_tracking_follows_both_directions_without_mutating_project(tmp_path):
    path = tmp_path / 'translation.avi'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'FFV1'), 24, (128, 80))
    texture = np.random.default_rng(10).integers(20, 230, (30, 30, 3), dtype=np.uint8)
    for i in range(48):
        frame = np.zeros((80, 128, 3), dtype=np.uint8)
        frame[25:55, 20+i:50+i] = texture
        writer.write(frame)
    writer.release()
    service = EditService()
    project = service.create_project('tracking-test')
    before = json.dumps(project.to_dict(), sort_keys=True)
    catalog = next(c for c in service.command_catalog() if c['type'] == 'media.trackPoint')
    assert catalog['inputSchema']['properties']['x']['type'] == 'number'
    result = service.execute(Command(type='media.trackPoint', project_id=project.project_id,
        payload=dict(videoPath=str(path), x=47/127, y=40/79, t=.5)))
    tracked = result.changed_entities[0]
    assert tracked['lostFrames'] == []
    assert len(tracked['anchors']) == 48
    for i, point in enumerate(tracked['anchors']):
        assert point['x'] == pytest.approx((35+i)/127, abs=.003)
    assert json.dumps(project.to_dict(), sort_keys=True) == before
    assert result.revision == ''
    with pytest.raises(ValueError, match='outside'):
        track_point(str(path), .4, .5, t=100)
    with pytest.raises(ValueError, match='finite'):
        track_point(str(path), float('nan'), .5)
