"""Deterministic legacy-limit coverage, also run under real Windows Python."""
import builtins
from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import io
import os
from pathlib import Path
import threading
import tempfile

import pytest

from cutvoke.storage import resource_pack as rp


@pytest.fixture
def tmp_path():
    # pytest's descriptive per-test dirname would consume the boundary budget
    # itself on Windows; still use a unique, automatically cleaned temp root.
    with tempfile.TemporaryDirectory(prefix="cv-") as directory:
        # Windows TEMP may contain RUNNER~1; installation resolves it to the
        # full account name. Budget and assert against that same actual path.
        yield Path(directory).resolve()


def _length(path):
    return len(str(path).encode("utf-16-le")) // 2


def _bounded_creates(monkeypatch):
    """Model a legacy process, independently of the runner's registry/manifest.

    Cover high-level open and O_EXCL allocation so old copyfile and new fd
    copying are subject to the same constraint. Real Windows E2E is separate.
    """
    writes = []
    def check(path):
        if isinstance(path, int):
            return
        writes.append(Path(path))
        if _length(path) >= 260:
            error = OSError(errno.ENAMETOOLONG, "legacy path boundary", str(path))
            error.winerror = 206
            raise error
    original_open, original_io, original_os = builtins.open, io.open, os.open
    def opened(original):
        def call(file, mode="r", *args, **kwargs):
            if any(flag in mode for flag in "wax+"):
                check(file)
            return original(file, mode, *args, **kwargs)
        return call
    def os_open(file, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR):
            check(file)
        return original_os(file, flags, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", opened(original_open))
    monkeypatch.setattr(io, "open", opened(original_io))
    monkeypatch.setattr(os, "open", os_open)
    return writes


def _case(tmp_path, *, legacy_length=None):
    source = tmp_path / "source.png"
    source.write_bytes(b"verified image bytes")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    name = "texture_optical_" + "l" * 40 + ".png"
    root = tmp_path / "cache"
    suffix = Path("pack/1") / digest / "assets/stickers" / name
    if legacy_length is not None:
        padding = legacy_length - 37 - _length(root / suffix) - 1
        assert padding > 0
        root /= "d" * padding
    target = root / suffix
    kwargs = dict(pack_id="pack", pack_version="1", relative_path=f"assets/stickers/{name}",
                  expected_sha256=digest, expected_size=source.stat().st_size)
    return source, root, target, kwargs


@pytest.mark.parametrize("legacy_length", [259, 260, 261])
def test_long_basename_does_not_extend_temporary_path(tmp_path, monkeypatch, legacy_length):
    source, root, target, kwargs = _case(tmp_path, legacy_length=legacy_length)
    assert _length(target) + 37 == legacy_length
    writes = _bounded_creates(monkeypatch)
    installed = rp.install_immutable_resource_file(source, root, **kwargs)
    assert installed == target
    assert installed.read_bytes() == source.read_bytes()
    assert writes and all(path.parent == target.parent for path in writes)
    assert all(len(path.name) <= 12 for path in writes)
    assert list(target.parent.iterdir()) == [target]


def test_short_install_is_idempotent_and_exclusively_reserves_temp(tmp_path, monkeypatch):
    source, root, target, kwargs = _case(tmp_path)
    original = rp.os.open
    reserved = []
    def opened(path, flags, *args, **kw):
        reserved.append((Path(path), flags))
        return original(path, flags, *args, **kw)
    monkeypatch.setattr(rp.os, "open", opened)
    rp.install_immutable_resource_file(source, root, **kwargs)
    assert len(reserved) == 1 and reserved[0][1] & os.O_EXCL
    assert reserved[0][0].parent == target.parent
    rp.install_immutable_resource_file(source, root, **kwargs)
    assert len(reserved) == 1


@pytest.mark.parametrize("iteration", range(10))
def test_concurrent_installers_use_independent_temps_and_atomic_verified_replace(tmp_path, monkeypatch, iteration):
    source, root, target, kwargs = _case(tmp_path)
    barrier = threading.Barrier(6, timeout=10)
    names = []
    original_temp, original_copy = rp.tempfile.mkstemp, rp.shutil.copyfileobj
    def allocate(*args, **kw):
        fd, name = original_temp(*args, **kw)
        names.append(name)
        return fd, name
    def copy(*args, **kw):
        barrier.wait()
        return original_copy(*args, **kw)
    monkeypatch.setattr(rp.tempfile, "mkstemp", allocate)
    monkeypatch.setattr(rp.shutil, "copyfileobj", copy)
    with ThreadPoolExecutor(max_workers=6) as pool:
        result = list(pool.map(lambda _: rp.install_immutable_resource_file(source, root, **kwargs), range(6)))
    assert result == [target] * 6
    assert len(set(names)) == 6
    assert target.read_bytes() == source.read_bytes()
    assert list(target.parent.iterdir()) == [target]


def test_verified_peer_publication_survives_windows_replace_conflict(tmp_path, monkeypatch):
    source, root, target, kwargs = _case(tmp_path)
    def competing_publish(temporary, destination):
        # Another process has published the exact bytes before our rename fails.
        Path(destination).write_bytes(Path(temporary).read_bytes())
        error = PermissionError(errno.EACCES, "concurrent rename", str(destination))
        error.winerror = 5
        raise error
    monkeypatch.setattr(rp.os, "replace", competing_publish)
    assert rp.install_immutable_resource_file(source, root, **kwargs) == target
    assert target.read_bytes() == source.read_bytes()
    assert list(target.parent.iterdir()) == [target]


def test_windows_sharing_conflict_retries_then_commits_verified_bytes(tmp_path, monkeypatch):
    source, root, target, kwargs = _case(tmp_path)
    original = rp.os.replace
    attempts = []
    def replace(src, dst):
        attempts.append(True)
        if len(attempts) < 3:
            error = OSError(errno.EACCES, "fixture sharing conflict", str(dst))
            error.winerror = 32
            raise error
        return original(src, dst)
    monkeypatch.setattr(rp.os, "replace", replace)
    assert rp.install_immutable_resource_file(source, root, **kwargs) == target
    assert len(attempts) == 3
    assert target.read_bytes() == source.read_bytes()
    assert list(target.parent.iterdir()) == [target]


def test_persistent_windows_denial_does_not_accept_corrupt_existing_file(tmp_path, monkeypatch):
    source, root, target, kwargs = _case(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing corrupt resource")
    attempts = []
    def denied(*args):
        attempts.append(True)
        error = PermissionError(errno.EACCES, "persistent denial", str(target))
        error.winerror = 5
        raise error
    monkeypatch.setattr(rp.os, "replace", denied)
    with pytest.raises(PermissionError) as caught:
        rp.install_immutable_resource_file(source, root, **kwargs)
    assert 1 < len(attempts) <= 10
    assert caught.value.installation_diagnostic["stage"] == "replace"
    assert caught.value.installation_diagnostic["winerror"] == 5
    assert target.read_bytes() == b"existing corrupt resource"
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize("stage", ["temporary_create", "copy", "copy_verify", "replace"])
def test_failures_preserve_stage_os_evidence_and_clean_temp(tmp_path, monkeypatch, stage):
    source, root, target, kwargs = _case(tmp_path)
    error = OSError(errno.EACCES, "fixture write denied", str(target))
    error.winerror = 5
    def fail(*args, **kw):
        raise error
    if stage == "temporary_create":
        monkeypatch.setattr(rp.tempfile, "mkstemp", fail)
    elif stage == "copy":
        monkeypatch.setattr(rp.shutil, "copyfileobj", fail)
    elif stage == "replace":
        monkeypatch.setattr(rp.os, "replace", fail)
    else:
        monkeypatch.setattr(rp.shutil, "copyfileobj", lambda source, output: output.write(b"corrupt copy"))
    with pytest.raises((OSError, ValueError)) as caught:
        rp.install_immutable_resource_file(source, root, **kwargs)
    diagnostic = caught.value.installation_diagnostic
    assert diagnostic["stage"] == stage
    assert diagnostic["destinationPathLength"] == _length(target)
    if stage != "copy_verify":
        assert caught.value is error
        assert diagnostic["errno"] == errno.EACCES and diagnostic["winerror"] == 5
    assert not target.exists()
    assert not list(root.rglob(".cv-*"))


def test_verification_read_error_keeps_original_os_failure(tmp_path, monkeypatch):
    source, root, target, kwargs = _case(tmp_path)
    original = Path.open
    def opened(path, mode="r", *args, **kw):
        if path.name.startswith(".cv-") and mode == "rb":
            error = PermissionError(errno.EACCES, "verification read denied", str(path))
            error.winerror = 5
            raise error
        return original(path, mode, *args, **kw)
    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises(PermissionError) as caught:
        rp.install_immutable_resource_file(source, root, **kwargs)
    assert caught.value.installation_diagnostic["stage"] == "copy_verify"
    assert caught.value.installation_diagnostic["winerror"] == 5
    assert not list(root.rglob(".cv-*"))


def test_final_path_over_limit_reports_replace_and_shorter_directory_remedy(tmp_path, monkeypatch):
    source, root, target, kwargs = _case(tmp_path, legacy_length=298)
    assert _length(target) == 261
    _bounded_creates(monkeypatch)
    def replace(src, dst):
        error = OSError(errno.ENAMETOOLONG, "final destination too long", str(dst))
        error.winerror = 206
        raise error
    monkeypatch.setattr(rp.os, "replace", replace)
    with pytest.raises(OSError) as caught:
        rp.install_immutable_resource_file(source, root, **kwargs)
    diagnostic = caught.value.installation_diagnostic
    assert diagnostic["stage"] == "replace"
    assert diagnostic["destinationPathLength"] == 261
    assert diagnostic["temporaryPathLength"] < 260
    assert diagnostic["winerror"] == 206
    assert "目标路径本身" in diagnostic["message"] and "缩短应用数据目录" in diagnostic["message"]
    assert not list(root.rglob(".cv-*"))


def test_cleanup_failure_does_not_replace_primary_error(tmp_path, monkeypatch):
    source, root, target, kwargs = _case(tmp_path)
    original_unlink = Path.unlink
    primary = OSError(errno.ENOSPC, "disk full", str(target))
    def replace(*args):
        raise primary
    def unlink(path, *args, **kw):
        if path.name.startswith(".cv-"):
            raise PermissionError(errno.EACCES, "cleanup denied", str(path))
        return original_unlink(path, *args, **kw)
    with monkeypatch.context() as patch:
        patch.setattr(rp.os, "replace", replace)
        patch.setattr(Path, "unlink", unlink)
        with pytest.raises(OSError) as caught:
            rp.install_immutable_resource_file(source, root, **kwargs)
    assert caught.value is primary
    assert primary.installation_diagnostic["stage"] == "replace"
    assert primary.installation_diagnostic["cleanupError"]["stage"] == "cleanup"
    for path in root.rglob(".cv-*"):
        path.unlink()


def test_fresh_database_installs_every_sticker_under_legacy_create_limit(tmp_path, monkeypatch):
    from cutvoke.editing.service import EditService
    from cutvoke.interfaces.httpapi import HttpApi
    from cutvoke.storage.store import ProjectStore
    manifest = rp.load_resource_pack_manifest()
    images = [f for f in manifest["files"] if f["path"].startswith("assets/stickers/") and f["path"].endswith(".png")]
    media = tmp_path / "media"
    def destination(record):
        return media / ".builtin-resources" / manifest["packId"] / manifest["version"] / record["sha256"] / record["path"]
    longest = max(_length(destination(f)) for f in images)
    media /= "d" * (240 - longest - 1)
    assert max(_length(destination(f)) for f in images) == 240
    assert sum(_length(destination(f)) + 37 >= 260 for f in images) > 0
    _bounded_creates(monkeypatch)
    store = ProjectStore(str(tmp_path / "fresh.sqlite"))
    api = None
    try:
        api = HttpApi(EditService(store=store), media_dir=str(media))
        status, response = api.handle("GET", "/api/v1/stickers", {})
        assert status == 200 and len(response["stickers"]) == 612
        assert not [(s["stickerId"], s["installationError"]) for s in response["stickers"] if not s["available"] or not s["previewAvailable"]]
        for sticker in response["stickers"]:
            if sticker["kind"] == "static":
                asset = store.get_asset(sticker["assetId"])
                assert hashlib.sha256(Path(asset["path"]).read_bytes()).hexdigest() == sticker["resourceRef"]["sha256"]
    finally:
        if api is not None:
            api.close()
        store.close()


def test_http_keeps_install_failure_evidence_and_clears_it_after_recovery(tmp_path, monkeypatch):
    from cutvoke.editing.service import EditService
    from cutvoke.interfaces.httpapi import HttpApi
    from cutvoke.storage.store import ProjectStore
    original = rp.os.replace
    def replace(source, destination):
        if Path(destination).name == "arrow_down.png":
            error = OSError(errno.ENAMETOOLONG, "fixture legacy process limit", str(destination))
            error.winerror = 206
            raise error
        return original(source, destination)
    store = ProjectStore(str(tmp_path / "fresh.sqlite"))
    api = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(rp.os, "replace", replace)
            api = HttpApi(EditService(store=store), media_dir=str(tmp_path / "media"))
        status, response = api.handle("GET", "/api/v1/stickers", {})
        item = next(s for s in response["stickers"] if s["assetId"] == "builtin_sticker_arrow_down")
        assert status == 200 and not item["available"]
        error = item["installationError"]
        assert error["stage"] == "replace" and error["winerror"] == 206
        assert error["errno"] == errno.ENAMETOOLONG
        assert error["destinationPathLength"] == _length(error["path"])
        assert "缩短应用数据目录" in item["availabilityMessage"]
        api._ensure_active_stickers()
        _, response = api.handle("GET", "/api/v1/stickers", {})
        item = next(s for s in response["stickers"] if s["assetId"] == "builtin_sticker_arrow_down")
        assert item["available"] and item["installationError"] is None
    finally:
        if api is not None:
            api.close()
        store.close()


@pytest.mark.parametrize("mode", ["serve", "mcp"])
@pytest.mark.parametrize("failed_install", [False, True])
def test_entrypoints_keep_immutable_paths_and_do_not_mask_install_errors(tmp_path, monkeypatch, mode, failed_install):
    from cutvoke.interfaces import cli, httpapi
    from cutvoke.interfaces.mcp_server import MCPServer
    from cutvoke.interfaces.security import SessionToken
    from cutvoke.storage import builtin_assets
    from cutvoke.rendering import gpu_effects
    monkeypatch.setattr(SessionToken, "is_enabled", lambda: False)
    monkeypatch.setattr(builtin_assets, "ensure_builtin_audio", lambda *args, **kw: [])
    monkeypatch.setattr(gpu_effects, "warmup", lambda *args: None)
    original = rp.os.replace
    def replace(source, destination):
        if failed_install and Path(destination).name == "arrow_down.png":
            error = OSError(errno.ENAMETOOLONG, "entrypoint install failure", str(destination))
            error.winerror = 206
            raise error
        return original(source, destination)
    monkeypatch.setattr(rp.os, "replace", replace)
    inspected = []
    def inspect(store):
        expected = tmp_path / "media/.builtin-resources"
        other = store.get_asset("builtin_sticker_arrow_up")
        assert Path(other["path"]).is_relative_to(expected)
        affected = store.get_asset("builtin_sticker_arrow_down")
        if failed_install:
            assert affected is None, "A failed immutable install must not fall back to mutable package files"
        else:
            assert Path(affected["path"]).is_relative_to(expected)
        inspected.append(True)
    def serve(api, **kw):
        inspect(api.service.store)
        _, catalog = api.handle("GET", "/api/v1/stickers", {})
        sticker = next(s for s in catalog["stickers"] if s["assetId"] == "builtin_sticker_arrow_down")
        assert sticker["available"] is not failed_install
        if failed_install:
            assert sticker["installationError"]["stage"] == "replace"
    monkeypatch.setattr(httpapi, "serve", serve)
    monkeypatch.setattr(MCPServer, "run_stdio", lambda self: inspect(self.service.store))
    args = cli.build_parser().parse_args([mode, "--data", str(tmp_path / "entrypoint.sqlite"), *(["--worker"] if mode == "mcp" else [])])
    assert (cli.cmd_serve(args) if mode == "serve" else cli.cmd_mcp(args)) == 0
    assert inspected == [True]
