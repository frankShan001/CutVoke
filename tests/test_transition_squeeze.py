"""Squeeze transitions must compress the named axis and survive the P=0 frame."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cutvoke.core.model import AssetReference, Clip, Project, Sequence, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class SqueezeTransitionTests(unittest.TestCase):
    def test_default_half_second_at_30fps_has_correct_axis_and_exact_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for color, duration, name in (("red", 3, "red-handle"),
                                          ("red", 2, "red-eof"), ("blue", 2, "blue")):
                subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y",
                    "-f", "lavfi", "-i", f"color=c={color}:s=640x360:r=30:d={duration}",
                    "-c:v", "libx264", "-preset", "ultrafast", str(root / f"{name}.mp4")],
                    check=True, capture_output=True, timeout=30)

            for effect_id, horizontal in (("cutvoke.transition.squeeze", True),
                                           ("cutvoke.transition.squeezev", False)):
                for outgoing_name in ("red-handle", "red-eof"):
                    with self.subTest(effect_id=effect_id, source=outgoing_name):
                        outgoing = Clip("outgoing", AssetReference("red", str(root / f"{outgoing_name}.mp4")),
                            Rational.of(0), Rational.of(2), Rational.of(0))
                        incoming = Clip("incoming", AssetReference("blue", str(root / "blue.mp4")),
                            Rational.of(2), Rational.of(4), Rational.of(0), effects=[{
                                "effectId": effect_id, "params": {"duration": .5}}])
                        project = Project("1", "squeeze", "0", Sequence("seq", 640, 360,
                            Rational.of(30), tracks=[Track("visual", "video", clips=[outgoing, incoming])]))
                        renderer = RenderService()
                        output = root / "export.mp4"
                        report = renderer.render(project, str(output), quality="low", overwrite=True)
                        self.assertAlmostEqual(report["duration"], 4, places=2)
                        raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(output),
                            "-an", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                            check=True, capture_output=True, timeout=30).stdout
                        frame_bytes = 640 * 360 * 3
                        self.assertEqual(len(raw) // frame_bytes, 120)
                        def pixel(frame, x, y):
                            start = frame * frame_bytes + (y * 640 + x) * 3
                            return tuple(raw[start:start + 3])
                        def assert_red(value):
                            self.assertGreater(value[0], value[2] + 150, value)
                        def assert_blue(value):
                            self.assertGreater(value[2], value[0] + 150, value)
                        assert_red(pixel(60, 320, 180))
                        assert_blue(pixel(75, 320, 180))
                        assert_red(pixel(67, 320, 180))
                        # A is a vertical strip for horizontal compression and
                        # a horizontal strip for vertical compression.
                        if horizontal:
                            assert_blue(pixel(67, 10, 180))
                            assert_red(pixel(67, 320, 10))
                        else:
                            assert_red(pixel(67, 10, 180))
                            assert_blue(pixel(67, 320, 10))

                        preview = root / "preview.mp4"
                        window = renderer.render_preview_window(project, str(preview), 1.8, 1.0)
                        self.assertAlmostEqual(window["duration"], 1.0, places=2)
                        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(preview),
                            "-f", "null", "-"], check=True, capture_output=True, timeout=30)
