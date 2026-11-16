"""Prerequisite codec tests; no claim of a completed migration or trim fix."""
from copy import deepcopy
import pytest
from cutvoke.domain.model import Project, Sequence
from cutvoke.domain.rational import Rational as R
from cutvoke.storage.project_format import (
    ProjectFormat, ProjectFormatError, inspect_snapshot, convert_snapshot,
    read_current_snapshot,
)


def legacy():
    return Project('1', 'p', 'r', Sequence('s', 160, 90, R.of(24))).to_dict()


def test_detection_is_not_conversion():
    source = legacy()
    before = deepcopy(source)
    assert inspect_snapshot(source) is ProjectFormat.LEGACY
    with pytest.raises(ProjectFormatError, match='explicit conversion'):
        read_current_snapshot(source)
    assert source == before


def test_explicit_conversion_preserves_original_and_data():
    source = legacy()
    before = deepcopy(source)
    current = convert_snapshot(source)
    assert source == before
    assert inspect_snapshot(current) is ProjectFormat.CURRENT
    decoded = read_current_snapshot(current)
    assert decoded == dict(before, schemaVersion='2')
    decoded['name'] = 'changed'
    assert current['document'] != decoded
    with pytest.raises(ProjectFormatError):
        convert_snapshot(current)


def test_current_model_requires_unwrapped_document():
    # Container decoding must occur explicitly before model construction.
    from cutvoke.domain.model import UnsupportedProjectSchema
    current = convert_snapshot(legacy())
    with pytest.raises(UnsupportedProjectSchema):
        Project.from_dict(current)


@pytest.mark.parametrize('bad', [None, [], {}, {'schemaVersion':'999'},
    {'format':'future'}, {'formatVersion':2}, {'document':{}},
    {'format':'cutvoke.project-v2','formatVersion':True,'document':{}},
])
def test_unknown_or_malformed_never_falls_back_to_legacy(bad):
    before = deepcopy(bad)
    with pytest.raises(ProjectFormatError):
        inspect_snapshot(bad)
    assert bad == before


def test_ambiguous_envelope_and_nested_future_schema_rejected():
    current = convert_snapshot(legacy())
    current['schemaVersion'] = '1'
    with pytest.raises(ProjectFormatError):
        inspect_snapshot(current)
    del current['schemaVersion']
    current['document']['schemaVersion'] = '999'
    with pytest.raises(ProjectFormatError):
        inspect_snapshot(current)
