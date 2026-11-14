"""Bounded, inert source handoffs. Reading/restoring never starts a renderer."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import zipfile

from cutvoke.storage.generated_media import (
    MAX_INPUT_BYTES, MAX_MANIFEST_BYTES, SHA, canonical, validate_manifest,
)

MAX_ARCHIVE_BYTES = MAX_INPUT_BYTES + MAX_MANIFEST_BYTES + 256 * 1024
MAX_ARCHIVES = 128
SUFFIX = ".cvsource.zip"


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate source manifest key: {key}")
        result[key] = value
    return result


def _portable_name(name):
    if (not isinstance(name, str) or not name or len(name) > 240
            or "\\" in name or ":" in name or "\x00" in name):
        raise ValueError("source archive requires relative portable file paths")
    for part in name.split("/"):
        if (part in ("", ".", "..") or part.endswith((".", " "))
                or re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)):
            raise ValueError("unsafe source archive path")
    return name


def read_source_archive(path, *, expected_sha=None, expected_bundle=None):
    """Freeze <=33 MiB, validate exact entries/bytes, return metadata and bytes."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("source archive must be a regular file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_ARCHIVE_BYTES + 1)
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("source archive size limit exceeded")
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha is not None and digest != expected_sha:
        raise ValueError("source archive SHA256 mismatch")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if not 2 <= len(infos) <= 129:
                raise ValueError("source archive requires manifest and 1..128 inputs")
            seen, total = set(), 0
            for info in infos:
                if info.orig_filename != info.filename:
                    raise ValueError("source archive path contains a NUL byte")
                name = _portable_name(info.filename)
                folded = name.casefold()
                if folded in seen:
                    raise ValueError("duplicate source archive entry")
                seen.add(folded)
                mode = stat.S_IFMT(info.external_attr >> 16)
                if (info.is_dir() or mode not in (0, stat.S_IFREG) or info.flag_bits & 1
                        or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise ValueError("unsupported source archive entry type or compression")
                total += info.file_size
            if total > MAX_INPUT_BYTES + MAX_MANIFEST_BYTES:
                raise ValueError("source archive expanded size limit exceeded")
            for name in seen:
                if any("/".join(name.split("/")[:i]) in seen for i in range(1, len(name.split("/")))):
                    raise ValueError("source archive file/directory collision")
            manifest_info = archive.getinfo("manifest.json")
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise ValueError("source manifest exceeds 256 KiB")
            manifest = json.loads(archive.read(manifest_info), object_pairs_hook=_unique)
            validate_manifest(manifest)
            expected = {"manifest.json", *(item["path"] for item in manifest["files"])}
            if set(archive.namelist()) != expected:
                raise ValueError("source archive must contain exactly the declared inputs")
            budget = MAX_INPUT_BYTES
            for item in manifest["files"]:
                budget -= archive.getinfo(item["path"]).file_size
                if budget < 0:
                    raise ValueError("source inputs exceed 32 MiB")
                h = hashlib.sha256()
                with archive.open(item["path"]) as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        h.update(chunk)
                if h.hexdigest() != item["sha256"]:
                    raise ValueError(f"source input SHA256 mismatch: {item['path']}")
    except (zipfile.BadZipFile, KeyError, TypeError, json.JSONDecodeError, NotImplementedError) as error:
        raise ValueError(f"invalid source archive: {error}") from error
    bundle = hashlib.sha256(canonical(manifest)).hexdigest()
    if expected_bundle is not None and bundle != expected_bundle:
        raise ValueError("source archive bundle identity mismatch")
    return {"path": str(path), "bundleId": bundle, "sha256": digest,
            "size": len(raw), "outputSha256": manifest["output"]["sha256"],
            "manifest": manifest, "runtimeExecuted": False}, raw


def referenced_sources(media_hashes, directories):
    """Find only used outputs in known .generated folders; no recursive search.

    Legacy archives have no registry. Manifest headers select used output hashes;
    a selected archive is then fully checked. Unrelated malformed files fail
    closed rather than disappearing silently from a handoff.
    """
    found = {}
    for directory in sorted({Path(d) for d in directories}, key=str):
        if not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("generated source directory must not be a symbolic link")
        candidates = sorted(directory.glob("*" + SUFFIX))
        if len(candidates) > MAX_ARCHIVES:
            raise ValueError("generated source archive count limit exceeded")
        for path in candidates:
            bundle = path.name[:-len(SUFFIX)]
            if not SHA.fullmatch(bundle) or path.is_symlink():
                raise ValueError("invalid generated source archive identity")
            # Validate a bounded header first so unused source payloads are not
            # read/copied. Never use archive paths as host filesystem paths.
            try:
                if path.stat().st_size > MAX_ARCHIVE_BYTES:
                    raise ValueError("source archive size limit exceeded")
                with zipfile.ZipFile(path) as archive:
                    info = archive.getinfo("manifest.json")
                    if info.file_size > MAX_MANIFEST_BYTES:
                        raise ValueError("source manifest exceeds 256 KiB")
                    manifest = json.loads(archive.read(info), object_pairs_hook=_unique)
                    validate_manifest(manifest)
                    output = manifest["output"]["sha256"]
            except (OSError, zipfile.BadZipFile, KeyError, TypeError, json.JSONDecodeError) as error:
                raise ValueError(f"invalid generated source archive: {path.name}") from error
            if output not in media_hashes:
                continue
            result, _ = read_source_archive(path, expected_bundle=bundle)
            if bundle in found and found[bundle]["sha256"] != result["sha256"]:
                raise ValueError("conflicting source archives for one bundle")
            found.setdefault(bundle, result)
            if len(found) > MAX_ARCHIVES:
                raise ValueError("referenced source archive count limit exceeded")
    return list(found.values())


def validate_source_directory(directory):
    """Keep writes inside their declared parent, including Windows junctions."""
    directory = Path(directory)
    expected = directory.parent.parent.resolve() / directory.parent.name / directory.name
    if (directory.is_symlink() or directory.parent.is_symlink()
            or directory.resolve() != expected):
        raise ValueError("source handoff destination contains directory links")


def retain_source_archive(info, directory):
    """Copy checked immutable ZIP, atomically and without replacing old bytes."""
    directory = Path(directory)
    validate_source_directory(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (info["bundleId"] + SUFFIX)
    _, raw = read_source_archive(info["path"], expected_sha=info["sha256"], expected_bundle=info["bundleId"])
    fd, pending = tempfile.mkstemp(dir=directory, prefix="source-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(pending, target)
        except FileExistsError:
            read_source_archive(target, expected_sha=info["sha256"], expected_bundle=info["bundleId"])
    finally:
        Path(pending).unlink(missing_ok=True)
    return str(target)


def restore_source_archive(info, destination):
    """Explicitly extract verified inputs into a new directory. Never executes."""
    checked, raw = read_source_archive(info["path"], expected_sha=info["sha256"], expected_bundle=info["bundleId"])
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("source restoration requires a new destination directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(dir=destination.parent, prefix=".source-restore-"))
    # Reserve the destination exclusively; do not rename over a competing empty
    # directory (POSIX rename would replace it). Failure removes only our copy.
    reserved = False
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for name in archive.namelist():
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(archive.read(name))
        destination.mkdir()
        reserved = True
        # Link the fully staged files into our exclusively allocated directory;
        # any failure is rolled back. No user-owned destination is overwritten.
        for source in stage.rglob("*"):
            if source.is_file():
                target = destination / source.relative_to(stage)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.link(source, target)
        return {"path": str(destination), "entrypoint": checked["manifest"]["entrypoint"],
                "bundleId": checked["bundleId"], "runtimeExecuted": False,
                "includesRenderedMedia": False, "dependencyCompleteness": "declared_only"}
    except BaseException:
        if reserved:
            shutil.rmtree(destination)
        raise
    finally:
        shutil.rmtree(stage)
