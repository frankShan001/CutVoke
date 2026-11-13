"""Issue #55 design spike, NOT a production fix or project format.

Run: PYTHONPATH=src python -m pytest tools/verify/prototype_animation_clock.py -q
Canonical manual curves + rational offset. Persistence here is an isolated JSON
file, NOT ProjectStore compatibility. No render/browser parity is asserted.
"""
from copy import deepcopy
from dataclasses import dataclass, replace
import json

import pytest
from cutvoke.domain.keyframes import Keyframe, evaluate
from cutvoke.domain.rational import Rational as R


@dataclass
class Clock:
    lanes: dict
    offset: R
    duration: R

    def value(self, lane, local):
        return evaluate(self.lanes[lane], self.offset + local)

    def trim(self, left, right):
        # Bounds are in the old clip's presentation clock, not media time.
        if right <= left:
            raise ValueError('empty interval')
        return Clock(deepcopy(self.lanes), self.offset + left, right - left)

    def split(self, at):
        if not R.of(0) < at < self.duration:
            raise ValueError('split outside clip')
        return self.trim(R.of(0), at), self.trim(at, self.duration)

    def put(self, lane, key, local):
        if not R.of(0) <= local <= self.duration:
            raise ValueError('edit outside visible interval')
        canonical = replace(key, time=self.offset + local)
        keys = self.lanes.setdefault(lane, [])
        # Replace by identity, then reject time collisions rather than guess.
        keys = [k for k in keys if k.id != canonical.id]
        if any(k.time == canonical.time for k in keys):
            raise ValueError('time collision')
        self.lanes[lane] = sorted(keys + [canonical], key=lambda k: k.time.to_fraction())

    def remove_lane(self, lane):
        self.lanes.pop(lane, None)  # no hidden backup can resurrect this lane

    def save(self, path):
        path.write_text(json.dumps({'prototypeVersion': 1,
            'offset': self.offset.to_json(), 'duration': self.duration.to_json(),
            'lanes': {name: [k.to_dict() for k in keys] for name, keys in self.lanes.items()}}))

    @classmethod
    def open(cls, path):
        data = json.loads(path.read_text())
        if data['prototypeVersion'] != 1:
            raise ValueError('unknown prototype format')
        return cls({name: [Keyframe.from_dict(k) for k in keys]
                    for name, keys in data['lanes'].items()},
                   R.from_json(**data['offset']), R.from_json(**data['duration']))


@pytest.mark.parametrize('fps', [R.of(24), R.of(30000, 1001)])
@pytest.mark.parametrize('mode', ['linear', 'ease-in', 'ease-out', 'hold', 'cubic-bezier'])
def test_reversible_clock_roundtrip(tmp_path, fps, mode):
    frame = R.of(1) / fps
    duration = frame * R.of(60)
    keys = [Keyframe('a', R.of(0), 20, mode,
                    (1/3, 0, 2/3, 1) if mode == 'cubic-bezier' else None),
            Keyframe('b', duration, 100)]
    original = Clock({'x': keys}, R.of(0), duration)
    trimmed = original.trim(frame * R.of(30), frame * R.of(55))
    path = tmp_path / 'clock.json'
    trimmed.save(path)
    trimmed = Clock.open(path)
    assert trimmed.offset == frame * R.of(30)
    for step in range(26):
        p = (30 + step) / 60
        weight = (p*p if mode == 'ease-in' else 1-(1-p)**2 if mode == 'ease-out'
                  else float(p >= 1) if mode == 'hold'
                  else 3*p*p-2*p*p*p if mode == 'cubic-bezier' else p)
        assert trimmed.value('x', frame*R.of(step)) == pytest.approx(20+80*weight, abs=1e-8)
    restored = trimmed.trim(frame*R.of(-30), frame*R.of(30))
    assert restored == original
    # Edit/retime maps visible time into canonical time without modifying IDs.
    trimmed.put('x', Keyframe('edit', R.of(0), 77), frame*R.of(5))
    trimmed.put('x', Keyframe('edit', R.of(0), 78), frame*R.of(6))
    assert sum(k.id == 'edit' for k in trimmed.lanes['x']) == 1
    assert trimmed.value('x', frame*R.of(6)) == 78
    extended = trimmed.trim(frame*R.of(-30), frame*R.of(30))
    assert extended.value('x', frame*R.of(36)) == 78
    extended.save(path)
    reopened = Clock.open(path)
    assert reopened == extended
    left, right = reopened.split(frame*R.of(23))
    for part, origin in [(left, 0), (right, 23)]:
        for step in range(int((part.duration/frame).to_fraction())+1):
            assert part.value('x', frame*R.of(step)) == pytest.approx(
                reopened.value('x', frame*R.of(origin+step)), abs=1e-8)
    # A split child owns its curves; editing it cannot mutate its sibling.
    left_before = deepcopy(left)
    right.put('x', Keyframe('child', R.of(0), 99), frame*R.of(1))
    assert left == left_before
    # Removing a lane while trimmed must remain removed after outward extension.
    trimmed.remove_lane('x')
    assert not trimmed.trim(frame*R.of(-30), frame*R.of(30)).lanes


def test_extension_before_original_origin_and_collision(tmp_path):
    clock = Clock({'x': [Keyframe('a', R.of(0), 1), Keyframe('b', R.of(10), 2)]}, R.of(0), R.of(10))
    extended = clock.trim(R.of(-2), R.of(12))
    assert extended.value('x', R.of(0)) == 1
    # This model can represent a canonical negative-time edit; production cannot
    # adopt it without explicit schema/invariant/editor migration.
    extended.put('x', Keyframe('pre', R.of(0), 3), R.of(1))
    assert extended.lanes['x'][0].time == R.of(-1)
    extended.save(tmp_path/'clock.json')
    assert Clock.open(tmp_path/'clock.json') == extended
    with pytest.raises(ValueError, match='collision'):
        extended.put('x', Keyframe('other', R.of(0), 4), R.of(1))
