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


def normalize_origin(clock):
    """Design-only coordinate translation; offset and ALL lanes move together.

    This is not a production migration. Persisting keys alone, or rebasing only
    the edited lane, would change other animation. Derived lanes are excluded.
    """
    minimum = min((key.time for keys in clock.lanes.values() for key in keys),
                  default=R.of(0))
    shift = R.of(0) - minimum if minimum < R.of(0) else R.of(0)
    return Clock({name: [replace(key, time=key.time + shift) for key in keys]
                  for name, keys in clock.lanes.items()},
                 clock.offset + shift, clock.duration)


@pytest.mark.parametrize('fps', [R.of(24), R.of(30000, 1001)])
@pytest.mark.parametrize('mode', ['linear', 'ease-in', 'ease-out', 'hold', 'cubic-bezier'])
def test_atomic_origin_translation_preserves_all_lanes(tmp_path, fps, mode):
    frame = R.of(1) / fps
    duration = frame * R.of(60)
    keys = [Keyframe('a', R.of(0), 20, mode,
                    (1/3, 0, 2/3, 1) if mode == 'cubic-bezier' else None),
            Keyframe('b', duration, 100)]
    # Separate untouched lane exposes the bug in translating only edited keys.
    source = Clock({'x': deepcopy(keys), 'opacity': deepcopy(keys)}, R.of(0), duration)
    before = deepcopy(source)
    extended = source.trim(frame*R.of(-10), frame*R.of(70))
    extended.put('x', Keyframe('pre', R.of(0), 99), frame*R.of(2))
    canonical = deepcopy(extended)
    normalized = normalize_origin(extended)
    assert source == before
    assert normalized.offset == frame*R.of(-2)
    assert all(k.time >= R.of(0) for keys in normalized.lanes.values() for k in keys)
    assert normalize_origin(normalized) == normalized  # idempotent
    for name in canonical.lanes:
        for sample in range(81):
            local = frame*R.of(sample)
            assert normalized.value(name, local) == pytest.approx(canonical.value(name, local), abs=1e-8)
    assert [k.id for k in normalized.lanes['x']] == [k.id for k in canonical.lanes['x']]
    assert normalized.lanes['opacity'][0].time == frame*R.of(8)
    path = tmp_path/'normalized.json'
    normalized.save(path)
    reopened = Clock.open(path)
    assert reopened == normalized
    # Multiple trim cycles add no fractional drift; restore both bounds exactly.
    for _ in range(25):
        reopened = reopened.trim(frame*R.of(3), reopened.duration-frame*R.of(4))
        reopened = reopened.trim(frame*R.of(-3), reopened.duration+frame*R.of(4))
    assert reopened == normalized
    left, right = reopened.split(frame*R.of(17))
    for part, origin in [(left, 0), (right, 17)]:
        for sample in range(int((part.duration/frame).to_fraction())+1):
            for name in reopened.lanes:
                assert part.value(name, frame*R.of(sample)) == pytest.approx(
                    canonical.value(name, frame*R.of(origin+sample)), abs=1e-8)
    # Edit by existing identity after translation maps the new local time once.
    right.put('x', Keyframe('pre', R.of(0), 41), frame*R.of(5))
    assert right.value('x', frame*R.of(5)) == 41
    assert left.value('x', frame*R.of(2)) == 99


@pytest.mark.parametrize('mode', ['linear', 'ease-in', 'ease-out', 'hold', 'cubic-bezier'])
def test_legacy_materialized_fallback_survives_actual_store(tmp_path, mode):
    """Probe compatibility alternative; recovery metadata is LOST on old save."""
    from cutvoke.domain.keyframes import crop_keyframes
    from cutvoke.domain.model import Clip, Project, Sequence, Track
    from cutvoke.storage.store import ProjectStore
    keys = [Keyframe('a', R.of(0), 20, mode,
                    (1/3, 0, 2/3, 1) if mode == 'cubic-bezier' else None),
            Keyframe('b', R.of(10), 100)]
    canonical = Clock({'x': keys}, R.of(3), R.of(5))
    visible = crop_keyframes(keys, R.of(3), R.of(8))
    wire = {'id': 'c', 'assetRef': {'assetId': 'a'},
            'timelineStart': R.of(0).to_json(), 'timelineEnd': R.of(5).to_json(),
            'sourceStart': R.of(3).to_json(),
            'keyframes': {'x': [k.to_dict() for k in visible]},
            'prototypeRecovery': {'offset': R.of(3).to_json(),
                                  'x': [k.to_dict() for k in keys]}}
    clip = Clip.from_dict(wire)  # existing reader, not a new schema implementation
    assert 'prototypeRecovery' not in clip.to_dict()
    project = Project('999', 'p', 'r', Sequence('s', 160, 90, R.of(24),
                       tracks=[Track('v', 'video', clips=[clip])]))
    path = str(tmp_path/'legacy.sqlite')
    with ProjectStore(path) as store:
        store.save(project)
    with ProjectStore(path) as store:
        loaded = store.load('p')
        assert loaded.schema_version == '999'  # bump alone is not rejection
        restored = loaded.sequence.tracks[0].clips[0]
        assert 'prototypeRecovery' not in restored.to_dict()
        for sample in range(41):
            local = R.of(sample, 8)
            assert evaluate(restored.keyframes['x'], local) == pytest.approx(
                canonical.value('x', local), abs=1e-8)
    # Legacy rendering keeps visible motion, but cannot reconstruct original
    # pre-trim keys after recovery metadata was discarded. No stale sidecar
    # should be silently reattached to the loaded project.
    assert restored.keyframes['x'][0].time == R.of(0)
    assert restored.keyframes['x'][-1].time == R.of(5)


def encode_compat(clock):
    """Prototype dual representation. Recovery is used only if still consistent."""
    from cutvoke.domain.keyframes import crop_keyframes
    return {'duration': clock.duration.to_json(),
            'keyframes': {name: [k.to_dict() for k in crop_keyframes(
                keys, clock.offset, clock.offset+clock.duration)]
                for name, keys in clock.lanes.items()},
            'prototypeRecovery': {'version': 1, 'offset': clock.offset.to_json(),
                'lanes': {name: [k.to_dict() for k in keys]
                          for name, keys in clock.lanes.items()}}}


def decode_compat(wire):
    """Return (clock, recovery retained). Never trust stale recovery authority.

    Exact rematerialization comparison is conservative: a harmless old-client
    rewrite may discard recovery. This is not an authenticated security format.
    """
    duration = R.from_json(**wire['duration'])
    visible = {name: [Keyframe.from_dict(k) for k in keys]
               for name, keys in wire['keyframes'].items()}
    fallback = Clock(visible, R.of(0), duration)
    recovery = wire.get('prototypeRecovery')
    if not isinstance(recovery, dict) or recovery.get('version') != 1:
        return fallback, False
    try:
        lanes = recovery['lanes']
        if not isinstance(lanes, dict) or any(
                not isinstance(keys, list) or any(not isinstance(k, dict) for k in keys)
                for keys in lanes.values()):
            return fallback, False
        candidate = Clock({name: [Keyframe.from_dict(k) for k in keys]
                          for name, keys in recovery['lanes'].items()},
                          R.from_json(**recovery['offset']), duration)
        if encode_compat(candidate)['keyframes'] == wire['keyframes']:
            return candidate, True
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass
    return fallback, False


@pytest.mark.parametrize('mode', ['linear', 'ease-in', 'ease-out', 'hold', 'cubic-bezier'])
def test_guarded_compat_recovery_and_stale_edits(tmp_path, mode):
    keys = [Keyframe('a', R.of(0), 20, mode,
                    (1/3, 0, 2/3, 1) if mode == 'cubic-bezier' else None),
            Keyframe('b', R.of(10), 100)]
    original = Clock({'x': keys}, R.of(0), R.of(10))
    trimmed = original.trim(R.of(3), R.of(8))
    path = tmp_path/'compat.json'
    path.write_text(json.dumps(encode_compat(trimmed)))
    wire = json.loads(path.read_text())
    reopened, retained = decode_compat(wire)
    assert retained
    assert reopened.trim(R.of(-3), R.of(7)) == original
    # Older client drops unknown recovery data: retain visible, do not invent it.
    legacy = deepcopy(wire)
    del legacy['prototypeRecovery']
    fallback, retained = decode_compat(legacy)
    assert not retained
    for step in range(41):
        assert fallback.value('x', R.of(step, 8)) == pytest.approx(
            trimmed.value('x', R.of(step, 8)), abs=1e-8)
    # Old client or partial write changes known fields while retaining unknown
    # metadata. Comparing the entire materialization prevents resurrection.
    for mutate in ['value', 'delete', 'retime', 'duration', 'recovery']:
        stale = deepcopy(wire)
        if mutate == 'value':
            stale['keyframes']['x'][0]['value'] = 123
        elif mutate == 'delete':
            stale['keyframes'] = {}
        elif mutate == 'retime':
            stale['keyframes']['x'][-1]['time'] = R.of(4).to_json()
        elif mutate == 'duration':
            stale['duration'] = R.of(6).to_json()
        else:
            stale['prototypeRecovery']['version'] = 999
        decoded, retained = decode_compat(stale)
        assert not retained, mutate
        assert {name: [k.to_dict() for k in keys] for name, keys in decoded.lanes.items()} == stale['keyframes']


@pytest.mark.parametrize('lanes', [None, [], 'invalid', {'x': None},
                                  {'x': [None]}, {'x': ['invalid']}])
def test_malformed_optional_recovery_keeps_visible_motion(lanes):
    """A corrupt optional sidecar must not prevent loading valid visible data."""
    original = Clock({'x': [Keyframe('a', R.of(0), 20),
                            Keyframe('b', R.of(10), 100)]}, R.of(3), R.of(5))
    wire = encode_compat(original)
    wire['prototypeRecovery']['lanes'] = lanes
    decoded, retained = decode_compat(wire)
    assert not retained
    for sample in range(41):
        local = R.of(sample, 8)
        assert decoded.value('x', local) == pytest.approx(original.value('x', local))
    assert wire['prototypeRecovery']['lanes'] == lanes
