"""Build an offline MCPB candidate from an extracted, qualified portable runtime.

No downloads, installs, Registry metadata, release uploads or code execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import zipfile

PLATFORMS = {"windows-x64": "win32", "linux-x64": "linux",
             "macos-x64": "darwin", "macos-arm64": "darwin"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else _digest(stream)


def _digest(stream) -> str:
    value = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        value.update(block)
    return value.hexdigest()


def safe_name(name: str) -> None:
    if (not name or "\\" in name or ":" in name or "\x00" in name
            or PurePosixPath(name).is_absolute()
            or any(part in {"", ".", ".."} for part in name.split("/"))):
        raise ValueError(f"Unsafe package path: {name!r}")


def build(staging: Path, output: Path) -> dict:
    staging = staging.resolve(strict=True)
    output = output.absolute()
    if output.suffix != ".mcpb":
        raise ValueError("Output must end in .mcpb")
    if output.resolve().is_relative_to(staging):
        raise ValueError("Output must be outside the portable staging tree")
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    # Metadata must not redirect reads outside the input directory.
    for name in ("build-info.json", "file-manifest.json"):
        if (staging / name).is_symlink():
            raise ValueError(f"Symlinked metadata: {name}")
    info = json.loads((staging / "build-info.json").read_text("utf-8"))
    hashes = json.loads((staging / "file-manifest.json").read_text("utf-8"))
    platform = info["platform"]
    if platform not in PLATFORMS:
        raise ValueError(f"Unsupported native platform: {platform}")
    version = info["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", version):
        raise ValueError("Invalid version")
    if not re.fullmatch(r"[0-9a-f]{40}", info["commit"]):
        raise ValueError("Missing full source commit")
    for name, expected in hashes.items():
        safe_name(name)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"Invalid checksum: {name}")
    prefix = "CutVoke.app/Contents/Resources/engine" if platform.startswith("macos-") else "resources/engine"
    suffix = ".exe" if platform.startswith("windows-") else ""
    entry = f"engine/cutvoke-engine{suffix}"
    required = [f"{prefix}/cutvoke-engine{suffix}", f"{prefix}/_internal/bin/ffmpeg{suffix}",
                f"{prefix}/_internal/bin/ffprobe{suffix}", "LICENSE.txt", "build-info.json",
                "licenses/runtime-components/provenance.json", "licenses/ffmpeg/Corresponding-Source.zip",
                "licenses/ffmpeg-sources.json"]
    for name in required:
        if not (staging / name).is_file():
            raise ValueError(f"Missing native payload: {name}")
    for name in ("licenses/python", "licenses/fonts", "licenses/runtime-components", "licenses/ffmpeg"):
        if not (staging / name).is_dir() or not any((staging / name).iterdir()):
            raise ValueError(f"Missing license tree: {name}")
    if not any((staging / "licenses").glob("Python-LICENSE.*")):
        raise ValueError("Missing Python license")
    sources = [(staging / prefix, "engine"), (staging / "licenses", "licenses"),
               (staging / "LICENSE.txt", "LICENSE.txt"), (staging / "build-info.json", "build-info.json")]
    # Catch deleted dependencies too, not merely corrupt files that still exist.
    for name in hashes:
        if name.startswith((prefix + "/", "licenses/")) or name in {"LICENSE.txt", "build-info.json"}:
            if not (staging / name).is_file():
                raise ValueError(f"Missing manifested payload: {name}")
    entries = []
    for source, destination in sources:
        if source.resolve(strict=True) != source:
            raise ValueError(f"Symlinked payload root: {source.name}")
        for path in [source, *sorted(source.rglob("*"))] if source.is_dir() else [source]:
            name = destination if path == source else f"{destination}/{path.relative_to(source).as_posix()}"
            safe_name(name)
            mode = path.lstat().st_mode
            read_path = path
            if path.is_symlink():
                target = os.readlink(path)
                # Materialize verified file links: official MCPB unpack does not restore links.
                if os.path.isabs(target) or "\\" in target or ":" in target:
                    raise ValueError(f"Unsafe symlink: {name}")
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(source):
                    raise ValueError(f"Escaping symlink: {name}")
                if not resolved.is_file():
                    raise ValueError(f"Directory or special symlink unsupported: {name}")
                read_path = resolved
                mode = resolved.stat().st_mode
            elif not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError(f"Special file: {name}")
            if path.is_file():
                relative = path.relative_to(staging).as_posix()
                if hashes.get(relative) != digest(read_path):
                    raise ValueError(f"Unverified file: {relative}")
            entries.append((read_path, name, mode, hashes.get(path.relative_to(staging).as_posix())))
    if suffix == "":
        for name in required[:3]:
            if not (staging / name).stat().st_mode & 0o111:
                raise ValueError(f"Native executable lacks execute mode: {name}")
    manifest = {"manifest_version": "0.3", "name": "cutvoke", "version": version,
                "description": f"CutVoke native editing MCP server ({platform}); offline candidate.",
                "author": {"name": "CutVoke contributors"},
                "server": {"type": "binary", "entry_point": entry,
                           "mcp_config": {"command": "${__dirname}/" + entry,
                                          "args": ["mcp", "--data", "${user_config.data_directory}"]}},
                "compatibility": {"platforms": [PLATFORMS[platform]]},
                "user_config": {"data_directory": {"type": "directory", "title": "CutVoke data directory",
                    "description": "Select persistent storage outside the extension installation directory. This is storage, not an access sandbox.",
                    "required": True}}}
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".mcpb-", dir=output.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path, name, mode, expected_hash in entries:
                # Official MCPB CLI creates parents from file paths and treats every
                # ZIP entry as a file. Explicit directory entries fail with EISDIR.
                if stat.S_ISDIR(mode):
                    continue
                item = zipfile.ZipInfo(name + ("/" if stat.S_ISDIR(mode) else ""), (1980, 1, 1, 0, 0, 0))
                item.create_system = 3
                # Preserve only ordinary rwx permissions, never setuid/setgid/sticky bits.
                item.external_attr = (stat.S_IFMT(mode) | (mode & 0o777)) << 16
                item.compress_type = zipfile.ZIP_DEFLATED
                with archive.open(item, "w", force_zip64=True) as writer:
                    if stat.S_ISREG(mode):
                        written = hashlib.sha256()
                        with path.open("rb") as reader:
                            for block in iter(lambda: reader.read(1024 * 1024), b""):
                                writer.write(block)
                                written.update(block)
                        if written.hexdigest() != expected_hash:
                            raise ValueError(f"Input changed during packaging: {name}")
            item = zipfile.ZipInfo("manifest.json", (1980, 1, 1, 0, 0, 0))
            item.create_system = 3
            item.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(item, json.dumps(manifest, indent=2) + "\n")
        checksum = digest(Path(temporary))
        # Atomic no-clobber publication on the destination filesystem.
        os.link(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"path": str(output), "sha256": checksum, "platform": platform,
            "commit": info["commit"], "version": version, "runtime_verified": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("staging", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.staging, args.output), indent=2))


if __name__ == "__main__":
    main()
