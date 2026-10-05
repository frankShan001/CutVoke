import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cutvoke.core import person_cutout
from cutvoke.core import person_model_setup as prepare_person_model


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class PreparePersonModelTests(unittest.TestCase):
    def test_download_is_verified_and_atomically_installed(self):
        payload = b"test model bytes"
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "selfie_segmenter.tflite"
            with patch.object(prepare_person_model, "MODEL_SHA256",
                              hashlib.sha256(payload).hexdigest()):
                result = prepare_person_model.prepare_model(
                    destination, opener=lambda *_args, **_kwargs: _Response(payload))
            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(result["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(list(Path(directory).glob("*.part")), [])

    def test_bad_download_is_removed_without_replacing_existing_model(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "selfie_segmenter.tflite"
            destination.write_bytes(b"previous valid model")
            with patch.object(prepare_person_model, "MODEL_SHA256", "0" * 64):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    prepare_person_model.prepare_model(
                        destination,
                        opener=lambda *_args, **_kwargs: _Response(b"corrupt"))
            self.assertEqual(destination.read_bytes(), b"previous valid model")
            self.assertEqual(list(Path(directory).glob("*.part")), [])

    def test_model_validator_checks_the_pinned_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "model.tflite"
            model.write_bytes(b"wrong model")
            self.assertFalse(person_cutout._model_is_valid(model))

    def test_model_validator_accepts_the_pinned_digest(self):
        payload = b"valid fixture model"
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "model.tflite"
            model.write_bytes(payload)
            with patch.object(person_cutout, "MODEL_SHA256",
                              hashlib.sha256(payload).hexdigest()):
                self.assertTrue(person_cutout._model_is_valid(model))

    def test_interactive_model_download_uses_its_own_digest_and_url(self):
        payload = b"interactive model fixture"
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "magic_touch.tflite"
            with patch.object(prepare_person_model, "INTERACTIVE_MODEL_SHA256",
                              hashlib.sha256(payload).hexdigest()), \
                 patch.object(prepare_person_model, "INTERACTIVE_MODEL_URL",
                              "https://example.invalid/magic_touch.tflite"), \
                 patch.object(person_cutout, "INTERACTIVE_MODEL_SHA256",
                              hashlib.sha256(payload).hexdigest()):
                result = prepare_person_model.prepare_model(
                    destination, interactive=True,
                    opener=lambda request, **_kwargs: _Response(
                        payload if request.full_url.endswith("magic_touch.tflite") else b"wrong"))
                self.assertTrue(person_cutout._interactive_model_is_valid(destination))
            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(result["sha256"], hashlib.sha256(payload).hexdigest())


if __name__ == "__main__":
    unittest.main()
