"""External code remains inert; generated media uses ordinary scoped editing."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

import pytest

from cutvoke.editing.service import EditService
from cutvoke.interfaces.httpapi import HttpApi
from cutvoke.interfaces.mcp_server import MCPServer
from cutvoke.storage.generated_media import canonical, inspect_render, snapshot, publish_source_archive
from cutvoke.storage.project_directory import ProjectLibraryStore
from cutvoke.storage.projectpack import unpack_project


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rat(value):
    return {"num": str(value), "den": "1"}


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    script = root / "scene.py"
    # If this ever executes, the test fails. Source is data, even at import.
    script.write_text("raise RuntimeError('must never execute source')\n")
    video = root / "scene.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x96:r=10:d=1",
                    "-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le",
                    "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                    "-bsf:v", "prores_metadata=color_primaries=bt709:color_trc=bt709:colorspace=bt709", str(video)], check=True)
    manifest = {"schemaVersion": 1, "renderer": {"name": "fixture", "version": "1", "build": "test"},
                "entrypoint": "scene.py", "files": [{"path": "scene.py", "sha256": sha(script), "role": "source"}],
                "params": {"accent": "blue"}, "seed": 7,
                "output": {"path": "scene.mov", "sha256": sha(video), "width": 160, "height": 96,
                           "fps": {"num": 10, "den": 1}, "durationFrames": 10,
                           "alphaMode": "straight", "colorSpace": "bt709", "audioMode": "none"}}
    path = root / "manifest.json"
    path.write_bytes(canonical(manifest))
    library = ProjectLibraryStore(str(tmp_path / "library.sqlite"))
    service = EditService(store=library)
    server = MCPServer(service)
    def call(name, **arguments):
        result = server.call_tool(name, arguments)
        assert result["ok"], result
        return result
    call("create_project", projectId="one", directory=str(tmp_path / "one"), width=160, height=96, fps=10)
    call("create_project", projectId="two", directory=str(tmp_path / "two"), width=160, height=96, fps=10)
    yield root, path, manifest, library, service, server, call
    server.close()
    library.close()


def test_inspect_import_scoped_edit_swap_undo_and_package(fixture, tmp_path):
    root, path, manifest, library, service, server, call = fixture
    args = dict(projectId="one", manifestPath=str(path))
    before = copy.deepcopy(service.get_project("one").to_dict())
    inspected = call("generated_media", operation="inspect", **args)
    assert inspected["validation"]["runtimeExecuted"] is False
    assert inspected["validation"]["alphaPixels"] == "not_assessed"
    assert not list((tmp_path / "one" / "media").glob(".generated/*"))
    imported = call("generated_media", operation="import", **args)
    again = call("generated_media", operation="import", **args)
    assert imported == again
    assert service.get_project("one").to_dict() == before
    assert library.get_asset(imported["assetId"]) is None
    assert library.get_project_asset("two", imported["assetId"]) is None
    archive = imported["sourceArchive"]
    assert sha(Path(archive["path"])) == archive["sha256"]
    assert archive["projectPackagePolicy"] == "include_when_referenced"
    with zipfile.ZipFile(archive["path"]) as zipped:
        assert set(zipped.namelist()) == {"manifest.json", "scene.py"}
        assert json.loads(zipped.read("manifest.json")) == manifest
        assert zipped.read("scene.py") == (root / "scene.py").read_bytes()
    asset = imported["assetId"]
    lease = call("edit_lock", projectId="one", action="acquire", owner="generated-test")["lease"]["leaseId"]
    def apply(kind, payload, revision, command_id):
        return call("command_apply", projectId="one", expectedRevision=revision, commandId=command_id,
                    editLeaseId=lease, type=kind, payload=payload)
    commands = [{"type": "clip.insert", "payload": {"trackId": "v", "createTrackKind": "video",
                 "clipId": name, "assetId": asset, "timelineStart": rat(start), "timelineEnd": rat(start + 1)}}
                for name, start in (("replace", 0), ("keep", 1))]
    commands.append({"type": "clip.insert", "payload": {"trackId": "titles", "createTrackKind": "text",
                    "clipId": "title", "timelineStart": rat(0), "timelineEnd": rat(2),
                    "text": {"content": "Editable", "fontSize": 30}}})
    commands[1]["payload"].pop("createTrackKind")
    apply("edit.batch", {"commands": commands}, "0", "assemble")
    original = service.get_project("one").to_dict()
    # Changed render, same source identity: only the selected clip is swapped.
    manifest["params"]["accent"] = "red"
    video = root / "scene.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=160x96:r=10:d=1",
                    "-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le",
                    "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                    "-bsf:v", "prores_metadata=color_primaries=bt709:color_trc=bt709:colorspace=bt709", str(video)], check=True)
    manifest["output"]["sha256"] = sha(video)
    path.write_bytes(canonical(manifest))
    replacement = call("generated_media", operation="import", **args)
    assert replacement["renderKey"] != imported["renderKey"]
    payload = {"clipIds": ["replace"], "assetId": replacement["assetId"]}
    call("command_preview", projectId="one", expectedRevision="1", type="asset.swap", payload=payload)
    assert service.get_project("one").to_dict() == original
    changed = apply("asset.swap", payload, "1", "swap")
    replay = apply("asset.swap", payload, "1", "swap")
    assert changed["revision"] == replay["revision"] == "2"
    clips = service.get_project("one").sequence.tracks[0].clips
    assert clips[0].asset_ref.asset_id == replacement["assetId"]
    assert clips[1].asset_ref.asset_id == asset
    assert service.get_project("one").sequence.tracks[1].to_dict() == original["sequence"]["tracks"][1]
    apply("history.undo", {}, "2", "undo")
    assert service.get_project("one").sequence.tracks[0].clips[0].asset_ref.asset_id == asset
    apply("history.redo", {}, "3", "redo")
    package = call("project_package", projectId="one", expectedRevision="4", outPath="handoff.cvkpkg")
    restored, _, _ = unpack_project(package["path"], str(tmp_path / "restore"))
    with zipfile.ZipFile(package["path"]) as zipped:
        assert json.loads(zipped.read("manifest.json"))["packageFormat"] == 4
        assert len([name for name in zipped.namelist() if name.startswith("sources/")]) == 2
    assert not list((tmp_path / "restore").rglob("scene.py"))
    assert Path(restored.sequence.tracks[0].clips[0].asset_ref.source_path).is_file()
    assert restored.sequence.tracks[1].clips[0].effects[0]["params"]["content"] == "Editable"
    call("edit_lock", projectId="one", action="release", leaseId=lease)


def test_source_tools_scope_http_restore_and_revision_preservation(fixture, tmp_path):
    root, path, manifest, library, service, server, call = fixture
    imported = call("generated_media", projectId="one", operation="import", manifestPath=str(path))
    # Unused imports are not part of an editable-project source handoff.
    assert call("generated_source", projectId="one", operation="list")["archives"] == []
    call("command_apply", projectId="one", type="clip.insert", expectedRevision="0",
         payload={"trackId": "v", "createTrackKind": "video", "clipId": "scene", "assetId": imported["assetId"],
                  "timelineEnd": rat(1)})
    before = copy.deepcopy(service.get_project("one").to_dict())
    bundle = imported["bundleId"]
    listed = call("generated_source", projectId="one", operation="list")
    assert [item["bundleId"] for item in listed["archives"]] == [bundle]
    assert call("generated_source", projectId="two", operation="list")["archives"] == []
    missing = server.call_tool("generated_source", {"projectId": "two", "operation": "inspect", "bundleId": bundle})
    assert not missing["ok"]
    inspected = call("generated_source", projectId="one", operation="inspect", bundleId=bundle)
    assert inspected["sourceArchive"]["manifest"] == manifest
    api = server._agent_tools().api
    status, result = api.handle("POST", "/api/v1/agent/generated_source",
                              {"projectId": "one", "operation": "inspect", "bundleId": bundle})
    assert status == 200 and result == inspected
    target = tmp_path / "one" / "exports" / "recovered"
    restored = call("generated_source", projectId="one", operation="restore", bundleId=bundle, outPath=str(target))
    assert not restored["runtimeExecuted"] and not restored["restored"]["runtimeExecuted"]
    assert (target / "scene.py").read_bytes() == (root / "scene.py").read_bytes()
    assert not (target / "scene.mov").exists()
    (target / "scene.py").write_text("user revision")
    retry = server.call_tool("generated_source", {"projectId": "one", "operation": "restore", "bundleId": bundle, "outPath": str(target)})
    assert not retry["ok"] and (target / "scene.py").read_text() == "user revision"
    assert service.get_project("one").to_dict() == before
    assert call("generated_source", projectId="one", operation="list") == listed


@pytest.mark.parametrize("mutation", ["source", "render", "traversal", "absolute", "symlink", "duplicate", "nan",
                                        "unknown", "entrypoint", "alpha-webm", "frame-count", "fps", "size", "audio"])
def test_reject_invalid_contract_without_importing(fixture, mutation):
    root, path, manifest, library, service, server, _ = fixture
    if mutation == "source": (root / "scene.py").write_text("changed")
    elif mutation == "render": (root / "scene.mov").write_bytes(b"changed")
    elif mutation == "traversal": manifest["files"][0]["path"] = "../secret.py"
    elif mutation == "absolute": manifest["output"]["path"] = str(root / "scene.mov")
    elif mutation == "symlink":
        (root / "alias.py").symlink_to(root / "scene.py")
        manifest["files"][0]["path"] = "alias.py"
    elif mutation == "entrypoint": manifest["entrypoint"] = "unlisted.py"
    elif mutation == "unknown": manifest["command"] = "echo unsafe"
    elif mutation == "alpha-webm": manifest["output"]["alphaMode"] = "premultiplied"
    elif mutation == "frame-count": manifest["output"]["durationFrames"] = 11
    elif mutation == "fps": manifest["output"]["fps"]["num"] = 11
    elif mutation == "size": manifest["output"]["width"] = 200
    elif mutation == "audio": manifest["output"]["audioMode"] = "mixed"
    if mutation == "duplicate": path.write_text('{"schemaVersion":1,"schemaVersion":1}')
    elif mutation == "nan": path.write_text(json.dumps({**manifest, "params": {"value": float("nan")}}))
    else: path.write_bytes(canonical(manifest))
    result = server.call_tool("generated_media", dict(operation="import", projectId="one", manifestPath=str(path)))
    assert not result["ok"], result
    assert service.get_project("one").revision == "0"
    assert not [asset for asset in library._target("one").list_assets() if not asset.get("builtin")]


def test_snapshot_is_frozen_and_request_identity_is_content_based(fixture):
    root, path, manifest, _, _, _, _ = fixture
    with snapshot(path) as (frozen, stage, video):
        (root / "scene.py").write_text("later mutation")
        assert (stage / "inputs/scene.py").read_text().startswith("raise RuntimeError")
        first = inspect_render(frozen, video, "ffprobe")
        frozen["params"]["accent"] = "green"
        second = inspect_render(frozen, video, "ffprobe")
        assert second["renderKey"] != first["renderKey"]
    assert not stage.exists()


def test_inspection_is_bounded_and_refuses_irregular_frame_clock(fixture, monkeypatch):
    _, path, _, _, _, _, _ = fixture
    from types import SimpleNamespace
    with snapshot(path) as (manifest, _stage, video):
        # Keep the real decoder result and only perturb frame timing, preserving
        # average rate/count: metadata alone must not accept variable timestamps.
        real = subprocess.run
        def shifted(command, **kwargs):
            assert command[command.index("-read_intervals") + 1] == "%+#18001"
            decoded = real(command, **kwargs)
            result = json.loads(decoded.stdout)
            result["frames"][3]["best_effort_timestamp_time"] = "0.310000"
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(result))
        monkeypatch.setattr("cutvoke.storage.generated_media.subprocess.run", shifted)
        with pytest.raises(ValueError, match="timestamps"):
            inspect_render(manifest, video, "ffprobe")


def test_http_parity_and_invalid_arguments(fixture):
    _, path, _, _, service, server, call = fixture
    args = dict(operation="inspect", projectId="one", manifestPath=str(path))
    # Resolve the selected project's API just as the shared agent route does.
    api = HttpApi(service, media_dir=str(path.parent / "unused-media"))
    try:
        status, result = api.handle("POST", "/api/v1/agent/generated_media", args)
        assert status == 200 and result == call("generated_media", **args)
        for invalid in ({**args, "operation": "run"}, {**args, "command": "unsafe"}, {"operation": "import"}):
            status, http = api.handle("POST", "/api/v1/agent/generated_media", invalid)
            assert status == 400 and http["error"]["code"] == "INVALID_ARGUMENT"
            assert not server.call_tool("generated_media", invalid)["ok"]
    finally:
        api.close()


def test_concurrent_source_archives_are_complete_and_corruption_is_reported(fixture, tmp_path):
    _, path, _, _, _, _, _ = fixture
    target = tmp_path / "archives"
    def publish(_):
        with snapshot(path) as (manifest, stage, _render):
            identity = hashlib.sha256(canonical(manifest)).hexdigest()
            return publish_source_archive(manifest, stage, target, identity)
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(publish, range(8)))
    assert all(item == receipts[0] for item in receipts)
    assert len(list(target.iterdir())) == 1
    Path(receipts[0]["path"]).write_bytes(b"corrupt archive")
    with pytest.raises(ValueError, match="integrity"):
        publish(0)
    assert len(list(target.iterdir())) == 1


def test_existing_corrupt_media_is_not_reused_or_overwritten(fixture):
    _, path, _, _, service, server, call = fixture
    args = dict(projectId="one", operation="import", manifestPath=str(path))
    first = call("generated_media", **args)
    managed = Path(first["asset"]["path"])
    managed.write_bytes(b"corrupt media")
    result = server.call_tool("generated_media", args)
    assert not result["ok"] and "integrity" in result["error"]["message"]
    assert managed.read_bytes() == b"corrupt media"
    assert service.get_project("one").revision == "0"
