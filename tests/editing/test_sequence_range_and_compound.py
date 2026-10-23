"""Sequence edits preserve synchronization and nested output, not just metadata."""
from __future__ import annotations

import copy
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid
import wave

import numpy as np
from PIL import Image

from cutvoke.domain.protocol import Actor, Command
from cutvoke.domain.rational import Rational
from cutvoke.editing.service import EditError, EditService
from cutvoke.rendering.render import RenderService
from cutvoke.storage.store import ProjectStore


def rat(value):
    return Rational.from_float(value).to_json()


class SequenceRangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ProjectStore(str(Path(self.temp.name)/"edits.sqlite"))
        self.addCleanup(self.store.close)
        self.service = EditService(store=self.store)
        self.service.create_project("p", width=160, height=90, fps=Rational.of(10))

    def apply(self, command, payload):
        project = self.service.get_project("p")
        return self.service.execute(Command(type=command, payload=payload,
            project_id="p", command_id=uuid.uuid4().hex,
            expected_revision=project.revision, actor=Actor("human", "test")))

    def insert(self, cid, lane, kind, start, end, **fields):
        return self.apply("clip.insert", {"clipId":cid,"trackId":lane,"createTrackKind":kind,
            "sourcePath":"source.wav" if kind == "audio" else "source.png",
            "timelineStart":rat(start),"timelineEnd":rat(end), **fields})

    def test_delete_range_crops_sources_captions_and_undo_persists(self):
        self.insert("v", "vtrack", "video", 0, 6)
        self.insert("a", "atrack", "audio", 0, 6)
        self.apply("caption.add", {"captionId":"c", "text":"one two three", "start":rat(0),"end":rat(6),
            "words":[{"text":"one ","start":rat(0),"end":rat(2)},
                     {"text":"two ","start":rat(2),"end":rat(4)},
                     {"text":"three","start":rat(4),"end":rat(6)}]})
        self.apply("marker.add", {"markerId":"m","name":"end", "time":rat(5)})
        before = self.service.get_project("p").sequence.to_dict()
        self.apply("sequence.deleteRange", {"start":2, "end":4})
        project = self.service.get_project("p")
        for lane in project.sequence.tracks:
            self.assertEqual([(c.timeline_start,c.timeline_end,c.source_start) for c in lane.clips],
                [(Rational.of(0),Rational.of(2),Rational.of(0)),
                 (Rational.of(2),Rational.of(4),Rational.of(4))])
        caption = project.sequence.captions[0]
        self.assertEqual((caption.text,caption.end), ("one three",Rational.of(4)))
        self.assertEqual(caption.words[-1].start, Rational.of(2))
        self.assertEqual(project.sequence.markers[0].time,Rational.of(3))
        # Reload through the authoritative store, then undo the entire edit once.
        self.service = EditService(store=self.store)
        self.apply("history.undo", {})
        self.assertEqual(self.service.get_project("p").sequence.to_dict(),before)

    def test_locked_lane_refuses_whole_operation(self):
        self.insert("v", "vtrack", "video", 0, 4)
        self.insert("a", "atrack", "audio", 0, 4)
        self.apply("track.update", {"trackId":"atrack","locked":True})
        before = self.service.get_project("p").to_dict()
        with self.assertRaises(EditError):
            self.apply("sequence.deleteRange", {"start":1,"end":2})
        self.assertEqual(self.service.get_project("p").to_dict(), before)

    def test_transcript_word_delete_cuts_media_and_preserves_other_words(self):
        self.insert("v", "vtrack", "video", 0, 4)
        self.apply("caption.add", {"captionId":"c","text":"你好世界","start":rat(0),"end":rat(4),
            "words":[{"text":"你好","start":rat(0),"end":rat(2)},
                     {"text":"世界","start":rat(2),"end":rat(4)}]})
        self.apply("transcript.delete", {"captionId":"c","wordIndices":[0]})
        seq = self.service.get_project("p").sequence
        self.assertEqual(seq.tracks[0].clips[0].source_start,Rational.of(2))
        self.assertEqual(seq.tracks[0].clips[0].timeline_start,Rational.of(0))
        self.assertEqual(seq.captions[0].text,"世界")
        self.assertEqual(seq.captions[0].words[0].start,Rational.of(0))

    def test_compound_roundtrip_restores_tracks_effects_roles_and_attachments(self):
        self.insert("v", "vtrack", "video", 0, 4)
        self.insert("s", "stickers", "video", 0, 4, createTrackRole="sticker",role="sticker",attachedToClipId="v")
        self.insert("a", "atrack", "audio", 0, 4)
        before = {t.id:t.to_dict() for t in self.service.get_project("p").sequence.tracks}
        self.apply("clip.group", {"clipIds":["v","s","a"],"groupId":"g"})
        compound = next(c for t in self.service.get_project("p").sequence.tracks for c in t.clips if c.id == "g")
        self.assertEqual(len(compound.nested.tracks),3)
        self.assertEqual(compound.nested.tracks[1].clips[0].attached_to_clip_id,"v")
        self.apply("clip.move", {"clipId":"g","timelineStart":rat(2)})
        self.apply("clip.move", {"clipId":"g","timelineStart":rat(0)})
        self.apply("clip.ungroup", {"clipId":"g"})
        seq = self.service.get_project("p").sequence
        self.assertEqual({t.id:t.to_dict() for t in seq.tracks if t.id in before},before)

    def test_compound_copy_rebuilds_child_ids_and_can_expand(self):
        self.insert("v", "vtrack", "video", 0, 2)
        self.insert("a", "atrack", "audio", 0, 2)
        self.apply("clip.group", {"clipIds":["v","a"],"groupId":"g"})
        self.apply("clip.duplicate", {"clipId":"g","newClipId":"g2"})
        self.apply("clip.ungroup", {"clipId":"g2"})
        project = self.service.get_project("p")
        from cutvoke.domain.invariants import validate_project
        self.assertTrue(validate_project(project).structurally_valid)
        restored = [c for t in project.sequence.tracks if t.id in {"vtrack","atrack"} for c in t.clips]
        self.assertEqual(len(restored),2)
        self.assertEqual({c.timeline_start for c in restored},{Rational.of(2)})

    def test_ungroup_rejects_loss_of_parent_audio_processing(self):
        self.insert("v", "vtrack", "video", 0, 2)
        self.insert("a", "atrack", "audio", 0, 2)
        self.apply("clip.group", {"clipIds":["v","a"],"groupId":"g"})
        self.apply("clip.audio", {"clipId":"g","volume":0.25})
        before = self.service.get_project("p").to_dict()
        with self.assertRaises(EditError):
            self.apply("clip.ungroup", {"clipId":"g"})
        self.assertEqual(self.service.get_project("p").to_dict(), before)

    def test_split_and_range_cut_of_nested_content_keep_unique_ids(self):
        self.insert("v", "vtrack", "video", 0, 4)
        self.insert("a", "atrack", "audio", 0, 4)
        self.apply("clip.group", {"clipIds":["v","a"],"groupId":"g"})
        self.apply("clip.split", {"clipId":"g","at":rat(2)})
        self.apply("history.undo", {})
        self.apply("sequence.deleteRange", {"start":1,"end":2})
        from cutvoke.domain.invariants import validate_project
        project = self.service.get_project("p")
        self.assertTrue(validate_project(project).structurally_valid)
        pieces = [c for t in project.sequence.tracks for c in t.clips if c.nested]
        self.assertEqual(len(pieces),2)
        self.assertEqual(pieces[1].source_start,Rational.of(2))

    def test_group_requires_attached_family_and_rejects_stateful_mix(self):
        self.insert("v", "vtrack", "video", 0, 4)
        self.insert("s", "stickers", "video", 0, 4, createTrackRole="sticker",role="sticker",attachedToClipId="v")
        before = self.service.get_project("p").to_dict()
        with self.assertRaises(EditError):
            self.apply("clip.group", {"clipIds":["v"]})
        self.assertEqual(self.service.get_project("p").to_dict(),before)
        self.apply("audio.mixer", {"trackId":"vtrack","mix":{"solo":True}})
        with self.assertRaises(EditError):
            self.apply("clip.group", {"clipIds":["v","s"]})


@unittest.skipUnless(shutil.which("ffmpeg"),"FFmpeg required")
class CompoundRenderTests(SequenceRangeTests):
    def test_cross_track_compound_preserves_pixels_and_audio_and_parent_speed(self):
        root = Path(self.temp.name)
        red, blue, audio = root/"red.png",root/"blue.png",root/"tone.wav"
        Image.new("RGB",(160,90),"red").save(red)
        Image.new("RGB",(40,40),"blue").save(blue)
        samples = (np.sin(np.arange(96000)*2*np.pi*440/48000)*0.15*32767).astype("<i2")
        with wave.open(str(audio),"wb") as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(48000)
            stream.writeframes(samples.tobytes())
        self.insert("v", "vtrack", "video", 0, 2, sourcePath=str(red))
        self.insert("s", "stickers", "video", 0, 2, sourcePath=str(blue),
                    createTrackRole="sticker",role="sticker")
        self.apply("effect.add", {"clipId":"s","effectId":"cutvoke.transform",
            "params":{"position":{"x":15,"y":10},"scale":1,"rotation":0,"opacity":1}})
        self.insert("a", "atrack", "audio", 0, 2, sourcePath=str(audio))
        renderer = RenderService(gpu_effects=False)
        renderer.extract_frame(self.service.get_project("p"),0.5,str(root/"before.png"))
        self.apply("clip.group", {"clipIds":["v","s","a"],"groupId":"g"})
        renderer.extract_frame(self.service.get_project("p"),0.5,str(root/"after.png"))
        before = np.array(Image.open(root/"before.png").convert("RGB"),dtype=float)
        after = np.array(Image.open(root/"after.png").convert("RGB"),dtype=float)
        self.assertLess(np.abs(before-after).mean(),3)
        self.assertGreater(float((after[:,:,0] > 180).mean()), 0.2)
        self.assertGreater(float((after[:,:,2] > 180).mean()), 0.01)
        self.apply("effect.add", {"clipId":"g","effectId":"cutvoke.transform",
            "params":{"scale":0.5,"position":{"x":0,"y":0},"rotation":0,"opacity":1}})
        renderer.extract_frame(self.service.get_project("p"),0.5,str(root/"transformed.png"))
        transformed = np.array(Image.open(root/"transformed.png").convert("RGB"))
        self.assertLess(int(transformed[-1,-1].sum()),15)
        self.assertGreater(int(transformed[20,40].max()),180)
        self.apply("clip.speed", {"clipId":"g","speed":2})
        output = root/"grouped.mp4"
        renderer.render(self.service.get_project("p"),str(output))
        probe = renderer.probe_media(str(output))
        self.assertAlmostEqual(probe["duration"],1,delta=0.15)
        self.assertTrue(probe["has_audio"])
        decoded = subprocess.run(["ffmpeg","-v","error","-i",str(output),"-f","f32le","-ac","1","-ar","48000","-"],capture_output=True,check=True)
        signal = np.frombuffer(decoded.stdout,dtype="<f4")
        self.assertGreater(float(np.sqrt(np.mean(signal**2))),0.05)
