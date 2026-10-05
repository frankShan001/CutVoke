"""Custom xfade blur keeps the old/new shot order and RGB planes intact."""

from __future__ import annotations

import subprocess
import unittest

from cutvoke.core.render import _xfade_blur_expr


class CustomBlurTransitionTests(unittest.TestCase):
    def test_midpoint_blends_colors_and_endpoints_keep_shot_order(self) -> None:
        width, height = 64, 48
        expression = _xfade_blur_expr("vertical")
        result = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=red:s={width}x{height}:r=30:d=2",
            "-f", "lavfi", "-i", f"color=c=blue:s={width}x{height}:r=30:d=2",
            "-filter_complex", ("[0:v][1:v]xfade=transition=custom:duration=1:"
                                f"offset=0.5:expr='{expression}'[v]"),
            "-map", "[v]", "-frames:v", "46", "-pix_fmt", "rgb24",
            "-f", "rawvideo", "pipe:1",
        ], capture_output=True, check=True)
        frame_bytes = width * height * 3

        def center(frame: int) -> tuple[int, int, int]:
            index = frame * frame_bytes + (height // 2 * width + width // 2) * 3
            return tuple(result.stdout[index:index + 3])  # type: ignore[return-value]

        old, middle, new = center(0), center(30), center(45)
        self.assertGreater(old[0], 200)
        self.assertLess(old[2], 20)
        self.assertTrue(90 < middle[0] < 170, middle)
        self.assertLess(middle[1], 20)
        self.assertTrue(90 < middle[2] < 170, middle)
        self.assertLess(new[0], 20)
        self.assertGreater(new[2], 200)


if __name__ == "__main__":
    unittest.main()
