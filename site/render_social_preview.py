#!/usr/bin/env python3
"""Render docs/images/social-card.png at 1280×640.

From the repository root:

    python site/render_social_preview.py

The card places docs/images/readme/en/editor-overview.png on the left and the
share text on the right. It does not modify that screenshot.

Requires Pillow. The committed PNG was rendered with DejaVu Sans from the
paths below. Noto Sans is only a fallback, and a fallback font will not match
the committed bytes.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/images/readme/en/editor-overview.png"
# "preview" contains the substring "review", which tools/verify/check_asset_layout.py
# rejects under docs/images/. The share card is therefore social-card.png.
OUTPUT = ROOT / "docs/images/social-card.png"

WIDTH = 1280
HEIGHT = 640
MAX_BYTES = 1_000_000

BG = (14, 17, 22)
BAR = (142, 180, 255)
BORDER = (61, 70, 84)
TITLE = (244, 246, 249)
SUB = (214, 220, 228)
FOOT = (168, 176, 188)
RULE = (55, 64, 78)

TITLE_LINES = (
    "CutVoke —",
    "open-source",
    "AI video editor",
)
SUB_LINES = (
    "Claude Code · Codex · Cursor",
    "edit over MCP. You finish",
    "on the same timeline.",
)
FOOTER = "Windows · macOS · Linux · MIT"

FONT_CANDIDATES = (
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ),
    (
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    ),
    (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ),
)


def load_fonts() -> tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont, ImageFont.FreeTypeFont, str]:
    for bold_path, regular_path in FONT_CANDIDATES:
        bold = Path(bold_path)
        regular = Path(regular_path)
        if bold.is_file() and regular.is_file():
            title = ImageFont.truetype(str(bold), 58)
            sub = ImageFont.truetype(str(regular), 26)
            foot = ImageFont.truetype(str(regular), 22)
            return title, sub, foot, f"{bold} + {regular}"
    raise SystemExit(
        "No supported sans font found. Install DejaVu Sans "
        "(fonts-dejavu-core) and re-run."
    )


def line_step(font: ImageFont.FreeTypeFont) -> int:
    ascent, descent = font.getmetrics()
    return ascent + descent


def render() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"Missing editor screenshot: {SOURCE}")
    title_font, sub_font, foot_font, font_desc = load_fonts()

    canvas = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, WIDTH, 6), fill=BAR)

    frame = (40, 40, 616, 600)  # 576×560
    shot = Image.open(SOURCE).convert("RGB")
    fitted = ImageOps.fit(
        shot,
        (frame[2] - frame[0], frame[3] - frame[1]),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.46),
    )
    mask = Image.new("L", fitted.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, fitted.size[0] - 1, fitted.size[1] - 1),
        radius=18,
        fill=255,
    )
    canvas.paste(fitted, frame[:2], mask)
    draw.rounded_rectangle(frame, radius=18, outline=BORDER, width=2)

    text_x = 664
    text_right = WIDTH - 44
    text_width = text_right - text_x
    for font, lines in (
        (title_font, TITLE_LINES),
        (sub_font, SUB_LINES),
        (foot_font, (FOOTER,)),
    ):
        for line in lines:
            if draw.textlength(line, font=font) > text_width:
                raise SystemExit(f"Line does not fit the card: {line}")

    title_step = line_step(title_font) + 6
    sub_step = line_step(sub_font) + 6
    block_h = title_step * len(TITLE_LINES) + 22 + sub_step * len(SUB_LINES)
    # Keep the headline block clear of the top bar and the footer rule.
    y = 40 + max(0, (520 - block_h) // 2)

    for line in TITLE_LINES:
        draw.text((text_x, y), line, font=title_font, fill=TITLE)
        y += title_step
    y += 16
    for line in SUB_LINES:
        draw.text((text_x, y), line, font=sub_font, fill=SUB)
        y += sub_step

    foot_y = HEIGHT - 36 - line_step(foot_font)
    draw.line((text_x, foot_y - 18, text_right, foot_y - 18), fill=RULE, width=2)
    draw.text((text_x, foot_y), FOOTER, font=foot_font, fill=FOOT)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUTPUT, "PNG", optimize=True)
    size = OUTPUT.stat().st_size
    if size >= MAX_BYTES:
        raise SystemExit(f"{OUTPUT} is {size} bytes; limit is {MAX_BYTES - 1}")
    print(f"Wrote {OUTPUT.relative_to(ROOT)} {WIDTH}x{HEIGHT} {size} bytes")
    print(f"Font: {font_desc}")


if __name__ == "__main__":
    try:
        render()
    except BrokenPipeError:
        sys.exit(0)
