"""CSS/ASS size conversion follows the selected file, including overrides."""
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cutvoke.core.caption_render import CaptionFontError, build_ass
from cutvoke.core.model import Caption
from cutvoke.core.rational import Rational
from cutvoke.core.httpapi import HttpApi
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService


def metric_font(path: Path, ascent: int, descent: int) -> None:
    head = bytearray(20)
    struct.pack_into(">H", head, 18, 1000)
    os2 = bytearray(78)
    struct.pack_into(">HH", os2, 74, ascent, descent)
    offset = 12 + 32
    path.write_bytes(struct.pack(">IHHHH", 0x10000, 2, 0, 0, 0)
                     + struct.pack(">4sIII", b"head", 0, offset, len(head))
                     + struct.pack(">4sIII", b"OS/2", 0, offset + len(head), len(os2))
                     + head + os2)


class CaptionFontMetricsTests(unittest.TestCase):
    def test_font_endpoint_whitelists_only_export_font_files(self):
        api = HttpApi(EditService(), RenderService())
        try:
            for name in ("NotoSansSC-VF.ttf", "NotoSerifSC-VF.ttf"):
                status, payload = api.handle("GET", f"/api/v1/fonts/{name}", {})
                self.assertEqual(status, 200)
                self.assertEqual(Path(payload["__media_path__"]).name, name)
                self.assertEqual(payload["__media_type__"], "font/ttf")
            for name in ("unknown.ttf", "%2E%2E%2Fcore%2Fservice.py"):
                status, _payload = api.handle("GET", f"/api/v1/fonts/{name}", {})
                self.assertEqual(status, 404)
        finally:
            api.close()

    def test_real_selected_font_changes_ass_size_and_keeps_outline_radius(self):
        caption = Caption("font", "Actual font metrics", Rational.of(0), Rational.of(1),
                          fontSize=32, strokeWidth=2)
        with tempfile.TemporaryDirectory() as directory:
            font = Path(directory) / "custom.ttf"
            for ascent, descent, expected in [(800, 200, 32.0), (1100, 300, 44.8)]:
                with self.subTest(ascent=ascent):
                    metric_font(font, ascent, descent)
                    with patch.dict(os.environ, {"CUTVOKE_FONT": str(font)}):
                        ass = build_ass([caption], 1920, 1080, css_caption_ids={caption.id})
                    style = next(line for line in ass.splitlines() if line.startswith("Style: C0,"))
                    fields = style.removeprefix("Style: ").split(",")
                    self.assertAlmostEqual(float(fields[2]), expected)
                    self.assertEqual(float(fields[16]), 1.0)
                    self.assertEqual(float(fields[17]), 1.0)

    def test_unreadable_metrics_raise_diagnosable_font_error(self):
        caption = Caption("font", "Bad font", Rational.of(0), Rational.of(1))
        with tempfile.TemporaryDirectory() as directory:
            font = Path(directory) / "bad.ttf"
            font.write_bytes(b"not a font")
            with patch.dict(os.environ, {"CUTVOKE_FONT": str(font)}):
                with self.assertRaisesRegex(CaptionFontError, "字体度量"):
                    build_ass([caption], 1920, 1080, css_caption_ids={caption.id})

    def test_mixed_title_and_caption_keep_separate_metric_semantics(self):
        caption = Caption("subtitle", "字幕", Rational.of(0), Rational.of(1), fontSize=32)
        title = Caption("title", "标题", Rational.of(0), Rational.of(1), fontSize=32)
        ass = build_ass([caption, title], 1920, 1080, css_caption_ids={caption.id})
        styles = [line.removeprefix("Style: ").split(",") for line in ass.splitlines()
                  if line.startswith("Style: C")]
        self.assertEqual(len(styles), 2)
        self.assertEqual([float(style[2]) for style in styles], [46.336, 32.0])
        self.assertEqual([float(style[16]) for style in styles], [1.0, 2.0])
