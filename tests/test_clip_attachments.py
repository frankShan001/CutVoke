"""Explicit clip anchors keep titles and stickers with their source video."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from cutvoke.core.protocol import Actor, Command, ErrorCode
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditError, EditService
from cutvoke.core.store import ProjectStore


def rat(value: int) -> dict:
    return {"num": str(value), "den": "1"}


class ClipAttachmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = EditService()
        self.service.create_project("anchors", width=320, height=180,
                                    fps=Rational.of(15))
        self._apply("clip.insert", {"trackId": "v", "createTrackKind": "video",
                                    "clipId": "video", "sourcePath": "still.png",
                                    "timelineStart": rat(0), "timelineEnd": rat(2)})
        self._apply("clip.insert", {"trackId": "title", "createTrackKind": "text",
                                    "clipId": "title-clip", "text": {"content": "标题"},
                                    "attachedToClipId": "video",
                                    "timelineStart": rat(0), "timelineEnd": rat(1)})
        self._apply("clip.insert", {"trackId": "sticker", "createTrackKind": "video",
                                    "createTrackRole": "sticker", "role": "sticker",
                                    "clipId": "sticker-clip", "sourcePath": "sticker.png",
                                    "attachedToClipId": "video",
                                    "timelineStart": rat(1), "timelineEnd": rat(2)})
        self._apply("clip.insert", {"trackId": "music", "createTrackKind": "audio",
                                    "clipId": "bgm", "sourcePath": "music.mp3",
                                    "timelineStart": rat(0), "timelineEnd": rat(3)})

    def _apply(self, name: str, payload: dict):
        project = self.service.get_project("anchors")
        return self.service.execute(Command(type=name, payload=payload,
            command_id=f"attachment-{name}-{project.revision}",
            project_id="anchors", expected_revision=project.revision,
            actor=Actor("human", "attachment-test")))

    def _clip(self, clip_id: str):
        return next(clip for track in self.service.get_project("anchors").sequence.tracks
                    for clip in track.clips if clip.id == clip_id)

    def test_move_undo_and_one_move_override(self) -> None:
        result = self._apply("clip.move", {"clipId": "video", "timelineStart": rat(3)})
        self.assertEqual({"title-clip", "sticker-clip"},
                         {entry["id"] for entry in result.changed_entities
                          if entry.get("reason") == "attached"})
        self.assertEqual([self._clip(cid).timeline_start for cid in
                          ("video", "title-clip", "sticker-clip", "bgm")],
                         [Rational.of(3), Rational.of(3), Rational.of(4), Rational.of(0)])
        self._apply("history.undo", {})
        self.assertEqual(self._clip("title-clip").timeline_start, Rational.of(0))
        self._apply("clip.move", {"clipId": "video", "timelineStart": rat(3),
                                  "followAttachments": False})
        self.assertEqual(self._clip("title-clip").timeline_start, Rational.of(0))
        self.assertEqual(self._clip("title-clip").attached_to_clip_id, "video")
        self._apply("clip.move", {"clipId": "video", "timelineStart": rat(4)})
        self.assertEqual(self._clip("title-clip").timeline_start, Rational.of(1))

    def test_undo_after_redo_restores_the_same_attached_state(self) -> None:
        self._apply("clip.move", {"clipId": "video", "timelineStart": rat(3)})
        self._apply("history.undo", {})
        self._apply("history.redo", {})
        self._apply("history.undo", {})

        project = self.service.get_project("anchors")
        clips = {clip.id: clip for track in project.sequence.tracks for clip in track.clips}
        self.assertEqual(clips["video"].timeline_start, Rational.of(0))
        self.assertEqual(clips["title-clip"].timeline_start, Rational.of(0))
        self.assertEqual(clips["sticker-clip"].timeline_start, Rational.of(1))
        self.assertEqual(clips["title-clip"].attached_to_clip_id, "video")
        self.assertEqual(clips["sticker-clip"].attached_to_clip_id, "video")
        self.assertIn("bgm", clips)

    def test_persisted_undo_after_redo_restores_the_same_attached_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, \
                ProjectStore(str(Path(temporary) / "project.sqlite")) as store:
            service = EditService(store=store)
            project_id = "persisted-anchors"
            service.create_project(project_id, width=320, height=180,
                                   fps=Rational.of(15))
            serial = 0

            def edit(name: str, payload: dict) -> None:
                nonlocal serial
                serial += 1
                current = service.get_project(project_id)
                service.execute(Command(
                    type=name, payload=payload,
                    command_id=f"persisted-attachment-{serial}",
                    project_id=project_id, expected_revision=current.revision,
                    actor=Actor("human", "persisted-attachment-test"),
                ))

            edit("clip.insert", {"trackId": "v", "createTrackKind": "video",
                                  "clipId": "video", "sourcePath": "still.png",
                                  "timelineStart": rat(0), "timelineEnd": rat(2)})
            edit("clip.insert", {"trackId": "title", "createTrackKind": "text",
                                  "clipId": "title-clip", "text": {"content": "标题"},
                                  "attachedToClipId": "video",
                                  "timelineStart": rat(0), "timelineEnd": rat(1)})
            edit("clip.insert", {"trackId": "sticker", "createTrackKind": "video",
                                  "createTrackRole": "sticker", "role": "sticker",
                                  "clipId": "sticker-clip", "sourcePath": "sticker.png",
                                  "attachedToClipId": "video",
                                  "timelineStart": rat(1), "timelineEnd": rat(2)})
            edit("clip.insert", {"trackId": "music", "createTrackKind": "audio",
                                  "clipId": "bgm", "sourcePath": "music.mp3",
                                  "timelineStart": rat(0), "timelineEnd": rat(3)})
            edit("clip.move", {"clipId": "video", "timelineStart": rat(3)})
            edit("history.undo", {})
            edit("history.redo", {})
            edit("history.undo", {})

            project = service.get_project(project_id)
            clips = {clip.id: clip for track in project.sequence.tracks for clip in track.clips}
            self.assertEqual(clips["video"].timeline_start, Rational.of(0))
            self.assertEqual(clips["title-clip"].timeline_start, Rational.of(0))
            self.assertEqual(clips["sticker-clip"].timeline_start, Rational.of(1))
            self.assertEqual(clips["title-clip"].attached_to_clip_id, "video")
            self.assertEqual(clips["sticker-clip"].attached_to_clip_id, "video")
            self.assertIn("bgm", clips)

    def test_persisted_new_edit_discards_the_redo_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, \
                ProjectStore(str(Path(temporary) / "project.sqlite")) as store:
            service = EditService(store=store)
            project_id = "persisted-redo-branch"
            service.create_project(project_id, width=320, height=180,
                                   fps=Rational.of(15))
            serial = 0

            def edit(name: str, payload: dict) -> None:
                nonlocal serial
                serial += 1
                current = service.get_project(project_id)
                service.execute(Command(
                    type=name, payload=payload,
                    command_id=f"redo-branch-{serial}",
                    project_id=project_id, expected_revision=current.revision,
                    actor=Actor("human", "redo-branch-test"),
                ))

            edit("clip.insert", {"trackId": "v", "createTrackKind": "video",
                                  "clipId": "video", "sourcePath": "still.png",
                                  "timelineStart": rat(0), "timelineEnd": rat(2)})
            edit("clip.insert", {"trackId": "title", "createTrackKind": "text",
                                  "clipId": "title-clip", "text": {"content": "标题"},
                                  "attachedToClipId": "video",
                                  "timelineStart": rat(0), "timelineEnd": rat(1)})
            edit("clip.move", {"clipId": "video", "timelineStart": rat(3)})
            edit("history.undo", {})
            edit("clip.move", {"clipId": "video", "timelineStart": rat(4)})

            current = service.get_project(project_id)
            with self.assertRaises(EditError) as stale_redo:
                service.execute(Command(
                    type="history.redo", payload={}, command_id="redo-branch-stale",
                    project_id=project_id, expected_revision=current.revision,
                    actor=Actor("human", "redo-branch-test"),
                ))
            self.assertEqual(stale_redo.exception.code, ErrorCode.UNDO_CONFLICT)
            project = service.get_project(project_id)
            clips = {clip.id: clip for track in project.sequence.tracks for clip in track.clips}
            self.assertEqual(clips["video"].timeline_start, Rational.of(4))
            self.assertEqual(clips["title-clip"].timeline_start, Rational.of(4))

    def test_locked_track_and_invalid_anchor_roll_back(self) -> None:
        self._apply("track.update", {"trackId": "title", "locked": True})
        revision = self.service.get_project("anchors").revision
        with self.assertRaises(EditError):
            self._apply("clip.move", {"clipId": "video", "timelineStart": rat(2)})
        self.assertEqual(self.service.get_project("anchors").revision, revision)
        self.assertEqual(self._clip("video").timeline_start, Rational.of(0))
        self._apply("track.update", {"trackId": "title", "locked": False})
        revision = self.service.get_project("anchors").revision
        with self.assertRaises(EditError):
            self._apply("clip.attach", {"clipId": "title-clip",
                                        "attachedToClipId": "bgm"})
        self.assertEqual(self.service.get_project("anchors").revision, revision)
        self.assertEqual(self._clip("title-clip").attached_to_clip_id, "video")
        self._apply("clip.remove", {"clipId": "video"})
        self.assertIsNone(self._clip("title-clip").attached_to_clip_id)
        self.assertIsNone(self._clip("sticker-clip").attached_to_clip_id)

    def test_reorder_moves_each_video_own_attachments(self) -> None:
        self._apply("clip.insert", {"trackId": "v", "clipId": "video-2",
                                    "sourcePath": "still-2.png",
                                    "timelineStart": rat(2), "timelineEnd": rat(4)})
        self._apply("clip.insert", {"trackId": "title-2", "createTrackKind": "text",
                                    "clipId": "title-clip-2", "text": {"content": "第二段"},
                                    "attachedToClipId": "video-2",
                                    "timelineStart": rat(2), "timelineEnd": rat(3)})
        self._apply("clip.move", {"clipId": "video", "mode": "reorder",
                                  "anchorClipId": "video-2", "anchorPosition": "after"})
        self.assertEqual([self._clip(cid).timeline_start for cid in
                          ("video", "video-2", "title-clip", "title-clip-2",
                           "sticker-clip", "bgm")],
                         [Rational.of(2), Rational.of(0), Rational.of(2),
                         Rational.of(0), Rational.of(3), Rational.of(0)])

    def test_close_gap_and_ripple_delete_follow_explicit_anchor(self) -> None:
        self._apply("clip.insert", {"trackId": "v", "clipId": "video-2",
                                    "sourcePath": "still-2.png",
                                    "timelineStart": rat(4), "timelineEnd": rat(6)})
        self._apply("clip.insert", {"trackId": "title-2", "createTrackKind": "text",
                                    "clipId": "title-clip-2", "text": {"content": "第二段"},
                                    "attachedToClipId": "video-2",
                                    "timelineStart": rat(4), "timelineEnd": rat(5)})
        self._apply("clip.closeGap", {"trackId": "v", "beforeClipId": "video-2"})
        self.assertEqual(self._clip("title-clip-2").timeline_start, Rational.of(2))
        self._apply("history.undo", {})
        self.assertEqual(self._clip("title-clip-2").timeline_start, Rational.of(4))
        self._apply("clip.rippleDelete", {"clipId": "video"})
        self.assertEqual(self._clip("title-clip-2").timeline_start, Rational.of(2))
        self.assertIsNone(self._clip("title-clip").attached_to_clip_id)
        self.assertEqual(self._clip("bgm").timeline_start, Rational.of(0))

    def test_split_reassigns_right_hand_attachments(self) -> None:
        self._apply("clip.split", {"clipId": "video", "at": rat(1)})
        self.assertEqual(self._clip("title-clip").attached_to_clip_id, "video")
        self.assertEqual(self._clip("sticker-clip").attached_to_clip_id, "video_r")

    def test_trim_start_moves_attachments_with_parent_and_undo(self) -> None:
        result = self._apply("clip.trim", {
            "clipId": "video", "timelineStart": rat(1), "sourceStart": rat(1),
        })
        self.assertEqual(self._clip("video").timeline_start, Rational.of(1))
        self.assertEqual(self._clip("title-clip").timeline_start, Rational.of(1))
        self.assertEqual(self._clip("sticker-clip").timeline_start, Rational.of(2))
        self.assertEqual(self._clip("bgm").timeline_start, Rational.of(0))
        self.assertEqual({"title-clip", "sticker-clip"},
                         {entry["id"] for entry in result.changed_entities
                          if entry.get("reason") == "attached"})

        self._apply("history.undo", {})
        self.assertEqual(self._clip("video").timeline_start, Rational.of(0))
        self.assertEqual(self._clip("title-clip").timeline_start, Rational.of(0))
        self.assertEqual(self._clip("sticker-clip").timeline_start, Rational.of(1))

    def test_trim_end_does_not_move_attachments(self) -> None:
        result = self._apply("clip.trim", {"clipId": "video", "timelineEnd": rat(1)})
        self.assertEqual(self._clip("title-clip").timeline_start, Rational.of(0))
        self.assertEqual(self._clip("sticker-clip").timeline_start, Rational.of(1))
        self.assertFalse(any(entry.get("reason") == "attached"
                             for entry in result.changed_entities))

    def test_curve_trim_start_moves_attached_title_and_undoes(self) -> None:
        self._apply("clip.insert", {"trackId": "v", "clipId": "video_curve",
                                    "sourcePath": "curve.mp4",
                                    "timelineStart": rat(6), "timelineEnd": rat(8)})
        self._apply("clip.insert", {"trackId": "title", "clipId": "curve-title",
                                    "text": {"content": "曲线片段标题"},
                                    "attachedToClipId": "video_curve",
                                    "timelineStart": rat(6), "timelineEnd": rat(7)})
        self._apply("clip.speed", {"clipId": "video_curve", "curve": {"points": [
            {"at": 0, "speed": 1}, {"at": 0.5, "speed": 2},
            {"at": 1, "speed": 1},
        ]}})

        self._apply("clip.trim", {"clipId": "video_curve", "timelineStart": rat(7)})
        self.assertEqual(self._clip("video_curve").timeline_start, Rational.of(7))
        self.assertEqual(self._clip("curve-title").timeline_start, Rational.of(7))

        self._apply("history.undo", {})
        self.assertEqual(self._clip("video_curve").timeline_start, Rational.of(6))
        self.assertEqual(self._clip("curve-title").timeline_start, Rational.of(6))

    def test_insert_shifts_later_video_and_its_title(self) -> None:
        self._apply("clip.insert", {"trackId": "v", "clipId": "video-2",
                                    "sourcePath": "still-2.png",
                                    "timelineStart": rat(2), "timelineEnd": rat(4)})
        self._apply("clip.insert", {"trackId": "title-2", "createTrackKind": "text",
                                    "clipId": "title-clip-2", "text": {"content": "第二段"},
                                    "attachedToClipId": "video-2",
                                    "timelineStart": rat(2), "timelineEnd": rat(3)})
        self._apply("clip.insert", {"trackId": "v", "clipId": "inserted",
                                    "sourcePath": "middle.png", "mode": "insert",
                                    "timelineStart": rat(2), "timelineEnd": rat(3)})
        self.assertEqual(self._clip("video-2").timeline_start, Rational.of(3))
        self.assertEqual(self._clip("title-clip-2").timeline_start, Rational.of(3))
        self.assertEqual(self._clip("bgm").timeline_start, Rational.of(0))

    def test_ripple_delete_after_insert_moves_following_attached_objects(self) -> None:
        self._apply("clip.insert", {"trackId": "v", "clipId": "video-2",
                                    "sourcePath": "still-2.png",
                                    "timelineStart": rat(2), "timelineEnd": rat(4)})
        self._apply("clip.insert", {"trackId": "title-2", "createTrackKind": "text",
                                    "clipId": "title-clip-2", "text": {"content": "第二段"},
                                    "attachedToClipId": "video-2",
                                    "timelineStart": rat(2), "timelineEnd": rat(3)})
        self._apply("clip.insert", {"trackId": "v", "clipId": "inserted",
                                    "sourcePath": "middle.png", "mode": "insert",
                                    "timelineStart": rat(2), "timelineEnd": rat(3)})
        self.assertEqual(self._clip("video-2").timeline_start, Rational.of(3))
        self.assertEqual(self._clip("title-clip-2").timeline_start, Rational.of(3))

        self._apply("clip.rippleDelete", {"clipId": "inserted"})

        self.assertEqual(self._clip("video-2").timeline_start, Rational.of(2))
        self.assertEqual(self._clip("title-clip-2").timeline_start, Rational.of(2))
        self.assertEqual(self._clip("title-clip-2").attached_to_clip_id, "video-2")
        video_track = next(track for track in self.service.get_project("anchors").sequence.tracks
                            if track.id == "v")
        self.assertEqual([clip.id for clip in video_track.clips], ["video", "video-2"])

    def test_insert_inside_video_reanchors_its_right_hand_sticker(self) -> None:
        self._apply("clip.insert", {"trackId": "v", "clipId": "middle",
                                    "sourcePath": "middle.png", "mode": "insert",
                                    "timelineStart": rat(1), "timelineEnd": rat(2)})
        right = next(c for t in self.service.get_project("anchors").sequence.tracks
                     for c in t.clips if c.id not in {"video", "middle", "title-clip",
                                                       "sticker-clip", "bgm"})
        self.assertEqual(right.timeline_start, Rational.of(2))
        self.assertEqual(self._clip("sticker-clip").attached_to_clip_id, right.id)
        self.assertEqual(self._clip("sticker-clip").timeline_start, Rational.of(2))

    def test_overwrite_trim_start_moves_attached_objects_but_not_bgm(self) -> None:
        self._apply("clip.insert", {"trackId": "v", "clipId": "replacement",
                                    "sourcePath": "replacement.png", "mode": "overwrite",
                                    "timelineStart": rat(0), "timelineEnd": rat(1)})

        self.assertEqual(self._clip("video").timeline_start, Rational.of(1))
        self.assertEqual(self._clip("title-clip").timeline_start, Rational.of(1))
        self.assertEqual(self._clip("sticker-clip").timeline_start, Rational.of(2))
        self.assertEqual(self._clip("title-clip").attached_to_clip_id, "video")
        self.assertEqual(self._clip("sticker-clip").attached_to_clip_id, "video")
        self.assertEqual(self._clip("bgm").timeline_start, Rational.of(0))

    def test_overwrite_split_reanchors_right_hand_attachment(self) -> None:
        self._apply("clip.insert", {"trackId": "v", "clipId": "middle",
                                    "sourcePath": "middle.png", "mode": "overwrite",
                                    "timelineStart": {"num": "1", "den": "2"},
                                    "timelineEnd": rat(1)})

        video_track = next(t for t in self.service.get_project("anchors").sequence.tracks
                           if t.id == "v")
        right = next(c for c in video_track.clips if c.id not in {"video", "middle"})
        self.assertEqual(right.timeline_start, Rational.of(1))
        self.assertEqual(self._clip("sticker-clip").attached_to_clip_id, right.id)
        self.assertEqual(self._clip("sticker-clip").timeline_start, Rational.of(1))

    def test_overwrite_delete_detaches_attachments(self) -> None:
        self._apply("clip.insert", {"trackId": "v", "clipId": "replacement",
                                    "sourcePath": "replacement.png", "mode": "overwrite",
                                    "timelineStart": rat(0), "timelineEnd": rat(2)})

        self.assertIsNone(self._clip("title-clip").attached_to_clip_id)
        self.assertIsNone(self._clip("sticker-clip").attached_to_clip_id)
        self.assertEqual(self._clip("title-clip").timeline_start, Rational.of(0))
        self.assertEqual(self._clip("sticker-clip").timeline_start, Rational.of(1))
        self.assertEqual(self._clip("bgm").timeline_start, Rational.of(0))

    def test_saved_anchor_is_used_by_preview_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            still = root / "still.png"
            Image.new("RGB", (320, 180), (24, 48, 72)).save(still)
            with ProjectStore(str(root / "project.sqlite")) as store:
                service = EditService(store)
                service.create_project("render-anchors", width=320, height=180,
                                       fps=Rational.of(15))

                def apply(name: str, payload: dict) -> None:
                    project = service.get_project("render-anchors")
                    service.execute(Command(type=name, payload=payload,
                        command_id=f"render-{name}-{project.revision}",
                        project_id="render-anchors", expected_revision=project.revision,
                        actor=Actor("human", "attachment-test")))

                apply("clip.insert", {"trackId": "v", "createTrackKind": "video",
                                      "clipId": "source", "sourcePath": str(still),
                                      "timelineStart": rat(0), "timelineEnd": rat(2)})
                apply("clip.insert", {"trackId": "t", "createTrackKind": "text",
                                      "clipId": "title", "text": {"content": "移动标题"},
                                      "attachedToClipId": "source",
                                      "timelineStart": rat(0), "timelineEnd": rat(2)})
                apply("clip.move", {"clipId": "source", "timelineStart": rat(1)})
            with ProjectStore(str(root / "project.sqlite")) as reopened:
                project = EditService(reopened).get_project("render-anchors")
                title = next(c for t in project.sequence.tracks for c in t.clips if c.id == "title")
                self.assertEqual(title.attached_to_clip_id, "source")
                self.assertEqual(title.timeline_start, Rational.of(1))
                renderer = RenderService()
                self.assertIsNotNone(renderer.extract_frame(project, 1.5, str(root / "preview.png")))
                report = renderer.render(project, str(root / "output.mp4"))
                self.assertAlmostEqual(report["duration"], 3, delta=0.15)


if __name__ == "__main__":
    unittest.main()
