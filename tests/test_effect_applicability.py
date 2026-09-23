"""Effect applicability must be enforced for people and Agents alike."""

from __future__ import annotations

import unittest

from cutvoke.core.protocol import Actor, Command, ErrorCode
from cutvoke.core.service import EditError, EditService


class EffectApplicabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = EditService()
        self.project = self.service.create_project("effect-targets")
        self.revision = self.project.revision
        self._apply("track.add", {"trackId": "v1", "kind": "video"})
        self._apply(
            "clip.insert",
            {
                "clipId": "still",
                "trackId": "v1",
                "sourcePath": "poster.png",
                "timelineStart": {"num": 0, "den": 1},
                "timelineEnd": {"num": 3, "den": 1},
            },
        )

    def _apply(self, command_type: str, payload: dict):
        result = self.service.execute(
            Command(
                type=command_type,
                payload=payload,
                command_id=f"effect-target-{self.revision}",
                project_id=self.project.project_id,
                expected_revision=self.revision,
                actor=Actor("human", "effect-target-test"),
            )
        )
        self.revision = result.revision
        return result

    def test_audio_effect_is_rejected_for_a_still_image(self) -> None:
        with self.assertRaises(EditError) as raised:
            self._apply(
                "effect.add",
                {"clipId": "still", "effectId": "cutvoke.fx.loudnorm"},
            )
        self.assertEqual(raised.exception.code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("不适用于图片", str(raised.exception))

    def test_visual_effect_remains_available_for_a_still_image(self) -> None:
        self._apply(
            "effect.add",
            {"clipId": "still", "effectId": "cutvoke.fx.vibrance"},
        )
        clip = self.service.get_project(self.project.project_id).sequence.tracks[0].clips[0]
        self.assertEqual(clip.effects[0]["effectId"], "cutvoke.fx.vibrance")


if __name__ == "__main__":
    unittest.main()
