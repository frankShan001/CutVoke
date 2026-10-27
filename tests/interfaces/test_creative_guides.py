"""Creative guidance is discoverable and lazy over real MCP/HTTP, without an installed Skill."""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cutvoke.editing.service import EditService
from cutvoke.interfaces.creative_guides import GUIDE_DIRECTORY, GUIDE_FILES, catalog, document
from cutvoke.interfaces.editor_guide import EditorGuide
from cutvoke.interfaces.httpapi import HttpApi
from cutvoke.interfaces.mcp_server import MCPServer
from cutvoke.storage.store import ProjectStore


@pytest.fixture
def server(tmp_path):
    store = ProjectStore(str(tmp_path / "projects.sqlite"))
    service = EditService(store)
    value = MCPServer(service)
    try:
        yield value, service, tmp_path
    finally:
        value.close()
        store.close()


def rpc(server, method, params=None):
    return server._handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def help_call(server, arguments):
    value = rpc(server, "tools/call", {"name": "editor_help", "arguments": arguments})["result"]
    assert value["isError"] is False, value
    return json.loads(value["content"][0]["text"])["help"]


def test_wire_discovery_and_selected_guides_match_skill_sources(server):
    instance, service, _root = server
    overview = help_call(instance, {})
    index = help_call(instance, overview["help"]["creative"])
    assert {entry["layer"] for entry in index["guides"]} == {1, 2, 3}
    assert {entry["id"] for entry in index["guides"]} == set(GUIDE_FILES)
    assert not any("content" in entry for entry in index["guides"])
    resources = rpc(instance, "resources/list")["result"]["resources"]
    uris = {entry["uri"] for entry in resources}
    skill = Path(__file__).resolve().parents[2] / "skills" / "cutvoke-editor"
    for entry in index["guides"]:
        received = help_call(instance, entry["request"])
        source = (skill / GUIDE_FILES[entry["id"]][0]).read_bytes()
        assert received["content"] == source.decode("utf8")
        assert received["sha256"] == hashlib.sha256(source).hexdigest()
        assert entry["uri"] in uris
        resource = rpc(instance, "resources/read", {"uri": entry["uri"]})["result"]
        assert received == json.loads(resource["contents"][0]["text"])
    assert service._projects == {}  # Reading instruction documents never creates or edits a project.


def test_index_opens_no_bodies_and_selection_opens_exactly_one():
    original = Path.read_bytes
    opened = []
    def selected(path):
        opened.append(path)
        assert path == GUIDE_DIRECTORY / "music-video.md"
        return original(path)
    with patch.object(Path, "read_bytes", selected):
        catalog()
        assert opened == []
        result = document("music-video")
    assert result["id"] == "music-video" and len(opened) == 1


def test_invalid_ids_and_paths_fail_without_filesystem_access(server):
    instance, service, _root = server
    for value in ("../../private", "music-video.md", "file:///secret", "music-video?other=1"):
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unexpected file read")):
            response = rpc(instance, "resources/read", {"uri": "cutvoke://creative/" + value})
            assert response["error"]["code"] == -32602
    with pytest.raises(ValueError, match="requires topic=creative"):
        EditorGuide(service).help({"topic": "overview", "guide": "principles"})


def test_http_uses_identical_selected_document_and_missing_bundle_is_explicit(server):
    instance, service, root = server
    api = HttpApi(service, media_dir=str(root / "media"))
    try:
        arguments = {"topic": "creative", "guide": "project-plan"}
        status, result = api.handle("POST", "/api/v1/agent/editor_help", arguments)
        assert status == 200
        assert result["help"] == help_call(instance, arguments)
    finally:
        api.close()
    with patch("cutvoke.interfaces.creative_guides.GUIDE_DIRECTORY", root / "missing"):
        with pytest.raises(ValueError, match="Bundled creative guide unavailable"):
            document("principles")
