"""Keep legacy Agent handoffs usable without serving arbitrary local files."""
import hashlib
from pathlib import Path
import pytest

from PIL import Image

from cutvoke.interfaces.agent_tools import AgentTools
from cutvoke.interfaces.httpapi import HttpApi
from cutvoke.interfaces.mcp_server import MCPServer
from cutvoke.editing.service import EditService
from cutvoke.storage.store import ProjectStore


def test_legacy_import_and_reimport_retain_saved_project_paths(tmp_path):
    store = ProjectStore(str(tmp_path / "projects.sqlite"))
    api = HttpApi(EditService(store))
    try:
        source = tmp_path / "original.png"
        Image.new("RGB", (64, 48), "blue").save(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        asset_id = "asset_" + digest
        legacy = tmp_path / "media" / "imports" / (digest + ".png")
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(source.read_bytes())
        asset = store.add_asset(asset_id=asset_id, path=str(legacy),
                                name="original.png", kind="image", size=source.stat().st_size,
                                has_video=True, has_audio=False, width=64, height=48)
        agent = AgentTools(api)
        assert api._asset_media_path(asset) == legacy
        assert agent.media_list({"query": "original.png"})["assets"][0]["available"]
        assert agent.media_import({"paths": [str(source)]})["assets"][0]["path"] == str(legacy)
        legacy.unlink()
        assert api._asset_media_path(asset) is None
        restored = agent.media_import({"paths": [str(source)]})["assets"][0]
        assert restored["path"] == str(legacy)
        assert legacy.read_bytes() == source.read_bytes()
        legacy.unlink()
        status, relinked = api.relink_asset(asset_id, "restored.png", source.read_bytes())
        assert status == 200, relinked
        assert relinked["path"] == str(legacy)
        assert api._asset_media_path(relinked) == legacy
    finally:
        api.close()
        store.close()


def test_media_layout_checks_reject_mismatched_ids_paths_and_symlinks(tmp_path):
    api = HttpApi(EditService(), media_dir=str(tmp_path / "media"))
    try:
        root = Path(api.media_dir)
        (root / "imports").mkdir()
        digest = "a" * 64
        asset_id = "asset_" + digest
        outside = tmp_path / "private.png"
        outside.write_bytes(b"private bytes")
        for path in (outside, root / "imports" / ("b" * 64 + ".png"),
                     root / "imports" / (asset_id + ".png"),
                     root / "other" / (digest + ".png")):
            assert api._imported_media_path({"assetId": asset_id, "path": str(path)}) is None
        linked = root / (asset_id + ".png")
        try:
            linked.symlink_to(outside)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314:
                pytest.skip('Windows symlink creation requires an unavailable privilege')
            raise
        assert api._asset_media_path({"assetId": asset_id, "path": str(linked)}) is None
        linked.unlink()
        legacy_link = root / "imports" / (digest + ".png")
        legacy_link.symlink_to(outside)
        assert api._asset_media_path({"assetId": asset_id, "path": str(legacy_link)}) is None
    finally:
        api.close()


def test_mcp_cache_respects_injected_media_directory(tmp_path):
    server = MCPServer(EditService(), media_dir=str(tmp_path / "injected"))
    try:
        result = server.call_tool("preview_cache_status", {})
        assert result["ok"], result
        assert Path(result["directory"]).parent == tmp_path / "injected"
        assert server._agent_tools().api.media_dir == str(tmp_path / "injected")
    finally:
        server.close()
