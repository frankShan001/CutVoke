from __future__ import annotations

import unittest

from scripts.effect_coverage import coverage
from cutvoke.core.preset_catalog import FAMILIES


class TestFirstBatchEffectCoverage(unittest.TestCase):
    def test_reviewed_effect_and_sticker_overlays_close_the_catalog_coverage_gap(self) -> None:
        report = coverage()
        self.assertEqual(
            set(FAMILIES), {"fx", "transition", "animation", "text", "filter", "sticker", "personFx"},
        )
        self.assertEqual(report["minimumFirstBatch"], 144)
        self.assertEqual(report["gapAcrossOpenAndRequiredSubcategories"], 0)
        self.assertEqual(report["stickerCandidates"], 0)
        self.assertEqual(report["qualifiedStickers"], 612)
        self.assertEqual(report["qualifiedContent"], 801)
        self.assertGreaterEqual(report["qualifiedContent"], 144)

        for family in FAMILIES:
            rows = [row for row in report["rows"] if row["family"] == family]
            self.assertGreaterEqual(len(rows), 3, f"{family} needs at least three subcategories")
            for row in rows:
                self.assertGreaterEqual(
                    row["qualified"], 8,
                    f"{family}/{row['subcategory']} has fewer than eight qualified items",
                )
        person_fx = [row for row in report["rows"] if row["family"] == "personFx"]
        self.assertEqual(len(person_fx), 3)
        self.assertTrue(all(row["qualified"] == 8 and row["candidate"] == 0
                            for row in person_fx),
                        "all reviewed person FX presets must be qualified")
        overlays = [row for row in report["rows"]
                    if row["family"] == "sticker" and row["subcategory"] == "特效贴图"]
        self.assertEqual(len(overlays), 1)
        self.assertEqual((overlays[0]["qualified"], overlays[0]["candidate"]), (95, 0))
        product_promo = [row for row in report["rows"]
                         if row["family"] == "sticker" and row["subcategory"] == "产品广告"]
        self.assertEqual(len(product_promo), 1)
        self.assertEqual((product_promo[0]["qualified"], product_promo[0]["candidate"]), (16, 0))
        movie_overlays = [row for row in report["rows"]
                          if row["family"] == "sticker" and row["subcategory"] == "电影叠加"]
        self.assertEqual(len(movie_overlays), 1)
        self.assertEqual((movie_overlays[0]["qualified"], movie_overlays[0]["candidate"]),
                         (30, 0))


if __name__ == "__main__":
    unittest.main()
