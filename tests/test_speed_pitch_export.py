"""Speed audio must keep its timeline duration and honor preservePitch."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

from cutvoke.core.model import Clip
from cutvoke.core.protocol import Actor, Command, ErrorCode
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditError, EditService


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class SpeedPitchExportTests(unittest.TestCase):
    def test_preserve_pitch_toggle_changes_tone_but_not_duration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "tone.mkv"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=red:s=160x90:r=30:d=2",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                "-map", "0:v", "-map", "1:a", "-c:v", "mpeg4", "-q:v", "5",
                "-c:a", "pcm_s16le", str(source),
            ], check=True, capture_output=True)

            service = EditService()
            project = service.create_project("speed-pitch", width=160, height=90)

            def apply(kind: str, payload: dict, command_id: str):
                current = service.get_project(project.project_id)
                return service.execute(Command(
                    type=kind, payload=payload, command_id=command_id,
                    project_id=project.project_id, expected_revision=current.revision,
                    actor=Actor("agent", "speed-test"),
                ))

            apply("track.add", {"trackId": "v1", "kind": "video"}, "track")
            apply("clip.insert", {
                "trackId": "v1", "clipId": "tone", "sourcePath": str(source),
                "timelineStart": {"num": 0, "den": 1},
                "timelineEnd": {"num": 2, "den": 1},
            }, "clip")

            renderer = RenderService()

            def peak_frequency(output: Path) -> float:
                audio = subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(output),
                    "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "-",
                ], check=True, capture_output=True).stdout
                samples = np.frombuffer(audio, dtype="<f4")
                middle = samples[round(0.2 * 48000):round(0.8 * 48000)]
                self.assertGreater(len(middle), 25000)
                spectrum = np.abs(np.fft.rfft(middle * np.hanning(len(middle))))
                frequencies = np.fft.rfftfreq(len(middle), 1 / 48000)
                return float(frequencies[int(np.argmax(spectrum))])

            for preserve, expected_hz in ((True, 440), (False, 880)):
                apply("clip.speed", {
                    "clipId": "tone", "speed": {"num": 2, "den": 1},
                    "preservePitch": preserve,
                }, f"speed-{preserve}")
                current = service.get_project(project.project_id)
                clip = current.sequence.tracks[0].clips[0]
                self.assertEqual(clip.preserve_pitch, preserve)
                self.assertEqual(Clip.from_dict(clip.to_dict()).preserve_pitch, preserve)
                output = root / f"speed-{preserve}.mp4"
                rendered = renderer.render(current, str(output))
                self.assertAlmostEqual(rendered["duration"], 1.0, delta=0.08)
                self.assertAlmostEqual(peak_frequency(output), expected_hz, delta=20)

            previous = service.get_project(project.project_id).revision
            with self.assertRaises(EditError) as bad:
                apply("clip.speed", {
                    "clipId": "tone", "speed": {"num": 2, "den": 1},
                    "preservePitch": "false",
                }, "invalid-preserve")
            self.assertEqual(bad.exception.code, ErrorCode.INVALID_ARGUMENT)
            self.assertEqual(service.get_project(project.project_id).revision, previous)
            apply("history.undo", {}, "undo-pitch")
            self.assertTrue(service.get_project(project.project_id).sequence.tracks[0].clips[0].preserve_pitch)
            apply("clip.audio", {"clipId": "tone", "pitch": 2}, "independent-pitch")
            pitched = root / "pitched.mp4"
            rendered = renderer.render(service.get_project(project.project_id), str(pitched))
            self.assertAlmostEqual(rendered["duration"], 1.0, delta=0.08)
            self.assertAlmostEqual(peak_frequency(pitched), 880, delta=20)
            apply("clip.speed", {
                "clipId": "tone", "speed": {"num": 2, "den": 1},
                "preservePitch": False,
            }, "disable-pitch-preservation")
            apply("clip.split", {
                "clipId": "tone", "at": {"num": 1, "den": 2},
            }, "split-fast-clip")
            left, right = service.get_project(project.project_id).sequence.tracks[0].clips
            self.assertFalse(right.preserve_pitch)
            self.assertEqual(right.pitch, left.pitch)
            self.assertEqual(float(right.source_start.to_fraction()), 1.0)


if __name__ == "__main__":
    unittest.main()
