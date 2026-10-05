"""Render versioned demo media for built-in preset candidates.

The samples use generated artwork (including an alpha-channel person layer for
Person FX) and the same RenderService path as export. This script only passes the cover/motion-preview checks; it never
approves a preset or fabricates editing, visual-review, or export evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cutvoke.core.effects import default_registry, animation_slot  # noqa: E402
from cutvoke.core.model import AssetReference, Clip, Track  # noqa: E402
from cutvoke.core.preset_catalog import PresetCatalog  # noqa: E402
from cutvoke.core.rational import Rational  # noqa: E402
from cutvoke.core.render import RenderService  # noqa: E402
from person_fx_preview_source import make_person_alpha_video  # noqa: E402


CATALOG = REPO / "src/cutvoke/core/builtin_presets.json"
ASSETS = REPO / "src/cutvoke/assets/preset_previews"


def _art(path: Path, variant: int) -> None:
    """Make clean, license-independent contrasting frames for motion review."""
    w, h = 320, 180
    top, bottom = (((27, 17, 70), (250, 84, 81)) if variant == 0
                   else ((5, 55, 74), (21, 203, 192)))
    image = Image.new("RGB", (w, h))
    pixels = image.load()
    for y in range(h):
        mix = y / (h - 1)
        color = tuple(round(top[i] * (1 - mix) + bottom[i] * mix) for i in range(3))
        for x in range(w):
            pixels[x, y] = color
    draw = ImageDraw.Draw(image, "RGBA")
    if variant == 0:
        draw.ellipse((180, -25, 345, 140), fill=(255, 219, 135, 225))
        draw.rounded_rectangle((31, 37, 169, 143), radius=19,
                               fill=(64, 31, 111, 218), outline=(253, 221, 227, 210), width=3)
        draw.polygon(((0, 156), (116, 91), (201, 180), (0, 180)), fill=(28, 19, 89, 220))
        for i in range(5):
            draw.line((41 + i * 18, 53, 91 + i * 18, 124), fill=(255, 193, 172, 120), width=3)
    else:
        draw.polygon(((232, -5), (335, 90), (245, 186), (138, 86)),
                     fill=(16, 31, 117, 225))
        draw.ellipse((16, 20, 169, 170), fill=(3, 225, 205, 175),
                     outline=(217, 255, 247, 240), width=4)
        draw.rounded_rectangle((73, 51, 260, 124), radius=17,
                               fill=(14, 35, 102, 195), outline=(181, 250, 255, 210), width=3)
        for i in range(6):
            draw.line((110 + i * 14, 63, 95 + i * 14, 111), fill=(166, 249, 241, 160), width=3)
    image.save(path)


def _title_art(path: Path) -> None:
    """Neutral dark stage keeps text treatment, position and backing legible."""
    image = Image.new("RGB", (320, 180))
    pixels = image.load()
    for y in range(180):
        mix = y / 179
        color = tuple(round(a * (1 - mix) + b * mix)
                      for a, b in zip((16, 24, 41), (33, 48, 68)))
        for x in range(320):
            pixels[x, y] = color
    draw = ImageDraw.Draw(image, "RGBA")
    draw.line((0, 142, 320, 142), fill=(255, 255, 255, 22), width=1)
    draw.rectangle((0, 0, 320, 5), fill=(106, 162, 202, 105))
    image.save(path)


def _frame(video: Path, at: float, output: Path) -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(at), "-i", str(video), "-frames:v", "1", str(output),
    ], check=True, capture_output=True)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(family: str | None = None, preset_id: str | None = None,
         *, include_approved: bool = False, preset_ids: list[str] | None = None) -> None:
    manifest = json.loads(CATALOG.read_text(encoding="utf-8"))
    catalog = PresetCatalog.builtin(default_registry())
    candidates = [spec for spec in catalog.all()
                  if spec.family in ("transition", "animation", "fx", "filter", "text", "personFx")
                  and (family is None or spec.family == family)
                  and (preset_id is None or spec.id == preset_id)
                  and (not preset_ids or spec.id in preset_ids)
                  and (spec.status == "candidate" or include_approved)]
    if not candidates:
        raise SystemExit("no supported candidates in built-in catalog")
    ASSETS.mkdir(parents=True, exist_ok=True)
    renderer = RenderService()
    entries = {entry["presetId"]: entry for entry in manifest["presets"]}
    with tempfile.TemporaryDirectory(prefix="cutvoke-preset-source-") as temporary:
        temp = Path(temporary)
        art_a, art_b = temp / "scene-a.png", temp / "scene-b.png"
        _art(art_a, 0)
        _art(art_b, 1)
        title_art = temp / "title-stage.png"
        _title_art(title_art)
        person_art = temp / "person-alpha.mov"
        if any(spec.family == "personFx" for spec in candidates):
            make_person_alpha_video(person_art, ffmpeg=renderer.ffmpeg, frames=60)
        for spec in candidates:
            slug = spec.id.removeprefix("cutvoke.preset.").replace(".", "-")
            version = spec.version.replace(".", "-")
            stem = f"{slug}-v{version}"
            video = ASSETS / f"{stem}.mp4"
            cover = ASSETS / f"{stem}.png"
            evidence = ASSETS / f"{stem}.qa.json"
            effect_stack = [dict(item) for item in spec.effects]
            if spec.family == "personFx":
                background_plain = Clip(
                    "scene-a-plain", AssetReference("generated-title", str(title_art)),
                    Rational.of(0), Rational.of(1), Rational.of(0))
                background_treated = Clip(
                    "scene-a-treated", AssetReference("generated-title", str(title_art)),
                    Rational.of(1), Rational.of(3), Rational.of(0))
                person_plain = Clip(
                    "person-plain", AssetReference("generated-person", str(person_art)),
                    Rational.of(0), Rational.of(1), Rational.of(0))
                person_treated = Clip(
                    "person-treated", AssetReference("generated-person", str(person_art)),
                    Rational.of(1), Rational.of(3), Rational.of(1), effects=effect_stack)
                clips = [background_plain, background_treated]
                person_clips = [person_plain, person_treated]
                start, duration, cover_at = 0.35, 1.3, 1.0
            elif spec.family == "transition":
                first = Clip("scene-a", AssetReference("generated-a", str(art_a)),
                             Rational.of(0), Rational.of(2), Rational.of(0))
                second = Clip("scene-b", AssetReference("generated-b", str(art_b)),
                              Rational.of(2), Rational.of(4), Rational.of(0), effects=effect_stack)
                clips = [first, second]
                start, duration = 1.45, 1.1
                cover_at = 0.78
            elif spec.family == "animation":
                clips = [Clip("scene-a", AssetReference("generated-a", str(art_a)),
                              Rational.of(0), Rational.of(2), Rational.of(0), effects=effect_stack)]
                if animation_slot(spec.effect_id) == "出场":
                    start, duration, cover_at = 0.9, 1.1, 0.78
                else:
                    start, duration, cover_at = 0.0, 1.1, 0.38
            elif spec.family == "text":
                background = Clip("title-background", AssetReference("generated-title", str(title_art)),
                                  Rational.of(0), Rational.of(2), Rational.of(0))
                title = Clip("title", AssetReference("", ""), Rational.of(0),
                             Rational.of(2), Rational.of(0), effects=effect_stack)
                clips = [background]
                start, duration, cover_at = ((0.0, 2.0, 1.35)
                                             if spec.subcategory == "文字动画"
                                             else (0.0, 1.5, 0.75))
            else:
                # Same source before and after the effect makes the preview an
                # honest comparison rather than a change in source artwork.
                plain = Clip("scene-a-plain", AssetReference("generated-a", str(art_a)),
                             Rational.of(0), Rational.of(1), Rational.of(0))
                treated = Clip("scene-a-treated", AssetReference("generated-a", str(art_a)),
                               Rational.of(1), Rational.of(3), Rational.of(0), effects=effect_stack)
                clips = [plain, treated]
                start, duration = 0.35, 1.3
                cover_at = 0.95
            # A Project is needed for preflight; EditService creates the schema-correct wrapper.
            from cutvoke.core.service import EditService
            service = EditService()
            project = service.create_project(f"preview-{slug}", width=320, height=180)
            if spec.family == "personFx":
                project.sequence.tracks = [
                    Track("video", "video", clips),
                    Track("person", "video", person_clips),
                ]
            else:
                project.sequence.tracks = [Track("video", "video", clips)]
            if spec.family == "text":
                project.sequence.tracks.append(Track("title", "text", [title]))
            result = renderer.render_preview_window(project, str(video), start=start, duration=duration)
            with tempfile.TemporaryDirectory(prefix="cutvoke-preset-frames-") as frame_temp:
                first_frame = Path(frame_temp) / "first.png"
                middle_frame = Path(frame_temp) / "middle.png"
                last_frame = Path(frame_temp) / "last.png"
                _frame(video, 0.02 if spec.family == "text" else 0.08, first_frame)
                # Show a middle state for transitions/animations and the treated
                # frame for the before/after effect preview.
                _frame(video, cover_at, cover)
                if spec.family == "text" and spec.subcategory == "文字动画":
                    # The actual full-frame title is too small in a 120 px card.
                    # Crop a real rendered frame around its title anchor for a
                    # legible cover; the playable sample remains uncropped.
                    with Image.open(cover) as title_frame:
                        closeup = title_frame.convert("RGB").crop((80, 45, 240, 135))
                        closeup.resize((320, 180), Image.Resampling.LANCZOS).save(cover)
                _frame(video, 1.0, last_frame)
                with Image.open(first_frame) as a, Image.open(last_frame) as b:
                    difference = ImageStat.Stat(ImageChops.difference(a.convert("RGB"), b.convert("RGB"))).mean
                    first_last_difference = sum(difference) / len(difference)
                mean_difference = first_last_difference
                if spec.family == "animation" and spec.subcategory == "循环":
                    # A periodic loop can return near its first state at 1 s.
                    # A middle sample catches real motion without relaxing the
                    # requirement that rendered frames actually differ.
                    _frame(video, 0.5, middle_frame)
                    with Image.open(first_frame) as a, Image.open(middle_frame) as b, \
                            Image.open(last_frame) as c:
                        pairs = (ImageChops.difference(a.convert("RGB"), b.convert("RGB")),
                                 ImageChops.difference(b.convert("RGB"), c.convert("RGB")))
                        mean_difference = max(sum(ImageStat.Stat(pair).mean) / 3 for pair in pairs)
            # Text occupies a small part of a 320×180 sample; its fade is still
            # visible locally even when whole-frame mean RGB changes little.
            minimum_difference = 0.25 if spec.family == "text" else 8
            if result["duration"] < 0.8 or mean_difference < minimum_difference:
                raise RuntimeError(f"preview motion check failed for {spec.id}: {result}, diff={mean_difference}")
            record = {
                "presetId": spec.id,
                "generator": "scripts/generate_preset_previews.py",
                "source": "generated geometric artwork; no external media",
                "renderPath": "RenderService.render_preview_window",
                "effects": effect_stack,
                "durationSeconds": result["duration"],
                "firstLastMeanRgbDifference": round(first_last_difference, 3),
                "maxSampledMeanRgbDifference": round(mean_difference, 3),
                "coverSha256": _sha(cover),
                "previewSha256": _sha(video),
                "coverCrop": "rendered frame center 160x90 enlarged 2x" if
                spec.family == "text" and spec.subcategory == "文字动画" else None,
                "checks": ["cover", "motionPreview"],
            }
            evidence.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            entry = entries[spec.id]
            relative = "../assets/preset_previews/"
            entry["cover"] = relative + cover.name
            entry["motionPreview"] = relative + video.name
            entry.setdefault("checks", {}).update({"cover": True, "motionPreview": True})
            entry.setdefault("evidence", {}).update({
                "cover": relative + evidence.name,
                "motionPreview": relative + evidence.name,
            })
            print(f"{spec.id}: {result['duration']:.2f}s, frame difference {mean_difference:.1f}")
    CATALOG.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"generated {len(candidates)} previews; visual approval was not updated")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=("transition", "animation", "fx", "filter", "text", "personFx"))
    parser.add_argument("--preset-id", help="Regenerate one candidate without rewriting other media")
    parser.add_argument("--preset-ids", nargs="+", help="Regenerate only the listed presets")
    parser.add_argument("--include-approved", action="store_true",
                        help="Rebuild existing media; visual approval must be reviewed again")
    args = parser.parse_args()
    main(args.family, args.preset_id, include_approved=args.include_approved,
         preset_ids=args.preset_ids)
