"""Source media duration is a hard editing boundary."""

from __future__ import annotations

import unittest

from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.model import AssetReference, Clip, Sequence
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditError, EditService


class SourceDurationBoundsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = EditService()
        self.project = self.service.create_project("source-bounds")
        self.revision = self.project.revision
        self.counter = 0
        self._apply("track.add", {"trackId": "v1", "kind": "video"})
        durations = {"five.mp4": Rational.of(5, 1), "long.mp4": Rational.of(10, 1)}
        self.service._probe_source_duration = lambda path: durations.get(path)  # type: ignore[method-assign]

    def _apply(self, command_type: str, payload: dict):
        self.counter += 1
        result = self.service.execute(
            Command(
                type=command_type,
                payload=payload,
                command_id=f"source-bound-{self.counter}",
                project_id=self.project.project_id,
                expected_revision=self.revision,
                actor=Actor("human", "source-bound-test"),
            )
        )
        self.revision = result.revision
        return result

    def _insert(self, path: str, end: int, clip_id: str = "clip"):
        return self._apply(
            "clip.insert",
            {
                "clipId": clip_id,
                "trackId": "v1",
                "sourcePath": path,
                "timelineStart": {"num": 0, "den": 1},
                "timelineEnd": {"num": end, "den": 1},
            },
        )

    def test_insert_cannot_exceed_source_duration(self) -> None:
        with self.assertRaisesRegex(EditError, "不能超过源素材末尾"):
            self._insert("five.mp4", 6)
        self.assertEqual(
            self.service.get_project(self.project.project_id).sequence.tracks[0].clips,
            [],
        )

    def test_right_trim_cannot_extend_past_source_duration(self) -> None:
        self._insert("five.mp4", 5)
        with self.assertRaisesRegex(EditError, "不能超过源素材末尾"):
            self._apply(
                "clip.trim",
                {"clipId": "clip", "timelineEnd": {"num": 6, "den": 1}},
            )
        clip = self.service.get_project(self.project.project_id).sequence.tracks[0].clips[0]
        self.assertEqual(clip.timeline_end, Rational.of(5, 1))

    def test_swap_rejects_a_shorter_source_without_corrupting_clip(self) -> None:
        self._insert("long.mp4", 8)
        with self.assertRaisesRegex(EditError, "不能超过源素材末尾"):
            self._apply(
                "asset.swap",
                {"clipIds": ["clip"], "sourcePath": "five.mp4"},
            )
        clip = self.service.get_project(self.project.project_id).sequence.tracks[0].clips[0]
        self.assertEqual(clip.asset_ref.source_path, "long.mp4")


class TransitionTimelineDurationTests(unittest.TestCase):
    def test_transition_keeps_absolute_timeline_duration(self) -> None:
        first = Clip(
            id="a",
            asset_ref=AssetReference("", source_path="a.png"),
            timeline_start=Rational.of(0, 1),
            timeline_end=Rational.of(5, 1),
            source_start=Rational.of(0, 1),
        )
        second = Clip(
            id="b",
            asset_ref=AssetReference("", source_path="b.png"),
            timeline_start=Rational.of(5, 1),
            timeline_end=Rational.of(10, 1),
            source_start=Rational.of(0, 1),
            effects=[{
                "effectId": "cutvoke.transition.zoom",
                "version": "1.0.0",
                "params": {"duration": 0.8},
            }],
        )
        sequence = Sequence(
            id="main", width=1920, height=1080, fps=Rational.of(30, 1)
        )
        parts: list[str] = []
        _label, duration = RenderService()._build_video_track(
            [first, second], sequence, 0, lambda _path: 0,
            parts, [0], (0, 0),
        )

        graph = ";".join(parts)
        self.assertAlmostEqual(duration, 10.0, places=6)
        self.assertIn("tpad=stop_mode=clone:stop_duration=0.800000", graph)
        self.assertIn("xfade=transition=zoomin:offset=5.000000:duration=0.800000", graph)


if __name__ == "__main__":
    unittest.main()
