"""Detaching embedded sound keeps one audible stream and an editable audio lane."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

from cutvoke.core.protocol import Actor, Command, ErrorCode
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditError, EditService
from cutvoke.core.store import ProjectStore


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class DetachAudioTests(unittest.TestCase):
    def test_detach_is_atomic_editable_and_audibly_equivalent(self) -> None:
        with tempfile.TemporaryDirectory() as temp, \
                ProjectStore(str(Path(temp) / "project.sqlite")) as store:
            root = Path(temp)
            source = root / "video-with-tone.mkv"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=30:d=2",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                "-map", "0:v", "-map", "1:a", "-c:v", "mpeg4", "-q:v", "5",
                "-c:a", "pcm_s16le", str(source),
            ], check=True, capture_output=True)
            service = EditService(store=store)
            service.create_project("detach-audio", width=160, height=90)

            serial = 0
            def edit(kind: str, payload: dict):
                nonlocal serial
                serial += 1
                project = service.get_project("detach-audio")
                return service.execute(Command(
                    type=kind, payload=payload, command_id=f"detach-{serial}",
                    project_id=project.project_id, expected_revision=project.revision,
                    actor=Actor("agent", "detach-test"),
                ))

            edit("track.add", {"trackId": "video-track", "kind": "video"})
            edit("clip.insert", {
                "trackId": "video-track", "clipId": "scene", "sourcePath": str(source),
                "timelineStart": {"num": 0, "den": 1},
                "timelineEnd": {"num": 2, "den": 1},
            })
            edit("clip.audio", {"clipId": "scene", "volume": 0.7,
                                "fadeIn": 0.1, "fadeOut": 0.1})
            edit("effect.add", {"clipId": "scene", "effectId": "cutvoke.fx.compressor"})
            renderer = RenderService()
            before = root / "before.mp4"
            renderer.render(service.get_project("detach-audio"), str(before))

            self.assertIn("clip.detachAudio", {item["type"] for item in service.command_catalog()})
            result = edit("clip.detachAudio", {"clipId": "scene"})
            self.assertEqual(len(result.changed_entities), 3)
            project = service.get_project("detach-audio")
            video, audio = project.sequence.tracks
            self.assertEqual((video.kind, audio.kind), ("video", "audio"))
            self.assertEqual(audio.clips[0].asset_ref.source_path, str(source))
            self.assertEqual(audio.clips[0].attached_to_clip_id, "scene")
            self.assertEqual(audio.clips[0].volume.to_json(), {"num": "7", "den": "10"})
            self.assertEqual(video.clips[0].volume.to_json(), {"num": "0", "den": "1"})
            self.assertEqual([item["effectId"] for item in audio.clips[0].effects],
                             ["cutvoke.fx.compressor"])
            self.assertFalse(video.clips[0].effects)

            after = root / "after.mp4"
            renderer.render(project, str(after))
            def samples(path: Path) -> np.ndarray:
                raw = subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
                    "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "-",
                ], check=True, capture_output=True).stdout
                return np.frombuffer(raw, dtype="<f4")
            original, separated = samples(before), samples(after)
            usable = min(len(original), len(separated), 48000 * 2)
            before_rms = float(np.sqrt(np.mean(original[4800:usable - 4800] ** 2)))
            after_rms = float(np.sqrt(np.mean(separated[4800:usable - 4800] ** 2)))
            self.assertAlmostEqual(after_rms / before_rms, 1.0, delta=0.03)

            with self.assertRaises(EditError) as duplicate:
                edit("clip.detachAudio", {"clipId": "scene"})
            self.assertEqual(duplicate.exception.code, ErrorCode.INVALID_ARGUMENT)
            with self.assertRaises(EditError) as wrong_lane:
                edit("clip.move", {"clipId": audio.clips[0].id, "trackId": "video-track"})
            self.assertEqual(wrong_lane.exception.code, ErrorCode.INVALID_ARGUMENT)
            edit("clip.audio", {"clipId": audio.clips[0].id, "volume": 0.2})
            quieter = root / "quieter.mp4"
            renderer.render(service.get_project("detach-audio"), str(quieter))
            quiet = samples(quieter)
            quiet_rms = float(np.sqrt(np.mean(quiet[4800:usable - 4800] ** 2)))
            self.assertLess(quiet_rms / after_rms, 0.4)

            edit("clip.move", {"clipId": "scene", "timelineStart": {"num": 1, "den": 1}})
            moved = service.get_project("detach-audio")
            self.assertEqual(moved.sequence.tracks[1].clips[0].timeline_start.to_json(),
                             {"num": "1", "den": "1"})
            edit("history.undo", {})
            self.assertEqual(service.get_project("detach-audio").sequence.tracks[1].clips[0]
                             .timeline_start.to_json(), {"num": "0", "den": "1"})
            edit("history.undo", {})
            edit("history.undo", {})
            restored = service.get_project("detach-audio")
            self.assertEqual(len(restored.sequence.tracks), 1)
            self.assertEqual(restored.sequence.tracks[0].clips[0].volume.to_json(),
                             {"num": "7", "den": "10"})
            self.assertEqual(len(EditService(store=store).get_project("detach-audio")
                                 .sequence.tracks), 1)

            silent = root / "silent.mp4"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=red:s=160x90:r=30:d=1",
                "-c:v", "mpeg4", str(silent),
            ], check=True, capture_output=True)
            edit("clip.insert", {
                "trackId": "video-track", "clipId": "silent", "sourcePath": str(silent),
                "timelineStart": {"num": 2, "den": 1},
                "timelineEnd": {"num": 3, "den": 1},
            })
            revision = service.get_project("detach-audio").revision
            with self.assertRaises(EditError) as no_sound:
                edit("clip.detachAudio", {"clipId": "silent"})
            self.assertEqual(no_sound.exception.code, ErrorCode.INVALID_ARGUMENT)
            self.assertEqual(service.get_project("detach-audio").revision, revision)


if __name__ == "__main__":
    unittest.main()
