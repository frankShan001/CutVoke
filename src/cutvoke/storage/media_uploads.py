"""Bounded-memory media staging and safe directory manifest matching."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import uuid


class UploadError(ValueError):
    def __init__(self, message, code="INVALID_UPLOAD", status=400):
        super().__init__(message)
        self.code, self.status = code, status


def relative_path(value: str) -> str:
    value = str(value).replace("\\", "/")
    path = PurePosixPath(value)
    if (not value or value == "." or "\0" in value or len(value) > 2048 or path.is_absolute() or ":" in value or
            any(part in ("..", "") for part in value.split("/"))):
        raise UploadError("素材相对路径无效", "INVALID_RELATIVE_PATH")
    return str(path)


def _owner_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if os.name == "nt":
        # Do not use os.kill(pid, 0) on Windows: this is a read-only process
        # query, with denied access conservatively treated as a live owner.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() not in (87, 1168)
        try:
            status = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(status)) or status.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def cleanup_uploads(directory: str) -> int:
    """Reap only owned staging names whose creating process has exited."""
    root = Path(directory).resolve()
    staging = root / ".uploads"
    if not staging.is_dir() or staging.resolve().parent != root:
        return 0
    removed = 0
    for path in staging.iterdir():
        match = re.fullmatch(r"(\d+)-[a-f0-9]{32}\.part[^/\\]*", path.name)
        if not match or _owner_alive(int(match[1])):
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass  # An inaccessible file is never a reason to affect projects.
    return removed


@dataclass
class StagedUpload:
    path: Path
    size: int
    sha256: str


@contextmanager
def receive_upload(stream, length: int, directory: str, filename: str):
    """Read at most 1 MiB at a time; every interruption removes staging data."""
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < length + 16 * 1024 * 1024:
        raise UploadError("磁盘空间不足，无法接收素材", "INSUFFICIENT_STORAGE", 507)
    staging = root / ".uploads"
    staging.mkdir(exist_ok=True)
    if staging.resolve().parent != root:
        raise UploadError("上传暂存目录越界", "INVALID_UPLOAD_DIRECTORY")
    suffix = Path(filename.replace("\\", "/")).suffix[:10].lower() or ".bin"
    path = staging / (f"{os.getpid()}-" + uuid.uuid4().hex + ".part" + suffix)
    digest = hashlib.sha256()
    try:
        remaining = length
        with path.open("xb") as target:
            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                if not block:
                    raise UploadError("素材上传已中断，临时文件已清理", "UPLOAD_INCOMPLETE")
                target.write(block)
                digest.update(block)
                remaining -= len(block)
            target.flush()
        yield StagedUpload(path, length, digest.hexdigest())
    finally:
        path.unlink(missing_ok=True)


def scan_relinks(assets, files):
    if not isinstance(files, list) or len(files) > 10000:
        raise UploadError("一次最多扫描 10000 个文件")
    entries, identifiers = [], set()
    for item in files:
        if not isinstance(item, dict):
            raise UploadError("文件清单格式无效")
        identifier = str(item.get("fileId", ""))
        if not identifier or identifier in identifiers:
            raise UploadError("文件标识缺失或重复")
        identifiers.add(identifier)
        name = relative_path(item.get("relativePath", item.get("name", "")))
        size = item.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise UploadError("文件大小无效")
        entries.append({"fileId": identifier, "relativePath": name, "size": size})
    results = []
    for asset in assets:
        if asset.get("builtin") or Path(asset["path"]).exists():
            continue
        name = asset.get("relativePath") or asset["name"]
        exact = [entry for entry in entries if entry["size"] == asset["size"] and
                 (entry["relativePath"] == name or entry["relativePath"].endswith("/" + name))]
        candidates = exact or [entry for entry in entries if entry["size"] == asset["size"] and
                              PurePosixPath(entry["relativePath"]).name == PurePosixPath(name).name]
        # A manifest only finds candidates. The upload later MUST match the
        # ledger's original SHA-256 before any project reference is restored.
        fingerprint = asset.get("sha256") or (
            asset["assetId"][6:] if re.fullmatch(r"asset_[a-f0-9]{64}", asset["assetId"]) else None)
        status = ("unverified" if not fingerprint else "missing" if not candidates else
                  "matched" if len(candidates) == 1 else "ambiguous")
        results.append({"assetId": asset["assetId"], "name": asset["name"], "size": asset["size"],
                        "expected": {key: asset.get(key) for key in ("relativePath", "kind", "duration", "width", "height")},
                        "status": status, "candidates": candidates})
    return {"items": results, "scannedFiles": len(entries), "identityCheck": "sha256-on-restore"}
