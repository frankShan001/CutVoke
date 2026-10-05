"""Closing one visible gap preserves later gaps and is one undoable edit."""

from __future__ import annotations

import unittest

from cutvoke.core.protocol import Actor, Command, ErrorCode
from cutvoke.core.service import EditError, EditService


class CloseSingleGapTests(unittest.TestCase):
    def test_close_one_gap_preserves_later_spacing_and_undo(self) -> None:
        service = EditService()
        project = service.create_project("single-gap")

        def apply(kind: str, payload: dict, command_id: str):
            current = service.get_project(project.project_id)
            return service.execute(Command(
                type=kind, payload=payload, command_id=command_id,
                project_id=project.project_id, expected_revision=current.revision,
                actor=Actor("agent", "gap-test"),
            ))

        apply("track.add", {"trackId": "v1", "kind": "video"}, "track")
        for clip_id, start, end in (("a", 0, 2), ("b", 3, 5), ("c", 7, 9)):
            apply("clip.insert", {
                "trackId": "v1", "clipId": clip_id,
                "sourcePath": f"{clip_id}.png",
                "timelineStart": {"num": start, "den": 1},
                "timelineEnd": {"num": end, "den": 1},
            }, f"insert-{clip_id}")

        def starts() -> list[int]:
            clips = service.get_project(project.project_id).sequence.tracks[0].clips
            return [round(float(c.timeline_start.to_fraction())) for c in clips]

        previous = service.get_project(project.project_id).revision
        with self.assertRaises(EditError) as invalid:
            apply("clip.closeGap", {
                "trackId": "v1", "beforeClipId": "b", "afterClipId": "a",
            }, "invalid-combination")
        self.assertEqual(invalid.exception.code, ErrorCode.INVALID_ARGUMENT)
        self.assertEqual(service.get_project(project.project_id).revision, previous)

        result = apply("clip.closeGap", {"trackId": "v1", "beforeClipId": "b"}, "close-b")
        self.assertEqual(starts(), [0, 2, 6])
        self.assertEqual([item["id"] for item in result.changed_entities], ["b", "c"])
        self.assertEqual({item["reason"] for item in result.changed_entities}, {"closed_gap"})
        with self.assertRaises(EditError) as absent:
            apply("clip.closeGap", {"trackId": "v1", "beforeClipId": "b"}, "close-again")
        self.assertEqual(absent.exception.code, ErrorCode.INVALID_ARGUMENT)
        apply("history.undo", {}, "undo-gap")
        self.assertEqual(starts(), [0, 3, 7])


if __name__ == "__main__":
    unittest.main()
