"""Package the approved CutVoke artwork for desktop, Web and repository docs."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
MASTER = ROOT / "resources/source/brand/cutvoke-icon-master.png"
WEB = ROOT / "apps/editor/public/brand"
DESKTOP = ROOT / "apps/desktop/assets/brand"


def outputs():
    with Image.open(MASTER) as image:
        source = image.convert("RGBA")
    if source.width != source.height or source.width < 1024:
        raise ValueError("The approved master must be a square PNG of at least 1024px")
    if source.getextrema()[3][0] != 0:
        raise ValueError("Preserve the approved artwork's transparent outer corners")
    result = {}
    for size in (32, 64, 128, 180, 192, 512):
        buffer = io.BytesIO()
        source.resize((size, size), Image.Resampling.LANCZOS).save(buffer, "PNG", optimize=True)
        result[WEB / f"icon-{size}.png"] = buffer.getvalue()
    buffer = io.BytesIO()
    source.resize((256, 256), Image.Resampling.LANCZOS).save(buffer, "PNG", optimize=True)
    result[DESKTOP / "icon-256.png"] = buffer.getvalue()
    buffer = io.BytesIO()
    source.save(buffer, "ICO", sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
    result[DESKTOP / "cutvoke.ico"] = buffer.getvalue()
    result[WEB / "favicon.ico"] = buffer.getvalue()
    buffer = io.BytesIO()
    source.resize((1024, 1024), Image.Resampling.LANCZOS).save(buffer, "ICNS")
    result[DESKTOP / "cutvoke.icns"] = buffer.getvalue()
    banner = ROOT / "docs/images/readme/banner.svg"
    embedded = base64.b64encode(result[WEB / "icon-128.png"]).decode("ascii")
    mark = ('<!-- CUTVOKE_BRAND_START -->\n'
            f'    <image x="45" y="63" width="84" height="84" href="data:image/png;base64,{embedded}"/>\n'
            '    <!-- CUTVOKE_BRAND_END -->')
    text = banner.read_text(encoding="utf-8")
    updated, count = re.subn(r'<!-- CUTVOKE_BRAND_START -->.*?<!-- CUTVOKE_BRAND_END -->', mark, text, flags=re.S)
    if count != 1:
        raise ValueError("README banner must have exactly one marked brand image")
    result[banner] = updated.encode("utf-8")
    manifest = {
        "name": "CutVoke CV icon", "approvedOn": "2026-10-21",
        "master": MASTER.relative_to(ROOT).as_posix(),
        "masterSha256": hashlib.sha256(MASTER.read_bytes()).hexdigest(),
        "masterSize": [source.width, source.height],
        "generatedBy": "tools/assets/build_brand_icons.py",
        "files": [{"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(data).hexdigest()}
                  for path, data in result.items()],
    }
    result[ROOT / "resources/source/brand/manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify generated files without writing")
    args = parser.parse_args()
    assets = outputs()
    if args.check:
        stale = [str(path.relative_to(ROOT)) for path, data in assets.items()
                 if not path.is_file() or path.read_bytes() != data]
        if stale:
            raise SystemExit("Regenerate brand assets: " + ", ".join(stale))
        print(f"Verified {len(assets)} brand assets against the approved master")
    else:
        for path, data in assets.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        print(f"Generated {len(assets)} brand assets")


if __name__ == "__main__":
    main()
