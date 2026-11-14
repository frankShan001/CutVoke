"""Runtime resource guard tests; use tiny static fixtures, not installed assets."""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from cutvoke.rendering import caption_render as c


@pytest.fixture
def resource(tmp_path, monkeypatch):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    source = fonts / "NotoSansSC-VF.ttf"
    source.write_bytes(b"source")
    directory = tmp_path / "caption_fonts"
    directory.mkdir()
    data = b"fixture-font"
    (directory / "sans-400-v1.ttf").write_bytes(data)
    (directory / "OFL-Sans.txt").write_bytes(b"license")
    digest = hashlib.sha256(b"source").hexdigest()
    manifest = {"recipe": 1, "source_sha256": digest,
                "license_sha256": hashlib.sha256(b"license").hexdigest(),
                "instances": {"400": {"filename": "sans-400-v1.ttf",
                                     "sha256": hashlib.sha256(data).hexdigest()}}}
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(c, "_PACKAGE_FONTS_DIR", str(fonts))
    monkeypatch.delenv("CUTVOKE_FONT", raising=False)
    return source, directory, digest, manifest


def test_custom_override_never_uses_packaged(resource, monkeypatch):
    source, _, digest, _ = resource
    monkeypatch.setenv("CUTVOKE_FONT", str(source))
    assert c._packaged_caption_font(str(source), digest, 400, "alias") is None


@pytest.mark.parametrize("failure", ["missing", "bad-json", "wrong-source", "corrupt-font",
                                     "corrupt-license", "traversal", "wrong-weight", "invalid-font"])
def test_invalid_resource_falls_back(resource, failure):
    source, directory, digest, manifest = resource
    if failure == "missing":
        (directory / "manifest.json").unlink()
    elif failure == "bad-json":
        (directory / "manifest.json").write_text("{", encoding="utf-8")
    elif failure == "wrong-source":
        digest = "other"
    elif failure == "corrupt-font":
        (directory / "sans-400-v1.ttf").write_bytes(b"bad")
    elif failure == "corrupt-license":
        (directory / "OFL-Sans.txt").write_bytes(b"bad")
    elif failure == "traversal":
        manifest["instances"]["400"]["filename"] = "../outside.ttf"
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    elif failure == "wrong-weight":
        manifest["instances"] = {}
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # invalid-font has a correct digest but malformed font bytes.
    assert c._packaged_caption_font(str(source), digest, 400, "alias") is None


def test_system_source_not_substituted(resource, tmp_path):
    _, _, digest, _ = resource
    other = tmp_path / "system.ttf"
    other.write_bytes(b"source")
    assert c._packaged_caption_font(str(other), digest, 400, "alias") is None


def test_packaged_bytes_published_atomically(tmp_path, monkeypatch):
    source = tmp_path / "source.ttf"
    source.write_bytes(b"source")
    monkeypatch.setattr(c, "resolve_title_font", lambda *a, **k: str(source))
    monkeypatch.setenv("CUTVOKE_FONT_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(c, "_packaged_caption_font", lambda *a: b"complete")
    monkeypatch.setattr(c, "_css_font_size_to_ass", lambda *a: 32)
    with patch("fontTools.varLib.instancer.instantiateVariableFont") as instantiate:
        _, path = c._caption_font_instance("Noto Sans SC", False)
    assert Path(path).read_bytes() == b"complete"
    assert not list(Path(path).parent.glob(".caption-*"))
    instantiate.assert_not_called()


def test_failed_publish_cleans_temporary(tmp_path, monkeypatch):
    source = tmp_path / "source.ttf"
    source.write_bytes(b"source")
    monkeypatch.setattr(c, "resolve_title_font", lambda *a, **k: str(source))
    monkeypatch.setenv("CUTVOKE_FONT_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(c, "_packaged_caption_font", lambda *a: b"complete")
    monkeypatch.setattr(c, "_css_font_size_to_ass", lambda *a: 32)
    with patch.object(c.os, "replace", side_effect=OSError("controlled failure")):
        with pytest.raises(c.CaptionFontError):
            c._caption_font_instance("Noto Sans SC", False)
    assert not list((tmp_path / "cache").iterdir())


def test_semantically_invalid_packaged_font_uses_original_path(tmp_path, monkeypatch):
    source = tmp_path / "source.ttf"
    source.write_bytes(b"source")
    monkeypatch.setattr(c, "resolve_title_font", lambda *a, **k: str(source))
    monkeypatch.setenv("CUTVOKE_FONT_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(c, "_packaged_caption_font", lambda *a: b"complete")
    monkeypatch.setattr(c, "_css_font_size_to_ass", lambda *a: (_ for _ in ()).throw(ValueError("bad metrics")))
    with patch("fontTools.ttLib.TTFont", side_effect=ValueError("original path reached")) as original:
        with pytest.raises(c.CaptionFontError, match="original path reached"):
            c._caption_font_instance("Noto Sans SC", False)
    original.assert_called_once_with(str(source), fontNumber=0)
    assert not list((tmp_path / "cache").iterdir())


def test_distinct_verified_resource_bytes_use_distinct_cache_keys(tmp_path, monkeypatch):
    source = tmp_path / "source.ttf"
    source.write_bytes(b"source")
    monkeypatch.setattr(c, "resolve_title_font", lambda *a, **k: str(source))
    monkeypatch.setenv("CUTVOKE_FONT_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(c, "_css_font_size_to_ass", lambda *a: 32)
    paths = []
    for data in (b"version-a", b"version-b"):
        monkeypatch.setattr(c, "_packaged_caption_font", lambda *a: data)
        _, path = c._caption_font_instance("Noto Sans SC", False)
        assert Path(path).read_bytes() == data
        paths.append(path)
    assert paths[0] != paths[1]
    assert all("-v2-" in path for path in paths)


def tiny_static_font(alias):
    import io
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    font = FontBuilder(1000, isTTF=True)
    font.setupGlyphOrder([".notdef"])
    font.setupCharacterMap({})
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0)); pen.lineTo((100, 0)); pen.lineTo((100, 100)); pen.closePath()
    font.setupGlyf({".notdef": pen.glyph()})
    font.setupHorizontalMetrics({".notdef": (500, 0)})
    font.setupHorizontalHeader(ascent=800, descent=-200)
    font.setupNameTable({"familyName": alias, "styleName": "Regular"})
    font.setupOS2(usWeightClass=400)
    font.setupPost()
    font.setupMaxp()
    result = io.BytesIO()
    font.save(result)
    return result.getvalue()


@pytest.mark.parametrize("damage", ["truncate", "parseable-metrics", "unchanged"])
def test_warm_packaged_cache_integrity_repaired(tmp_path, monkeypatch, damage):
    from fontTools.ttLib import TTFont
    source = tmp_path / "source.ttf"
    source.write_bytes(b"source")
    digest = hashlib.sha256(b"source").hexdigest()
    data = tiny_static_font(f"CutVoke Caption {digest[:12]}")
    monkeypatch.setattr(c, "resolve_title_font", lambda *a, **k: str(source))
    monkeypatch.setenv("CUTVOKE_FONT_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(c, "_packaged_caption_font", lambda *a: data)
    monkeypatch.setattr(c, "_css_font_size_to_ass", lambda *a: 32)
    _, path = c._caption_font_instance("Noto Sans SC", False)
    if damage == "truncate":
        Path(path).write_bytes(data[:12])
    elif damage == "parseable-metrics":
        with TTFont(path) as font:
            font["hmtx"][".notdef"] = (900, 0)
            font.save(path)
        with TTFont(path) as font:
            assert font["hmtx"][".notdef"][0] == 900
            assert font["name"].getDebugName(1) == f"CutVoke Caption {digest[:12]}"
    with patch.object(c.os, "replace", wraps=c.os.replace) as replace:
        with patch("fontTools.varLib.instancer.instantiateVariableFont",
                   side_effect=RuntimeError("must not instantiate")):
            _, repeated = c._caption_font_instance("Noto Sans SC", False)
    assert repeated == path
    assert Path(path).read_bytes() == data
    assert replace.call_count == (0 if damage == "unchanged" else 1)
    assert not list(Path(path).parent.glob(".caption-*"))
