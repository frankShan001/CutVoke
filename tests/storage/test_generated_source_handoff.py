"""Portable inert source, strict verification and fault-safe package publication."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import zipfile

import pytest

from cutvoke.domain.protocol import Command
from cutvoke.editing.service import EditService
from cutvoke.storage import projectpack
from cutvoke.storage.generated_media import canonical, publish_source_archive, snapshot
from cutvoke.storage.generated_sources import (
    read_source_archive, referenced_sources, restore_source_archive,
)
from cutvoke.storage.project_directory import ProjectLibraryStore
from cutvoke.storage.projectpack import ProjectPackError, pack_project, unpack_project


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def handoff(tmp_path):
    root = tmp_path / "original"
    root.mkdir()
    code = b"raise RuntimeError('source must never execute')\n"
    (root / "scene.py").write_bytes(code)
    media = root / "render.mp4"
    media.write_bytes(b"static package fixture; render decoding is tested by the interface suite")
    manifest = {"schemaVersion": 1, "renderer": {"name": "test", "version": "1", "build": "test"},
                "entrypoint": "scene.py", "files": [{"path": "scene.py", "sha256": sha(code), "role": "source"}],
                "params": {}, "seed": 1, "output": {"path": "render.mp4", "sha256": sha(media.read_bytes()),
                "width": 32, "height": 32, "fps": {"num": 10, "den": 1}, "durationFrames": 10,
                "alphaMode": "opaque", "colorSpace": "bt709", "audioMode": "none"}}
    path = root / "manifest.json"
    path.write_bytes(canonical(manifest))
    bundle = sha(canonical(manifest))
    with snapshot(path) as (declared, stage, _):
        receipt = publish_source_archive(declared, stage, root / ".generated", bundle)
    service = EditService()
    project = service.create_project("handoff", width=32, height=32)
    service.execute(Command("clip.insert", {"clipId": "scene", "trackId": "v", "createTrackKind": "video",
        "sourcePath": str(media), "timelineEnd": {"num": "1", "den": "1"}},
        project_id="handoff", expected_revision=project.revision))
    project = service.get_project("handoff")
    yield project, root, Path(receipt["path"]), manifest
    service.close()


def test_nested_dedup_package_directory_move_and_lazy_restore(handoff, tmp_path):
    project, root, source, manifest = handoff
    original = copy.deepcopy(project.to_dict())
    nested = copy.deepcopy(project.sequence)
    nested.id = "nested"
    compound = copy.deepcopy(project.sequence.tracks[0].clips[0])
    compound.id = "compound"
    compound.nested = nested
    compound.asset_ref.source_path = ""
    project.sequence.tracks[0].clips.append(compound)
    other = copy.deepcopy(project.sequence)
    other.id = "other"
    project.sequences.append(other)
    package = tmp_path / "portable.cvkpkg"
    pack_project(project, str(package))
    with zipfile.ZipFile(package) as archive:
        package_manifest = json.loads(archive.read("manifest.json"))
        assert package_manifest["packageFormat"] == 4
        assert len(package_manifest["generatedSources"]) == 1
        assert len([n for n in archive.namelist() if n.startswith("sources/")]) == 1
    expected_source = source.read_bytes()
    shutil.rmtree(root)
    restored, mapping, warnings = unpack_project(str(package), str(tmp_path / "unpacked"))
    assert not warnings
    assert len(mapping) == 1
    assert not list((tmp_path / "unpacked").rglob("scene.py"))
    library = ProjectLibraryStore(str(tmp_path / "library.sqlite"))
    service = EditService(library)
    try:
        imported = service.import_project(restored, project_id="copy")
        directory = library.directory("copy")
        assert imported.sequence.tracks[0].clips[1].nested is not None
    finally:
        service.close(); library.close()
    shutil.rmtree(tmp_path / "unpacked")
    moved = tmp_path / "moved"
    shutil.move(str(directory), moved)
    archived = next((moved / "media" / ".generated").glob("*.cvsource.zip"))
    assert archived.read_bytes() == expected_source
    info, _ = read_source_archive(archived)
    result = restore_source_archive(info, tmp_path / "restored source")
    assert not result["runtimeExecuted"]
    assert (Path(result["path"]) / "scene.py").read_bytes().startswith(b"raise RuntimeError")
    assert not (Path(result["path"]) / "render.mp4").exists()
    assert json.loads((Path(result["path"]) / "manifest.json").read_bytes()) == manifest
    assert original["revision"] == project.revision


def rewritten_zip(raw, change):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = [(item, archive.read(item)) for item in archive.infolist()]
    entries = change(entries)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for item, data in entries:
            archive.writestr(item, data)
    return output.getvalue()


@pytest.mark.parametrize("fault", ["hash", "duplicate", "traversal", "absolute", "backslash", "drive", "symlink", "schema", "undeclared", "case", "collision", "compression", "reserved"])
def test_source_archive_rejects_invalid_input_before_any_restore(handoff, tmp_path, fault):
    _, _, archive, _ = handoff
    def mutate(entries):
        source, data = entries[1]
        if fault == "hash": entries[1] = (source, b"corrupt")
        elif fault == "duplicate": entries.append((source, data))
        elif fault == "schema":
            m = json.loads(entries[0][1]); m["schemaVersion"] = 2
            entries[0] = (entries[0][0], canonical(m))
        elif fault == "symlink": source.external_attr = (stat.S_IFLNK | 0o777) << 16
        elif fault == "compression": source.compress_type = zipfile.ZIP_BZIP2
        elif fault == "collision": entries.append(("scene.py/child", b"x"))
        elif fault == "case": entries.append(("SCENE.py", data))
        else:
            names = {"traversal": "../outside.py", "absolute": "/outside.py", "backslash": "a\\scene.py",
                     "drive": "C:scene.py", "undeclared": "extra.py", "reserved": "CON.py"}
            entries.append((names[fault], data))
        return entries
    archive.write_bytes(rewritten_zip(archive.read_bytes(), mutate))
    destination = tmp_path / "restore"
    with pytest.raises(ValueError):
        info, _ = read_source_archive(archive)
        restore_source_archive(info, destination)
    assert not destination.exists()
    assert not (tmp_path / "outside.py").exists()


@pytest.mark.parametrize("fault", ["interrupted", "changed", "replace"])
def test_pack_failure_preserves_previous_package_and_cleans_temporary(handoff, tmp_path, monkeypatch, fault):
    project, root, _, _ = handoff
    destination = tmp_path / "old.cvkpkg"
    destination.write_bytes(b"previous complete delivery")
    before = project.to_dict()
    original = projectpack._stream_copy_into_zip
    def copy_into(archive, source, name):
        if fault == "interrupted":
            with archive.open(name, "w") as target:
                target.write(b"partial")
            raise OSError("injected disk failure")
        if fault == "changed":
            source.write_bytes(b"changed between hash and copy")
        return original(archive, source, name)
    monkeypatch.setattr(projectpack, "_stream_copy_into_zip", copy_into)
    if fault == "replace":
        def fail(*args): raise OSError("injected rename failure")
        monkeypatch.setattr(projectpack.os, "replace", fail)
    with pytest.raises((OSError, ProjectPackError)):
        pack_project(project, str(destination))
    assert destination.read_bytes() == b"previous complete delivery"
    assert project.to_dict() == before
    assert not list(tmp_path.glob(".project-package-*.tmp"))


@pytest.mark.parametrize("fault", ["sourcehash", "innerhash", "duplicate", "path", "future", "source_schema"])
def test_package_source_failure_preserves_existing_destination(handoff, tmp_path, fault):
    project, _, _, _ = handoff
    package = tmp_path / "original.cvkpkg"
    pack_project(project, str(package))
    def mutate(entries):
        m = json.loads(entries[0][1])
        source_entry = m["generatedSources"][0]
        if fault == "sourcehash": source_entry["sha256"] = "0" * 64
        elif fault == "path": source_entry["packagePath"] = "sources/../../outside.zip"
        elif fault == "future": m["packageFormat"] = 99
        elif fault == "duplicate": entries.append(entries[-1])
        else:
            index = next(i for i, (item, _) in enumerate(entries) if item.filename.startswith("sources/"))
            def inner(items):
                if fault == "innerhash": items[1] = (items[1][0], b"changed")
                else:
                    manifest = json.loads(items[0][1]); manifest["schemaVersion"] = 2
                    items[0] = (items[0][0], canonical(manifest))
                return items
            changed = rewritten_zip(entries[index][1], inner)
            entries[index] = (entries[index][0], changed)
            source_entry.update(sha256=sha(changed), size=len(changed))
        entries[0] = (entries[0][0], canonical(m))
        return entries
    bad = tmp_path / "bad.cvkpkg"
    bad.write_bytes(rewritten_zip(package.read_bytes(), mutate))
    destination = tmp_path / "existing"
    destination.mkdir()
    sentinel = destination / "user.txt"
    sentinel.write_bytes(b"preserve")
    before = {str(p.relative_to(destination)): p.read_bytes() for p in destination.rglob("*") if p.is_file()}
    with pytest.raises(ProjectPackError):
        unpack_project(str(bad), str(destination), overwrite=True)
    assert before == {str(p.relative_to(destination)): p.read_bytes() for p in destination.rglob("*") if p.is_file()}
    with pytest.raises(ProjectPackError):
        unpack_project(str(bad), str(tmp_path / "absent"))
    assert not (tmp_path / "absent").exists()


def test_explicit_restore_preserves_existing_and_rolls_back_io_failure(handoff, tmp_path, monkeypatch):
    _, _, archive, _ = handoff
    info, _ = read_source_archive(archive)
    destination = tmp_path / "owned"
    destination.mkdir()
    (destination / "scene.py").write_text("user edits")
    with pytest.raises(ValueError, match="new destination"):
        restore_source_archive(info, destination)
    assert (destination / "scene.py").read_text() == "user edits"
    def fail(*args): raise OSError("injected restore disk failure")
    monkeypatch.setattr("cutvoke.storage.generated_sources.os.link", fail)
    with pytest.raises(OSError):
        restore_source_archive(info, tmp_path / "new")
    assert not (tmp_path / "new").exists()
    assert not list(tmp_path.glob(".source-restore-*"))


def test_unreferenced_source_excluded_and_legacy_package_unchanged(handoff, tmp_path):
    project, root, _, _ = handoff
    other = root / "ordinary.mp4"
    other.write_bytes(b"different ordinary media")
    project.sequence.tracks[0].clips[0].asset_ref.source_path = str(other)
    package = tmp_path / "legacy.cvkpkg"
    pack_project(project, str(package))
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["packageFormat"] == 1
        assert "generatedSources" not in manifest
    restored, _, warnings = unpack_project(str(package), str(tmp_path / "legacy"))
    assert not warnings
    assert Path(restored.sequence.tracks[0].clips[0].asset_ref.source_path).read_bytes() == other.read_bytes()
    assert referenced_sources({sha(other.read_bytes())}, {root / ".generated"}) == []


def test_source_package_rejects_linked_media_directory_before_writing(handoff, tmp_path):
    project, _, _, _ = handoff
    package = tmp_path / "portable.cvkpkg"
    pack_project(project, str(package))
    destination = tmp_path / "linked-destination"
    destination.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "user.txt").write_bytes(b"keep")
    if os.name == "nt":
        # Junctions require no developer-mode/admin grant and are not reported
        # by Path.is_symlink(). Test the actual Windows filesystem boundary.
        subprocess.run(["cmd", "/c", "mklink", "/J", str(destination / "media"), str(outside)],
                       capture_output=True, text=True, check=True)
        assert not (destination / "media").is_symlink()
    else:
        (destination / "media").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProjectPackError, match="directory links"):
        unpack_project(str(package), str(destination), overwrite=True)
    assert sorted(p.name for p in outside.iterdir()) == ["user.txt"]
    assert (outside / "user.txt").read_bytes() == b"keep"
