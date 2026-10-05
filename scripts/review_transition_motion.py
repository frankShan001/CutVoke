"""Make preset motion sheets and nearest-neighbor measurements for review.

The report helps a human find similar candidates; it never approves presets.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from itertools import combinations
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cutvoke.core.preset_catalog import PresetCatalog  # noqa: E402
from generate_preset_previews import _frame  # noqa: E402


TRANSITION_TIMES = (0.55, 0.65, 0.75, 0.85, 0.95)


def _sample_times(family: str, subcategory: str) -> tuple[float, ...]:
    if family == "animation":
        if subcategory in ("入场", "组合"):
            return (0.02, 0.08, 0.16, 0.28, 0.45)
        if subcategory == "循环":
            return (0.08, 0.25, 0.55, 0.80, 1.00)
        return (0.10, 0.30, 0.55, 0.80, 1.00)
    if family == "text":
        return (0.08, 0.30, 0.55, 0.85, 1.15)
    if family == "fx":
        return (0.08, 0.30, 0.55, 0.85, 1.15)
    return TRANSITION_TIMES


def _video_path(spec) -> Path:
    path = Path(spec.motion_preview)
    return path if path.is_absolute() else spec.asset_root / path


def _distance(left: list[Image.Image], right: list[Image.Image]) -> float:
    values = []
    for a, b in zip(left, right, strict=True):
        values.append(sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3)
    return round(sum(values) / len(values), 3)


def review(output: Path, family: str = "transition") -> dict:
    output.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list] = {}
    for spec in PresetCatalog.builtin().all():
        if spec.family == family and spec.status != "retired":
            groups.setdefault(spec.subcategory, []).append(spec)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 16) if font_path.exists() else ImageFont.load_default()
    small = ImageFont.truetype(str(font_path), 11) if font_path.exists() else ImageFont.load_default()
    report = {"family": family, "groups": {}}
    for subcategory, specs in groups.items():
        sample_times = _sample_times(family, subcategory)
        samples: dict[str, list[Image.Image]] = {}
        sheet = Image.new("RGB", (len(sample_times) * 208 + 170,
                                  len(specs) * 138 + 28), "#18202d")
        draw = ImageDraw.Draw(sheet)
        for row, spec in enumerate(specs):
            draw.text((8, row * 138 + 35), spec.name, font=font, fill="white")
            draw.text((8, row * 138 + 62), spec.id.rsplit(".", 1)[-1],
                      font=small, fill="#b6c6d9")
            frames = []
            with tempfile.TemporaryDirectory(prefix="cutvoke-transition-frames-") as temp:
                for column, time in enumerate(sample_times):
                    extracted = Path(temp) / f"{column}.png"
                    _frame(_video_path(spec), time, extracted)
                    with Image.open(extracted) as original:
                        frame = original.convert("RGB")
                        frame.thumbnail((192, 108))
                        frames.append(frame.copy())
                        sheet.paste(frame, (170 + column * 208, row * 138 + 8))
                    draw.text((170 + column * 208, row * 138 + 118), f"{time:.2f}s",
                              font=small, fill="#b6c6d9")
            samples[spec.id] = frames
        sheet_path = output / f"{family}-{subcategory}-motion.png"
        sheet.save(sheet_path)
        pairs = sorted(
            ({"a": a.id, "b": b.id,
              "meanRgbDistance": _distance(samples[a.id], samples[b.id])}
             for a, b in combinations(specs, 2)),
            key=lambda pair: pair["meanRgbDistance"],
        )
        report["groups"][subcategory] = {
            "count": len(specs), "sampleTimes": sample_times,
            "sheet": str(sheet_path), "nearestPairs": pairs[:8]
        }
    (output / f"{family}-motion-review.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO / "output/transition_review")
    parser.add_argument("--family", choices=("transition", "animation", "fx", "filter", "text"),
                        default="transition")
    args = parser.parse_args()
    result = review(args.output, args.family)
    for name, group in result["groups"].items():
        print(f"{name}: {group['count']} candidates; closest: {group['nearestPairs'][:2]}")
