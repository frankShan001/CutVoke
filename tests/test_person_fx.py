"""Person FX must affect transparent cutouts and reject opaque media."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat

from scripts.person_fx_preview_source import make_person_alpha_video
from cutvoke.core.effects import default_registry
from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderError, RenderService
from cutvoke.core.service import EditService


PERSON_HALO = {
    "effectId": "cutvoke.person.halo",
    "version": "1.1.0",
    "params": {"sigma": 22, "hueShift": 165, "saturation": 1.8,
               "opacity": 1.0},
}


def _project(background: Path, person: Path, effects: list[dict]):
    project = EditService().create_project("person-fx-test", width=320, height=180,
                                           fps=Rational.of(30))
    project.sequence.tracks = [
        Track("background", "video", [Clip(
            "background", AssetReference("background", str(background)),
            Rational.of(0), Rational.of(2), Rational.of(0),
        )]),
        Track("person", "video", [Clip(
            "person", AssetReference("person", str(person)),
            Rational.of(0), Rational.of(2), Rational.of(0), effects=effects,
        )]),
    ]
    return project


class PersonFxTests(unittest.TestCase):
    def test_person_operators_are_registered_in_renderable_fx_category(self) -> None:
        registry = default_registry()
        person_ids = ("cutvoke.person.halo", "cutvoke.person.echo", "cutvoke.person.tint")
        for effect_id in person_ids:
            with self.subTest(effect_id=effect_id):
                spec = registry.find(effect_id)
                self.assertIsNotNone(spec)
                assert spec is not None
                self.assertEqual(spec.category, "fx")
                self.assertIn("person-cutout-alpha", spec.dependencies)

        clip = Clip("person", AssetReference("person", "alpha.mov"),
                    Rational.of(0), Rational.of(1), Rational.of(0), effects=[
                        {"effectId": effect_id, "params": {}}
                        for effect_id in person_ids
                    ])
        self.assertEqual(RenderService._enabled_fx(clip), clip.effects)

    def test_person_fx_changes_the_rendered_transparent_person_layer(self) -> None:
        renderer = RenderService()
        with tempfile.TemporaryDirectory(prefix="cutvoke-person-fx-test-") as temporary:
            root = Path(temporary)
            background = root / "dark-stage.png"
            Image.new("RGB", (320, 180), (10, 14, 22)).save(background)
            person = root / "person-alpha.mov"
            make_person_alpha_video(person, ffmpeg=renderer.ffmpeg, frames=60)
            effected = _project(background, person, [copy.deepcopy(PERSON_HALO)])
            control = _project(background, person, [])

            self.assertEqual(renderer._preflight(effected, str(root / "check.mp4"), True), [])
            effected_frame, plain_frame = root / "effect.png", root / "plain.png"
            renderer.extract_frame(effected, 0.5, str(effected_frame))
            renderer.extract_frame(control, 0.5, str(plain_frame))
            with Image.open(effected_frame) as rendered, Image.open(plain_frame) as plain:
                difference = ImageChops.difference(rendered.convert("RGB"),
                                                   plain.convert("RGB"))
                changed = sum(max(pixel) > 8 for pixel in difference.get_flattened_data())
                mean_difference = sum(ImageStat.Stat(difference).mean) / 3
            self.assertGreater(changed, 500)
            self.assertGreater(mean_difference, 0.5)

    def test_person_halo_has_a_visible_colored_ring_outside_the_cutout(self) -> None:
        renderer = RenderService()
        background_color = (10, 14, 22)
        with tempfile.TemporaryDirectory(prefix="cutvoke-person-halo-ring-") as temporary:
            root = Path(temporary)
            background = root / "dark-stage.png"
            Image.new("RGB", (320, 180), background_color).save(background)
            person = root / "person-alpha.mov"
            make_person_alpha_video(person, ffmpeg=renderer.ffmpeg, frames=60)
            effected_frame, plain_frame = root / "effect.png", root / "plain.png"
            renderer.extract_frame(_project(background, person, [copy.deepcopy(PERSON_HALO)]),
                                   0.5, str(effected_frame))
            renderer.extract_frame(_project(background, person, []), 0.5, str(plain_frame))

            with Image.open(effected_frame) as effected, Image.open(plain_frame) as plain:
                effected_rgb, plain_rgb = effected.convert("RGB"), plain.convert("RGB")
                stage = Image.new("RGB", plain_rgb.size, background_color)
                silhouette = ImageChops.difference(plain_rgb, stage).convert("L")
                silhouette = silhouette.point(lambda value: 255 if value > 12 else 0)
                dilated = silhouette.filter(ImageFilter.MaxFilter(57))
                outside_person = ImageChops.subtract(dilated, silhouette)
                changed = ImageChops.difference(effected_rgb, plain_rgb).convert("L")
                visible_ring = ImageChops.multiply(
                    outside_person, changed.point(lambda value: 255 if value > 16 else 0))

            ring_pixels = sum(visible_ring.histogram()[1:])
            self.assertGreater(ring_pixels, 1000)

    def test_person_fx_survives_an_adjacent_transparent_clip_boundary(self) -> None:
        renderer = RenderService()
        with tempfile.TemporaryDirectory(prefix="cutvoke-person-fx-boundary-") as temporary:
            root = Path(temporary)
            background = root / "dark-stage.png"
            Image.new("RGB", (320, 180), (10, 14, 22)).save(background)
            person = root / "person-alpha.mov"
            make_person_alpha_video(person, ffmpeg=renderer.ffmpeg, frames=60)

            def project(with_effect: bool, *, include_before: bool = True):
                result = EditService().create_project(
                    "person-fx-boundary", width=320, height=180, fps=Rational.of(30))
                person_clips = []
                if include_before:
                    person_clips.append(Clip(
                        "person-before", AssetReference("person", str(person)),
                        Rational.of(0), Rational.of(1), Rational.of(0)))
                person_clips.append(Clip(
                    "person-after", AssetReference("person", str(person)),
                    Rational.of(1), Rational.of(2), Rational.of(0),
                    effects=[copy.deepcopy(PERSON_HALO)] if with_effect else []))
                result.sequence.tracks = [
                    Track("background", "video", [Clip(
                        "background", AssetReference("background", str(background)),
                        Rational.of(0), Rational.of(2), Rational.of(0),
                    )]),
                    Track("person", "video", person_clips),
                ]
                return result

            effected_before = root / "effect-before.png"
            plain_before = root / "plain-before.png"
            effected_after = root / "effect-after.png"
            plain_after = root / "plain-after.png"
            single_after = root / "single-after.png"
            renderer.extract_frame(project(True), 0.5, str(effected_before))
            renderer.extract_frame(project(False), 0.5, str(plain_before))
            renderer.extract_frame(project(True), 1.5, str(effected_after))
            renderer.extract_frame(project(False), 1.5, str(plain_after))
            renderer.extract_frame(
                project(False, include_before=False), 1.5, str(single_after))

            with Image.open(effected_before) as effected, Image.open(plain_before) as plain:
                before = ImageChops.difference(effected.convert("RGB"), plain.convert("RGB"))
                self.assertLess(sum(ImageStat.Stat(before).mean) / 3, 0.1)
            with Image.open(effected_after) as effected, Image.open(plain_after) as plain:
                after = ImageChops.difference(effected.convert("RGB"), plain.convert("RGB"))
                changed = sum(max(pixel) > 8 for pixel in after.get_flattened_data())
                mean_difference = sum(ImageStat.Stat(after).mean) / 3
            with Image.open(plain_after) as split, Image.open(single_after) as single:
                expired_clip = ImageChops.difference(split.convert("RGB"), single.convert("RGB"))
                self.assertLess(sum(ImageStat.Stat(expired_clip).mean) / 3, 0.1)
            self.assertGreater(changed, 500)
            self.assertGreater(mean_difference, 0.5)

    def test_preflight_rejects_person_fx_on_opaque_source(self) -> None:
        renderer = RenderService()
        with tempfile.TemporaryDirectory(prefix="cutvoke-person-fx-opaque-") as temporary:
            root = Path(temporary)
            opaque = root / "opaque.png"
            Image.new("RGB", (320, 180), (60, 70, 80)).save(opaque)
            project = _project(opaque, opaque, [copy.deepcopy(PERSON_HALO)])
            with self.assertRaisesRegex(RenderError, "PERSON_FX_REQUIRES_ALPHA"):
                renderer._preflight(project, str(root / "rejected.mp4"), True)


if __name__ == "__main__":
    unittest.main()
