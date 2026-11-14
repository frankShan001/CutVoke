"""Generate complete caption instances; no user-text subsets or network access."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

SOURCE_SHA = "d68bafcb48a2707749396aa12bbbd833cb70401f3a9a689fd2902c7e0d295964"
VERSION = "4.66.1"


def prepare(root=None):
    import fontTools
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
    if fontTools.__version__ != VERSION:
        raise RuntimeError(f"caption font build requires fontTools {VERSION}")
    root = Path(root or Path(__file__).resolve().parents[2])
    assets = root / "src/cutvoke/assets"
    source = assets / "fonts/NotoSansSC-VF.ttf"
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA:
        raise RuntimeError("unexpected bundled caption source; review regeneration recipe")
    license_file = assets / "fonts/OFL-Sans.txt"
    license_data = license_file.read_bytes()
    output = assets / "caption_fonts"
    output.mkdir(parents=True, exist_ok=True)
    alias = f"CutVoke Caption {SOURCE_SHA[:12]}"
    entries = {}
    for weight in (400, 700):
        with TTFont(source, fontNumber=0, recalcTimestamp=False) as font:
            axes = {axis.axisTag: axis.defaultValue for axis in font["fvar"].axes}
            axes["wght"] = weight
            instantiateVariableFont(font, axes, inplace=True, optimize=False, static=True)
            if "fvar" in font:
                raise RuntimeError("incomplete instance")
            bold = weight == 700
            font["OS/2"].usWeightClass = weight
            font["OS/2"].fsSelection = ((font["OS/2"].fsSelection & ~(0x20 | 0x40)) | (0x20 if bold else 0x40))
            font["head"].macStyle = (font["head"].macStyle & ~1) | int(bold)
            style = "Bold" if bold else "Regular"
            names = {1: alias, 16: alias, 2: style, 17: style,
                     4: f"{alias} {style}", 6: f"{alias.replace(' ', '')}-{style}"}
            locales = {(r.platformID, r.platEncID, r.langID)
                       for r in font["name"].names if r.nameID in names}
            locales.add((3, 1, 0x409))
            for name_id, value in names.items():
                for platform, encoding, language in locales:
                    font["name"].setName(value, name_id, platform, encoding, language)
            filename = f"sans-{weight}-v1.ttf"
            with tempfile.TemporaryDirectory(dir=output) as temporary:
                path = Path(temporary) / filename
                font.save(path)
                entries[str(weight)] = {"filename": filename, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                os.replace(path, output / filename)
    shutil.copyfile(license_file, output / "OFL-Sans.txt")
    manifest = {"recipe": 1, "source_sha256": SOURCE_SHA, "fonttools": VERSION,
                "license_sha256": hashlib.sha256(license_data).hexdigest(), "instances": entries}
    # Publish manifest last; partial output fails build and runtime verification.
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(prepare(), sort_keys=True))
