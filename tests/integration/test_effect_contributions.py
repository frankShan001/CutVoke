"""Review metadata guardrails and real manifest discovery, without user data."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from cutvoke.rendering.effects import EffectRegistry, EffectParamInvalid, ManifestInvalid
from tools.verify.validate_effect_contributions import validate_folder, validate_tree

SAMPLE = Path(__file__).resolve().parents[2] / "effects/transitions/cutvoke.transition.community.soft-reveal"


class ContributionTests(unittest.TestCase):
    def test_discovery_and_duration_bounds(self):
        registry = EffectRegistry()
        loaded = registry.discover([SAMPLE.parent], strict=True)
        self.assertIn("cutvoke.transition.community.soft-reveal", loaded)
        sample_id = "cutvoke.transition.community.soft-reveal"
        self.assertEqual(registry.validate_params(sample_id, {}), {"duration": 0.6})
        self.assertEqual(registry.get(sample_id).xfade_transition, "fade")
        for params in ({"duration": -1}, {"duration": 2}, {"invented": 1}):
            with self.assertRaises(EffectParamInvalid):
                registry.validate_params(sample_id, params)
        with self.assertRaises(ManifestInvalid):
            registry.discover([SAMPLE.parent], strict=True)

    def test_complete_sample(self):
        self.assertEqual(validate_folder(SAMPLE), [])
        self.assertTrue(validate_tree(SAMPLE.parents[1])["ok"])

    def test_interface_locale_metadata_and_fallback(self):
        registry = EffectRegistry()
        registry.discover([SAMPLE.parent], strict=True)
        effect = registry.get("cutvoke.transition.community.soft-reveal")
        for locale in ("zh-CN", "zh-TW", "en", "ja", "ko", "es", "fr", "de", "pt-BR", "ru", "ar"):
            with self.subTest(locale=locale):
                payload = effect.to_dict(locale)
                self.assertEqual(payload["name"], effect.name[locale])
                self.assertEqual(payload["description"], effect.description[locale])
                self.assertEqual(payload["effectId"], effect.id)
                self.assertEqual(payload["parameters"], effect.parameters)
                if locale not in ("en",):
                    self.assertNotEqual(payload["name"], effect.name["en"])
        self.assertEqual(effect.to_dict("unsupported")["name"], effect.name["en"])
        self.assertEqual(effect.to_dict("unsupported")["description"], effect.description["en"])

    def check_mutation(self, mutate, needle):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "transitions" / SAMPLE.name
            shutil.copytree(SAMPLE, folder)
            manifest = folder / "manifest.json"
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            mutate(raw)
            manifest.write_text(json.dumps(raw), encoding="utf-8")
            self.assertTrue(any(needle in error for error in validate_folder(folder)))

    def test_preview_path_cannot_escape(self):
        self.check_mutation(lambda raw: raw["x-cutvoke-contribution"].update(previewFile="../../outside.png"), "inside")

    def test_dynamic_code_engine_rejected(self):
        self.check_mutation(lambda raw: raw.update(implementation={"engine": "python", "module": "arbitrary"}), "dynamic-code")

    def test_author_and_license_required(self):
        self.check_mutation(lambda raw: raw.update(author={}, license=""), "author.name")


if __name__ == "__main__":
    unittest.main()
