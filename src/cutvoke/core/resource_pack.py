"""Runtime status for the versioned, offline-capable built-in resource pack."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import threading
import uuid
import zipfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from .resource_pack_signing import (
    SIGNATURE_NAME,
    TRUST_STORE_NAME,
    load_trusted_publishers,
    load_trusted_publishers_file,
    read_signature,
    verify_signature,
)


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = Path(__file__).with_name("builtin_resource_pack.json")
_HASH_CACHE: dict[tuple[str, int, int, str], bool] = {}
_HASH_CACHE_LOCK = threading.Lock()


def _matches_sha256(path: Path, stat, expected: str) -> bool:
    """Verify content once per file metadata signature; edits invalidate the cache."""
    if (len(expected) != 64
            or any(char not in "0123456789abcdef" for char in expected.lower())):
        return False
    key = (str(path), stat.st_size, stat.st_mtime_ns, expected.lower())
    with _HASH_CACHE_LOCK:
        cached = _HASH_CACHE.get(key)
        if cached is not None:
            return cached
        digest = hashlib.sha256()
        try:
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
        except OSError:
            _HASH_CACHE[key] = False
            return False
        matches = digest.hexdigest() == expected.lower()
        # Keep one entry per path so long-running editor sessions do not retain
        # stale signatures after resource files are replaced.
        for old_key in [item for item in _HASH_CACHE if item[0] == str(path)]:
            del _HASH_CACHE[old_key]
        _HASH_CACHE[key] = matches
        return matches


def file_matches_sha256(path: Path, expected: str, size_bytes: int | None = None) -> bool:
    """Check an installed resource against its content identity."""
    if not isinstance(expected, str) or len(expected) != 64:
        return False
    try:
        stat = path.stat()
    except OSError:
        return False
    if not path.is_file() or (size_bytes is not None and stat.st_size != size_bytes):
        return False
    return _matches_sha256(path, stat, expected)


def install_immutable_resource_file(
    source: Path,
    resource_root: Path,
    *,
    pack_id: str,
    pack_version: str,
    relative_path: str,
    expected_sha256: str,
    expected_size: int | None = None,
) -> Path:
    """Atomically retain one verified resource at a versioned, content-addressed path."""
    safe_component = lambda value: (
        isinstance(value, str) and bool(value) and
        all(char.isascii() and (char.isalnum() or char in "._-") for char in value)
    )
    if not safe_component(pack_id) or not safe_component(pack_version):
        raise ValueError("invalid resource pack identity")
    relative = PurePosixPath(relative_path)
    if (relative.is_absolute() or not relative.parts or
            any(part in ("", ".", "..") or ":" in part for part in relative.parts)):
        raise ValueError("invalid resource pack path")
    if (len(expected_sha256) != 64 or
            any(char not in "0123456789abcdef" for char in expected_sha256.lower())):
        raise ValueError("invalid resource SHA-256")

    source = source.resolve()
    if not source.is_file() or not file_matches_sha256(source, expected_sha256, expected_size):
        raise ValueError(f"source resource does not match pack manifest: {relative_path}")

    root = resource_root.resolve()
    destination = root.joinpath(pack_id, pack_version, expected_sha256.lower(), *relative.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve()
    if not destination.is_relative_to(root):
        raise ValueError("resource destination escapes the immutable resource root")
    if file_matches_sha256(destination, expected_sha256, expected_size):
        return destination

    temporary = destination.with_name(f"{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copyfile(source, temporary)
        if not file_matches_sha256(temporary, expected_sha256, expected_size):
            raise ValueError(f"resource copy failed verification: {relative_path}")
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return destination


def _manifest_path(package_root: Path | None = None) -> Path:
    if package_root is None:
        return MANIFEST_PATH
    return package_root / "core" / "builtin_resource_pack.json"


def load_resource_pack_manifest(package_root: Path | None = None) -> dict[str, Any]:
    data = json.loads(_manifest_path(package_root).read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("schemaVersion") != 1
            or not isinstance(data.get("packId"), str)
            or not isinstance(data.get("version"), str)
            or not isinstance(data.get("resources"), list)
            or not isinstance(data.get("files"), list)):
        raise ValueError("invalid built-in resource pack manifest")
    return data


def resource_pack_summary(package_root: Path | None = None) -> dict[str, Any]:
    """Return pack metadata and detect missing, truncated or modified bundled files."""
    root = (package_root or PACKAGE_ROOT).resolve()
    manifest_path = _manifest_path(package_root)
    raw = manifest_path.read_bytes()
    manifest = load_resource_pack_manifest(package_root)
    missing: list[str] = []
    invalid: list[str] = []
    for entry in manifest["files"]:
        relative = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(relative, str):
            raise ValueError("invalid resource pack file record")
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            missing.append(relative)
            continue
        try:
            stat = path.stat()
            if stat.st_size != entry.get("sizeBytes"):
                invalid.append(relative)
                continue
            expected_hash = entry.get("sha256")
            if not isinstance(expected_hash, str) or not _matches_sha256(path, stat, expected_hash):
                invalid.append(relative)
        except OSError:
            missing.append(relative)

    resources = manifest["resources"]
    presets = [item for item in resources if item.get("kind") == "preset"]
    stickers = [item for item in resources if item.get("kind") == "sticker"]
    backgrounds = [item for item in resources if item.get("kind") == "background"]
    licenses_declared = sum(
        isinstance(item.get("license"), str) and bool(item["license"].strip())
        for item in resources if isinstance(item, dict))
    sources_declared = sum(
        isinstance(item.get("source"), str) and bool(item["source"].strip())
        for item in resources if isinstance(item, dict))
    provenance_complete = sum(
        isinstance(item.get("license"), str) and bool(item["license"].strip()) and
        isinstance(item.get("source"), str) and bool(item["source"].strip())
        for item in resources if isinstance(item, dict))
    if root == PACKAGE_ROOT.resolve():
        publisher = {"signatureStatus": "bundled", "publisherVerified": True,
                     "publisherId": "cutvoke.builtin", "keyId": "", "fingerprint": ""}
    else:
        signature_path = root / SIGNATURE_NAME
        signature_bytes = signature_path.read_bytes() if signature_path.is_file() else None
        if (root.parent.parent.name == ".resource-packs" and
                (root.parent.parent / TRUST_STORE_NAME).is_file()):
            trusted = load_trusted_publishers_file(root.parent.parent / TRUST_STORE_NAME)
        else:
            trusted = {}
        publisher = verify_signature(raw, read_signature(signature_bytes), trusted)
    return {
        "packId": manifest["packId"],
        "version": manifest["version"],
        "manifestSha256": hashlib.sha256(raw).hexdigest(),
        "resourceCount": len(resources),
        "presetCount": len(presets),
        "stickerCount": len(stickers),
        "backgroundCount": len(backgrounds),
        "provenanceCoverage": {
            "resourceCount": len(resources),
            "licenseDeclaredCount": licenses_declared,
            "sourceDeclaredCount": sources_declared,
            "completeCount": provenance_complete,
            "incompleteCount": len(resources) - provenance_complete,
        },
        "fileCount": len(manifest["files"]),
        "offlineAvailable": not missing and not invalid,
        "missingFiles": missing,
        "invalidFiles": invalid,
        **publisher,
    }


_PACK_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_PACK_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_CATALOG_FILES = {"core/builtin_presets.json", "core/builtin_stickers.json"}
_MAX_ARCHIVE_FILES = 10000
_MAX_ARCHIVE_EXPANDED_BYTES = 512 * 1024 * 1024
_MAX_ARCHIVE_MANIFEST_BYTES = 16 * 1024 * 1024


def _safe_pack_component(value: Any, *, version: bool = False) -> bool:
    pattern = _PACK_VERSION_RE if version else _PACK_ID_RE
    return isinstance(value, str) and bool(pattern.fullmatch(value)) and value not in (".", "..")


def _safe_pack_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts:
        return False
    if any(part in ("", ".", "..") or ":" in part for part in path.parts):
        return False
    return value in _CATALOG_FILES or value.startswith("assets/")


def _validated_archive_manifest(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or value.get("schemaVersion") != 1
            or not _safe_pack_component(value.get("packId"))
            or not _safe_pack_component(value.get("version"), version=True)
            or not isinstance(value.get("resources"), list)
            or not isinstance(value.get("files"), list)):
        raise ValueError("invalid resource pack manifest")
    files: dict[str, dict[str, Any]] = {}
    for item in value["files"]:
        if not isinstance(item, dict) or not _safe_pack_path(item.get("path")):
            raise ValueError("invalid resource pack file path")
        relative = item["path"]
        size = item.get("sizeBytes")
        digest = item.get("sha256")
        if (relative in files or isinstance(size, bool) or not isinstance(size, int)
                or size < 0 or size > _MAX_ARCHIVE_EXPANDED_BYTES
                or not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest.lower())):
            raise ValueError(f"invalid resource pack file record: {relative}")
        files[relative] = item
    if not _CATALOG_FILES.issubset(files):
        raise ValueError("resource pack must contain both preset and sticker catalogs")
    resource_ids: set[str] = set()
    for item in value["resources"]:
        if (not isinstance(item, dict) or not isinstance(item.get("resourceId"), str)
                or item["resourceId"] in resource_ids
                or item.get("kind") not in ("preset", "sticker", "background")
                or not isinstance(item.get("mediaFiles"), list)):
            raise ValueError("invalid resource pack resource record")
        resource_ids.add(item["resourceId"])
        for path in item["mediaFiles"]:
            if not isinstance(path, str) or path not in files:
                raise ValueError(f"resource refers to an unlisted file: {path}")
    return value


def _validate_pack_catalogs(package_root: Path, manifest: dict[str, Any]) -> None:
    """Validate catalog syntax and prevent references from escaping the package."""
    files = {item["path"] for item in manifest["files"]}
    presets_path = package_root / "core" / "builtin_presets.json"
    presets = json.loads(presets_path.read_text(encoding="utf-8"))
    entries = presets.get("presets") if isinstance(presets, dict) else None
    if (not isinstance(presets, dict) or presets.get("schemaVersion") != 1
            or not isinstance(entries, list)):
        raise ValueError("invalid preset catalog in resource pack")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid preset catalog entry")
        references = [entry.get("cover", ""), entry.get("motionPreview", "")]
        evidence = entry.get("evidence", {})
        if not isinstance(evidence, dict):
            raise ValueError("invalid preset evidence map")
        references.extend(evidence.values())
        for reference in references:
            if not reference:
                continue
            if not isinstance(reference, str) or Path(reference).is_absolute():
                raise ValueError("invalid resource path in preset catalog")
            resolved = (package_root / "core" / reference).resolve()
            if not resolved.is_relative_to(package_root.resolve()):
                raise ValueError("preset resource path escapes the installed pack")
            relative = resolved.relative_to(package_root.resolve()).as_posix()
            if relative not in files or not resolved.is_file():
                raise ValueError(f"preset resource is missing from package: {relative}")

    from .builtin_stickers import load_builtin_stickers
    stickers = load_builtin_stickers(package_root=package_root, include_quality=False)
    sticker_resources = {item["resourceId"]: item for item in manifest["resources"]
                         if item.get("kind") == "sticker"}
    sticker_ids = set(sticker_resources)
    if {item["stickerId"] for item in stickers} != sticker_ids:
        raise ValueError("sticker catalog does not match resource pack inventory")
    for sticker in stickers:
        stem = sticker["stickerId"].rsplit(".", 1)[-1]
        preview_root = package_root / "assets" / "sticker_previews"
        expected = {
            Path(sticker["path"]).resolve().relative_to(package_root.resolve()).as_posix(),
            Path(sticker["previewPath"]).resolve().relative_to(package_root.resolve()).as_posix(),
            *[(preview_root / f"{stem}{suffix}").relative_to(package_root).as_posix()
              for suffix in (".qa.json", ".audit.json", ".audit.mp4")],
        }
        record = sticker_resources[sticker["stickerId"]]
        visual_review = (preview_root / f"{stem}.visual.json").relative_to(package_root).as_posix()
        if (record.get("status") == "approved" or record.get("qualified") or
                visual_review in record["mediaFiles"]):
            expected.add(visual_review)
        if not expected.issubset(set(record["mediaFiles"])):
            raise ValueError(f"sticker evidence is incomplete: {sticker['stickerId']}")
        if any(not (package_root / relative).is_file() for relative in expected):
            raise ValueError(f"sticker media is missing: {sticker['stickerId']}")
    preset_ids = {item.get("presetId") for item in entries}
    inventory_preset_ids = {item["resourceId"] for item in manifest["resources"]
                            if item.get("kind") == "preset"}
    if preset_ids != inventory_preset_ids:
        raise ValueError("preset catalog does not match resource pack inventory")

    for background in (item for item in manifest["resources"]
                       if item.get("kind") == "background"):
        images = [relative for relative in background["mediaFiles"]
                  if relative.startswith("assets/backgrounds/")
                  and Path(relative).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
        if not images:
            raise ValueError(f"background resource has no bundled image: {background['resourceId']}")
        for relative in images:
            path = (package_root / relative).resolve()
            if not path.is_relative_to(package_root.resolve()) or not path.is_file():
                raise ValueError(f"background media is missing from package: {relative}")

    from .preset_catalog import PresetCatalog
    catalog = PresetCatalog.builtin(package_root=package_root)
    if {preset.id for preset in catalog.all()} != preset_ids:
        raise ValueError("invalid preset catalog in resource pack")


def install_resource_pack_archive(raw: bytes, media_dir: str | Path) -> dict[str, Any]:
    """Install a ZIP only after every declared file and catalog passes validation."""
    manager_root = (Path(media_dir).resolve() / ".resource-packs")
    manager_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
        infos = archive.infolist()
        if not infos or len(infos) > _MAX_ARCHIVE_FILES:
            raise ValueError("resource package contains an invalid number of files")
        names: set[str] = set()
        expanded = 0
        for info in infos:
            name = info.filename
            if info.is_dir() or not _safe_pack_path(name) and name not in ("manifest.json", SIGNATURE_NAME):
                raise ValueError(f"invalid file path in resource package: {name}")
            if name in names:
                raise ValueError(f"duplicate file in resource package: {name}")
            names.add(name)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise ValueError("symbolic links are not allowed in resource packages")
            expanded += info.file_size
            if expanded > _MAX_ARCHIVE_EXPANDED_BYTES:
                raise ValueError("resource package expands beyond the 512 MiB limit")
        if "manifest.json" not in names:
            raise ValueError("resource package is missing manifest.json")
        manifest_info = archive.getinfo("manifest.json")
        if manifest_info.file_size > _MAX_ARCHIVE_MANIFEST_BYTES:
            raise ValueError("resource package manifest is too large")
        manifest_bytes = archive.read(manifest_info)
        manifest = _validated_archive_manifest(json.loads(manifest_bytes))
        if SIGNATURE_NAME in names:
            signature_info = archive.getinfo(SIGNATURE_NAME)
            if signature_info.file_size > 16 * 1024:
                raise ValueError("resource pack signature file is too large")
            signature_bytes = archive.read(signature_info)
        else:
            signature_bytes = None
        publisher = verify_signature(
            manifest_bytes, read_signature(signature_bytes),
            load_trusted_publishers(media_dir),
        )
        expected_names = {"manifest.json"} | {item["path"] for item in manifest["files"]}
        if signature_bytes is not None:
            expected_names.add(SIGNATURE_NAME)
        if names != expected_names:
            raise ValueError("resource package files do not match the manifest")

        pack_id, version = manifest["packId"], manifest["version"]
        target = manager_root / pack_id / version
        if target.exists():
            existing_manifest = _manifest_path(target)
            existing_signature = target / SIGNATURE_NAME
            existing_signature_bytes = (existing_signature.read_bytes()
                                        if existing_signature.is_file() else None)
            if (existing_manifest.is_file() and
                    hashlib.sha256(existing_manifest.read_bytes()).digest() ==
                    hashlib.sha256(manifest_bytes).digest() and
                    existing_signature_bytes == signature_bytes):
                return resource_pack_summary(target)
            raise ValueError("this resource pack version is already installed with different contents or publisher signature")

        staging = manager_root / f".staging-{uuid.uuid4().hex}"
        staging.mkdir()
        try:
            for record in manifest["files"]:
                relative = record["path"]
                destination = (staging / relative).resolve()
                if not destination.is_relative_to(staging.resolve()):
                    raise ValueError("resource package path escapes installation directory")
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                count = 0
                with archive.open(relative, "r") as source, destination.open("wb") as output:
                    while block := source.read(1024 * 1024):
                        count += len(block)
                        if count > record["sizeBytes"]:
                            raise ValueError(f"resource file exceeds declared size: {relative}")
                        digest.update(block)
                        output.write(block)
                if count != record["sizeBytes"] or digest.hexdigest() != record["sha256"]:
                    raise ValueError(f"resource file failed SHA-256 verification: {relative}")
            manifest_path = staging / "core" / "builtin_resource_pack.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_bytes(manifest_bytes)
            if signature_bytes is not None:
                (staging / SIGNATURE_NAME).write_bytes(signature_bytes)
            _validate_pack_catalogs(staging, manifest)
            builtin_manifest = load_resource_pack_manifest()
            if (manifest["packId"], manifest["version"]) == (
                    builtin_manifest["packId"], builtin_manifest["version"]):
                if manifest == builtin_manifest:
                    return resource_pack_summary()
                raise ValueError("resource pack version collides with different built-in contents")
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
    installed = resource_pack_summary(target)
    # Re-evaluate through the installed trust store so the returned status is
    # authoritative even when the caller installed a key at the same time.
    if publisher["signatureStatus"] == "verified" and not installed["publisherVerified"]:
        raise ValueError("resource pack signature changed during installation")
    return installed


def _state_path(media_dir: str | Path) -> Path:
    return Path(media_dir).resolve() / ".resource-packs" / "state.json"


def _read_state(media_dir: str | Path) -> dict[str, Any]:
    path = _state_path(media_dir)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict) or state.get("schemaVersion") != 1:
        state = {}
    active = state.get("active")
    if not isinstance(active, dict):
        active = {"packId": load_resource_pack_manifest()["packId"],
                  "version": load_resource_pack_manifest()["version"]}
    history = state.get("history")
    if not isinstance(history, list):
        history = []
    return {"schemaVersion": 1, "active": active, "history": history[-16:]}


def active_resource_pack_root(media_dir: str | Path) -> Path:
    """Resolve active pack, falling back to the shipped pack if state is stale."""
    state = _read_state(media_dir)
    builtin = load_resource_pack_manifest()
    active = state["active"]
    if active.get("packId") == builtin["packId"] and active.get("version") == builtin["version"]:
        return PACKAGE_ROOT
    if (_safe_pack_component(active.get("packId"))
            and _safe_pack_component(active.get("version"), version=True)):
        root = Path(media_dir).resolve() / ".resource-packs" / active["packId"] / active["version"]
        try:
            manifest = load_resource_pack_manifest(root)
            if (manifest["packId"] == active["packId"]
                    and manifest["version"] == active["version"]
                    and resource_pack_summary(root)["offlineAvailable"]):
                return root
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    return PACKAGE_ROOT


def _write_state(media_dir: str | Path, state: dict[str, Any]) -> None:
    path = _state_path(media_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def activate_resource_pack(media_dir: str | Path, pack_id: str, version: str) -> dict[str, Any]:
    if not _safe_pack_component(pack_id) or not _safe_pack_component(version, version=True):
        raise ValueError("invalid resource pack identity")
    builtin = load_resource_pack_manifest()
    if pack_id == builtin["packId"] and version == builtin["version"]:
        root = PACKAGE_ROOT
    else:
        root = Path(media_dir).resolve() / ".resource-packs" / pack_id / version
    manifest = _validated_archive_manifest(load_resource_pack_manifest(root))
    summary = resource_pack_summary(root)
    if not summary["offlineAvailable"]:
        raise ValueError("resource pack has missing or invalid files and cannot be activated")
    _validate_pack_catalogs(root, manifest)
    state = _read_state(media_dir)
    current = state["active"]
    next_active = {"packId": pack_id, "version": version}
    if current != next_active:
        state["history"] = [*state["history"], current][-16:]
        state["active"] = next_active
        _write_state(media_dir, state)
    return summary


def rollback_resource_pack(media_dir: str | Path) -> dict[str, Any]:
    state = _read_state(media_dir)
    if not state["history"]:
        raise ValueError("no previous resource pack version is available")
    previous = state["history"].pop()
    if not isinstance(previous, dict):
        raise ValueError("resource pack rollback history is invalid")
    pack_id, version = previous.get("packId"), previous.get("version")
    if not _safe_pack_component(pack_id) or not _safe_pack_component(version, version=True):
        raise ValueError("resource pack rollback history is invalid")
    current = state["active"]
    builtin = load_resource_pack_manifest()
    root = (PACKAGE_ROOT if pack_id == builtin["packId"] and version == builtin["version"]
            else Path(media_dir).resolve() / ".resource-packs" / pack_id / version)
    summary = resource_pack_summary(root)
    if not summary["offlineAvailable"]:
        raise ValueError("previous resource pack is no longer available")
    state["active"] = {"packId": pack_id, "version": version}
    _write_state(media_dir, state)
    return summary


def resource_pack_manager_status(media_dir: str | Path) -> dict[str, Any]:
    builtin = load_resource_pack_manifest()
    active_root = active_resource_pack_root(media_dir)
    active = resource_pack_summary(active_root)
    installed: list[dict[str, Any]] = []
    builtin_entry = {**resource_pack_summary(PACKAGE_ROOT), "builtin": True,
                     "active": active_root.resolve() == PACKAGE_ROOT.resolve()}
    installed.append(builtin_entry)
    manager_root = Path(media_dir).resolve() / ".resource-packs"
    if manager_root.is_dir():
        for pack_dir in sorted(manager_root.iterdir()):
            if not pack_dir.is_dir() or pack_dir.name.startswith("."):
                continue
            for version_dir in sorted(pack_dir.iterdir()):
                if not version_dir.is_dir():
                    continue
                try:
                    item = resource_pack_summary(version_dir)
                    if (item["packId"], item["version"]) == (builtin["packId"], builtin["version"]):
                        continue
                    installed.append({**item, "builtin": False,
                                      "active": version_dir.resolve() == active_root.resolve()})
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
    state = _read_state(media_dir)
    return {"active": active, "installed": installed,
            "history": state["history"], "canRollback": bool(state["history"])}
