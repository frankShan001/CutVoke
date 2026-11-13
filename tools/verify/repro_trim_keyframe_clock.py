"""Known failing diagnostic: left trim restarts the retained keyframe clock.

Run explicitly with: python -m pytest tools/verify/repro_trim_keyframe_clock.py -q
Not collected by the normal tests/ suite. This probe records the intended
retained-interval behavior; it is not a claim that reversible extension is fixed.
"""
import pytest

from cutvoke.domain.keyframes import evaluate
from cutvoke.domain.protocol import Actor, Command
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditService
from cutvoke.storage.store import ProjectStore


@pytest.mark.parametrize("fps", [R.of(24), R.of(30000, 1001)])
@pytest.mark.parametrize("mode", ["linear", "ease-in", "ease-out", "hold", "cubic-bezier"])
def test_split_trim_curves_survive_reopen_and_persisted_undo_redo(tmp_path, fps, mode):
    # Integer frame indices ensure the NTSC case never enters through float time.
    frame = R.of(fps.den, fps.num)
    origin, duration, cut = frame * R.of(30), frame * R.of(60), frame * R.of(23)
    serial = 0
    db = str(tmp_path / "project.sqlite")
    lanes = {"x": (20, 100), "scale": (.5, 1.5), "rotation": (-20, 40),
             "opacity": (.2, .9), "volume": (.1, .8)}

    def apply(service, kind, **payload):
        nonlocal serial
        serial += 1
        return service.execute(Command(kind, payload, f"split-{serial}", "p",
            service.get_project("p").revision, Actor("human", "split-test")))

    def snapshot(service):
        return service.get_project("p").sequence.to_dict()

    def verify(service, expected_ranges):
        clips = service.get_project("p").sequence.tracks[0].clips
        assert [(c.timeline_start, c.timeline_end) for c in clips] == expected_ranges
        for clip in clips:
            assert set(clip.keyframes) == set(lanes)
            assert clip.source_start == clip.timeline_start - origin
            for name, (first, last) in lanes.items():
                keys = clip.keyframes[name]
                assert keys[0].time == R.of(0)
                # Independent analytic oracle; do not reuse crop/ease to create expectations.
                for step in range(17):
                    local = clip.duration * R.of(step, 16)
                    progress = float((clip.timeline_start - origin + local).to_fraction()
                                     / duration.to_fraction())
                    weight = (progress ** 2 if mode == "ease-in" else
                              1 - (1 - progress) ** 2 if mode == "ease-out" else
                              float(progress >= 1) if mode == "hold" else
                              3 * progress ** 2 - 2 * progress ** 3 if mode == "cubic-bezier" else
                              progress)
                    assert evaluate(keys, local) == pytest.approx(first + (last-first)*weight, abs=1e-8)

    with ProjectStore(db) as store:
        service = EditService(store)
        service.create_project("p", width=160, height=90, fps=fps)
        apply(service, "clip.insert", clipId="c", trackId="v", createTrackKind="video",
              sourcePath="synthetic.png", timelineStart=origin.to_json(),
              timelineEnd=(origin+duration).to_json())
        for name, (first, last) in lanes.items():
            apply(service, "clip.keyframe", clipId="c", param=name, time=R.of(0).to_json(),
                  value=first, interpolation=mode,
                  **({"bezier": [1/3, 0, 2/3, 1]} if mode == "cubic-bezier" else {}))
            apply(service, "clip.keyframe", clipId="c", param=name,
                  time=duration.to_json(), value=last)
        before = snapshot(service)
        apply(service, "clip.split", clipId="c", at=(origin+cut).to_json())
        split = snapshot(service)
        verify(service, [(origin, origin+cut), (origin+cut, origin+duration)])

    # A new database connection and service must recover both curves and history.
    with ProjectStore(db) as store:
        service = EditService(store)
        assert snapshot(service) == split
        verify(service, [(origin, origin+cut), (origin+cut, origin+duration)])
        apply(service, "history.undo")
        assert snapshot(service) == before
        apply(service, "history.redo")
        assert snapshot(service) == split
        right_start = origin + cut + frame * R.of(7)
        apply(service, "clip.trim", clipId="c_r", timelineStart=right_start.to_json(),
              timelineEnd=(origin+duration).to_json(), sourceStart=(right_start-origin).to_json())
        trimmed = snapshot(service)
        verify(service, [(origin, origin+cut), (right_start, origin+duration)])
    with ProjectStore(db) as store:
        service = EditService(store)
        assert snapshot(service) == trimmed
        verify(service, [(origin, origin+cut), (right_start, origin+duration)])
        apply(service, "history.undo")
        assert snapshot(service) == split
        apply(service, "history.redo")
        assert snapshot(service) == trimmed
