"""Detected attacks are usable as real timeline beat candidates."""

from __future__ import annotations

import array
import shutil
import tempfile
import unittest
import wave
from pathlib import Path

from cutvoke.core.audio_analysis import analyze_audio


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class AudioBeatTests(unittest.TestCase):
    def test_click_track_returns_tempo_and_source_time_attacks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clicks.wav"
            sample_rate = 44100
            samples = array.array("h", [0]) * (sample_rate * 8)
            for beat in range(15):
                onset = round((0.2 + beat * 0.5) * sample_rate)
                for index in range(onset, onset + 600):
                    samples[index] = 25000
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(sample_rate)
                output.writeframes(samples.tobytes())

            result = analyze_audio(str(path))
            self.assertAlmostEqual(result["bpm"], 120, delta=2)
            self.assertGreaterEqual(len(result["beats"]), 12)
            self.assertAlmostEqual(result["beats"][0], 0.2, delta=0.03)
            self.assertAlmostEqual(result["beats"][1], 0.7, delta=0.03)


if __name__ == "__main__":
    unittest.main()
