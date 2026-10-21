"""Check versioned artwork locations, provenance hashes and documentation links.

Run from any directory. Includes non-ignored new files so a local pre-commit check
sees the same additions that CI will see after checkout. Does not modify files.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
CATALOG = "docs/development/asset-relocations.json"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".ico", ".avif"}


def check(root: Path = ROOT) -> list[str]:
    raw = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root)
    files = sorted({name for name in raw.decode("utf-8").split("\0")
                    if name and (root / name).is_file()})
    errors: list[str] = []
    catalog = json.loads((root / CATALOG).read_text(encoding="utf-8"))
    locations = {entry["previousPath"]: entry for entry in catalog["entries"]}
    for entry in catalog["entries"]:
        target = (root / entry["path"]).resolve()
        if not target.is_relative_to(root.resolve()):
            errors.append(f"Provenance path escapes repository: {entry['path']}")
        elif not target.is_file() or entry["path"] not in files:
            errors.append(f"Missing versioned provenance target: {entry['path']}")
        elif "sha256" in entry and hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
            errors.append(f"Historical evidence bytes changed: {entry['path']}")

    for name in files:
        path = root / name
        parts = Path(name).parts
        if parts[0] in {".artifacts", "output", "tmp", "dist", "build", ".cutvoke", ".codegraph"} or any(
            p in {"node_modules", "__pycache__", ".pytest_cache", ".venv", "venv"} for p in parts
        ) or path.suffix.lower() in {".sqlite", ".db", ".pyc", ".log"}:
            errors.append(f"Local/generated file in repository: {name}")
        if name.startswith("docs/assets/"):
            errors.append(f"Use source, documentation or dated review directory: {name}")
        if name.startswith("resources/source/materials/") and any(
            word in path.stem.lower() for word in ("contact", "review", "rendered", "crop")):
            errors.append(f"Review/intermediate mixed with source artwork: {name}")
        if name.startswith("docs/images/") and path.suffix.lower() in IMAGE_SUFFIXES and any(
            word in path.stem.lower() for word in ("atlas", "sheet", "review", "crop")):
            errors.append(f"Production/review image mixed with documentation: {name}")
        if name.startswith("docs/history/reviews/") and path.name != "README.md" and not re.match(
            r"docs/history/reviews/\d{4}-\d{2}/", name):
            errors.append(f"Review needs a dated directory: {name}")
        if name.startswith("tools/") and path.suffix in {".py", ".mjs", ".js", ".cjs"}:
            text = path.read_text(encoding="utf-8")
            if path.name != "check_asset_layout.py" and re.search(r"docs/assets|docs[\"']\s*/\s*[\"']assets", text):
                errors.append(f"Executable reference to retired directory: {name}")
            for reference in re.findall(r'["\'](resources/source/[^"\'\n]+)["\']', text):
                if "{" not in reference and not (root / reference).exists():
                    errors.append(f"Missing source input in {name}: {reference}")
        if path.suffix != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        # Markdown image/link targets and HTML images. Ignore external URLs, code
        # examples and runtime paths; validate all local image/source/review links.
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        for reference in re.findall(r'\]\(([^\s)]+)\)|\bsrc="([^"]+)"', text):
            ref = next(v for v in reference if v)
            if re.match(r"[a-zA-Z]+:|#|/", ref):
                continue
            local = unquote(ref.split("#", 1)[0])
            target = (path.parent / local).resolve()
            if not target.is_relative_to(root.resolve()):
                continue
            relative = target.relative_to(root.resolve()).as_posix()
            if target.suffix.lower() in IMAGE_SUFFIXES or relative.startswith(
                ("resources/", "docs/images/", "docs/history/reviews/", "docs/assets/")):
                if not target.exists():
                    errors.append(f"Broken artwork link in {name}: {ref}")
                elif target.is_file() and relative not in files:
                    errors.append(f"Artwork link depends on unversioned file in {name}: {ref}")
        for old in re.findall(r"docs/assets(?:/[^\s`\"'<>;)]*)?", text):
            if old not in locations:
                errors.append(f"Unmapped historical artwork reference in {name}: {old}")
            else:
                errors.append(f"Update documentation artwork reference in {name}: {old}")
    return sorted(set(errors))


def main() -> int:
    errors = check()
    print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
