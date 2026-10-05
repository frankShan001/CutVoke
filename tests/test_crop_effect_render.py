from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class CropEffectRenderTests(unittest.TestCase):
    def test_crop_selects_requested_region_in_preview_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "quadrants.png"
            artwork = Image.new("RGB", (64, 64))
            for y in range(64):
                for x in range(64):
                    if y < 32:
                        color = (232, 24, 24) if x < 32 else (20, 220, 28)
                    else:
                        color = (24, 32, 232) if x < 32 else (232, 220, 20)
                    artwork.putpixel((x, y), color)
            artwork.save(source)

            service = EditService()
            project = service.create_project(
                "crop-render", width=64, height=64, fps=Rational.of(15))
            project.sequence.tracks = [Track("v1", "video", [Clip(
                "quadrants", AssetReference("quadrants", str(source)),
                Rational.of(0), Rational.of(2), Rational.of(0),
                effects=[{"effectId": "cutvoke.fx.crop", "version": "1.0.0",
                          "params": {"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0}}],
            )])]
            renderer = RenderService()
            preview = root / "crop-preview.png"
            renderer.extract_frame(project, 0.5, str(preview))
            with Image.open(preview) as frame:
                top = frame.convert("RGB").getpixel((8, 8))
                bottom = frame.convert("RGB").getpixel((8, 56))
                self.assertGreater(top[1], top[0] + 100, f"top-left should come from green right half: {top}")
                self.assertGreater(bottom[0], bottom[2] + 100, f"bottom-left should come from yellow right half: {bottom}")

            output = root / "crop-export.mp4"
            result = renderer.render(project, str(output), quality="low")
            self.assertEqual((result["width"], result["height"]), (64, 64))
            decoded = subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", "0.5", "-i", str(output), "-frames:v", "1",
                str(root / "export-frame.png"),
            ], check=True, capture_output=True)
            self.assertEqual(decoded.returncode, 0)
            with Image.open(preview) as expected, Image.open(root / "export-frame.png") as actual:
                difference = sum(ImageStat.Stat(ImageChops.difference(
                    expected.convert("RGB"), actual.convert("RGB"))).mean) / 3
            self.assertLess(difference, 8.0, f"preview/export mean RGB difference: {difference}")


if __name__ == "__main__":
    unittest.main()
