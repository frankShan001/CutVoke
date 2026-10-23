"""Separation refuses unsafe outputs and unavailable real engines honestly."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cutvoke.rendering.audio_separation import separate_audio


class SeparationFailureTests(unittest.TestCase):
    def test_unavailable_model_fails_without_synthetic_or_channel_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.wav"
            source.write_bytes(b"input-is-not-replaced")
            with patch("cutvoke.rendering.audio_separation.separation_status", return_value={"available": False}), \
                 patch("cutvoke.rendering.audio_separation.subprocess.run") as run:
                with self.assertRaisesRegex(RuntimeError, r"cutvoke\[separation\]"):
                    separate_audio(str(source), str(root / "stems"))
                run.assert_not_called()
            self.assertEqual(source.read_bytes(), b"input-is-not-replaced")
            self.assertFalse((root / "stems").exists())

    def test_existing_stems_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.wav"
            source.write_bytes(b"source")
            output = root / "stems"
            output.mkdir()
            existing = output / "vocals.wav"
            existing.write_bytes(b"previous-vocals")
            with self.assertRaisesRegex(ValueError, "new or empty"):
                separate_audio(str(source), str(output))
            self.assertEqual(existing.read_bytes(), b"previous-vocals")

    def test_explicit_cuda_does_not_silently_run_cpu(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.wav"
            source.write_bytes(b"source")
            with patch("cutvoke.rendering.audio_separation.separation_status", return_value={"available": True, "cuda": False}):
                with self.assertRaisesRegex(RuntimeError, "CUDA requested"):
                    separate_audio(str(source), str(root / "stems"), device="cuda")
