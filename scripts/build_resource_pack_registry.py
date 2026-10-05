#!/usr/bin/env python3
"""Build a detached Ed25519-signed static resource-pack registry document."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import sys
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cutvoke.core.resource_pack_registry import sign_registry_payload


IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024


def _archive_identity(path: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    except (OSError, zipfile.BadZipFile, KeyError, UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"invalid resource package archive {path}: {error}") from error
    if not isinstance(manifest, dict):
        raise ValueError(f"resource package manifest is not an object: {path}")
    pack_id, version = manifest.get("packId"), manifest.get("version")
    if (not isinstance(pack_id, str) or not IDENTIFIER.fullmatch(pack_id) or
            not isinstance(version, str) or not IDENTIFIER.fullmatch(version)):
        raise ValueError(f"resource package manifest has an invalid identity: {path}")
    return pack_id, version


def _load_signing_key(path: Path) -> Ed25519PrivateKey:
    raw = path.read_bytes()
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except TypeError:
        password = getpass.getpass("Private key passphrase: ").encode("utf-8")
        key = serialization.load_pem_private_key(raw, password=password)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("registry signing key must be Ed25519")
    return key


def _package_record(args: tuple[str, ...]) -> dict[str, object]:
    pack_id, version, name, url, archive_name = args
    if not IDENTIFIER.fullmatch(pack_id) or not IDENTIFIER.fullmatch(version):
        raise ValueError("package pack ID and version must use letters, digits, dot, underscore, or hyphen")
    if not 1 <= len(name.strip()) <= 160:
        raise ValueError("package display name must contain 1-160 characters")
    parsed = urlsplit(url)
    if (parsed.scheme.lower() != "https" or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or parsed.fragment):
        raise ValueError("package archive URL must be an absolute HTTPS URL without credentials or fragment")
    archive_path = Path(archive_name).expanduser().resolve()
    size = archive_path.stat().st_size
    if size <= 0 or size > MAX_ARCHIVE_BYTES:
        raise ValueError(f"resource package archive must be 1-{MAX_ARCHIVE_BYTES} bytes")
    actual_id, actual_version = _archive_identity(archive_path)
    if (actual_id, actual_version) != (pack_id, version):
        raise ValueError(
            f"archive identity is {actual_id} v{actual_version}, not requested {pack_id} v{version}")
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    return {"packId": pack_id, "version": version, "name": name.strip(),
            "url": url, "sha256": digest, "sizeBytes": size}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-id", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--publisher-id", required=True)
    parser.add_argument("--private-key", required=True, type=Path,
                        help="Ed25519 PEM private key; it should not be committed or published")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--package", action="append", nargs=5, required=True,
                        metavar=("PACK_ID", "VERSION", "NAME", "HTTPS_URL", "ARCHIVE_ZIP"),
                        help="package item; may be repeated")
    args = parser.parse_args()

    try:
        if not IDENTIFIER.fullmatch(args.registry_id):
            raise ValueError("registry ID must use letters, digits, dot, underscore, or hyphen")
        if not 1 <= len(args.display_name.strip()) <= 128:
            raise ValueError("registry display name must contain 1-128 characters")
        if not IDENTIFIER.fullmatch(args.publisher_id):
            raise ValueError("publisher ID must use letters, digits, dot, underscore, or hyphen")
        packages = [_package_record(tuple(item)) for item in args.package]
        identities = [(item["packId"], item["version"]) for item in packages]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate package ID and version")
        payload = {"schemaVersion": 1, "registryId": args.registry_id,
                   "displayName": args.display_name.strip(), "packages": packages}
        document = sign_registry_payload(payload, _load_signing_key(args.private_key), args.publisher_id)
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f"{output.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        print(f"Signed {len(packages)} resource package(s) for {args.publisher_id}: {output}")
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
