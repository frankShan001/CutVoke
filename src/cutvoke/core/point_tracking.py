"""Bounded source-space attachment tracking owned by CutVoke."""
from __future__ import annotations

import math
from pathlib import Path


def track_point(video_path: str, x: float, y: float, t: float = 0,
                seconds: float = 30) -> dict:
    """Track a source point in both directions; explicitly flag lost tracks.

    Time and normalized coordinates are in the original source, before timeline
    speed and camera transforms. No media or project state is modified.
    """
    import cv2
    import numpy as np

    values = [float(v) for v in (x, y, t, seconds)]
    if not all(math.isfinite(v) for v in values):
        raise ValueError('Tracking parameters must be finite')
    x, y, t, seconds = values
    if not (0 <= x <= 1 and 0 <= y <= 1 and t >= 0 and 0 < seconds <= 30):
        raise ValueError('Point must be normalized; duration must be 0..30 seconds')
    if not Path(video_path).is_file():
        raise ValueError('Source video does not exist')
    capture = cv2.VideoCapture(str(video_path))
    frames = []
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError('Source has no valid video frame rate')
        limit = min(900, int(math.ceil(seconds * fps)))
        if count > limit:
            raise ValueError('Source exceeds tracking bound; trim to <=30 seconds / 900 frames')
        while len(frames) <= limit:
            ok, image = capture.read()
            if not ok:
                break
            h, w = image.shape[:2]
            if w > 640:
                image = cv2.resize(image, (640, max(1, round(h * 640 / w))))
            frames.append(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
        if not frames or len(frames) > limit:
            raise ValueError('Source is empty or exceeds the tracking frame bound')
    finally:
        capture.release()
    seed = round(t * fps)
    if seed >= len(frames):
        raise ValueError('Seed time is outside the source')
    h, w = frames[0].shape
    positions = [None] * len(frames)
    positions[seed] = (x, y)
    for direction in [-1, 1]:
        previous = seed
        point = np.array([[[x * (w - 1), y * (h - 1)]]], dtype=np.float32)
        for i in range(seed + direction, len(frames) if direction > 0 else -1, direction):
            kwargs = dict(winSize=(31, 31), maxLevel=4,
                          criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, .001))
            nxt, status, _ = cv2.calcOpticalFlowPyrLK(frames[previous], frames[i], point, None, **kwargs)
            if nxt is None or status is None or not status[0, 0]:
                break
            back, valid, _ = cv2.calcOpticalFlowPyrLK(frames[i], frames[previous], nxt, None, **kwargs)
            px, py = (float(v) for v in nxt[0, 0])
            if (back is None or valid is None or not valid[0, 0]
                    or not np.isfinite(nxt).all() or not np.isfinite(back).all()
                    or np.linalg.norm(back - point) > 2
                    or not (0 <= px < w and 0 <= py < h)
                    or np.linalg.norm(nxt - point) > math.hypot(w, h) * .12):
                break
            positions[i] = (px / max(1, w - 1), py / max(1, h - 1))
            previous, point = i, nxt
    return dict(method='bidirectional-lk-forward-backward', fps=fps,
                sourceFrames=len(frames), seedFrame=seed,
                anchors=[dict(time=i / fps, x=p[0], y=p[1])
                         for i, p in enumerate(positions) if p is not None],
                lostFrames=[i for i, p in enumerate(positions) if p is None])
