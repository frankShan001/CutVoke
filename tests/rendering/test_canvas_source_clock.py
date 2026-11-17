"""Canvas source clocks must not depend on the chosen export window."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.editing.service import EditService
from cutvoke.domain.keyframes import Keyframe
from cutvoke.domain.rational import Rational
from cutvoke.rendering.render import RenderService
from tests.rendering import test_window_cache as recovery_fixtures


def decode(path):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-an',
        '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
        '-threads', '1', 'pipe:1'], check=True, capture_output=True, timeout=30).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 48, 64, 3)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class CanvasSourceClock(unittest.TestCase):
    def test_dense_motion_windows_match_independent_source_frames(self):
        # Each frame has a unique lossless RGB identity. A whole render is a
        # comparison, not the oracle: it also selected wrong frames before the fix.
        source_pixels = np.empty((240, 48, 64, 3), np.uint8)
        for index in range(240):
            source_pixels[index] = [(index * multiplier) % 256 for multiplier in (53, 97, 193)]
        for continuous in (False, True):
            with self.subTest(continuous=continuous), tempfile.TemporaryDirectory() as tmp, \
                    patch('os.cpu_count', return_value=1):
                root = Path(tmp)
                project = recovery_fixtures.RealRecovery().project(root)
                source = root / 'numbered.mkv'
                subprocess.run(['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                    '-s', '64x48', '-r', '12', '-i', 'pipe:0', '-c:v', 'ffv1',
                    '-pix_fmt', 'bgr0', str(source)], input=source_pixels.tobytes(), check=True, timeout=30)
                for clip in project.sequence.tracks[0].clips:
                    clip.asset_ref.source_path = str(source)
                    clip.source_start = clip.timeline_start if continuous else Rational.of(0)
                project.sequence.tracks[0].clips[-1].keyframes['opacity'] = [
                    Keyframe('late-opacity', Rational.of(0), .5)]
                owner = self
                class Inspect(RenderService):
                    def _verify_window_seams(self, seq, chunks, directory, start,
                                             overlay_position, cancel_event, alpha, quality):
                        report = super()._verify_window_seams(seq, chunks, directory, start,
                            overlay_position, cancel_event, alpha, quality)
                        owner.assertEqual(len(report['boundaries']), 2)
                        windows = np.concatenate([decode(path) for path, _duration in chunks])
                        owner.assertEqual(len(windows), 234)
                        # First 25 clips are opaque. The final animated clip
                        # still changes the entire track's compositing topology.
                        indices = np.arange(225) if continuous else np.arange(225) % 9
                        np.testing.assert_array_equal(windows[:225], source_pixels[indices])
                        saved_audio = getattr(self._render_ctx, 'export_audio_sequence', None)
                        self._render_ctx.export_audio_sequence = None
                        try:
                            graph, inputs, _, _, _, cwd = self._compile(seq, overlay_position)
                            full = root / 'whole.mkv'
                            self._run_ffmpeg(seq, graph, inputs, str(full), quality, None, False,
                                caption_cwd=cwd, output_start=0, output_duration=19.5, lossless=True)
                            np.testing.assert_array_equal(windows, decode(full))
                        finally:
                            self._render_ctx.export_audio_sequence = saved_audio
                        return report
                result = Inspect(gpu_effects=False).render(project, str(root / 'export.mp4'))
                self.assertEqual(result['reusedWindowCount'], 0)

    def test_original_testsrc2_late_opacity_seams(self):
        for continuous in (False, True):
            with self.subTest(continuous=continuous), tempfile.TemporaryDirectory() as tmp, \
                    patch('os.cpu_count', return_value=1):
                root = Path(tmp)
                project = recovery_fixtures.RealRecovery().project(root)
                source = root / 'motion.mkv'
                subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=64x48:rate=12:duration=20', '-c:v', 'ffv1', str(source)],
                    check=True, timeout=30)
                for clip in project.sequence.tracks[0].clips:
                    clip.asset_ref.source_path = str(source)
                    clip.source_start = clip.timeline_start if continuous else Rational.of(0)
                project.sequence.tracks[0].clips[-1].keyframes['opacity'] = [
                    Keyframe('late-opacity', Rational.of(0), .5)]
                result = RenderService(gpu_effects=False).render(project, str(root / 'export.mp4'))
                self.assertEqual(result['reusedWindowCount'], 0)

    def test_single_source_picture_is_held_through_canvas_eof(self):
        for source_fps in (7, 30, 60):
            with self.subTest(source_fps=source_fps), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / 'single.mkv'
                subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    f'color=red:s=64x48:r={source_fps}', '-frames:v', '1',
                    '-c:v', 'ffv1', str(source)], check=True, timeout=30)
                project = EditService().create_project('single', width=64, height=48)
                project.sequence.fps = Rational.of(12)
                clip = Clip('one', AssetReference('one', str(source)),
                    Rational.of(0), Rational.of(1, 12), Rational.of(0))
                clip.effects = [{'effectId': 'cutvoke.transform', 'params': {'opacity': .5}}]
                project.sequence.tracks = [Track('v', 'video', [clip])]
                renderer = RenderService(gpu_effects=False)
                graph, inputs, _, audio, _, cwd = renderer._compile(project.sequence, (0, 0))
                self.assertIn('canvasclock', graph)
                output = root / 'frame.mkv'
                renderer._run_ffmpeg(project.sequence, graph, inputs, str(output),
                    'high', None, audio, caption_cwd=cwd, lossless=True,
                    output_start=0, output_duration=1 / 12)
                frames = decode(output)
                self.assertEqual(len(frames), 1)
                self.assertGreater(int(frames[0, 20, 20, 0]), 100)
                self.assertLess(int(frames[0, 20, 20, 1]), 5)
