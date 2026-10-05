"""Extract the ImageGen music-rhythm overlay atlas into individual stickers."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHEET = ROOT / "docs/assets/music-rhythm-overlay-atlas-20260926.png"
DEFAULT_OUTPUT = ROOT / "src/cutvoke/assets/stickers"
DEFAULT_REPORT = ROOT / "docs/assets/music-rhythm-overlay-atlas-20260926.extraction.json"
DEFAULT_CONTACT = ROOT / "docs/assets/music-rhythm-overlay-contact-20260926.png"
CELL_COUNT = 4
OUTPUT_SIZE = 320
PADDING = 32
CROP_MARGIN = 16
SUBCATEGORY = "音乐节奏"
LICENSE = "Project-internal original (AI-generated); not separately licensed for redistribution"

STICKERS = (
    ("audiofx_waveform_cyan_coral", "青珊瑚波形光带", ("音乐", "波形", "节奏", "青色", "珊瑚")),
    ("audiofx_bass_pulse_violet", "紫色低音脉冲环", ("音乐", "低音", "脉冲", "紫色")),
    ("audiofx_equalizer_arc_gold", "金色弧形均衡器", ("音乐", "均衡器", "频谱", "金色")),
    ("audiofx_music_notes_neon", "霓虹音符轨迹", ("音乐", "音符", "霓虹", "轨迹")),
    ("audiofx_spectrum_halo_rainbow", "彩虹频谱光环", ("音乐", "频谱", "光环", "彩虹")),
    ("audiofx_soundwave_dots_aqua", "青色点阵声波", ("音乐", "声波", "点阵", "青色")),
    ("audiofx_rhythm_pulse_coral", "珊瑚节奏波形", ("音乐", "节拍", "波形", "珊瑚")),
    ("audiofx_acoustic_arcs_pearl", "珍珠声波弧线", ("音乐", "声波", "弧线", "珍珠")),
    ("audiofx_beat_burst_mint", "薄荷节拍光束", ("音乐", "节拍", "光束", "薄荷")),
    ("audiofx_frequency_badge_violet", "蓝紫频谱徽章", ("音乐", "频谱", "圆形", "蓝紫")),
    ("audiofx_vinyl_orbit_amber", "琥珀唱片光环", ("音乐", "唱片", "环绕", "琥珀")),
    ("audiofx_mic_aura_magenta", "洋红麦克风光晕", ("音乐", "麦克风", "光晕", "洋红")),
    ("audiofx_beat_stars_cyan", "星光节奏曲线", ("音乐", "星光", "节拍", "曲线")),
    ("audiofx_lyric_spotlight_peach", "蜜桃歌词聚光", ("音乐", "歌词", "聚光", "蜜桃")),
    ("audiofx_energy_spiral_teal", "青金声能螺旋", ("音乐", "声能", "螺旋", "青金")),
    ("audiofx_stereo_waveform_rainbow", "彩虹立体声波形", ("音乐", "立体声", "波形", "彩虹")),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clean_alpha(image: Image.Image) -> Image.Image:
    pixels = bytearray(image.convert("RGBA").tobytes())
    for offset in range(0, len(pixels), 4):
        if pixels[offset + 3] < 8:
            pixels[offset:offset + 4] = b"\0\0\0\0"
    return Image.frombytes("RGBA", image.size, bytes(pixels))


def _write_contact_sheet(paths: list[Path], output: Path) -> None:
    tile, block = 220, 20
    sheet = Image.new("RGB", (tile * CELL_COUNT, tile * CELL_COUNT), (28, 31, 39))
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(paths):
        row, column = divmod(index, CELL_COUNT)
        x, y = column * tile, row * tile
        background = Image.new("RGBA", (tile, tile), (242, 243, 246, 255))
        checker = ImageDraw.Draw(background)
        for cy in range(0, tile, block):
            for cx in range(0, tile, block):
                if (cx // block + cy // block) % 2:
                    checker.rectangle((cx, cy, cx + block - 1, cy + block - 1),
                                      fill=(220, 223, 228, 255))
        with Image.open(path) as source:
            source = source.convert("RGBA")
            source.thumbnail((tile - 30, tile - 30), Image.Resampling.LANCZOS)
            background.alpha_composite(source, ((tile - source.width) // 2,
                                                (tile - source.height) // 2))
        sheet.paste(background.convert("RGB"), (x, y))
        draw.rectangle((x + 6, y + 6, x + 35, y + 25), fill=(22, 27, 35))
        draw.text((x + 12, y + 9), f"{index + 1:02d}", fill=(255, 255, 255))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, optimize=True)


def extract(sheet: Path, output: Path, report: Path, contact: Path) -> list[dict]:
    source = Image.open(sheet).convert("RGBA")
    width, height = source.size
    alpha_extrema = source.getchannel("A").getextrema()
    if width < CELL_COUNT or height < CELL_COUNT or alpha_extrema[0] != 0 or alpha_extrema[1] < 240:
        raise ValueError("source atlas must be a 4x4 image with genuine transparent alpha")
    cell_w, cell_h = width / CELL_COUNT, height / CELL_COUNT
    assets: list[dict] = []
    targets: list[Path] = []
    prepared_images: list[tuple[Path, Image.Image, bytes]] = []
    output.mkdir(parents=True, exist_ok=True)

    for index, (stem, name, keywords) in enumerate(STICKERS):
        row, column = divmod(index, CELL_COUNT)
        x0, x1 = round(column * cell_w), round((column + 1) * cell_w)
        y0, y1 = round(row * cell_h), round((row + 1) * cell_h)
        side = min(x1 - x0, y1 - y0)
        cell = source.crop((x0, y0, x1, y1))
        cell_visible = cell.getchannel("A").point(lambda value: 255 if value >= 32 else 0)
        cell_bounds = cell_visible.getbbox()
        if cell_bounds is None:
            raise ValueError(f"{stem} has no visible artwork")
        left = max(0, cell_bounds[0] - CROP_MARGIN)
        top = max(0, cell_bounds[1] - CROP_MARGIN)
        right = min(x1 - x0, cell_bounds[2] + CROP_MARGIN)
        bottom = min(y1 - y0, cell_bounds[3] + CROP_MARGIN)
        box = (x0 + left, y0 + top, x0 + right, y0 + bottom)
        crop_side = max(right - left, bottom - top)
        square = Image.new("RGBA", (crop_side, crop_side), (0, 0, 0, 0))
        tile = _clean_alpha(source.crop(box))
        square.alpha_composite(tile, ((crop_side - tile.width) // 2,
                                      (crop_side - tile.height) // 2))
        cropped = square
        visible = cropped.getchannel("A").point(lambda value: 255 if value >= 32 else 0)
        bounds = visible.getbbox()
        if bounds is None or min(bounds[0], bounds[1], crop_side - bounds[2], crop_side - bounds[3]) < 4:
            raise ValueError(f"{stem} is empty or touches the safe crop boundary")
        if visible.histogram()[255] < crop_side * crop_side * 0.06:
            raise ValueError(f"{stem} contains too little visible artwork")

        target = output / f"{stem}.png"
        if target.exists():
            raise FileExistsError(f"will not overwrite existing asset: {target}")
        art_size = OUTPUT_SIZE - PADDING * 2
        art = cropped.copy()
        art.thumbnail((art_size, art_size), Image.Resampling.LANCZOS)
        prepared = Image.new("RGBA", (OUTPUT_SIZE, OUTPUT_SIZE), (0, 0, 0, 0))
        prepared.alpha_composite(art, ((OUTPUT_SIZE - art.width) // 2,
                                       (OUTPUT_SIZE - art.height) // 2))
        payload = io.BytesIO()
        prepared.save(payload, format="PNG", optimize=True)
        png_bytes = payload.getvalue()
        prepared_images.append((target, prepared, png_bytes))
        targets.append(target)
        alpha = prepared.getchannel("A")
        assets.append({
            "index": index + 1,
            "stem": stem,
            "name": name,
            "subcategory": SUBCATEGORY,
            "keywords": list(keywords),
            "row": row,
            "column": column,
            "sourceCell": [x0, y0, x1, y1],
            "cropBox": list(box),
            "size": [OUTPUT_SIZE, OUTPUT_SIZE],
            "alphaBounds": list(alpha.point(lambda value: 255 if value >= 32 else 0).getbbox()),
            "alphaExtrema": list(alpha.getextrema()),
            "visiblePixelsAtAlpha32": visible.histogram()[255],
            "sha256": hashlib.sha256(png_bytes).hexdigest(),
        })

    for target, _prepared, png_bytes in prepared_images:
        if target.exists():
            raise FileExistsError(f"will not overwrite existing asset: {target}")
    for target, _prepared, png_bytes in prepared_images:
        target.write_bytes(png_bytes)

    _write_contact_sheet(targets, contact)
    source_sha = _sha(sheet)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "generator": "OpenAI ImageGen",
        "layout": "4x4",
        "source": sheet.relative_to(ROOT).as_posix(),
        "sourceSha256": source_sha,
        "sourceSize": [width, height],
        "sourceAlphaExtrema": list(alpha_extrema),
        "cellCropMargin": CROP_MARGIN,
        "outputSize": [OUTPUT_SIZE, OUTPUT_SIZE],
        "padding": PADDING,
        "subcategory": SUBCATEGORY,
        "license": LICENSE,
        "contactSheet": contact.relative_to(ROOT).as_posix(),
        "items": assets,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return assets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--contact", type=Path, default=DEFAULT_CONTACT)
    args = parser.parse_args()
    items = extract(args.sheet, args.output, args.report, args.contact)
    print(f"extracted {len(items)} music-rhythm stickers; contact sheet: {args.contact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
