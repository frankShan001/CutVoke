"""MCP preview/apply share imports in the selected independent project."""
from pathlib import Path
import shutil

from PIL import Image

from cutvoke.editing.service import EditService
from cutvoke.interfaces.mcp_server import MCPServer
from cutvoke.storage.project_directory import ProjectLibraryStore
from cutvoke.storage.projectpack import unpack_project


def test_scoped_asset_id_preview_apply_package_and_reopen(tmp_path):
    library = ProjectLibraryStore(str(tmp_path / "library.sqlite"))
    server = MCPServer(EditService(store=library))
    source = tmp_path / "source.png"; Image.new("RGB", (160, 90), "blue").save(source)
    def call(name, **arguments):
        result = server.call_tool(name, arguments)
        assert result["ok"], result
        return result
    try:
        call("create_project", projectId="one", directory=str(tmp_path / "one"), width=160, height=90)
        call("create_project", projectId="two", directory=str(tmp_path / "two"), width=160, height=90)
        imported = call("media_import", projectId="one", paths=[str(source)])["assets"][0]
        assert library.get_asset(imported["assetId"]) is None
        payload = {"trackId": "v", "createTrackKind": "video", "clipId": "c", "assetId": imported["assetId"],
                   "timelineStart": {"num": "0", "den": "1"}, "timelineEnd": {"num": "1", "den": "1"}}
        call("command_preview", projectId="one", type="clip.insert", expectedRevision="0", payload=payload)
        assert library.load("one").revision == "0"
        missing = server.call_tool("command_preview", {"projectId": "two", "type": "clip.insert", "expectedRevision": "0", "payload": payload})
        assert not missing["ok"] and "asset not found" in missing["error"]["message"]
        applied = call("command_apply", projectId="one", type="clip.insert", expectedRevision="0", payload=payload)
        assert applied["revision"] == "1"
        # A swap resolves the same ledger through the root MCP EditService too.
        call("command_preview", projectId="one", type="asset.swap", expectedRevision="1",
             payload={"clipIds": ["c"], "assetId": imported["assetId"]})
        package = call("project_package", projectId="one", expectedRevision="1", outPath="roundtrip.cvkpkg")
        project, _, _ = unpack_project(package["path"], str(tmp_path / "unpacked"))
        assert Path(project.sequence.tracks[0].clips[0].asset_ref.source_path).is_file()
    finally:
        server.close(); library.close()
    moved = tmp_path / "moved"; shutil.move(str(tmp_path / "one"), moved)
    source.unlink()
    library = ProjectLibraryStore(str(tmp_path / "other-library.sqlite"))
    try:
        library.open_directory(moved)
        asset = library.get_project_asset("one", imported["assetId"])
        assert Path(asset["path"]).is_relative_to(moved / "media")
        assert Path(asset["path"]).is_file()
    finally:
        library.close()
