"""Source-runtime simulation of missing frozen sources, not packaged-app QA."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cutvoke.rendering import window_cache
from cutvoke.rendering.render import RenderService
from tests.rendering import test_window_cache as fixtures


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class FrozenRecoveryFallback(unittest.TestCase):
    def test_source_less_layout_still_exports_without_opening_recovery_cache(self):
        with tempfile.TemporaryDirectory() as temporary, patch('os.cpu_count', return_value=1):
            root = Path(temporary)
            project = fixtures.RealRecovery().project(root)
            reference_dir = root / 'reference'
            reference_dir.mkdir()
            reference = reference_dir / 'cold.mp4'
            RenderService(gpu_effects=False).render(project, str(reference))

            def decoded(path, audio=False):
                options = (['-vn', '-f', 'f32le', '-acodec', 'pcm_f32le'] if audio else
                           ['-an', '-f', 'rawvideo', '-pix_fmt', 'rgba'])
                return subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path),
                    *options, 'pipe:1'], check=True, capture_output=True, timeout=30).stdout

            expected = [decoded(reference, audio) for audio in (False, True)]
            self.assertTrue(all(expected))
            for partial_sources in (False, True):
                with self.subTest(partial_sources=partial_sources):
                    output_dir = root / ('partial' if partial_sources else 'bytecode-only')
                    output_dir.mkdir()
                    package = output_dir / '_internal' / 'cutvoke'
                    rendering = package / 'rendering'
                    rendering.mkdir(parents=True)
                    # A real directory tree with a bytecode-like module location;
                    # no import or PyInstaller bootloader is being simulated.
                    module = rendering / 'window_cache.pyc'
                    module.write_bytes(b'fixture only')
                    if partial_sources:
                        (rendering / 'window_cache.py').write_text('# auxiliary source\n')
                    self.assertFalse((rendering / 'render.py').exists())
                    with patch.object(window_cache, '__file__', str(module)), \
                            patch.object(window_cache, 'WindowCache',
                                side_effect=AssertionError('unverified cache must not open')), \
                            patch.object(window_cache, 'recovery_session',
                                wraps=window_cache.recovery_session) as session:
                        for attempt in range(2):
                            target = output_dir / f'export-{attempt}.mp4'
                            renderer = RenderService(gpu_effects=False)
                            with patch.object(renderer, '_render_continuous_audio',
                                    wraps=renderer._render_continuous_audio) as audio_mix, \
                                    patch.object(renderer, '_verify_window_seams',
                                        wraps=renderer._verify_window_seams) as seams:
                                result = renderer.render(project, str(target))
                            self.assertEqual(result['reusedWindowCount'], 0)
                            self.assertIsNotNone(result['audioVerification'])
                            self.assertEqual(audio_mix.call_count, 1)
                            self.assertEqual(seams.call_count, 1)
                            for audio in (False, True):
                                self.assertEqual(decoded(target, audio), expected[int(audio)])
                        self.assertEqual(session.call_count, 2)
                        for call in session.call_args_list:
                            self.assertIsNotNone(call.args[1], 'exercise eligible recovery, not snapshot rejection')
                    self.assertFalse((output_dir / '.cutvoke-render-cache').exists())
                    self.assertFalse(list(output_dir.glob('cutvoke-export-windows-*')))
