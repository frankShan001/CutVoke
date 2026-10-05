"""Generate or verify the deterministic inventory for bundled resource media."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
import zipfile
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO / "src" / "cutvoke"
MANIFEST_PATH = PACKAGE_ROOT / "core" / "builtin_resource_pack.json"
PACK_ID = "cutvoke.builtin-resources"
PACK_VERSION = "1.38.1"
sys.path.insert(0, str(REPO / "src"))

from cutvoke.core.builtin_stickers import load_builtin_stickers  # noqa: E402
from cutvoke.core.builtin_assets import (  # noqa: E402
    BACKGROUND_DIR,
    _BUILTIN_BACKGROUND_NAMES,
)
from cutvoke.core.preset_catalog import PresetCatalog  # noqa: E402


def _relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PACKAGE_ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"resource media escapes the package: {path}") from exc


def build_manifest() -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    resources: list[dict[str, Any]] = []

    def add_file(path: Path) -> str:
        relative = _relative(path)
        if not path.is_file():
            raise FileNotFoundError(f"bundled resource media is missing: {relative}")
        if relative not in files:
            content = path.read_bytes()
            files[relative] = {
                "path": relative,
                "sizeBytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "mediaType": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            }
        return relative

    def add_resource(item: dict[str, Any], media_paths: list[Path]) -> None:
        relative_paths = []
        for path in media_paths:
            relative_paths.append(add_file(path))
        resources.append({**item, "mediaFiles": relative_paths, "offlineAvailable": True})

    for preset in PresetCatalog.builtin().all():
        preset_media = [preset._path(preset.cover), preset._path(preset.motion_preview)]
        preset_media.extend(preset._path(reference) for reference in preset.evidence.values()
                            if reference)
        add_resource({
            "resourceId": preset.id,
            "kind": "preset",
            "name": preset.name,
            "family": preset.family,
            "subcategory": preset.subcategory,
            "version": preset.version,
            "license": preset.license,
            "source": preset.source,
            "status": preset.status,
            "qualified": preset.qualified,
            "downloadState": preset.download_state,
        }, preset_media)

    for sticker in load_builtin_stickers():
        sticker_stem = sticker["stickerId"].rsplit(".", 1)[-1]
        preview_root = Path(sticker["previewPath"]).parent
        sticker_media = [Path(sticker["path"]), Path(sticker["previewPath"])]
        sticker_media.extend(preview_root / f"{sticker_stem}{suffix}" for suffix in (
            ".qa.json", ".audit.json", ".audit.mp4"))
        visual_review = preview_root / f"{sticker_stem}.visual.json"
        # Candidate assets can ship with complete machine evidence while a
        # human visual decision is pending. Include the approval sidecar only
        # when it exists; its absence must not silently approve the resource.
        if visual_review.is_file():
            sticker_media.append(visual_review)
        resource = {
            "resourceId": sticker["stickerId"],
            "kind": "sticker",
            "name": sticker["name"],
            "family": "sticker",
            "subcategory": sticker["subcategory"],
            "version": sticker["version"],
            "license": sticker["license"],
            "status": sticker["status"],
            "qualified": sticker["qualified"],
            "downloadState": "bundled",
        }
        if sticker.get("source"):
            resource["source"] = sticker["source"]
        if sticker.get("defaultScale") is not None:
            resource["defaultScale"] = sticker["defaultScale"]
        add_resource(resource, sticker_media)

    for filename in sorted(Path(BACKGROUND_DIR).iterdir()):
        if not filename.is_file() or filename.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        stem = filename.stem
        if stem.startswith("bgfx_"):
            report_path = REPO / "docs/assets/cinematic-effect-texture-atlas-20260927.extraction.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            item = next((entry for entry in report.get("items", [])
                         if entry.get("stem") == stem), None)
            if (report.get("generator") != "OpenAI ImageGen"
                    or report.get("layout") != "4x4 equal square cells"
                    or item is None
                    or item.get("sha256") != hashlib.sha256(filename.read_bytes()).hexdigest()):
                raise ValueError(f"generated background provenance is incomplete or stale: {stem}")
            source = (
                f"OpenAI ImageGen atlas {report['source']} (SHA-256 {report['sourceSha256']}), "
                f"cell row {item['row'] + 1} column {item['column'] + 1}; extraction evidence "
                "docs/assets/cinematic-effect-texture-atlas-20260927.extraction.json"
            )
            license = report["license"]
            subcategory = "氛围纹理"
        elif stem.startswith("bgmat_"):
            report_path = REPO / "docs/assets/video-background-material-atlas-20260927.extraction.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            item = next((entry for entry in report.get("items", [])
                         if entry.get("stem") == stem), None)
            if (report.get("generator") != "OpenAI ImageGen"
                    or report.get("layout") != "4x4 equal square cells"
                    or item is None
                    or item.get("sha256") != hashlib.sha256(filename.read_bytes()).hexdigest()):
                raise ValueError(f"generated background provenance is incomplete or stale: {stem}")
            source = (
                f"OpenAI ImageGen atlas {report['source']} (SHA-256 {report['sourceSha256']}), "
                f"cell row {item['row'] + 1} column {item['column'] + 1}; extraction evidence "
                "docs/assets/video-background-material-atlas-20260927.extraction.json"
            )
            license = report["license"]
            subcategory = "标题卡材质背景"
        else:
            source = "CutVoke 内置背景资源；源文件位于 src/cutvoke/assets/backgrounds/"
            license = "Project-internal original; not separately licensed for redistribution"
            subcategory = "构图背景"
        add_resource({
            "resourceId": f"builtin_background_{stem}",
            "kind": "background",
            "name": f"内置·背景·{_BUILTIN_BACKGROUND_NAMES.get(stem, stem)}",
            "family": "background",
            "subcategory": subcategory,
            "version": "1.0.0",
            "license": license,
            "source": source,
            "status": "approved",
            "downloadState": "bundled",
        }, [filename])

    add_file(PACKAGE_ROOT / "core" / "builtin_presets.json")
    add_file(PACKAGE_ROOT / "core" / "builtin_stickers.json")

    resources.sort(key=lambda item: item["resourceId"])
    return {
        "schemaVersion": 1,
        "packId": PACK_ID,
        "version": PACK_VERSION,
        "resources": resources,
        "files": [files[path] for path in sorted(files)],
    }


def write_archive(manifest: dict[str, Any], out_path: Path) -> int:
    """Write a deterministic ZIP containing its manifest and all verified files."""
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    entries = [("manifest.json", rendered.encode("utf-8"))]
    for record in manifest["files"]:
        path = PACKAGE_ROOT / record["path"]
        content = path.read_bytes()
        if (len(content) != record["sizeBytes"] or
                hashlib.sha256(content).hexdigest() != record["sha256"]):
            raise ValueError(f"resource changed while creating archive: {record['path']}")
        entries.append((record["path"], content))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as archive:
        for name, content in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return out_path.stat().st_size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true", help="write the current inventory")
    action.add_argument("--check", action="store_true", help="fail if the committed inventory is stale")
    action.add_argument("--archive", type=Path, help="write a deterministic offline resource pack ZIP")
    args = parser.parse_args()
    manifest = build_manifest()
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if args.archive:
        size = write_archive(manifest, args.archive)
        print(f"wrote {len(manifest['resources'])} resources and {len(manifest['files'])} files ({size} bytes)")
        return 0
    if args.write:
        MANIFEST_PATH.write_text(rendered, encoding="utf-8")
        print(f"wrote {len(manifest['resources'])} resources and {len(manifest['files'])} files")
        return 0
    if not MANIFEST_PATH.is_file() or MANIFEST_PATH.read_text(encoding="utf-8") != rendered:
        print("built-in resource pack manifest is stale; run with --write")
        return 1
    print(f"verified {len(manifest['resources'])} resources and {len(manifest['files'])} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
