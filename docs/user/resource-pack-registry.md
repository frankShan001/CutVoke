# Signed resource registry

CutVoke can read static JSON catalogs over HTTPS and download resource-pack ZIP files listed in those catalogs. A catalog is authenticated independently from each pack; trusting the catalog publisher does not silently trust or enable a pack. Pack downloads are checked against the catalog's exact size and SHA-256, then passed through the normal archive manifest, file hash, and signature validation. A downloaded pack stays installed but inactive until the user explicitly enables it.

## Catalog format

The JSON document has a canonicalized payload and a detached Ed25519 signature:

```json
{
  "payload": {
    "schemaVersion": 1,
    "registryId": "publisher.resources",
    "displayName": "Publisher resources",
    "packages": [
      {
        "packId": "publisher.effects",
        "version": "1.0.0",
        "name": "Effects 1.0",
        "url": "./publisher-effects-1.0.0.zip",
        "sha256": "64 lowercase hexadecimal characters",
        "sizeBytes": 123456
      }
    ]
  },
  "signature": {
    "schemaVersion": 1,
    "algorithm": "Ed25519",
    "publisherId": "publisher-name",
    "keyId": "SHA-256 of the raw Ed25519 public key",
    "manifestSha256": "SHA-256 of canonical payload JSON",
    "signature": "base64 Ed25519 signature"
  }
}
```

Canonical payload JSON is UTF-8 with sorted object keys, no insignificant whitespace, and unescaped Unicode. Package URLs may be relative to the catalog, but the resolved URL must use HTTPS. Every HTTP redirect is checked against the same rule before it is followed. Local loopback HTTP is accepted only to support local development. The client allows up to 32 configured catalogs, 2,000 packages per catalog, a 2 MiB catalog document, and a 128 MiB compressed package.

## Build and publish

Keep the publisher's Ed25519 private key outside the repository and deployment directory. Generate a catalog from the exact ZIP files that will be hosted:

```powershell
uv run python tools/build/build_resource_pack_registry.py `
  --registry-id cutvoke.resources `
  --display-name "CutVoke Resource Packs" `
  --publisher-id cutvoke-official `
  --private-key C:\secure\cutvoke-registry-ed25519.pem `
  --output .\dist\resources.json `
  --package cutvoke.builtin-resources 1.13.0 "CutVoke built-in resources" `
    https://downloads.example.com/cutvoke-builtin-resources-1.13.0.zip `
    .\.artifacts\resource-packs\cutvoke-builtin-resources-1.13.0.zip
```

Upload the ZIP and the generated catalog to static HTTPS hosting. Publish the Ed25519 public key and its SHA-256 fingerprint through a separate trusted channel, such as a signed release or the publisher's authenticated website. Users add the catalog URL, compare the displayed fingerprint with that independent record, then import the public key to trust the catalog publisher. The installed resource pack's own signature is shown and governed separately.

The current repository does not configure a production publisher key, public catalog URL, hosting account, or release automation. The example above is a deployment shape, not an assertion that `example.com` or the publisher identity is live.

## Client behavior

- Registry sources and their last successful catalog are stored under the media directory at `.resource-packs/registries.json`.
- Refresh sends `If-None-Match` when the source provides an ETag. A failed refresh preserves the last good catalog and shows the error.
- Package bytes are stored temporarily under `.resource-packs/downloads/` and checked against the signed catalog's size and SHA-256 before installation.
- A retry resumes only when the prior request recorded a strong ETag; it uses `Range` and `If-Range`. If the server changes the ETag, ignores the range, returns an invalid range, or the final digest differs, the client rejects the result.
- Removing a catalog only removes that source from the list. Installed packages remain available for explicit activation or rollback.
- Catalog signature trust and resource-pack signature trust are separate decisions. Unknown catalog publishers are labeled. Downloaded packages are not activated automatically.

Backend tests use an actual loopback HTTP server to verify HTTPS policy, signature trust state, invalid signed-payload rejection, interrupted transfer resume, final digest rejection, and no automatic activation. These local tests do not establish the official publisher's identity, public availability, license provenance, or production CDN behavior.

## Built-in resource pack

`tools/build/build_builtin_resource_pack.py` assembles the offline built-in pack.
Its current version, resource inventory, sizes and SHA-256 values are recorded
in `src/cutvoke/catalogs/builtin_resource_pack.json`. Validate checked-in assets
with `python tools/build/build_builtin_resource_pack.py --check`; use `--help`
for archive generation options and write temporary packages under `.artifacts/`.

Background entries use resource kind `background` and include a stable resource
ID, name, version, source, license and media path under `assets/backgrounds/`.
At service startup, validated built-in and active-pack backgrounds are copied
into an immutable versioned cache and registered in the media library. The
resource-pack summary exposes `backgroundCount` alongside preset and sticker counts.
