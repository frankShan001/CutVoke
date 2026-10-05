"""Signed resource catalogs and resumable, hash-pinned resource-pack downloads."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .resource_pack import install_resource_pack_archive
from .resource_pack_signing import (
    load_trusted_publishers,
    make_signature,
    verify_signature,
)


REGISTRY_STATE_NAME = "registries.json"
_REGISTRY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$", re.IGNORECASE)
_MAX_CATALOG_BYTES = 2 * 1024 * 1024
_MAX_CATALOGS = 32
_MAX_PACKAGES_PER_CATALOG = 2000
_MAX_REMOTE_ARCHIVE_BYTES = 128 * 1024 * 1024
_DOWNLOAD_LOCKS: dict[str, threading.Lock] = {}
_DOWNLOAD_LOCKS_GUARD = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_registry_payload(payload: dict[str, Any]) -> bytes:
    """Return the stable UTF-8 representation that registry signatures cover."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sign_registry_payload(payload: dict[str, Any], private_key: Any,
                           publisher_id: str) -> dict[str, Any]:
    """Build a detached-signature document suitable for static HTTPS hosting."""
    return {
        "payload": payload,
        "signature": make_signature(canonical_registry_payload(payload),
                                    private_key, publisher_id),
    }


def _state_path(media_dir: str | Path) -> Path:
    return Path(media_dir).resolve() / ".resource-packs" / REGISTRY_STATE_NAME


def _read_state(media_dir: str | Path) -> dict[str, Any]:
    path = _state_path(media_dir)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schemaVersion": 1, "registries": []}
    except (OSError, ValueError) as error:
        raise ValueError(f"resource registry state cannot be read: {error}") from error
    if (not isinstance(value, dict) or value.get("schemaVersion") != 1 or
            not isinstance(value.get("registries"), list)):
        raise ValueError("invalid resource registry state")
    return value


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


def _validate_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("resource registry URL must be a string no longer than 2048 characters")
    parsed = urllib.parse.urlsplit(value.strip())
    if (parsed.scheme.lower() not in ("https", "http") or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or
            parsed.fragment):
        raise ValueError("resource registry URLs must be absolute HTTPS URLs without credentials or fragments")
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("resource registry URL has an invalid port") from error
    host = parsed.hostname.lower().rstrip(".")
    loopback = host == "localhost" or host.endswith(".localhost")
    if not loopback:
        try:
            import ipaddress
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
    if parsed.scheme.lower() != "https" and not loopback:
        raise ValueError("resource registry and package URLs must use HTTPS")
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/",
                                    parsed.query, ""))


class _ValidatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Apply the same scheme and URL policy before following every redirect."""

    def redirect_request(self, request, response, code, message, headers, new_url):
        target = urllib.parse.urljoin(request.full_url, new_url)
        _validate_url(target)
        return super().redirect_request(request, response, code, message, headers, target)


def _open_url(request: urllib.request.Request, *, timeout: int):
    opener = urllib.request.build_opener(_ValidatedRedirectHandler())
    return opener.open(request, timeout=timeout)


def _read_limited(response, limit: int) -> bytes:
    length = response.headers.get("Content-Length")
    if length is not None:
        try:
            if int(length) > limit:
                raise ValueError("remote resource registry is too large")
        except ValueError as error:
            if str(error) == "remote resource registry is too large":
                raise
            raise ValueError("remote response has an invalid Content-Length") from error
    data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("remote resource registry is too large")
    return data


def _fetch_catalog(url: str, *, etag: str = "", cached: dict[str, Any] | None = None
                   ) -> tuple[dict[str, Any] | None, str, str]:
    headers = {"Accept": "application/json"}
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        response = _open_url(request, timeout=20)
    except urllib.error.HTTPError as error:
        if error.code == 304 and cached is not None:
            return None, etag, ""
        raise ValueError(f"resource registry request failed with HTTP {error.code}") from error
    except (OSError, TimeoutError, urllib.error.URLError) as error:
        raise ValueError(f"resource registry request failed: {error}") from error
    with response:
        _validate_url(response.geturl())
        if response.status != 200:
            raise ValueError(f"resource registry request returned HTTP {response.status}")
        raw = _read_limited(response, _MAX_CATALOG_BYTES)
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError("resource registry is not valid UTF-8 JSON") from error
        if not isinstance(document, dict):
            raise ValueError("resource registry document must be an object")
        return document, response.headers.get("ETag", ""), response.headers.get("Last-Modified", "")


def _validate_catalog(document: dict[str, Any], catalog_url: str,
                      media_dir: str | Path) -> dict[str, Any]:
    payload = document.get("payload")
    signature = document.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature, dict):
        raise ValueError("resource registry must include a signed payload and signature")
    if (payload.get("schemaVersion") != 1 or
            not isinstance(payload.get("registryId"), str) or
            not _REGISTRY_ID.fullmatch(payload["registryId"]) or
            not isinstance(payload.get("displayName"), str) or
            not 1 <= len(payload["displayName"].strip()) <= 128 or
            not isinstance(payload.get("packages"), list) or
            len(payload["packages"]) > _MAX_PACKAGES_PER_CATALOG):
        raise ValueError("invalid resource registry payload")

    seen: set[tuple[str, str]] = set()
    packages: list[dict[str, Any]] = []
    for entry in payload["packages"]:
        if not isinstance(entry, dict):
            raise ValueError("invalid resource registry package entry")
        pack_id, version = entry.get("packId"), entry.get("version")
        digest, size = entry.get("sha256"), entry.get("sizeBytes")
        if (not isinstance(pack_id, str) or not _REGISTRY_ID.fullmatch(pack_id) or
                not isinstance(version, str) or not _REGISTRY_ID.fullmatch(version) or
                not isinstance(digest, str) or not _SHA256.fullmatch(digest) or
                isinstance(size, bool) or not isinstance(size, int) or
                not 0 < size <= _MAX_REMOTE_ARCHIVE_BYTES):
            raise ValueError("invalid resource registry package identity, hash, or size")
        identity = (pack_id, version)
        if identity in seen:
            raise ValueError("duplicate package version in resource registry")
        seen.add(identity)
        relative_url = entry.get("url")
        if not isinstance(relative_url, str) or not relative_url.strip():
            raise ValueError("resource registry package URL must be a non-empty string")
        archive_url = _validate_url(urllib.parse.urljoin(catalog_url, relative_url))
        name = entry.get("name", f"{pack_id} v{version}")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
            raise ValueError("invalid resource registry package name")
        packages.append({
            "packId": pack_id,
            "version": version,
            "name": name,
            "url": archive_url,
            "sha256": digest,
            "sizeBytes": size,
        })

    publisher = verify_signature(canonical_registry_payload(payload), signature,
                                 load_trusted_publishers(media_dir))
    return {
        "registryId": payload["registryId"],
        "displayName": payload["displayName"].strip(),
        "publisherId": publisher["publisherId"],
        "keyId": publisher["keyId"],
        "fingerprint": publisher["fingerprint"],
        "signatureStatus": publisher["signatureStatus"],
        "publisherVerified": publisher["publisherVerified"],
        "packages": packages,
    }


def _public_registry(source: dict[str, Any], media_dir: str | Path) -> dict[str, Any]:
    document = source.get("document")
    if not isinstance(document, dict):
        return {key: value for key, value in source.items() if key != "document"}
    catalog = _validate_catalog(document, source["url"], media_dir)
    return {
        **catalog,
        "url": source["url"],
        "lastCheckedAt": source.get("lastCheckedAt", ""),
        "etag": source.get("etag", ""),
        "lastModified": source.get("lastModified", ""),
        "lastError": source.get("lastError", ""),
    }


def list_registries(media_dir: str | Path) -> dict[str, Any]:
    state = _read_state(media_dir)
    return {"registries": [_public_registry(source, media_dir)
                           for source in state["registries"]]}


def add_registry(media_dir: str | Path, url: str) -> dict[str, Any]:
    """Fetch and pin one signed registry URL in the user's local source list."""
    url = _validate_url(url)
    state = _read_state(media_dir)
    existing = next((item for item in state["registries"] if item.get("url") == url), None)
    document, etag, modified = _fetch_catalog(
        url, etag=(existing or {}).get("etag", ""),
        cached=(existing or {}).get("document"))
    if document is None and (existing is None or not isinstance(existing.get("document"), dict)):
        raise ValueError("resource registry returned not-modified without a cached catalog")
    if document is not None:
        _validate_catalog(document, url, media_dir)
    source = {
        "url": url,
        "document": document if document is not None else existing["document"],
        "etag": etag or (existing or {}).get("etag", ""),
        "lastModified": modified or (existing or {}).get("lastModified", ""),
        "lastCheckedAt": _now(),
        "lastError": "",
    }
    registries = [item for item in state["registries"] if item.get("url") != url]
    if len(registries) >= _MAX_CATALOGS:
        raise ValueError(f"at most {_MAX_CATALOGS} resource registries may be configured")
    registries.append(source)
    state["registries"] = registries
    _write_state(media_dir, state)
    return _public_registry(source, media_dir)


def refresh_registries(media_dir: str | Path, url: str | None = None) -> dict[str, Any]:
    """Refresh one configured source or all sources using conditional HTTP requests."""
    state = _read_state(media_dir)
    sources = state["registries"]
    if url is not None:
        normalized = _validate_url(url)
        sources = [item for item in sources if item.get("url") == normalized]
        if not sources:
            raise ValueError("resource registry URL is not configured")
    refreshed = []
    for original in sources:
        source = dict(original)
        try:
            document, etag, modified = _fetch_catalog(
                source["url"], etag=source.get("etag", ""), cached=source.get("document"))
            if document is not None:
                _validate_catalog(document, source["url"], media_dir)
                source["document"] = document
            source["etag"] = etag or source.get("etag", "")
            source["lastModified"] = modified or source.get("lastModified", "")
            source["lastCheckedAt"] = _now()
            source["lastError"] = ""
            state["registries"] = [source if item.get("url") == source["url"] else item
                                   for item in state["registries"]]
            _write_state(media_dir, state)
            refreshed.append(_public_registry(source, media_dir))
        except (OSError, ValueError, urllib.error.URLError) as error:
            source["lastError"] = str(error)
            source["lastCheckedAt"] = _now()
            state["registries"] = [source if item.get("url") == source["url"] else item
                                   for item in state["registries"]]
            _write_state(media_dir, state)
            refreshed.append(_public_registry(source, media_dir))
    return {"registries": refreshed}


def remove_registry(media_dir: str | Path, url: str) -> dict[str, Any]:
    normalized = _validate_url(url)
    state = _read_state(media_dir)
    before = len(state["registries"])
    state["registries"] = [item for item in state["registries"]
                           if item.get("url") != normalized]
    if len(state["registries"]) == before:
        raise KeyError("resource registry URL is not configured")
    _write_state(media_dir, state)
    return list_registries(media_dir)


def _part_paths(media_dir: str | Path, digest: str) -> tuple[Path, Path]:
    root = Path(media_dir).resolve() / ".resource-packs" / "downloads"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{digest}.part", root / f"{digest}.json"


def _download_lock(digest: str) -> threading.Lock:
    with _DOWNLOAD_LOCKS_GUARD:
        return _DOWNLOAD_LOCKS.setdefault(digest, threading.Lock())


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _content_range_start(value: str | None) -> tuple[int, int] | None:
    match = _CONTENT_RANGE.fullmatch(value or "")
    if match is None:
        return None
    return int(match.group(1)), int(match.group(3))


def _download_archive(media_dir: str | Path, url: str, digest: str, size: int) -> tuple[bytes, int]:
    url = _validate_url(url)
    if not _SHA256.fullmatch(digest) or not 0 < size <= _MAX_REMOTE_ARCHIVE_BYTES:
        raise ValueError("remote resource package hash or size is invalid")
    part, metadata_path = _part_paths(media_dir, digest)
    with _download_lock(digest):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            metadata = {}
        if metadata.get("url") != url or metadata.get("sizeBytes") != size:
            part.unlink(missing_ok=True)
            metadata = {}
        if part.is_file() and part.stat().st_size == size:
            if _hash_file(part) == digest:
                return part.read_bytes(), size
            part.unlink(missing_ok=True)
        offset = part.stat().st_size if part.is_file() else 0
        etag = metadata.get("etag", "")
        if (offset and (not isinstance(etag, str) or not etag or etag.startswith("W/"))):
            part.unlink(missing_ok=True)
            offset = 0

        headers = {"Accept": "application/zip, application/octet-stream"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            headers["If-Range"] = etag
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            response = _open_url(request, timeout=30)
        except urllib.error.HTTPError as error:
            if error.code == 416 and offset == size and part.is_file() and _hash_file(part) == digest:
                return part.read_bytes(), offset
            raise ValueError(f"resource package download failed with HTTP {error.code}") from error
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            raise ValueError(f"resource package download failed: {error}") from error

        with response:
            _validate_url(response.geturl())
            status = response.status
            response_etag = response.headers.get("ETag", "")
            append = False
            if offset and status == 206:
                range_start = _content_range_start(response.headers.get("Content-Range"))
                append = (range_start is not None and range_start == (offset, size)
                          and response_etag == etag)
                if not append:
                    part.unlink(missing_ok=True)
                    raise ValueError("server returned an invalid or changed range; retry the package download")
            elif status == 200:
                offset = 0
            elif status == 206:
                range_start = _content_range_start(response.headers.get("Content-Range"))
                if range_start != (0, size):
                    raise ValueError("server returned an invalid package byte range")
            else:
                raise ValueError(f"resource package download returned HTTP {status}")

            returned_length = response.headers.get("Content-Length")
            if returned_length is not None:
                expected_length = size - offset
                try:
                    if int(returned_length) != expected_length:
                        raise ValueError("server package length does not match signed registry metadata")
                except ValueError as error:
                    if str(error) == "server package length does not match signed registry metadata":
                        raise
                    raise ValueError("server returned an invalid package Content-Length") from error

            metadata = {"url": url, "sizeBytes": size,
                        "etag": response_etag if response_etag and not response_etag.startswith("W/") else ""}
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
            downloaded_before = offset
            mode = "ab" if append else "wb"
            try:
                with part.open(mode) as output:
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        output.write(block)
                        downloaded = output.tell()
                        if downloaded > size:
                            raise ValueError("server package exceeds signed registry size")
                    output.flush()
                    os.fsync(output.fileno())
            except http.client.IncompleteRead as error:
                if error.partial:
                    with part.open("ab" if append else "wb") as output:
                        output.write(error.partial)
                        output.flush()
                        os.fsync(output.fileno())
                raise ValueError("package download was interrupted; retry to resume") from error
            received = part.stat().st_size
            if received != size:
                raise ValueError(
                    f"package download is incomplete ({received}/{size} bytes); retry to resume")
            if _hash_file(part) != digest:
                part.unlink(missing_ok=True)
                metadata_path.unlink(missing_ok=True)
                raise ValueError("downloaded resource package SHA-256 does not match the signed registry")
            return part.read_bytes(), received - downloaded_before


def download_registry_package(media_dir: str | Path, registry_url: str,
                              pack_id: str, version: str) -> dict[str, Any]:
    registry_url = _validate_url(registry_url)
    state = _read_state(media_dir)
    source = next((item for item in state["registries"]
                   if item.get("url") == registry_url), None)
    if source is None:
        raise KeyError("resource registry URL is not configured")
    registry = _public_registry(source, media_dir)
    package = next((item for item in registry["packages"]
                    if item["packId"] == pack_id and item["version"] == version), None)
    if package is None:
        raise KeyError("resource package is not listed in this registry")
    raw, bytes_received = _download_archive(media_dir, package["url"],
                                            package["sha256"], package["sizeBytes"])
    from .resource_pack import install_resource_pack_archive
    installed = install_resource_pack_archive(raw, media_dir)
    part, metadata = _part_paths(media_dir, package["sha256"])
    part.unlink(missing_ok=True)
    metadata.unlink(missing_ok=True)
    return {"installed": installed, "bytesReceived": bytes_received,
            "registryId": registry["registryId"],
            "catalogPublisherId": registry["publisherId"],
            "catalogSignatureStatus": registry["signatureStatus"]}
