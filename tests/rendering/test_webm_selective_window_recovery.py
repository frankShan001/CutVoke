"""CPU window reuse must preserve the decoded transparent WebM composition."""
import copy
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image

from cutvoke.domain.keyframes import Keyframe
from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational as R
from cutvoke.rendering.render import RenderService


def _ffmpeg(*args):
    return subprocess.run(
        ['ffmpeg', '-nostdin', '-v', 'error', *map(str, args)],
        check=True, capture_output=True,
    ).stdout


def _make_moving_overlay(root, codec):
    for n, x in enumerate((8, 36)):
        image = Image.new('RGBA', (64, 48), (0, 0, 0, 0))
        image.paste((0, 255, 0, 255), (x, 14, x + 20, 34))
        image.save(root / f'overlay-{n}.png')
    path = root / 'overlay.webm'
    _ffmpeg('-framerate', '1/10', '-i', root / 'overlay-%d.png',
            '-vf', 'fps=12', '-c:v', codec, '-pix_fmt', 'yuva420p',
            '-auto-alt-ref', '0', '-threads', '1', path)
    return path


class WebmSelectiveWindowRecoveryTests(unittest.TestCase):
    def _exercise(self, codec):
        from tests.rendering.test_window_cache import RealRecovery

        if not (shutil.which('ffmpeg') and shutil.which('ffprobe')):
            self.skipTest('FFmpeg and FFprobe required')
        encoders = subprocess.check_output(['ffmpeg', '-hide_banner', '-encoders'], text=True)
        if not any(len(row) > 1 and row[1] == codec for row in map(str.split, encoders.splitlines())):
            self.skipTest(f'fixture encoder {codec} unavailable')
        with TemporaryDirectory() as tmp, patch('os.cpu_count', return_value=1):
            root = Path(tmp)
            project = RealRecovery().project(root)
            path = _make_moving_overlay(root, codec)
            project.sequence.tracks.insert(1, Track('overlay', 'video', [
                Clip('overlay', AssetReference('overlay-source', str(path)),
                     R.of(0), R.of(39, 2), R.of(0)),
            ], muted=True))
            edited = copy.deepcopy(project)
            edited.sequence.tracks[0].clips[-1].keyframes['opacity'] = [
                Keyframe('late-opacity', R.of(0), .5),
            ]
            cold_directory = root / 'cold'
            cold_directory.mkdir()
            frames = 234

            def export(document, destination, expected):
                renderer = RenderService(gpu_effects=False)
                renderer.gpu_acceleration = False
                self.assertEqual(renderer._video_input_arguments(str(path)), ['-c:v', codec])
                result = renderer.render(document, str(destination))
                self.assertEqual(result['renderStrategy'], 'lossless-windows')
                self.assertEqual(result['windowCount'], 3)
                self.assertEqual(result['reusedWindowCount'], expected)
                rgb = _ffmpeg('-i', destination, '-an', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1')
                pcm = _ffmpeg('-i', destination, '-vn', '-f', 'f32le', '-acodec', 'pcm_f32le', 'pipe:1')
                self.assertEqual(len(rgb), frames * 64 * 48 * 3)
                self.assertTrue(pcm)

                def pixel(frame, x):
                    offset = (frame * 64 * 48 + 22 * 64 + x) * 3
                    return tuple(rgb[offset:offset + 3])

                # The transparent foreground reveals red below it and moves
                # between windows. Check both the opaque and uncovered regions.
                for frame, green_x, red_x in ((12, 18, 46), (132, 46, 18), (205, 46, 18)):
                    g = pixel(frame, green_x)
                    r = pixel(frame, red_x)
                    self.assertTrue(g[1] > 190 and g[0] < 50, (frame, g))
                    self.assertTrue(r[0] > 180 and r[1] < 50, (frame, r))
                return rgb, pcm

            baseline = export(project, root / 'base.mp4', 0)
            selective = export(edited, root / 'edited.mp4', 2)
            retry = export(edited, root / 'retry.mp4', 3)
            cold = export(edited, cold_directory / 'out.mp4', 0)
            self.assertNotEqual(baseline[0], cold[0])
            self.assertEqual(baseline[1], cold[1])
            self.assertEqual(selective, cold)
            self.assertEqual(retry, cold)

    def test_vp8(self):
        self._exercise('libvpx')

    def test_vp9(self):
        self._exercise('libvpx-vp9')
