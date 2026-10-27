"""Folder ownership through real HTTP streaming, Agent tools, and export queues."""
import http.client
import io
import json
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
import time

import pytest
from PIL import Image

from cutvoke.editing.service import EditService
from cutvoke.interfaces.httpapi import HttpApi, build_handler
from cutvoke.interfaces.agent_tools import AgentTools
from cutvoke.rendering.render import RenderService
from cutvoke.storage.project_directory import ProjectLibraryStore


@pytest.fixture
def folder_api(tmp_path):
    store = ProjectLibraryStore(str(tmp_path / "library.sqlite"))
    service = EditService(store=store)
    api = HttpApi(service, RenderService(), media_dir=str(tmp_path / "shared-media"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(api))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    yield api, server.server_address[1], store
    server.shutdown(); server.server_close(); thread.join(); api.close(); store.close()


def request(port, method, path, value=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    binary = isinstance(value, bytes)
    connection.request(method, path, body=value if binary else json.dumps(value) if value is not None else None,
        headers={"Content-Type": "application/octet-stream" if binary else "application/json"})
    response = connection.getresponse(); payload = response.read()
    result = json.loads(payload) if "application/json" in response.getheader("Content-Type", "") else payload
    connection.close()
    return response.status, result


def test_scoped_upload_list_stream_and_cache_are_isolated(folder_api, tmp_path):
    api, port, store = folder_api
    for pid in ("one", "two"):
        status, data = request(port, "POST", "/api/v1/projects", {"projectId": pid,
            "directory": str(tmp_path / pid), "width": 128, "height": 72, "fps": 24})
        assert status == 201, data
    raw = io.BytesIO(); Image.new("RGB", (128, 72), "blue").save(raw, format="PNG")
    status, asset = request(port, "POST", "/api/v1/assets?" + urlencode({"name": "source.png", "projectId": "one"}), raw.getvalue())
    assert status == 201, asset
    assert Path(asset["path"]).is_relative_to(tmp_path / "one" / "media")
    for pid in ("one", "two"):
        status, data = request(port, "GET", f"/api/v1/assets?projectId={pid}")
        assert status == 200, data
        assert (asset["assetId"] in {a["assetId"] for a in data["assets"]}) == (pid == "one")
        status, cache = request(port, "GET", f"/api/v1/preview-cache?projectId={pid}")
        assert status == 200 and Path(cache["directory"]).is_relative_to(tmp_path / pid / "cache"), cache
    status, data = request(port, "GET", f"/api/v1/assets/{asset['assetId']}/media?projectId=one")
    assert status == 200 and data == raw.getvalue()
    status, data = request(port, "GET", f"/api/v1/assets/{asset['assetId']}/media?projectId=two")
    assert status == 404, data
    for _ in range(3):
        status, data = request(port, "POST", "/api/v1/assets?name=bad.png&projectId=missing", raw.getvalue())
        assert status == 400 and data["error"]["code"] == "PROJECT_DIRECTORY_UNAVAILABLE", data


def test_agent_import_and_queued_export_stay_in_project(folder_api, tmp_path):
    api, port, store = folder_api
    api.service.create_project("one", width=128, height=72)
    source = tmp_path / "source.png"; Image.new("RGB", (128, 72), "red").save(source)
    tools = AgentTools(api)
    imported = tools.call("media_import", {"projectId": "one", "paths": [str(source)]})
    assert imported["ok"], imported
    status, _ = request(port, "POST", "/api/v1/projects/one/commands", {"type": "track.add", "commandId": "track", "expectedRevision": "0", "payload": {"trackId": "v", "kind": "video"}})
    assert status == 200
    status, result = request(port, "POST", "/api/v1/projects/one/commands", {"type": "clip.insert", "commandId": "clip", "expectedRevision": "1", "payload": {
        "trackId": "v", "sourcePath": str(source), "timelineStart": {"num": "0", "den": "1"}, "timelineEnd": {"num": "1", "den": "1"}}})
    assert status == 200, result
    source.unlink()
    info = tools.call("project_directory", {"projectId": "one"})
    assert info["directory"] == str(store.directory("one"))
    status, job = request(port, "POST", "/api/v1/projects/one/exports", {"outPath": "final.mp4", "expectedRevision": "2"})
    assert status == 202, job
    for _ in range(150):
        status, job = request(port, "GET", f"/api/v1/jobs/{job['jobId']}")
        if job["status"] in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(.1)
    assert status == 200 and job["status"] == "succeeded", job
    assert Path(job["outPath"]).is_relative_to(store.directory("one") / "exports")
    assert Path(job["outPath"]).is_file()
    status, data = request(port, "POST", "/api/v1/projects/one/exports", {"outPath": str(tmp_path / "outside.mp4"), "expectedRevision": "2"})
    assert status == 400, data
