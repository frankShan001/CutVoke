"""Require real complete packaged fonts and prove cold runtime uses them."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch


def verify(require_installed=False):
    import cutvoke
    from cutvoke.rendering import caption_render as c
    package = Path(cutvoke.__file__).resolve().parent
    checkout = Path(__file__).resolve().parents[2] / "src"
    if require_installed and package.is_relative_to(checkout):
        raise RuntimeError("installed-wheel verification imported checkout")
    directory = package / "assets/caption_fonts"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest["recipe"] != 1:
        raise RuntimeError("unsupported resource recipe")
    license_data = (directory / "OFL-Sans.txt").read_bytes()
    if hashlib.sha256(license_data).hexdigest() != manifest["license_sha256"]:
        raise RuntimeError("resource license mismatch")
    if license_data != (package / "assets/fonts/OFL-Sans.txt").read_bytes():
        raise RuntimeError("resource license differs from original")
    source = package / "assets/fonts/NotoSansSC-VF.ttf"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != manifest["source_sha256"]:
        raise RuntimeError("resource source mismatch")
    results = []
    with tempfile.TemporaryDirectory(prefix="cutvoke-font-verification-") as cache:
        environment = dict(os.environ)
        environment.pop("CUTVOKE_FONT", None)
        environment["CUTVOKE_FONT_CACHE"] = cache
        with patch.dict(os.environ, environment, clear=True):
            # Reject false success from slow fallback: actual resources must work.
            with patch("fontTools.varLib.instancer.instantiateVariableFont",
                       side_effect=RuntimeError("unexpected runtime font conversion")):
                for weight in (400, 700):
                    expected = (directory / f"sans-{weight}-v1.ttf").read_bytes()
                    if hashlib.sha256(expected).hexdigest() != manifest["instances"][str(weight)]["sha256"]:
                        raise RuntimeError("resource hash mismatch")
                    start = time.perf_counter()
                    alias, path = c._caption_font_instance("Noto Sans SC", weight == 700)
                    elapsed = time.perf_counter() - start
                    if not Path(path).resolve().is_relative_to(Path(cache).resolve()):
                        raise RuntimeError("runtime escaped isolated cache")
                    if Path(path).read_bytes() != expected:
                        raise RuntimeError("runtime output differs from packaged font")
                    results.append({"weight": weight, "seconds": elapsed, "alias": alias,
                                    "sha256": hashlib.sha256(expected).hexdigest()})
    return {"ok": True, "package": str(package), "installedRequired": require_installed,
            "runtimeConversionForbidden": True, "fonts": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-installed", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify(args.require_installed), indent=2))
