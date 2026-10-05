"""Create a small license-independent transparent person layer for preset QA."""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw


def make_person_alpha_video(path: Path, *, ffmpeg: str = "ffmpeg",
                            width: int = 320, height: int = 180,
                            fps: int = 30, frames: int = 120) -> None:
    """Write a moving, transparent cutout-shaped source in QuickTime Animation."""
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pixel_format", "rgba",
        "-video_size", f"{width}x{height}", "-framerate", str(fps),
        "-i", "pipe:0", "-an", "-c:v", "qtrle", "-pix_fmt", "argb",
        str(path),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    assert process.stdin is not None
    try:
        for frame in range(frames):
            phase = 2 * math.pi * frame / max(1, frames - 1)
            center_x = width * (0.5 + 0.16 * math.sin(phase))
            bob = 3.0 * math.sin(phase * 2)
            head_r = 12
            head_y = 35 + bob
            torso_top = 47 + bob
            torso_bottom = 112 + bob
            image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            skin = (243, 187, 143, 255)
            jacket = (44, 154, 205, 255)
            dark = (27, 45, 77, 255)
            draw.ellipse((center_x - head_r, head_y - head_r,
                          center_x + head_r, head_y + head_r), fill=skin)
            shoulder = 22
            draw.polygon((
                (center_x - 12, torso_top), (center_x - shoulder, torso_top + 13),
                (center_x - 15, torso_bottom), (center_x + 15, torso_bottom),
                (center_x + shoulder, torso_top + 13), (center_x + 12, torso_top),
            ), fill=jacket)
            arm_swing = 8 * math.sin(phase)
            draw.line((center_x - 17, torso_top + 12,
                       center_x - 32 - arm_swing, torso_bottom - 5), fill=skin, width=9)
            draw.line((center_x + 17, torso_top + 12,
                       center_x + 32 + arm_swing, torso_bottom - 5), fill=skin, width=9)
            draw.line((center_x - 8, torso_bottom - 2,
                       center_x - 13 - arm_swing * 0.35, height - 9), fill=dark, width=11)
            draw.line((center_x + 8, torso_bottom - 2,
                       center_x + 13 + arm_swing * 0.35, height - 9), fill=dark, width=11)
            process.stdin.write(image.tobytes())
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        process.stdin.close()
    stderr = process.stderr.read() if process.stderr else b""
    return_code = process.wait()
    if return_code:
        raise RuntimeError(
            f"FFmpeg failed to encode transparent person preview: "
            f"{stderr.decode('utf-8', errors='replace')[-1200:]}")
