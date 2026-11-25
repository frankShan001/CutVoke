"""A generated leading gap must not replace the following clip's media clock."""

from fractions import Fraction
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cutvoke.domain.model import CURRENT_PROJECT_SCHEMA, AssetReference, Clip, Project, Sequence, Track
from cutvoke.domain.rational import Rational
from cutvoke.rendering.render import RenderService


FPS = Fraction(30000, 1001)
WIDTH, HEIGHT = 160, 96


def rational(value):
    value = Fraction(value)
    return Rational.of(value.numerator, value.denominator)


def frame_time(frame):
    return rational(frame / FPS)


def ffmpeg(*args, raw=None):
    return subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", *map(str, args)],
        input=raw, capture_output=True, check=True, timeout=45,
    ).stdout


def ids(path):
    raw = ffmpeg("-i", path, "-map", "0:v:0", "-fps_mode", "passthrough",
                 "-threads", 1, "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1")
    size = WIDTH * HEIGHT
    assert raw and len(raw) % size == 0
    frames = (
        [raw[i + 40 * WIDTH + 16 * lane + 8] > 128 for lane in range(10)]
        for i in range(0, len(raw), size)
    )
    return [sum(bit << k for k, bit in enumerate(frame[:9])) if frame[9] else None
            for frame in frames]


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires FFmpeg")
class LeadingGapRetimeTests(unittest.TestCase):
    def test_whole_export_keeps_source_samples_and_next_cut_after_a_leading_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            sources = {rate: directory / f"numbered-{rate.numerator}.mp4"
                       for rate in (FPS, 2 * FPS, Fraction(24000, 1001))}
            # Measure source identity independently of the output clock. The last
            # stripe distinguishes source frame zero from the generated canvas.
            raw = bytearray()
            for frame in range(320):
                row = b"".join(bytes([224 if frame & (1 << bit) else 32]) * 16
                               for bit in range(9)) + bytes([224]) * 16
                raw.extend(row * HEIGHT)
            for rate, path in sources.items():
                ffmpeg("-f", "rawvideo", "-pixel_format", "gray", "-video_size", f"{WIDTH}x{HEIGHT}",
                       "-framerate", str(rate), "-i", "pipe:0", "-c:v", "libx264", "-pix_fmt",
                       "yuv420p", "-threads", 1, "-g", 30, "-bf", 2, path, raw=raw)
                self.assertEqual(ids(path), list(range(320)))
            renderer = RenderService(gpu_effects=False)
            for label, rate, speed, start, source_frame in (
                ("fast", FPS, 2, 100, 0),
                ("reverse", FPS, -2, 100, 0),
                ("slow", FPS, Fraction(1, 2), 100, 12),
                ("high-rate", 2 * FPS, 1, 100, 0),
                ("low-rate", Fraction(24000, 1001), 1, 100, 0),
                ("same-rate", FPS, 1, 100, 12),
                ("fast-no-gap", FPS, 2, 0, 0),
            ):
                with self.subTest(case=label):
                    cut = start + 45
                    picture = Track("v", "video", clips=[
                        Clip("first", AssetReference("first", str(sources[rate])),
                             frame_time(start), frame_time(cut), rational(source_frame / rate),
                             speed=rational(speed)),
                        Clip("second", AssetReference("second", str(sources[FPS])),
                             frame_time(cut), frame_time(cut + 57), frame_time(240)),
                    ])
                    seq = Sequence("seq", WIDTH, HEIGHT, rational(FPS), tracks=[picture])
                    project = Project(schema_version=CURRENT_PROJECT_SCHEMA, project_id=label,
                                      revision="0", name=label, sequence=seq)
                    target = directory / f"{label}.mp4"
                    renderer.render(project, str(target), quality="low")
                    observed = ids(target)
                    self.assertEqual(len(observed), cut + 57)
                    self.assertEqual(observed[:start], [None] * start)
                    self.assertEqual(observed[cut:], list(range(240, 297)))
                    self.assertTrue(all(sample is not None for sample in observed[start:cut]))
                    for offset, sample in enumerate(observed[start:cut]):
                        # A source cell is determined by the edit's rate in
                        # rational units, not by the FFmpeg output under test.
                        step = Fraction(abs(speed)) * rate / FPS
                        start_source = source_frame + offset * step
                        if speed < 0:
                            start_source = source_frame + (44 - offset) * step
                        if step >= 1:
                            self.assertGreaterEqual(sample, start_source)
                            self.assertLess(sample, start_source + step)
                        else:
                            self.assertLess(abs(sample - start_source), 1)
