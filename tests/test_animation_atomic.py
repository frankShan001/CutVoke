"""Animation replacement must not expose a remove-then-add intermediate state."""

from __future__ import annotations

import unittest

from cutvoke.core.protocol import Actor, Command, ErrorCode
from cutvoke.core.service import EditError, EditService


class AnimationAtomicTests(unittest.TestCase):
    def test_transition_rejects_a_broken_seam_without_mutating_project(self) -> None:
        service = EditService()
        project = service.create_project("transition-gap")

        def apply(command_type: str, payload: dict, command_id: str):
            current = service.get_project(project.project_id)
            return service.execute(Command(
                type=command_type, payload=payload, command_id=command_id,
                project_id=project.project_id, expected_revision=current.revision,
                actor=Actor("agent", "transition-test"),
            ))

        apply("track.add", {"trackId": "v1", "kind": "video"}, "gap-track")
        for clip_id, start, end in (("a", 0, 3), ("b", 4, 7)):
            apply("clip.insert", {
                "trackId": "v1", "clipId": clip_id,
                "sourcePath": f"{clip_id}.png",
                "timelineStart": {"num": start, "den": 1},
                "timelineEnd": {"num": end, "den": 1},
            }, f"gap-{clip_id}")
        before = service.get_project(project.project_id).revision
        with self.assertRaises(EditError) as error:
            apply("effect.setTransition", {
                "clipId": "b", "effectId": "cutvoke.transition.crossfade",
                "params": {"duration": 0.5},
            }, "gap-transition")
        self.assertEqual(error.exception.code, ErrorCode.INVALID_ARGUMENT)
        with self.assertRaises(EditError) as direct_error:
            apply("effect.add", {
                "clipId": "b", "effectId": "cutvoke.transition.crossfade",
                "params": {"duration": 0.5},
            }, "gap-transition-direct")
        self.assertEqual(direct_error.exception.code, ErrorCode.INVALID_ARGUMENT)
        after = service.get_project(project.project_id)
        self.assertEqual(after.revision, before)
        self.assertEqual(after.sequence.tracks[0].clips[1].effects, [])

    def test_transition_switch_is_one_revision_and_failed_switch_keeps_old(self) -> None:
        service = EditService()
        project = service.create_project("transition-atomic")

        def apply(command_type: str, payload: dict, command_id: str):
            current = service.get_project(project.project_id)
            return service.execute(Command(
                type=command_type, payload=payload, command_id=command_id,
                project_id=project.project_id, expected_revision=current.revision,
                actor=Actor("agent", "transition-test"),
            ))

        apply("track.add", {"trackId": "v1", "kind": "video"}, "transition-track")
        for index in range(2):
            apply("clip.insert", {
                "trackId": "v1", "clipId": f"image-{index}",
                "sourcePath": f"image-{index}.png",
                "timelineStart": {"num": index * 4, "den": 1},
                "timelineEnd": {"num": (index + 1) * 4, "den": 1},
            }, f"transition-clip-{index}")
        first = apply("effect.setTransition", {
            "clipId": "image-1", "effectId": "cutvoke.transition.crossfade",
            "params": {"duration": 0.5},
        }, "transition-first")

        def transition_ids() -> list[str]:
            clip = service.get_project(project.project_id).sequence.tracks[0].clips[1]
            return [effect["effectId"] for effect in clip.effects]

        with self.assertRaises(EditError) as invalid:
            apply("effect.setTransition", {
                "clipId": "image-1", "effectId": "cutvoke.transition.wipe",
                "params": {"duration": 99},
            }, "transition-invalid")
        self.assertEqual(invalid.exception.code, ErrorCode.INVALID_ARGUMENT)
        self.assertEqual(service.get_project(project.project_id).revision, first.revision)
        self.assertEqual(transition_ids(), ["cutvoke.transition.crossfade"])

        switched = apply("effect.setTransition", {
            "clipId": "image-1", "effectId": "cutvoke.transition.wipe",
            "params": {"duration": 0.8},
        }, "transition-switch")
        self.assertEqual(switched.previous_revision, first.revision)
        self.assertEqual(transition_ids(), ["cutvoke.transition.wipe"])
        apply("history.undo", {}, "transition-undo")
        self.assertEqual(transition_ids(), ["cutvoke.transition.crossfade"])

    def test_preview_replace_failure_and_undo_preserve_previous_animation(self) -> None:
        service = EditService()
        project = service.create_project("animation-atomic")

        def apply(command_type: str, payload: dict, command_id: str):
            current = service.get_project(project.project_id)
            return service.execute(Command(
                type=command_type, payload=payload, command_id=command_id,
                project_id=project.project_id, expected_revision=current.revision,
                actor=Actor("agent", "animation-test"),
            ))

        apply("track.add", {"trackId": "v1", "kind": "video"}, "add-track")
        apply("clip.insert", {
            "trackId": "v1", "clipId": "image", "sourcePath": "image.png",
            "timelineEnd": {"num": 4, "den": 1},
        }, "add-image")
        first = apply("effect.setAnimation", {
            "clipId": "image", "effectId": "cutvoke.anim.fadeIn",
            "params": {"duration": 1.0},
        }, "first-animation")

        def animation_ids() -> list[str]:
            clip = service.get_project(project.project_id).sequence.tracks[0].clips[0]
            return [item["effectId"] for item in clip.effects]

        self.assertEqual(animation_ids(), ["cutvoke.anim.fadeIn"])
        request = Command(
            type="effect.setAnimation",
            payload={"clipId": "image", "effectId": "cutvoke.anim.zoomIn",
                     "params": {"duration": 1.2}},
            command_id="replacement", project_id=project.project_id,
            expected_revision=first.revision, actor=Actor("agent", "animation-test"),
        )
        preview = service.preview_command(request)
        self.assertTrue(preview["valid"])
        self.assertEqual(animation_ids(), ["cutvoke.anim.fadeIn"])

        with self.assertRaises(EditError) as invalid:
            apply("effect.setAnimation", {
                "clipId": "image", "effectId": "cutvoke.anim.zoomIn",
                "params": {"duration": 99},
            }, "invalid-replacement")
        self.assertEqual(invalid.exception.code, ErrorCode.INVALID_ARGUMENT)
        self.assertEqual(service.get_project(project.project_id).revision, first.revision)
        self.assertEqual(animation_ids(), ["cutvoke.anim.fadeIn"])

        replacement = service.execute(request)
        self.assertEqual(replacement.previous_revision, first.revision)
        self.assertEqual(animation_ids(), ["cutvoke.anim.zoomIn"])
        apply("history.undo", {}, "undo-replacement")
        self.assertEqual(animation_ids(), ["cutvoke.anim.fadeIn"])

        apply("effect.setAnimation", {"clipId": "image", "effectId": ""}, "clear-animation")
        self.assertEqual(animation_ids(), [])
        apply("history.undo", {}, "undo-clear")
        self.assertEqual(animation_ids(), ["cutvoke.anim.fadeIn"])


if __name__ == "__main__":
    unittest.main()
