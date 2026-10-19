"""Real MCP creation/application/sharing, and adversarial content exclusion."""
import copy
import io
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageStat

from cutvoke.core.effect_modules import EffectModuleLibrary, PRIVACY_ZH, PRIVACY_EN, encoded
from cutvoke.core.httpapi import HttpApi
from cutvoke.core.mcp_server import MCPServer
from cutvoke.core.protocol import Command
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditError, EditService
from cutvoke.core.store import ProjectStore
from scripts.verify.validate_effect_contributions import validate_tree


RECIPE = {"effects": [{"effectId": "cutvoke.fx.vibrance", "params": {"intensity": 0.4}}],
          "motion": [{"param": "opacity", "at": 0, "value": 0.2},
                     {"param": "opacity", "at": 0.5, "value": 1},
                     {"param": "x", "at": 0.5, "value": 0.25}]}
PRIVATE = "PRIVATE_USER_秘密_482903"


@pytest.fixture
def session(tmp_path):
    (tmp_path / "data").mkdir()
    store = ProjectStore(str(tmp_path / "data" / "projects.sqlite"))
    service = EditService(store)
    server = MCPServer(service, media_dir=str(tmp_path / "data" / "media"))
    yield service, server
    server.close()
    store.close()


def call(server, name, arguments=None):
    response = server._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": name, "arguments": arguments or {}}})["result"]
    assert not response["isError"], response
    result = json.loads(response["content"][0]["text"])
    assert result["ok"], result
    return result


def edit(service, kind, payload, **kwargs):
    project = service.get_project("private-project")
    command = Command(kind, payload, project_id=project.project_id,
                      expected_revision=project.revision, **kwargs)
    return service.execute(command)


def seed_project(service, tmp_path, duration=4):
    source = tmp_path / (PRIVATE + ".png")
    image = Image.new("RGB", (320, 180), (30, 60, 100))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 130, 150), fill=(220, 70, 30))
    draw.ellipse((160, 25, 280, 145), fill=(40, 180, 80))
    image.save(source)
    service.create_project("private-project", name_hint=PRIVATE, width=320, height=180,
                           fps=Rational.of(15))
    edit(service, "clip.insert", {"clipId": "visual", "trackId": "v", "createTrackKind": "video",
        "sourcePath": str(source), "timelineStart": {"num": "0", "den": "1"},
        "timelineEnd": {"num": str(duration), "den": "1"}})
    edit(service, "clip.insert", {"clipId": "title", "trackId": "t", "createTrackKind": "text",
        "timelineStart": {"num": "0", "den": "1"}, "timelineEnd": {"num": str(duration), "den": "1"},
        "text": {"content": PRIVATE, "fontSize": 24}})
    edit(service, "caption.add", {"captionId": "private-caption", "text": PRIVATE,
        "start": {"num": "0", "den": "1"}, "end": {"num": str(duration), "den": "1"}})


def test_mcp_onboarding_and_complete_local_loop(session, tmp_path, monkeypatch):
    service, server = session
    initialized = server._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})["result"]
    for text in ("sharing", "effect_module_export", "privacyNotice", "explicit user consent"):
        assert text in initialized["instructions"]
    guide = call(server, "editor_help", {"topic": "sharing"})["help"]
    assert guide["privacyNotice"]["zh-CN"] == PRIVACY_ZH
    assert "project_package/project_archive" in " ".join(guide["rules"])
    discovered = call(server, "effect_module_list", {"includeSchemas": True})
    ids = {item["effectId"] for item in discovered["creationSchemas"]["effects"]}
    assert "cutvoke.fx.vibrance" in ids
    assert "cutvoke.text" not in ids
    assert "cutvoke.fx.lut" not in ids
    saved = call(server, "effect_module_save", {"recipe": RECIPE})
    module_id = saved["moduleId"]
    assert call(server, "effect_module_save", {"recipe": RECIPE})["moduleId"] == module_id
    assert call(server, "effect_module_list")["total"] == 1
    assert call(server, "effect_module_list", {"moduleId": module_id})["module"] == saved["module"]
    assert service.effect_modules.root == tmp_path / "data" / "effect-modules"
    seed_project(service, tmp_path)
    edit(service, "effect.add", {"clipId": "visual", "effectId": "cutvoke.fx.blur", "params": {"sigma": 2}})
    before = service.get_project("private-project").to_dict()
    args = {"projectId": "private-project", "expectedRevision": before["revision"],
            "type": "effectModule.apply", "payload": {"clipId": "visual", "moduleId": module_id}}
    call(server, "command_preview", args)
    assert service.get_project("private-project").to_dict() == before
    lease = call(server, "edit_lock", {"projectId": "private-project", "action": "acquire"})["lease"]["leaseId"]
    args.update(editLeaseId=lease, commandId="apply-once")
    committed = call(server, "command_apply", args)
    assert call(server, "command_apply", args)["revision"] == committed["revision"]
    call(server, "edit_lock", {"projectId": "private-project", "action": "release", "leaseId": lease})
    clip = service._clip_by_id(service.get_project("private-project"), "visual")
    assert [effect["effectId"] for effect in clip.effects] == ["cutvoke.fx.blur", "cutvoke.fx.vibrance"]
    assert clip.keyframes["x"][0].time == Rational.of(2)
    assert clip.keyframes["x"][0].value == 80
    assert clip.keyframes["opacity"][1].time == Rational.of(2)
    after = service.get_project("private-project").to_dict()
    edit(service, "history.undo", {})
    assert service.get_project("private-project").to_dict()["sequences"] == before["sequences"]
    edit(service, "history.redo", {})
    assert service.get_project("private-project").to_dict()["sequences"] == after["sequences"]
    # The exporter must not even consult a project to prepare a contribution.
    monkeypatch.setattr(service, "get_project", lambda *_: pytest.fail("Sharing read a private project"))
    (service.effect_modules.root / "private.mp4").write_bytes(PRIVATE.encode())
    exported = call(server, "effect_module_export", {"moduleId": module_id})
    assert exported["containsUserContent"] is False and exported["uploaded"] is False
    assert exported["requiresExplicitConsentToPublish"] is True
    assert exported["privacyNotice"] == {"zh-CN": PRIVACY_ZH, "en": PRIVACY_EN}
    assert len(exported["files"]) == 4
    with zipfile.ZipFile(exported["path"]) as archive:
        assert sorted(archive.namelist()) == sorted(exported["files"])
        for info in archive.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert not info.comment and not info.extra
            raw = archive.read(info)
            for secret in (PRIVATE, str(tmp_path), "private-project", "private-caption", "apply-once"):
                assert secret.encode() not in raw
        archive.extractall(tmp_path / "contribution")
    assert validate_tree(tmp_path / "contribution" / "effects")["ok"]
    other = EffectModuleLibrary(tmp_path / "recipient-library")
    assert other.import_package(exported["path"])["moduleId"] == module_id
    assert call(server, "effect_module_import", {"path": exported["path"]})["moduleId"] == module_id


def test_reopen_and_apply_without_original_library_or_source_project(tmp_path):
    original = EffectModuleLibrary(tmp_path / "creator")
    module_id = original.save(RECIPE)["moduleId"]
    archive = original.export(module_id, str(tmp_path / "share.zip"))
    database = tmp_path / "recipient.sqlite"
    with ProjectStore(str(database)) as store:
        service = EditService(store)
        imported = service.effect_modules.import_package(archive["path"])
        seed_project(service, tmp_path, duration=2)
        edit(service, "effectModule.apply", {"clipId": "visual", "moduleId": imported["moduleId"]})
        revision = service.get_project("private-project").revision
        service.close()
    with ProjectStore(str(database)) as store:
        service = EditService(store)
        project = service.get_project("private-project")
        assert project.revision == revision
        clip = service._clip_by_id(project, "visual")
        assert clip.keyframes["x"][0].time == Rational.of(1)
        assert service.effect_modules.load(module_id) == original.load(module_id)
        service.close()


@pytest.mark.parametrize("recipe", [
    {"sourcePath": "C:/private/movie.mp4", **RECIPE},
    {"name": PRIVATE, **RECIPE},
    {"description": PRIVATE, **RECIPE},
    {"projectId": PRIVATE, **RECIPE},
    {"thumbnail": PRIVATE, **RECIPE},
    {"effects": [{"effectId": "cutvoke.text", "params": {"content": PRIVATE}}]},
    {"effects": [{"effectId": "cutvoke.fx.lut", "params": {"file": "C:/private/grade.cube"}}]},
    {"effects": [{"effectId": "cutvoke.fx.mask", "params": {"content": PRIVATE}}]},
    {"effects": [{"effectId": "cutvoke.fx.blur", "sourcePath": PRIVATE}]},
    {"effects": [{"effectId": "cutvoke.fx.blur", "params": {"content": PRIVATE}}]},
    {"effects": [{"effectId": "cutvoke.fx.blur", "params": {"sigma": PRIVATE}}]},
    {"effects": [{"effectId": "cutvoke.anim.fadeIn", "params": {"easing": PRIVATE}}]},
    {"effects": [{"effectId": "cutvoke.fx.blur", "params": {"sigma": 50}}]},
    {"effects": [{"effectId": "cutvoke.fx.blur", "params": {"sigma": True}}]},
    {"effects": [{"effectId": "cutvoke.fx.blur", "params": {"sigma": float("nan")}}]},
    {"effects": [{"effectId": "cutvoke.fx.blur", "params": {"sigma": float("inf")}}]},
    {"effects": [{"effectId": "cutvoke.fx.blur", "params": {"sigma": 10 ** 400}}]},
    {"textStyle": {"content": PRIVATE}}, {"textStyle": {"fontFamily": PRIVATE}},
    {"textStyle": {"color": PRIVATE}}, {"textStyle": {"color": "#123456junk"}},
    {"textStyle": {"align": PRIVATE}}, {"textStyle": {"fontSize": False}},
    {"textStyle": {"color": "#44aaff"}, "effects": [{"effectId": "cutvoke.fx.blur"}]},
    {"motion": [{"param": "opacity", "at": 0.5, "value": 1, "text": PRIVATE}]},
    {"motion": [{"param": "opacity", "at": 1.1, "value": 1}]},
    {"motion": [{"param": "opacity", "at": 0, "value": 3}]},
    {"motion": [{"param": "x", "at": 0, "value": 0.123456789}]},
    {"motion": [{"param": "scale", "at": 0, "value": 1, "interpolation": PRIVATE}]},
    {"motion": [{"param": "opacity", "at": 0, "value": 1}] * 2},
    {"effects": [{"effectId": "cutvoke.fx.blur"}] * 2},
    {"effects": [{"effectId": "cutvoke.anim.fadeIn"}, {"effectId": "cutvoke.anim.zoomIn"}]},
    {},
])
def test_reject_content_and_invalid_settings_before_any_file_write(tmp_path, recipe):
    library = EffectModuleLibrary(tmp_path / "library")
    with pytest.raises(ValueError):
        library.save(recipe)
    assert not library.root.exists()


def test_tampered_local_modules_cannot_be_exported(tmp_path):
    library = EffectModuleLibrary(tmp_path / "library")
    module_id = library.save(RECIPE)["moduleId"]
    path = library.root / (module_id + ".json")
    document = library.load(module_id)
    document["recipe"]["textStyle"] = {"content": PRIVATE}
    path.write_bytes(encoded(document))
    with pytest.raises(ValueError):
        library.export(module_id, str(tmp_path / "bad.zip"))
    assert not (tmp_path / "bad.zip").exists()
    assert library.list()["rejectedCount"] == 1
    assert library.list()["total"] == 0
    document["recipe"]["textStyle"] = {}
    document["recipe"]["effects"][0]["params"]["intensity"] = 0.8
    path.write_bytes(encoded(document))
    with pytest.raises(ValueError, match="content ID mismatch"):
        library.load(module_id)
    for invalid in ("../private", "cutvoke.module." + PRIVATE, "C:/private.json"):
        with pytest.raises(ValueError):
            library.load(invalid)


def test_duplicate_json_fields_and_invalid_versions_are_rejected(tmp_path):
    library = EffectModuleLibrary(tmp_path / "library")
    module_id = library.save(RECIPE)["moduleId"]
    path = library.root / (module_id + ".json")
    valid = path.read_bytes()
    path.write_bytes(valid.replace(b'"formatVersion":1', b'"formatVersion":1,"formatVersion":1'))
    with pytest.raises(ValueError, match="Duplicate fields"):
        library.load(module_id)
    document = json.loads(valid)
    document["formatVersion"] = True
    path.write_bytes(encoded(document))
    with pytest.raises(ValueError, match="Unsupported effect module format"):
        library.load(module_id)


def test_symlink_module_and_unmodified_builtin_requirement(session, tmp_path):
    from dataclasses import replace
    service, server = session
    seed_project(service, tmp_path)
    module_id = call(server, "effect_module_save", {"recipe": RECIPE})["moduleId"]
    spec = service._effects.get("cutvoke.fx.vibrance")
    service._effects.register(replace(spec, implementation={"engine": "internal", "kind": "different"}), override=True)
    try:
        with pytest.raises(EditError, match="unmodified built-in"):
            edit(service, "effectModule.apply", {"clipId": "visual", "moduleId": module_id})
    finally:
        service._effects.register(spec, override=True)
    target = service.effect_modules.root / (module_id + ".json")
    alternate = tmp_path / "external-module.json"
    alternate.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(alternate)
    except OSError:
        pytest.skip("Symlinks require Windows developer mode or privileges")
    with pytest.raises(ValueError, match="inside the local library"):
        service.effect_modules.export(module_id)


def test_module_export_never_copies_extra_files_and_import_rejects_them(tmp_path):
    library = EffectModuleLibrary(tmp_path / "library")
    module_id = library.save(RECIPE)["moduleId"]
    exported = library.export(module_id, str(tmp_path / "safe.zip"))
    with pytest.raises(ValueError):
        library.export(module_id, exported["path"])
    with zipfile.ZipFile(exported["path"]) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    for variant in ("extra-media", "private-readme", "duplicate", "traversal", "oversize"):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, PRIVATE.encode() if variant == "private-readme" and name.endswith("README.md") else data)
            if variant in ("extra-media", "traversal"):
                archive.writestr("../private.png" if variant == "traversal" else "private.mp4", PRIVATE)
            if variant == "duplicate":
                with pytest.warns(UserWarning):
                    archive.writestr(next(iter(files)), b"duplicate")
            if variant == "oversize":
                archive.writestr("large.txt", b"x" * 65537)
        path = tmp_path / (variant + ".zip")
        path.write_bytes(buffer.getvalue())
        with pytest.raises(ValueError):
            library.import_package(str(path))
    with zipfile.ZipFile(exported["path"]) as archive:
        archive.extractall(tmp_path / "review")
    folder = tmp_path / "review" / "effects" / "recipes" / module_id
    (folder / "private.png").write_text(PRIVATE)
    assert not validate_tree(tmp_path / "review" / "effects")["ok"]


def test_title_style_preserves_user_content_and_late_failure_is_atomic(session, tmp_path):
    service, server = session
    seed_project(service, tmp_path)
    recipe = {"effects": [{"effectId": "cutvoke.fx.vibrance", "params": {"intensity": 0.4}},
                           {"effectId": "cutvoke.transition.crossfade", "params": {"duration": 0.5}}]}
    module_id = call(server, "effect_module_save", {"recipe": recipe})["moduleId"]
    before = service.get_project("private-project").to_dict()
    with pytest.raises(EditError):
        edit(service, "effectModule.apply", {"clipId": "visual", "moduleId": module_id})
    assert service.get_project("private-project").to_dict() == before
    # Title style is a separate module; a transition requires an adjacent clip.
    style_id = call(server, "effect_module_save", {"recipe": {
        "textStyle": {"color": "#44aaff", "fontSize": 32, "bold": True}}})["moduleId"]
    edit(service, "effectModule.apply", {"clipId": "title", "moduleId": style_id})
    title = service._clip_by_id(service.get_project("private-project"), "title")
    params = next(e["params"] for e in title.effects if e["effectId"] == "cutvoke.text")
    assert params["content"] == PRIVATE and params["color"] == "#44aaff"
    assert service.get_project("private-project").sequence.captions[0].text == PRIVATE
    archive = call(server, "effect_module_export", {"moduleId": style_id})
    assert archive["containsUserContent"] is False


def test_revision_lease_locked_track_and_batch_contract(session, tmp_path):
    service, server = session
    seed_project(service, tmp_path)
    module_id = call(server, "effect_module_save", {"recipe": RECIPE})["moduleId"]
    payload = {"clipId": "visual", "moduleId": module_id}
    before = service.get_project("private-project").to_dict()
    with pytest.raises(EditError) as error:
        service.execute(Command("effectModule.apply", payload, project_id="private-project", expected_revision="0"))
    assert error.value.code == "REVISION_CONFLICT"
    lease = service.acquire_edit_lease("private-project")
    with pytest.raises(EditError):
        edit(service, "effectModule.apply", payload)
    service.release_edit_lease("private-project", lease["leaseId"])
    assert service.get_project("private-project").to_dict() == before
    edit(service, "track.update", {"trackId": "v", "locked": True})
    with pytest.raises(EditError):
        edit(service, "effectModule.apply", payload)
    edit(service, "track.update", {"trackId": "v", "locked": False})
    edit(service, "edit.batch", {"commands": [{"type": "effectModule.apply", "payload": payload}]})
    assert service._clip_by_id(service.get_project("private-project"), "visual").keyframes


def test_http_tools_share_the_same_privacy_contract(session, tmp_path):
    service, _ = session
    api = HttpApi(service, media_dir=str(tmp_path / "http-media"))
    try:
        status, saved = api.handle("POST", "/api/v1/agent/effect_module_save", {"recipe": RECIPE})
        assert status == 200 and saved["privacyNotice"]["zh-CN"] == PRIVACY_ZH
        status, result = api.handle("POST", "/api/v1/agent/effect_module_export", {"moduleId": saved["moduleId"]})
        assert status == 200 and result["uploaded"] is False
        for extra in ("projectId", "name", "description", "files", "sourcePath", "text"):
            status, _ = api.handle("POST", "/api/v1/agent/effect_module_save", {"recipe": RECIPE, extra: PRIVATE})
            assert status == 400
    finally:
        api.close()


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_real_module_preview_export_and_imported_recipe_match(session, tmp_path):
    service, server = session
    seed_project(service, tmp_path, duration=2)
    recipe = {"effects": [{"effectId": "cutvoke.fx.vibrance", "params": {"intensity": 0.7}}],
              "motion": [{"param": "opacity", "at": 0, "value": 0.3},
                         {"param": "opacity", "at": 0.5, "value": 1}]}
    module_id = call(server, "effect_module_save", {"recipe": recipe})["moduleId"]
    edit(service, "effectModule.apply", {"clipId": "visual", "moduleId": module_id})
    renderer = RenderService()
    project = service.get_project("private-project")
    preview, output = tmp_path / "preview.mp4", tmp_path / "export.mp4"
    renderer.render_preview_window(project, str(preview), 0, 2)
    renderer.render(project, str(output), quality="high")
    pictures = []
    for index, video in enumerate((preview, output)):
        frame = tmp_path / f"frame-{index}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.2", "-i", str(video),
                        "-frames:v", "1", str(frame)], check=True, capture_output=True, timeout=30)
        pictures.append(Image.open(frame).convert("RGB"))
    assert sum(ImageStat.Stat(ImageChops.difference(*pictures)).mean) / 3 < 5
    assert sum(ImageStat.Stat(pictures[1]).stddev) > 50
    probe = renderer.probe_media(str(output))
    assert abs(float(probe["duration"]) - 2) < 0.1
    # Reapply the imported recipe to the same project; it must replace, not stack.
    package = call(server, "effect_module_export", {"moduleId": module_id})
    imported = call(server, "effect_module_import", {"path": package["path"]})
    edit(service, "effectModule.apply", {"clipId": "visual", "moduleId": imported["moduleId"]})
    clip = service._clip_by_id(service.get_project("private-project"), "visual")
    assert sum(e["effectId"] == "cutvoke.fx.vibrance" for e in clip.effects) == 1
