"""Editable filter strength must alter rendered pixels, including alpha overlays."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from cutvoke.core.effects import EffectParamInvalid, EffectRegistry
from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService


class FilterStrengthTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_strength_changes_pixels_continuously(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "color.png"
            Image.new("RGB", (96, 64), (70, 130, 190)).save(source)
            project = EditService().create_project("filter-strength", width=96, height=64,
                                                   fps=Rational.of(15))
            clip = Clip("image", AssetReference("image", str(source)),
                        Rational.of(0), Rational.of(1), Rational.of(0))
            project.sequence.tracks = [Track("v1", "video", [clip])]
            renderer = RenderService()
            for effect_id in ("cutvoke.fx.grayscale", "cutvoke.fx.invert",
                              "cutvoke.fx.sepia", "cutvoke.fx.vintage"):
                pixels = []
                for strength in (0.0, 0.5, 1.0):
                    clip.effects = [{"effectId": effect_id,
                                     "params": {"strength": strength}}]
                    frame = root / f"{effect_id.rsplit('.', 1)[-1]}-{strength}.png"
                    renderer.extract_frame(project, 0.5, str(frame))
                    with Image.open(frame) as image:
                        pixels.append(image.convert("RGB").getpixel((48, 32)))
                distance = lambda a, b: sum(abs(x - y) for x, y in zip(a, b))
                with self.subTest(effect=effect_id, pixels=pixels):
                    self.assertGreater(distance(pixels[0], pixels[2]), 8)
                    self.assertLess(distance(pixels[0], pixels[1]),
                                    distance(pixels[0], pixels[2]))
                    self.assertLess(distance(pixels[1], pixels[2]),
                                    distance(pixels[0], pixels[2]))

    def test_edge_threshold_pair_is_validated(self) -> None:
        registry = EffectRegistry()
        with self.assertRaises(EffectParamInvalid):
            registry.validate_params("cutvoke.fx.edge", {"low": 0.4, "high": 0.2})

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_invert_and_glow_keep_transparent_area_revealing_lower_track(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lower_path, upper_path = root / "lower.png", root / "upper.png"
            Image.new("RGB", (96, 64), (220, 25, 25)).save(lower_path)
            overlay = Image.new("RGBA", (96, 64), (25, 25, 220, 0))
            overlay.paste((25, 25, 220, 255), (48, 0, 96, 64))
            overlay.save(upper_path)
            project = EditService().create_project("filter-alpha", width=96, height=64,
                                                   fps=Rational.of(15))
            lower = Clip("lower", AssetReference("lower", str(lower_path)),
                         Rational.of(0), Rational.of(1), Rational.of(0))
            upper = Clip("upper", AssetReference("upper", str(upper_path)),
                         Rational.of(0), Rational.of(1), Rational.of(0))
            project.sequence.tracks = [Track("v1", "video", [lower]),
                                       Track("v2", "video", [upper])]
            renderer = RenderService()
            for name, params in (("invert", {"strength": 0.5}),
                                 ("glow", {"intensity": 1.7})):
                upper.effects = [{"effectId": f"cutvoke.fx.{name}", "params": params}]
                frame = root / f"{name}-composite.png"
                renderer.extract_frame(project, 0.5, str(frame))
                with Image.open(frame) as image:
                    left = image.convert("RGB").getpixel((8, 32))
                    right = image.convert("RGB").getpixel((88, 32))
                with self.subTest(effect=name):
                    self.assertGreater(left[0], left[2] * 2)
                    if name == "invert":
                        self.assertLess(abs(right[0] - right[2]), 30)
                    else:
                        self.assertGreater(right[2], right[0] * 2)


if __name__ == "__main__":
    unittest.main()
