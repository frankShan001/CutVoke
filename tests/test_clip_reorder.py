"""Regression tests for whole-clip timeline reordering."""

from __future__ import annotations

import unittest

from cutvoke.core.protocol import Actor, Command
from cutvoke.core.service import EditService


class ClipReorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = EditService()
        self.project = self.service.create_project("clip-reorder")
        self.revision = self.project.revision
        self._apply("track.add", {"trackId": "v1", "kind": "video"})
        for index, clip_id in enumerate(("a", "b", "c", "d")):
            start = index * 4
            self._apply(
                "clip.insert",
                {
                    "clipId": clip_id,
                    "trackId": "v1",
                    "sourcePath": f"{clip_id}.png",
                    "timelineStart": {"num": start, "den": 1},
                    "timelineEnd": {"num": start + 4, "den": 1},
                },
            )

    def _apply(self, command_type: str, payload: dict):
        result = self.service.execute(
            Command(
                type=command_type,
                payload=payload,
                command_id=f"{command_type}-{self.revision}",
                project_id=self.project.project_id,
                expected_revision=self.revision,
                actor=Actor("human", "timeline-test"),
            )
        )
        self.revision = result.revision
        return result

    def _track_state(self) -> list[tuple[str, int, int]]:
        track = self.service.get_project(self.project.project_id).sequence.tracks[0]
        return [
            (
                clip.id,
                clip.timeline_start.num // clip.timeline_start.den,
                clip.timeline_end.num // clip.timeline_end.den,
            )
            for clip in track.clips
        ]

    def test_reorder_last_clip_between_second_and_third(self) -> None:
        result = self._apply(
            "clip.move",
            {
                "clipId": "d",
                "trackId": "v1",
                "mode": "reorder",
                "anchorClipId": "c",
                "anchorPosition": "before",
            },
        )

        self.assertEqual(
            self._track_state(),
            [("a", 0, 4), ("b", 4, 8), ("d", 8, 12), ("c", 12, 16)],
        )
        self.assertFalse(
            any(change.get("change") in {"split", "created", "deleted"}
                for change in result.changed_entities)
        )

    def test_reorder_middle_clip_after_later_clip_closes_source_gap(self) -> None:
        self._apply(
            "clip.move",
            {
                "clipId": "b",
                "mode": "reorder",
                "anchorClipId": "c",
                "anchorPosition": "after",
            },
        )

        self.assertEqual(
            self._track_state(),
            [("a", 0, 4), ("c", 4, 8), ("b", 8, 12), ("d", 12, 16)],
        )


if __name__ == "__main__":
    unittest.main()
