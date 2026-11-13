"""A request cancelled before entry must not prepare assets or launch work."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditService
from cutvoke.rendering.render import RenderCancelled, RenderService


class PrecancelledPreparationTests(unittest.TestCase):
    def setUp(self):
        self.service = EditService()
        self.addCleanup(self.service.close)
        self.project = self.service.create_project('pre-cancel', width=96, height=64)
        self.renderer = RenderService(gpu_effects=False)

    def test_all_cancelable_entry_points_stop_before_preparation(self):
        cancelled = threading.Event()
        cancelled.set()
        callback = Mock()
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / 'untouched.png')
            Path(output).write_bytes(b'existing output')
            calls = [
                lambda: self.renderer.extract_frame(self.project, 0, output, cancel_event=cancelled),
                lambda: self.renderer.render(self.project, output, cancel_event=cancelled, progress_callback=callback),
                lambda: self.renderer.render_preview_window(self.project, output, 0, 1, cancel_event=cancelled),
                # Positional cancellation must be recognized by signature binding too.
                lambda: self.renderer.extract_frame(self.project, 0, output, None, 30, True, cancelled),
            ]
            with patch('cutvoke.domain.essential_properties.resolve', side_effect=AssertionError('must not prepare')) as prepare:
                for call in calls:
                    with self.subTest(call=call), self.assertRaises(RenderCancelled):
                        call()
                prepare.assert_not_called()
            callback.assert_not_called()
            self.assertEqual(Path(output).read_bytes(), b'existing output')

    def test_invalid_arguments_are_not_hidden_by_cancellation(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(TypeError):
            self.renderer.extract_frame(self.project, 0, 'unused.png', cancel_event=cancelled, unknown_argument=True)

    def test_normal_requests_still_propagate_preparation_errors(self):
        for cancel in (None, threading.Event()):
            with self.subTest(cancel=cancel):
                with patch('cutvoke.domain.essential_properties.resolve', side_effect=ValueError('preparation failed')):
                    with self.assertRaisesRegex(ValueError, 'preparation failed'):
                        self.renderer.extract_frame(self.project, 0, 'unused.png', cancel_event=cancel)

    def test_normal_requests_still_render_identical_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.png'
            Image.new('RGB', (96, 64), (180, 40, 20)).save(source)
            self.project.sequence.tracks = [Track('video', 'video', [Clip('clip', AssetReference('asset', str(source)), R.of(0), R.of(1), R.of(0))])]
            pixels = []
            for index, cancel in enumerate((None, threading.Event())):
                output = root / f'{index}.png'
                self.assertEqual(self.renderer.extract_frame(self.project, .2, str(output), cancel_event=cancel), str(output))
                with Image.open(output) as frame:
                    self.assertEqual(frame.size, (96, 64))
                    pixels.append(frame.convert('RGB').tobytes())
            self.assertEqual(pixels[0], pixels[1])
