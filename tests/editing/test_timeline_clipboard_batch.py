"""User timeline clipboard and multi-selection operations stay atomic and undoable."""
from __future__ import annotations

import tempfile
from pathlib import Path
import uuid
import unittest

from cutvoke.domain.protocol import Actor, Command
from cutvoke.domain.rational import Rational
from cutvoke.domain.invariants import validate_project
from cutvoke.editing.service import EditService, EditError
from cutvoke.storage.store import ProjectStore


def rat(value):
    return Rational.from_float(value).to_json()


class TimelineClipboardBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ProjectStore(str(Path(self.temp.name) / "clipboard.sqlite"))
        self.addCleanup(self.store.close)
        self.service = EditService(store=self.store)
        self.service.create_project("p", width=160, height=90, fps=Rational.of(10))

    def apply(self, command, payload):
        project = self.service.get_project("p")
        return self.service.execute(Command(type=command, payload=payload, project_id="p",
            expected_revision=project.revision, command_id=uuid.uuid4().hex, actor=Actor("human", "timeline-test")))

    def insert(self, cid, track, start, end, **extra):
        creating = not any(t.id == track for t in self.service.get_project("p").sequence.tracks)
        if extra.get("attachedToClipId"):
            extra["role"] = "sticker"
            if creating:
                extra["createTrackRole"] = "sticker"
        self.apply("clip.insert", {"trackId": track, **({"createTrackKind":"video"} if creating else {}), "clipId": cid,
            "sourcePath": "source.png", "timelineStart": rat(start), "timelineEnd": rat(end), **extra})

    def clips(self):
        return {clip.id: clip for track in self.service.get_project("p").sequence.tracks for clip in track.clips}

    def snapshot(self, ids):
        return [{"trackId": track.id, "trackIndex": i, "trackKind": track.kind, "clip": clip.to_dict()}
                for i, track in enumerate(self.service.get_project("p").sequence.tracks)
                for clip in track.clips if clip.id in ids]

    def test_adjacent_duplicate_inserts_and_ripples_bound_layers_undo_reopen(self):
        self.insert("a", "main", 0, 5)
        self.insert("b", "main", 5, 10)
        self.insert("title", "overlay", 1, 3, attachedToClipId="a")
        self.insert("later", "overlay", 6, 8, attachedToClipId="b")
        self.apply("marker.add", {"markerId": "m", "time": rat(6)})
        before = self.service.get_project("p").sequence.to_dict()
        result = self.apply("clip.duplicateGroup", {"clipIds": ["a"]})
        clips = self.clips()
        copies = [c for cid, c in clips.items() if cid not in {"a", "b", "title", "later"}]
        anchor = next(c for c in copies if c.attached_to_clip_id is None)
        follower = next(c for c in copies if c.attached_to_clip_id == anchor.id)
        self.assertEqual((anchor.timeline_start, follower.timeline_start), (Rational.of(5), Rational.of(6)))
        self.assertEqual((clips["b"].timeline_start, clips["later"].timeline_start), (Rational.of(10), Rational.of(11)))
        self.assertEqual(self.service.get_project("p").sequence.markers[0].time, Rational.of(11))
        self.service = EditService(store=self.store)
        self.apply("history.undo", {})
        self.assertEqual(self.service.get_project("p").sequence.to_dict(), before)
        self.apply("history.redo", {})
        self.assertEqual(len(self.clips()), 6)

    def test_single_duplicate_keeps_legacy_requested_id_and_adjacent_insert(self):
        self.insert("a", "v", 0, 2)
        self.insert("b", "v", 2, 4)
        self.apply("clip.duplicate", {"clipId": "a", "newClipId": "copy-a"})
        clips = self.clips()
        self.assertEqual(clips["copy-a"].timeline_start, Rational.of(2))
        self.assertEqual(clips["b"].timeline_start, Rational.of(4))

    def test_paste_snapshot_splits_crossing_clip_and_keeps_original_automation(self):
        self.insert("a", "v", 0, 4)
        self.apply("clip.keyframe", {"clipId": "a", "action": "batch", "keyframes": [
            {"param": "volume", "time": 0, "value": 0}, {"param": "volume", "time": 4, "value": 1}]})
        items = self.snapshot({"a"})
        self.apply("clip.audio", {"clipId": "a", "volume": .25})
        self.apply("caption.add", {"captionId": "c", "text": "hello", "start": rat(1), "end": rat(3),
            "words": [{"text": "hello", "start": rat(1), "end": rat(3)}]})
        self.apply("clip.paste", {"items": items, "timelineStart": rat(2)})
        ordered = sorted(self.clips().values(), key=lambda c: c.timeline_start)
        self.assertEqual([(c.timeline_start, c.timeline_end) for c in ordered],
            [(Rational.of(0), Rational.of(2)), (Rational.of(2), Rational.of(6)), (Rational.of(6), Rational.of(8))])
        self.assertEqual(ordered[1].volume, Rational.of(1))  # copied state, not the later edit
        self.assertEqual(ordered[2].source_start, Rational.of(2))
        self.assertAlmostEqual(ordered[2].keyframes["volume"][0].value, .5)
        self.assertEqual([(c.start,c.end) for c in self.service.get_project("p").sequence.captions],
                         [(Rational.of(1),Rational.of(2)),(Rational.of(6),Rational.of(7))])

    def test_bulk_move_preserves_relative_lanes_and_attachment_clock(self):
        for cid, track, start, end in [("a","v1",0,2),("b","v2",1,3)]:
            self.insert(cid,track,start,end)
        self.apply("track.add", {"trackId":"v3","kind":"video"})
        self.apply("clip.batch", {"clipIds":["a","b"], "action":"move", "offset":3, "trackOffset":1})
        seq = self.service.get_project("p").sequence
        self.assertEqual([(t.id,[(c.id,c.timeline_start) for c in t.clips]) for t in seq.tracks],
            [("v1",[]),("v2",[("a",Rational.of(3))]),("v3",[("b",Rational.of(4))])])

    def test_conflict_locked_follower_and_invalid_properties_leave_whole_project_unchanged(self):
        self.insert("a","v",0,2)
        self.insert("b","v",3,5)
        self.insert("child","other",0,2,attachedToClipId="a")
        before = self.service.get_project("p").to_dict()
        with self.assertRaises(EditError):
            self.apply("clip.batch",{"clipIds":["a"],"action":"move","offset":2})
        self.assertEqual(self.service.get_project("p").to_dict(),before)
        self.apply("track.update",{"trackId":"other","locked":True})
        before = self.service.get_project("p").to_dict()
        for command, payload in [("clip.batch",{"clipIds":["a"],"action":"remove"}),
                                 ("clip.duplicateGroup",{"clipIds":["a"]}),
                                 ("clip.batch",{"clipIds":["a","b"],"action":"audio","volume":-1})]:
            with self.subTest(command=command,payload=payload), self.assertRaises(EditError):
                self.apply(command,payload)
            self.assertEqual(self.service.get_project("p").to_dict(),before)

    def test_compound_and_effect_instance_references_are_fresh_without_rewriting_text(self):
        self.insert("a","v1",0,2)
        self.insert("b","v2",0,2)
        self.apply("effect.add",{"clipId":"a","effectId":"cutvoke.fx.blur","params":{"sigma":2}})
        instance = self.clips()["a"].effects[0]["instanceId"]
        self.apply("clip.keyframe",{"clipId":"a","action":"add","param":f"effect:{instance}:sigma","time":0,"value":3})
        self.apply("clip.group",{"clipIds":["a","b"],"groupId":"g"})
        old = self.clips()["g"].nested
        items = self.snapshot({"g"})
        items[0]["clip"]["nested"]["captions"] = [{"id":"caption-id", "text":"a", "start":rat(0), "end":rat(1)}]
        self.apply("clip.paste",{"items":items,"timelineStart":rat(2)})
        new = next(c for cid,c in self.clips().items() if cid!="g").nested
        self.assertTrue({c.id for t in old.tracks for c in t.clips}.isdisjoint({c.id for t in new.tracks for c in t.clips}))
        clip = next(c for t in new.tracks for c in t.clips if c.effects)
        self.assertNotEqual(clip.effects[0]["instanceId"],instance)
        self.assertIn(f'effect:{clip.effects[0]["instanceId"]}:sigma',clip.keyframes)
        self.assertEqual(new.captions[0].text,"a")  # an exact match to an old clip ID is still user text
        self.assertTrue(validate_project(self.service.get_project("p")).structurally_valid)

    def test_paste_into_empty_project_recreates_sticker_lane_and_shifts_caption_words(self):
        self.insert("a","v",0,2)
        self.insert("child","stickers",0,2,attachedToClipId="a")
        items = self.snapshot({"a","child"})
        self.apply("clip.batch",{"clipIds":["a"],"action":"remove"})
        self.apply("track.remove",{"trackId":"v"})
        self.apply("track.remove",{"trackId":"stickers"})
        self.apply("caption.add",{"captionId":"cap","text":"next","start":rat(3),"end":rat(4),
            "words":[{"text":"next","start":rat(3),"end":rat(4)}]})
        self.apply("clip.paste",{"items":items,"timelineStart":rat(0)})
        seq = self.service.get_project("p").sequence
        self.assertEqual(len(seq.tracks),2)
        self.assertEqual(seq.tracks[1].role,"sticker")
        self.assertEqual(seq.captions[0].words[0].start,Rational.of(5))

    def test_batch_audio_and_delete_are_one_undo_each_with_selected_followers(self):
        self.insert("a","v",0,2)
        self.insert("b","v",2,4)
        self.insert("child","other",0,2,attachedToClipId="a")
        self.apply("clip.batch",{"clipIds":["a","b"],"action":"audio","volume":.5,"fadeIn":.2})
        self.assertEqual([self.clips()[cid].volume for cid in ("a","b")],[Rational.of(1,2)]*2)
        self.assertEqual(self.clips()["child"].volume,Rational.of(1))
        self.apply("history.undo",{})
        self.assertTrue(all(c.volume==Rational.of(1) for c in self.clips().values()))
        self.apply("clip.batch",{"clipIds":["a","b"],"action":"remove"})
        self.assertFalse(self.clips())
        self.apply("history.undo",{})
        self.assertEqual(set(self.clips()),{"a","b","child"})

    def test_legal_overlapping_audio_and_text_do_not_block_video_duplicate_move_or_paste(self):
        self.insert("a","v",0,2)
        self.insert("b","v",2,4)
        self.apply("track.add",{"trackId":"audio","kind":"audio"})
        self.apply("track.add",{"trackId":"text","kind":"text"})
        for cid, start, end in [("sound-a",0,4),("sound-b",2,5)]:
            self.apply("clip.insert",{"clipId":cid,"trackId":"audio","sourcePath":"source.wav",
                "timelineStart":rat(start),"timelineEnd":rat(end)})
        for cid, start, end in [("text-a",0,4),("text-b",2,5)]:
            self.apply("clip.insert",{"clipId":cid,"trackId":"text","text":{"content":cid},
                "timelineStart":rat(start),"timelineEnd":rat(end)})
        snapshot = self.snapshot({"a"})
        before = self.service.get_project("p").sequence.to_dict()
        self.apply("clip.duplicate",{"clipId":"a","newClipId":"copy-a"})
        self.assertEqual(self.clips()["copy-a"].timeline_start,Rational.of(2))
        self.apply("clip.batch",{"clipIds":["a"],"action":"move","offset":6})
        self.assertEqual(self.clips()["a"].timeline_start,Rational.of(6))
        self.apply("clip.paste",{"items":snapshot,"timelineStart":rat(1)})
        self.assertTrue(validate_project(self.service.get_project("p")).structurally_valid)
        for _ in range(3):
            self.apply("history.undo",{})
        self.assertEqual(self.service.get_project("p").sequence.to_dict(),before)

    def test_500_clip_batch_copy_and_paste_do_not_truncate_selection(self):
        self.insert("template","v",0,1)
        template = self.snapshot({"template"})[0]
        self.apply("clip.batch",{"clipIds":["template"],"action":"remove"})
        items = [{**template,"clip":{**template["clip"],"id":f"large-{i}",
                   "timelineStart":rat(i),"timelineEnd":rat(i+1)}} for i in range(500)]
        self.apply("clip.paste",{"items":items,"timelineStart":rat(0)})
        ids = list(self.clips())
        self.assertEqual(len(ids),500)
        self.apply("clip.batch",{"clipIds":ids,"action":"audio","volume":.5})
        self.assertTrue(all(c.volume==Rational.of(1,2) for c in self.clips().values()))
        self.apply("history.undo",{})
        self.assertTrue(all(c.volume==Rational.of(1) for c in self.clips().values()))
        self.apply("clip.duplicateGroup",{"clipIds":ids})
        self.assertEqual(len(self.clips()),1000)
        self.apply("history.undo",{})
        self.assertEqual(len(self.clips()),500)
