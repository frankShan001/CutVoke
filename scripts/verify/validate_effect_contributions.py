"""Validate review metadata with CutVoke's existing manifest loader; never run effect code."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cutvoke.core.effects import EffectRegistry, EffectSpec, ManifestInvalid, _builtin_specs
from cutvoke.core.effect_modules import EffectModuleLibrary


FAMILIES = {"transitions": "transition", "filters": "fx", "color": "fx", "video": "fx",
            "audio": "fx", "animations": "animation", "text": "text", "transforms": "transform"}


def validate_folder(folder: Path) -> list[str]:
    errors = []
    try:
        raw = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        spec = EffectSpec.from_manifest(raw, source=str(folder / "manifest.json"))
    except (OSError, ValueError, ManifestInvalid) as error:
        return [str(error)]
    if not re.fullmatch(r"[a-z][a-z0-9.-]*", spec.id):
        errors.append("Use a lowercase effect ID")
    if spec.category == "transition" and not re.fullmatch(r"cutvoke\.transition\.[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*", spec.id):
        errors.append("Transitions need cutvoke.transition.<contributor>.<slug> for the current editor")
    if folder.name != spec.id:
        errors.append("Folder name must equal manifest.id")
    if not re.fullmatch(r"\d+\.\d+\.\d+", spec.version):
        errors.append("version must be MAJOR.MINOR.PATCH")
    family = folder.parent.name
    if family not in FAMILIES or spec.category != FAMILIES[family]:
        errors.append("Storage family and engine category disagree; consult effects/README.md")
    if not isinstance(raw.get("author"), dict) or not raw["author"].get("name"):
        errors.append("author.name is required for review")
    if not spec.license:
        errors.append("A declared license is required")
    for locale in ("en", "zh-CN"):
        if not spec.name.get(locale) or not spec.description.get(locale):
            errors.append(f"name and description need {locale}")
    if not spec.compat_min_core:
        errors.append("Declare x-cutvoke-min-core; the loader treats it as metadata")
    metadata = raw.get("x-cutvoke-contribution", {})
    if not isinstance(metadata, dict) or not metadata.get("testedWith"):
        errors.append("x-cutvoke-contribution.testedWith is required")
    preview_name = metadata.get("previewFile", "") if isinstance(metadata, dict) else ""
    if not isinstance(preview_name, str) or not preview_name:
        errors.append("Declare a local previewFile")
    else:
        preview = (folder / preview_name).resolve()
        if not preview.is_relative_to(folder.resolve()) or not preview.is_file():
            errors.append("previewFile must exist inside the effect folder")
        elif preview.stat().st_size > 2 * 1024 * 1024:
            errors.append("previewFile must be at most 2 MiB")
        elif preview.suffix.lower() != ".png" or not preview.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            errors.append("previewFile must be an actual PNG")
    if not (folder / "README.md").is_file() or not (folder / "LICENSE").is_file():
        errors.append("Include README.md (use, provenance, test recipe) and LICENSE")
    try:
        registry = EffectRegistry()
        registry.register(spec)
        registry.validate_params(spec.id, {})
    except Exception as error:
        errors.append(f"Registration/default parameters failed: {error}")
    if spec.engine == "ffmpeg-xfade":
        known = {s.xfade_transition for s in _builtin_specs() if s.engine == "ffmpeg-xfade"}
        if spec.category != "transition" or spec.implementation.get("filter") != "xfade" or spec.xfade_transition not in known:
            errors.append("This validator accepts existing xfade mappings; new algorithms require a reviewed core change")
    elif spec.engine == "internal":
        known = [s.implementation for s in _builtin_specs() if s.category == spec.category]
        if spec.implementation not in known:
            errors.append("Unknown internal implementation; add and test a core renderer before claiming support")
    else:
        errors.append("No dynamic-code engine is supported by this contribution convention")
    return errors


def validate_tree(root: Path) -> dict:
    manifests = sorted(root.glob("*/*/manifest.json"))
    recipes = sorted((root / "recipes").iterdir()) if (root / "recipes").is_dir() else []
    failures = {}
    seen = set()
    for manifest in manifests:
        errors = validate_folder(manifest.parent)
        try:
            effect_id = json.loads(manifest.read_text(encoding="utf-8"))["id"]
            if effect_id in seen:
                errors.append("Duplicate effect ID across contribution families")
            seen.add(effect_id)
        except (KeyError, ValueError, OSError):
            pass
        if errors:
            failures[str(manifest.relative_to(root))] = errors
    library = EffectModuleLibrary(root / ".unused-library")
    for recipe in recipes:
        try:
            library.validate_contribution(recipe)
        except (ValueError, OSError) as error:
            failures[str(recipe.relative_to(root))] = [str(error)]
    return {"ok": bool(manifests or recipes) and not failures,
            "checked": len(manifests) + len(recipes), "errors": failures}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "effects")
    args = parser.parse_args()
    result = validate_tree(args.root.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
