from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cutvoke.core.resource_pack import install_resource_pack_archive, resource_pack_summary
from cutvoke.core.httpapi import HttpApi
from cutvoke.core.resource_pack_signing import (
    add_trusted_publisher,
    make_signature,
    publisher_key_id,
    sign_archive,
    verify_signature,
)


def _small_pack(path: Path) -> None:
    files = {
        "core/builtin_presets.json": json.dumps(
            {"schemaVersion": 1, "presets": []}).encode(),
        "core/builtin_stickers.json": json.dumps({
            "schemaVersion": 1, "version": "1.0.0", "license": "CC0-1.0",
            "stickers": [{"stem": "sample", "name": "Sample",
                          "subcategory": "decor", "keywords": []}],
            "motionVariants": [],
        }).encode(),
        "assets/stickers/sample.png": b"sample-png",
        "assets/sticker_previews/sample.mp4": b"sample-preview",
        "assets/sticker_previews/sample.qa.json": b"{}",
        "assets/sticker_previews/sample.audit.json": b"{}",
        "assets/sticker_previews/sample.audit.mp4": b"sample-audit",
        "assets/sticker_previews/sample.visual.json": b"{}",
    }
    files_table = [{
        "path": name, "sizeBytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "mediaType": "application/octet-stream",
    } for name, content in sorted(files.items())]
    manifest = {
        "schemaVersion": 1,
        "packId": "test.signed-resources",
        "version": "1.0.0",
        "resources": [{
            "resourceId": "cutvoke.sticker.sample", "kind": "sticker",
            "name": "Sample", "family": "sticker", "subcategory": "decor",
            "version": "1.0.0", "license": "CC0-1.0", "status": "candidate",
            "qualified": False, "downloadState": "bundled",
            "mediaFiles": [name for name in files if name.startswith("assets/")],
            "offlineAvailable": True,
        }],
        "files": files_table,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(
            manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        for name, content in files.items():
            archive.writestr(name, content)


def _pem_private_key(path: Path, key: Ed25519PrivateKey) -> None:
    path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))


def _pem_public_key(path: Path, key: Ed25519PrivateKey) -> None:
    path.write_bytes(key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ))


def test_manifest_signature_requires_pinned_key_and_checks_fingerprint(tmp_path: Path) -> None:
    private = Ed25519PrivateKey.generate()
    public_bytes = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    manifest = b'{"packId":"test.resources","version":"1.0.0"}\n'
    signature = make_signature(manifest, private, "test.publisher")
    unknown = verify_signature(manifest, signature, {})
    assert unknown["signatureStatus"] == "unknown_publisher"
    assert unknown["publisherVerified"] is False

    fingerprint = publisher_key_id(public_bytes)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        add_trusted_publisher(
            tmp_path, "test.publisher", public_bytes,
            expected_fingerprint="0" * 64)
    added = add_trusted_publisher(
        tmp_path, "test.publisher", public_bytes,
        expected_fingerprint=fingerprint)
    assert added["fingerprint"] == fingerprint
    assert verify_signature(manifest, signature, {
        added["keyId"]: {
            "publisherId": "test.publisher", "keyId": added["keyId"],
            "publicKey": base64.b64encode(public_bytes).decode("ascii"),
        },
    })["publisherVerified"] is True
    with pytest.raises(ValueError, match="does not match its manifest"):
        verify_signature(manifest + b"tampered", signature, {})


def test_signed_archive_is_unknown_until_trusted_then_verified(tmp_path: Path) -> None:
    source = tmp_path / "unsigned.zip"
    signed = tmp_path / "signed.zip"
    _small_pack(source)
    private = Ed25519PrivateKey.generate()
    private_path = tmp_path / "publisher-private.pem"
    public_path = tmp_path / "publisher-public.pem"
    _pem_private_key(private_path, private)
    _pem_public_key(public_path, private)

    signed_metadata = sign_archive(source, signed, private_path, "test.publisher")
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(signed) as new:
        assert set(new.namelist()) == {*old.namelist(), "signature.json"}
        for name in old.namelist():
            assert old.read(name) == new.read(name)

    media_dir = tmp_path / "media"
    installed = install_resource_pack_archive(signed.read_bytes(), media_dir)
    assert installed["signatureStatus"] == "unknown_publisher"
    assert installed["publisherVerified"] is False
    assert installed["publisherId"] == "test.publisher"

    result = add_trusted_publisher(
        media_dir, "test.publisher", private.public_key(),
        expected_fingerprint=signed_metadata["keyId"])
    assert result["keyId"] == signed_metadata["keyId"]
    installed_root = (media_dir / ".resource-packs" / "test.signed-resources" / "1.0.0")
    verified = resource_pack_summary(installed_root)
    assert verified["signatureStatus"] == "verified"
    assert verified["publisherVerified"] is True


def test_tampered_signed_archive_is_rejected_before_install(tmp_path: Path) -> None:
    source = tmp_path / "unsigned.zip"
    signed = tmp_path / "signed.zip"
    tampered = tmp_path / "tampered.zip"
    _small_pack(source)
    private = Ed25519PrivateKey.generate()
    key_path = tmp_path / "publisher-private.pem"
    _pem_private_key(key_path, private)
    sign_archive(source, signed, key_path, "test.publisher")

    with zipfile.ZipFile(signed, "r") as old, zipfile.ZipFile(tampered, "w") as new:
        manifest = json.loads(old.read("manifest.json"))
        manifest["version"] = "1.0.1"
        for info in old.infolist():
            content = (json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
                       if info.filename == "manifest.json" else old.read(info))
            new.writestr(info.filename, content)

    with pytest.raises(ValueError, match="signature does not match its manifest"):
        install_resource_pack_archive(tampered.read_bytes(), tmp_path / "media")
    assert not (tmp_path / "media" / ".resource-packs" /
                "test.signed-resources" / "1.0.0").exists()


def test_unsigned_local_archive_is_explicitly_unverified(tmp_path: Path) -> None:
    source = tmp_path / "unsigned.zip"
    _small_pack(source)
    installed = install_resource_pack_archive(
        source.read_bytes(), tmp_path / "media")
    assert installed["signatureStatus"] == "unsigned"
    assert installed["publisherVerified"] is False


def test_http_trust_route_requires_the_advertised_fingerprint(tmp_path: Path) -> None:
    source = tmp_path / "unsigned.zip"
    signed = tmp_path / "signed.zip"
    _small_pack(source)
    private = Ed25519PrivateKey.generate()
    private_path = tmp_path / "publisher-private.pem"
    public_path = tmp_path / "publisher-public.pem"
    _pem_private_key(private_path, private)
    _pem_public_key(public_path, private)
    metadata = sign_archive(source, signed, private_path, "test.publisher")

    media_dir = tmp_path / "media"
    install_resource_pack_archive(signed.read_bytes(), media_dir)
    api = HttpApi(SimpleNamespace(store=None), render=object(), media_dir=str(media_dir))
    before = api.handle("GET", "/api/v1/resource-packs", {})
    assert before[0] == 200
    assert next(item for item in before[1]["installed"]
                if item["packId"] == "test.signed-resources")["signatureStatus"] == "unknown_publisher"

    body = {
        "publisherId": "test.publisher",
        "publicKeyPem": public_path.read_text(encoding="utf-8"),
        "fingerprint": "0" * 64,
    }
    rejected = api.handle("POST", "/api/v1/resource-packs/trust", body)
    assert rejected[0] == 422
    assert "fingerprint mismatch" in rejected[1]["error"]["message"]

    body["fingerprint"] = metadata["keyId"]
    accepted = api.handle("POST", "/api/v1/resource-packs/trust", body)
    assert accepted[0] == 200
    assert next(item for item in accepted[1]["installed"]
                if item["packId"] == "test.signed-resources")["signatureStatus"] == "verified"
