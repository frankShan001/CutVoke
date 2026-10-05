"""Render regressions for defects found while reviewing real project frames."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class AnimationVisualMotionTests(unittest.TestCase):
    def _project(self, source: Path, effect_id: str, params: dict):
        project = EditService().create_project("visual-motion", width=192, height=108,
                                               fps=Rational.of(30))
        clip = Clip("upper", AssetReference("upper", str(source)), Rational.of(0),
                    Rational.of(2), Rational.of(0), effects=[
                        {"effectId": effect_id, "params": params},
                    ])
        project.sequence.tracks = [Track("v1", "video", [clip])]
        return project

    def _frames(self, movie: Path) -> list[Image.Image]:
        raw = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(movie),
            "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ], check=True, capture_output=True).stdout
        size = 192 * 108 * 3
        return [Image.frombytes("RGB", (192, 108), raw[i:i + size])
                for i in range(0, len(raw), size)]

    def test_30fps_slide_updates_geometry_each_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, movie = root / "red.png", root / "slide.mp4"
            Image.new("RGB", (192, 108), (230, 25, 25)).save(source)
            project = self._project(source, "cutvoke.anim.slideDown",
                                    {"duration": 1, "distance": 1, "easing": "linear"})
            RenderService().render(project, str(movie), quality="high")
            frames = self._frames(movie)
            self.assertEqual(len(frames), 60)
            boundaries = [max((y for y in range(108)
                               if frame.getpixel((96, y))[0] > 150), default=-1)
                          for frame in frames[:30]]
            # Old sampling held the transform for about three frames, yielding
            # only eleven different positions during this one-second slide.
            self.assertGreaterEqual(len(set(boundaries)), 25, boundaries)
            self.assertTrue(all(a <= b for a, b in zip(boundaries, boundaries[1:])))
            self.assertGreater(frames[30].getpixel((96, 106))[0], 150)

    def test_combo_pull_up_does_not_snap_scale_at_lane_end(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, movie = root / "marker.png", root / "combo.mp4"
            image = Image.new("RGB", (192, 108), (20, 40, 100))
            ImageDraw.Draw(image).rectangle((65, 35, 125, 75), fill=(245, 225, 20))
            image.save(source)
            project = self._project(source, "cutvoke.anim.comboPullUp",
                                    {"duration": 1, "primary": "zoomOut",
                                     "secondary": "slideUp", "toScale": .8,
                                     "distance": .3, "easing": "ease-out"})
            RenderService().render(project, str(movie), quality="high")
            frames = self._frames(movie)

            def marker_bounds(frame: Image.Image):
                mask = Image.new("L", frame.size)
                mask.putdata([255 if r > 180 and g > 160 and b < 90 else 0
                              for r, g, b in frame.getdata()])
                return mask.getbbox()

            before, after = marker_bounds(frames[29]), marker_bounds(frames[30])
            self.assertIsNotNone(before)
            self.assertIsNotNone(after)
            # Previously the marker grew by around 50% in this single frame.
            for left, right in zip(before, after):
                self.assertLessEqual(abs(left - right), 2, (before, after))

    def test_slow_source_fills_each_animated_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, movie = root / "red-15fps.mp4", root / "slow-fade.mp4"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=red:s=192x108:r=15:d=1",
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
            ], check=True, capture_output=True)
            project = self._project(source, "cutvoke.anim.fadeIn",
                                    {"duration": 1, "easing": "linear"})
            project.sequence.tracks[0].clips[0].speed = Rational.of(1, 2)
            RenderService().render(project, str(movie), quality="high")
            frames = self._frames(movie)
            self.assertEqual(len(frames), 60)
            for index, frame in enumerate(frames[6:]):
                with self.subTest(frame=index + 6):
                    self.assertGreater(frame.getpixel((96, 54))[0], 30)

    def test_reveal_keeps_source_geometry_and_lower_layer_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, lower = root / "quadrants.png", root / "lower.png"
            image = Image.new("RGB", (192, 108))
            drawing = ImageDraw.Draw(image)
            drawing.rectangle((0, 0, 95, 53), fill=(25, 25, 230))
            drawing.rectangle((96, 0, 191, 53), fill=(25, 230, 25))
            drawing.rectangle((0, 54, 95, 107), fill=(230, 230, 25))
            drawing.rectangle((96, 54, 191, 107), fill=(230, 25, 230))
            image.save(source)
            Image.new("RGB", image.size, (230, 25, 25)).save(lower)
            checks = {
                "left": ((65, 25), (170, 25)),
                "right": ((125, 25), (20, 25)),
                "up": ((30, 65), (30, 15)),
                "down": ((30, 40), (30, 95)),
            }
            for direction, (visible, hidden) in checks.items():
                with self.subTest(direction=direction):
                    project = self._project(source, "cutvoke.anim.reveal",
                                            {"duration": 1, "direction": direction,
                                             "easing": "linear"})
                    backing = Clip("lower", AssetReference("lower", str(lower)),
                                   Rational.of(0), Rational.of(2), Rational.of(0))
                    project.sequence.tracks.insert(0, Track("backing", "video", [backing]))
                    target = root / f"reveal-{direction}.png"
                    RenderService().extract_frame(project, .45, str(target))
                    with Image.open(target) as rendered:
                        actual = rendered.convert("RGB")
                        for observed, expected in zip(actual.getpixel(visible),
                                                      image.getpixel(visible)):
                            self.assertLessEqual(abs(observed - expected), 8)
                        red, green, blue = actual.getpixel(hidden)
                        self.assertGreater(red, 150)
                        self.assertLess(max(green, blue), 70)

    def test_loop_animation_preserves_temporal_trail_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(60):
                image = Image.new("RGB", (192, 108), (0, 0, 0))
                ImageDraw.Draw(image).rectangle((index * 2, 34, index * 2 + 23, 73),
                                               fill=(230, 25, 25))
                image.save(root / f"frame{index:03d}.png")
            source = root / "moving-marker.mp4"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-framerate", "30", "-i", str(root / "frame%03d.png"),
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
            ], check=True, capture_output=True)
            project = self._project(source, "cutvoke.anim.breathe",
                                    {"amplitude": .01, "period": 2})
            plain_movie, trail_movie = root / "plain.mp4", root / "trail.mp4"
            renderer = RenderService()
            renderer.render(project, str(plain_movie), quality="high")
            project.sequence.tracks[0].clips[0].effects.append({
                "effectId": "cutvoke.fx.trail", "params": {"frames": 8, "decay": .78},
            })
            renderer.render(project, str(trail_movie), quality="high")

            def red_width(frame: Image.Image) -> int:
                red = [x for x in range(192) if frame.getpixel((x, 54))[0] > 25]
                return max(red) - min(red) + 1

            plain = red_width(self._frames(plain_movie)[24])
            trail = red_width(self._frames(trail_movie)[24])
            # Per-frame tmix restarts produced the same single sharp marker.
            self.assertGreater(trail, plain + 6, (plain, trail))


if __name__ == "__main__":
    unittest.main()
