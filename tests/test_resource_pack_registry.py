from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from cutvoke.core.httpapi import HttpApi
from cutvoke.core.resource_pack import resource_pack_manager_status
from cutvoke.core.resource_pack_registry import (
    add_registry,
    canonical_registry_payload,
    download_registry_package,
    list_registries,
    refresh_registries,
    sign_registry_payload,
)
from cutvoke.core.resource_pack_signing import (
    add_trusted_publisher,
    load_trusted_publishers,
    verify_signature,
)
from cutvoke.core.service import EditService


def _small_pack_archive(path: Path) -> None:
    files = {
        "core/builtin_presets.json": json.dumps({"schemaVersion": 1, "presets": []}).encode(),
        "core/builtin_stickers.json": json.dumps({
            "schemaVersion": 1, "version": "1.0.0", "license": "CC0-1.0",
            "stickers": [{"stem": "sample", "name": "Sample", "subcategory": "decor",
                          "keywords": []}],
            "motionVariants": [],
        }).encode(),
        "assets/stickers/sample.png": b"sample-png",
        "assets/sticker_previews/sample.mp4": b"sample-preview",
        "assets/sticker_previews/sample.qa.json": b"{}",
        "assets/sticker_previews/sample.audit.json": b"{}",
        "assets/sticker_previews/sample.audit.mp4": b"sample-audit",
        "assets/sticker_previews/sample.visual.json": b"{}",
    }
    records = [{"path": name, "sizeBytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "mediaType": "application/octet-stream"}
               for name, content in sorted(files.items())]
    manifest = {
        "schemaVersion": 1,
        "packId": "test.resources",
        "version": "1.0.0",
        "resources": [{
            "resourceId": "cutvoke.sticker.sample", "kind": "sticker",
            "name": "Sample", "family": "sticker", "subcategory": "decor",
            "version": "1.0.0", "license": "CC0-1.0", "status": "candidate",
            "qualified": False, "downloadState": "bundled",
            "mediaFiles": [name for name in files if name.startswith("assets/")],
            "offlineAvailable": True,
        }],
        "files": records,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest).encode())
        for name, content in files.items():
            archive.writestr(name, content)


class _RegistryServer:
    def __init__(self, catalog: dict, archive: bytes, *, interrupt_once: bool = False):
        self.catalog = catalog
        self.archive = archive
        self.interrupt_once = interrupt_once
        self.package_requests: list[tuple[str, str]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                if self.path == "/redirect.json":
                    self.send_response(302)
                    self.send_header("Location", "http://example.com/resources.json")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == "/catalog.json":
                    body = json.dumps(owner.catalog).encode("utf-8")
                    etag = hashlib.sha256(body).hexdigest()
                    if self.headers.get("If-None-Match") == f'"{etag}"':
                        self.send_response(304)
                        self.send_header("ETag", f'"{etag}"')
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("ETag", f'"{etag}"')
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path != "/package.zip":
                    self.send_error(404)
                    return
                range_header = self.headers.get("Range", "")
                if_range = self.headers.get("If-Range", "")
                owner.package_requests.append((range_header, if_range))
                etag = '"resource-pack-v1"'
                start = 0
                if range_header.startswith("bytes=") and if_range == etag:
                    start = int(range_header.removeprefix("bytes=").split("-", 1)[0])
                    payload = owner.archive[start:]
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(owner.archive)-1}/{len(owner.archive)}")
                else:
                    payload = owner.archive
                    self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(owner.archive) - start))
                self.send_header("ETag", etag)
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                if owner.interrupt_once and not range_header:
                    owner.interrupt_once = False
                    self.wfile.write(payload[:max(1, len(payload) // 3)])
                    self.wfile.flush()
                    self.close_connection = True
                else:
                    self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/catalog.json"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=5)


class ResourcePackRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive_path = self.root / "resource-pack.zip"
        _small_pack_archive(self.archive_path)
        self.archive = self.archive_path.read_bytes()
        self.key = Ed25519PrivateKey.generate()
        self.payload = {
            "schemaVersion": 1,
            "registryId": "test.registry",
            "displayName": "Test Resource Registry",
            "packages": [{
                "packId": "test.resources",
                "version": "1.0.0",
                "name": "Test Pack",
                "url": "/package.zip",
                "sha256": hashlib.sha256(self.archive).hexdigest(),
                "sizeBytes": len(self.archive),
            }],
        }

    def tearDown(self):
        self.temporary.cleanup()

    def _server(self, *, interrupt_once=False, bad_digest=False):
        payload = json.loads(json.dumps(self.payload))
        if bad_digest:
            payload["packages"][0]["sha256"] = "0" * 64
        catalog = sign_registry_payload(payload, self.key, "test.publisher")
        return _RegistryServer(catalog, self.archive, interrupt_once=interrupt_once)

    def _trust_publisher(self, media_dir: Path):
        fingerprint = hashlib.sha256(
            self.key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )).hexdigest()
        add_trusted_publisher(media_dir, "test.publisher", self.key.public_key(),
                              expected_fingerprint=fingerprint)
        return fingerprint

    def test_registry_url_requires_https_except_loopback(self):
        from cutvoke.core.resource_pack_registry import add_registry

        with self.assertRaisesRegex(ValueError, "HTTPS"):
            add_registry(self.root / "media", "http://example.com/resources.json")
        server = self._server()
        self.addCleanup(server.close)
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            add_registry(self.root / "redirect-media", server.url.replace("catalog.json", "redirect.json"))

    def test_publisher_cli_builds_a_verifiable_catalog_from_the_archive(self):
        key_path = self.root / "publisher-ed25519.pem"
        key_path.write_bytes(self.key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        output_path = self.root / "catalog.json"
        script = Path(__file__).resolve().parents[1] / "scripts" / "build_resource_pack_registry.py"
        result = subprocess.run([
            sys.executable, str(script),
            "--registry-id", "test.registry",
            "--display-name", "Test Registry",
            "--publisher-id", "test.publisher",
            "--private-key", str(key_path),
            "--output", str(output_path),
            "--package", "test.resources", "1.0.0", "Test Pack",
            "https://downloads.example.com/test.zip", str(self.archive_path),
        ], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(output_path.read_text(encoding="utf-8"))
        record = document["payload"]["packages"][0]
        self.assertEqual(record["sizeBytes"], len(self.archive))
        self.assertEqual(record["sha256"], hashlib.sha256(self.archive).hexdigest())
        fingerprint = hashlib.sha256(self.key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )).hexdigest()
        media_dir = self.root / "trusted-media"
        add_trusted_publisher(media_dir, "test.publisher", self.key.public_key(),
                              expected_fingerprint=fingerprint)
        verified = verify_signature(canonical_registry_payload(document["payload"]),
                                    document["signature"],
                                    load_trusted_publishers(media_dir))
        self.assertEqual(verified["signatureStatus"], "verified")

    def test_signed_catalog_refresh_and_resumable_package_install(self):
        server = self._server(interrupt_once=True)
        self.addCleanup(server.close)
        media_dir = self.root / "media"

        registry = add_registry(media_dir, server.url)
        self.assertEqual(registry["signatureStatus"], "unknown_publisher")
        fingerprint = self._trust_publisher(media_dir)
        self.assertEqual(list_registries(media_dir)["registries"][0]["signatureStatus"], "verified")
        self.assertEqual(list_registries(media_dir)["registries"][0]["fingerprint"], fingerprint)

        api = HttpApi(EditService(), media_dir=str(media_dir))
        self.addCleanup(api.close)
        code, result = api.handle("GET", "/api/v1/resource-packs/registries", {})
        self.assertEqual(code, 200)
        self.assertEqual(result["registries"][0]["signatureStatus"], "verified")
        code, refreshed = api.handle("POST", "/api/v1/resource-packs/registries/refresh", {"url": server.url})
        self.assertEqual(code, 200)
        self.assertEqual(refreshed["registries"][0]["registryId"], "test.registry")
        code, added = api.handle("POST", "/api/v1/resource-packs/registries/add", {"url": server.url})
        self.assertEqual(code, 201)
        self.assertEqual(added["signatureStatus"], "verified")

        with self.assertRaisesRegex(ValueError, "interrupted|incomplete"):
            download_registry_package(media_dir, server.url,
                                      "test.resources", "1.0.0")
        part = media_dir / ".resource-packs" / "downloads" / f"{hashlib.sha256(self.archive).hexdigest()}.part"
        self.assertGreater(part.stat().st_size, 0)
        partial_size = part.stat().st_size

        downloaded = download_registry_package(media_dir, server.url,
                                               "test.resources", "1.0.0")
        self.assertEqual(downloaded["installed"]["packId"], "test.resources")
        self.assertGreater(downloaded["bytesReceived"], 0)
        self.assertEqual(server.package_requests[1][0], f"bytes={partial_size}-")
        self.assertEqual(server.package_requests[1][1], '"resource-pack-v1"')
        self.assertEqual(resource_pack_manager_status(media_dir)["active"]["packId"],
                         "cutvoke.builtin-resources")
        code, routed = api.handle("POST", "/api/v1/resource-packs/registries/download", {
            "url": server.url, "packId": "test.resources", "version": "1.0.0",
        })
        self.assertEqual(code, 201)
        self.assertEqual(routed["installed"]["packId"], "test.resources")
        code, removed = api.handle("POST", "/api/v1/resource-packs/registries/remove", {"url": server.url})
        self.assertEqual(code, 200)
        self.assertEqual(removed["registries"], [])

    def test_modified_signed_payload_is_rejected_and_hash_mismatch_is_not_installed(self):
        server = self._server(bad_digest=True)
        self.addCleanup(server.close)
        media_dir = self.root / "media"
        add_registry(media_dir, server.url)

        server.catalog["payload"]["displayName"] = "Tampered Catalog"
        refreshed = refresh_registries(media_dir, server.url)["registries"][0]
        self.assertIn("signature does not match", refreshed["lastError"])
        self.assertEqual(refreshed["displayName"], "Test Resource Registry")

        # Restore a correctly signed catalog whose archive digest is deliberately wrong.
        server.catalog = sign_registry_payload(
            json.loads(json.dumps(self.payload | {"packages": [
                {**self.payload["packages"][0], "sha256": "0" * 64}
            ]})), self.key, "test.publisher")
        refreshed = refresh_registries(media_dir, server.url)["registries"][0]
        self.assertEqual(refreshed["lastError"], "")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            download_registry_package(media_dir, server.url,
                                      "test.resources", "1.0.0")
        self.assertFalse((media_dir / ".resource-packs" / "test.resources" / "1.0.0").exists())


if __name__ == "__main__":
    unittest.main()
