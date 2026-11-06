"""Original offline test scenes. Run explicitly; the bridge never runs this file."""
import argparse
import json
import math
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw

WIDTH, HEIGHT, FPS, FRAMES = 640, 360, 24, 72


def frame(kind, index, accent):
    progress = index / (FRAMES - 1)
    ease = 1 - (1 - min(1, progress * 1.7)) ** 3
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0) if kind == "annotation" else (12, 18, 30, 255))
    draw = ImageDraw.Draw(image)
    if kind == "title":
        for radius in range(40, 400, 36):
            r = radius + progress * 30
            draw.ellipse((410-r, 165-r, 410+r, 165+r), outline=(*accent, 255), width=2)
        for n in range(6):
            x = int(-100 + n * 150 + progress * 90)
            draw.polygon([(x, 250), (x+72, 250), (x+185, 360), (x+110, 360)], fill=(23, 44, 65, 255))
    elif kind == "chart":
        for n, value in enumerate((.36, .62, .48, .84, 1)):
            x, bottom = 90 + n * 96, 300
            height = int(185 * value * ease)
            draw.rounded_rectangle((x, bottom-height, x+58, bottom), radius=8, fill=(*accent, 255))
        draw.line((70, 302, 590, 302), fill=(135, 153, 177, 255), width=2)
    elif kind == "annotation":
        draw.arc((365, 95, 555, 265), start=-90, end=-90 + 359 * ease, fill=(*accent, 255), width=10)
        x = int(215 + 135 * ease)
        draw.line((145, 180, x, 180), fill=(*accent, 255), width=8)
        draw.polygon([(x, 180), (x-24, 163), (x-24, 197)], fill=(*accent, 255))
    else:
        raise ValueError(kind)
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--params", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    params = json.loads(args.params.read_text())
    kind, accent = params["kind"], params["accent"]
    command = ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{WIDTH}x{HEIGHT}",
               "-r", str(FPS), "-i", "pipe:0", "-an", "-vf", "scale=in_range=full:out_range=tv:out_color_matrix=bt709",
               "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    if kind == "annotation":
        command += ["-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le",
                    "-bsf:v", "prores_metadata=color_primaries=bt709:color_trc=bt709:colorspace=bt709"]
    else:
        command += ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-bsf:v", "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1"]
    command += [str(args.output)]
    with subprocess.Popen(command, stdin=subprocess.PIPE) as process:
        try:
            for index in range(FRAMES):
                process.stdin.write(frame(kind, index, accent).tobytes())
        finally:
            process.stdin.close()
        if process.wait(timeout=60):
            raise RuntimeError("scene encoding failed")


if __name__ == "__main__":
    main()
