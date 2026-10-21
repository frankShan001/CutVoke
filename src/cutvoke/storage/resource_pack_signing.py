"""Ed25519 signatures and explicit publisher trust for resource pack archives.

Signatures authenticate the exact UTF-8 bytes of manifest.json. A signature
only counts as a verified publisher when its public-key fingerprint has been
added to the local trust store after an out-of-band fingerprint check.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import getpass
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


TRUST_STORE_NAME = "trusted-publishers.json"
SIGNATURE_NAME = "signature.json"
_PUBLISHER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_SIGNATURE_BYTES = 16 * 1024


def trust_store_path(media_dir: str | Path) -> Path:
    return Path(media_dir).resolve() / ".resource-packs" / TRUST_STORE_NAME


def load_trusted_publishers_file(path: str | Path) -> dict[str, dict[str, str]]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ValueError(f"publisher trust store cannot be read: {exc}") from exc
    if not isinstance(data, dict) or data.get("schemaVersion") != 1:
        raise ValueError("invalid publisher trust store")
    entries = data.get("publishers")
    if not isinstance(entries, list):
        raise ValueError("invalid publisher trust store")
    trusted: dict[str, dict[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid publisher trust entry")
        publisher_id = entry.get("publisherId")
        key_id = entry.get("keyId")
        public_key = entry.get("publicKey")
        if (not isinstance(publisher_id, str) or not _PUBLISHER_ID.fullmatch(publisher_id)
                or not isinstance(key_id, str) or not _HEX_SHA256.fullmatch(key_id)
                or not isinstance(public_key, str)):
            raise ValueError("invalid publisher trust entry")
        try:
            raw = base64.b64decode(public_key, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid public key in publisher trust store") from exc
        if len(raw) != 32 or publisher_key_id(raw) != key_id or key_id in trusted:
            raise ValueError("publisher trust key fingerprint does not match")
        trusted[key_id] = {
            "publisherId": publisher_id,
            "keyId": key_id,
            "publicKey": base64.b64encode(raw).decode("ascii"),
        }
    return trusted


def _raw_public_bytes(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def publisher_key_id(public_key: bytes | Ed25519PublicKey) -> str:
    raw = _raw_public_bytes(public_key) if isinstance(public_key, Ed25519PublicKey) else public_key
    if len(raw) != 32:
        raise ValueError("Ed25519 public key must contain exactly 32 bytes")
    return hashlib.sha256(raw).hexdigest()


def load_trusted_publishers(media_dir: str | Path) -> dict[str, dict[str, str]]:
    return load_trusted_publishers_file(trust_store_path(media_dir))


def add_trusted_publisher(
    media_dir: str | Path,
    publisher_id: str,
    public_key: bytes | Ed25519PublicKey,
    *,
    expected_fingerprint: str,
) -> dict[str, str]:
    if not isinstance(publisher_id, str) or not _PUBLISHER_ID.fullmatch(publisher_id):
        raise ValueError("publisher ID must use 1-128 ASCII letters, digits, dot, underscore, or hyphen")
    raw = _raw_public_bytes(public_key) if isinstance(public_key, Ed25519PublicKey) else public_key
    key_id = publisher_key_id(raw)
    expected = expected_fingerprint.lower().removeprefix("sha256:").replace(":", "")
    if expected != key_id:
        raise ValueError(f"publisher key fingerprint mismatch; expected {key_id}")

    path = trust_store_path(media_dir)
    trusted = load_trusted_publishers(media_dir)
    previous = trusted.get(key_id)
    if previous and previous["publisherId"] != publisher_id:
        raise ValueError("this public key is already trusted for a different publisher ID")
    trusted[key_id] = {
        "publisherId": publisher_id,
        "keyId": key_id,
        "publicKey": base64.b64encode(raw).decode("ascii"),
    }
    data = {"schemaVersion": 1, "publishers": [trusted[key] for key in sorted(trusted)]}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"publisherId": publisher_id, "keyId": key_id, "fingerprint": key_id}


def make_signature(manifest_bytes: bytes, private_key: Ed25519PrivateKey,
                   publisher_id: str) -> dict[str, Any]:
    if not isinstance(publisher_id, str) or not _PUBLISHER_ID.fullmatch(publisher_id):
        raise ValueError("invalid publisher ID")
    public_key = private_key.public_key()
    return {
        "schemaVersion": 1,
        "algorithm": "Ed25519",
        "publisherId": publisher_id,
        "keyId": publisher_key_id(public_key),
        "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "signature": base64.b64encode(private_key.sign(manifest_bytes)).decode("ascii"),
    }


def verify_signature(manifest_bytes: bytes, signature_data: Any,
                     trusted: dict[str, dict[str, str]]) -> dict[str, Any]:
    if signature_data is None:
        return {"signatureStatus": "unsigned", "publisherVerified": False,
                "publisherId": "", "keyId": "", "fingerprint": ""}
    if not isinstance(signature_data, dict):
        raise ValueError("invalid resource pack signature")
    publisher_id = signature_data.get("publisherId")
    key_id = signature_data.get("keyId")
    if (signature_data.get("schemaVersion") != 1
            or signature_data.get("algorithm") != "Ed25519"
            or not isinstance(publisher_id, str)
            or not _PUBLISHER_ID.fullmatch(publisher_id)
            or not isinstance(key_id, str) or not _HEX_SHA256.fullmatch(key_id)
            or signature_data.get("manifestSha256") != hashlib.sha256(manifest_bytes).hexdigest()):
        raise ValueError("resource pack signature does not match its manifest")
    encoded_signature = signature_data.get("signature")
    if not isinstance(encoded_signature, str):
        raise ValueError("invalid resource pack signature bytes")
    try:
        signature = base64.b64decode(encoded_signature, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid resource pack signature bytes") from exc
    if len(signature) != 64:
        raise ValueError("invalid Ed25519 signature length")

    entry = trusted.get(key_id)
    if entry is None:
        return {"signatureStatus": "unknown_publisher", "publisherVerified": False,
                "publisherId": publisher_id, "keyId": key_id, "fingerprint": key_id}
    if entry["publisherId"] != publisher_id:
        raise ValueError("resource pack signature publisher ID does not match trusted key")
    try:
        raw_public_key = base64.b64decode(entry["publicKey"], validate=True)
        Ed25519PublicKey.from_public_bytes(raw_public_key).verify(signature, manifest_bytes)
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("resource pack publisher signature verification failed") from exc
    return {"signatureStatus": "verified", "publisherVerified": True,
            "publisherId": publisher_id, "keyId": key_id, "fingerprint": key_id}


def read_signature(signature_bytes: bytes | None) -> Any:
    if signature_bytes is None:
        return None
    if len(signature_bytes) > _MAX_SIGNATURE_BYTES:
        raise ValueError("resource pack signature file is too large")
    try:
        return json.loads(signature_bytes)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid resource pack signature JSON") from exc


def _load_private_key(path: Path, passphrase: bytes | None) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=passphrase)
    except TypeError:
        if passphrase is not None:
            raise
        if not sys.stdin.isatty():
            raise ValueError("encrypted signing key requires CUTVOKE_RESOURCE_PACK_KEY_PASSPHRASE")
        entered = getpass.getpass("Signing key passphrase: ").encode("utf-8")
        key = serialization.load_pem_private_key(path.read_bytes(), password=entered)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("signing key must be an Ed25519 private key")
    return key


def sign_archive(archive_path: str | Path, output_path: str | Path,
                 private_key_path: str | Path, publisher_id: str,
                 *, passphrase: bytes | None = None) -> dict[str, Any]:
    """Add a detached signature.json entry; content files remain byte-identical."""
    source = Path(archive_path).resolve()
    target = Path(output_path).resolve()
    key = _load_private_key(Path(private_key_path), passphrase)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(source, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if names.count("manifest.json") != 1 or SIGNATURE_NAME in names:
                raise ValueError("archive must contain one manifest.json and no existing signature")
            manifest_bytes = archive.read("manifest.json")
            manifest = json.loads(manifest_bytes)
            if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
                raise ValueError("invalid resource pack manifest")
            signature_data = make_signature(manifest_bytes, key, publisher_id)
            with zipfile.ZipFile(temporary, "w", allowZip64=True) as signed:
                for info in infos:
                    copied = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                    copied.compress_type = info.compress_type
                    copied.create_system = info.create_system
                    copied.external_attr = info.external_attr
                    copied.flag_bits = info.flag_bits
                    with archive.open(info, "r") as src, signed.open(copied, "w") as dst:
                        shutil.copyfileobj(src, dst, length=1024 * 1024)
                sig_info = zipfile.ZipInfo(SIGNATURE_NAME, date_time=(1980, 1, 1, 0, 0, 0))
                sig_info.compress_type = zipfile.ZIP_DEFLATED
                sig_info.create_system = 3
                sig_info.external_attr = 0o100644 << 16
                signed.writestr(sig_info, json.dumps(
                    signature_data, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":")).encode("utf-8"))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"archive": str(target), "publisherId": publisher_id,
            "keyId": signature_data["keyId"],
            "manifestSha256": signature_data["manifestSha256"]}


def _load_public_key(path: Path) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("publisher key must be an Ed25519 public key")
    return key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sign = sub.add_parser("sign", help="sign a pack manifest with an Ed25519 key")
    sign.add_argument("archive", type=Path)
    sign.add_argument("output", type=Path)
    sign.add_argument("--private-key", required=True, type=Path)
    sign.add_argument("--publisher-id", required=True)
    trust = sub.add_parser("trust", help="trust a publisher after checking its fingerprint out of band")
    trust.add_argument("--media-dir", required=True, type=Path)
    trust.add_argument("--publisher-id", required=True)
    trust.add_argument("--public-key", required=True, type=Path)
    trust.add_argument("--fingerprint", required=True)
    verify = sub.add_parser("verify", help="verify an archive against the local publisher trust store")
    verify.add_argument("archive", type=Path)
    verify.add_argument("--media-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "sign":
        secret = os.environ.get("CUTVOKE_RESOURCE_PACK_KEY_PASSPHRASE")
        result = sign_archive(args.archive, args.output, args.private_key,
                              args.publisher_id,
                              passphrase=secret.encode("utf-8") if secret else None)
    elif args.command == "trust":
        result = add_trusted_publisher(
            args.media_dir, args.publisher_id, _load_public_key(args.public_key),
            expected_fingerprint=args.fingerprint)
    else:
        with zipfile.ZipFile(args.archive, "r") as archive:
            if archive.namelist().count("manifest.json") != 1:
                raise ValueError("archive must contain exactly one manifest.json")
            manifest_bytes = archive.read("manifest.json")
            raw_signature = (archive.read(SIGNATURE_NAME)
                             if SIGNATURE_NAME in archive.namelist() else None)
        result = verify_signature(manifest_bytes, read_signature(raw_signature),
                                  load_trusted_publishers(args.media_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
