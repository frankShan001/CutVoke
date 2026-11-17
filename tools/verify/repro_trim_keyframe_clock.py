"""Compatibility entry point for the former #55 failing diagnostic.

The single authoritative oracle now runs in normal discovery at
``tests/editing/test_trim_keyframe_analytic.py``. The historical explicit command
``python -m pytest tools/verify/repro_trim_keyframe_clock.py -q`` remains usable.
This is now a passing signed-local clock regression, not a known failing probe.
"""
from tests.editing.test_trim_keyframe_analytic import (  # noqa: F401
    test_split_trim_curves_survive_reopen_and_persisted_undo_redo,
)
