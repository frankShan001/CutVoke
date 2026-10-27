from pathlib import Path
import json
import shutil

import pytest

from cutvoke.domain.model import Project
from cutvoke.editing.service import EditService
from cutvoke.domain.protocol import Command
from cutvoke.storage.project_directory import ProjectLibraryStore, DirectoryProjectStore, ENTRY
from cutvoke.storage.store import ProjectStore
from cutvoke.storage.project_directory_migration import plan_migration, migrate_project, rollback_migration
from cutvoke.storage.store import RevisionConflict


def project_with_media(pid, source):
    return Project.from_dict({"schemaVersion": "1", "projectId": pid, "revision": "0",
        "sequence": {"id": "s", "width": 128, "height": 72, "fps": {"num": "24", "den": "1"},
            "tracks": [{"id": "v", "kind": "video", "clips": [{"id": "c",
                "assetRef": {"assetId": "test-source", "sourcePath": str(source)},
                "timelineStart": {"num": "0", "den": "1"},
                "timelineEnd": {"num": "1", "den": "1"},
                "sourceStart": {"num": "0", "den": "1"}}]}]}})


def test_new_projects_are_independent_and_survive_a_move(tmp_path):
    source = tmp_path / "source.png"
    from PIL import Image
    Image.new("RGB", (128, 72), "blue").save(source)
    library = ProjectLibraryStore(str(tmp_path / "library.sqlite"))
    one = project_with_media("one", source)
    two = project_with_media("two", source)
    library.commit(one)
    library.commit(two)
    first = library.directory("one")
    second = library.directory("two")
    assert first != second
    assert Path(one.sequence.tracks[0].clips[0].asset_ref.source_path).is_relative_to(first / "media")
    assert Path(two.sequence.tracks[0].clips[0].asset_ref.source_path).is_relative_to(second / "media")
    assert (first / ENTRY).is_file()
    assert len(list((first / "media").iterdir())) == 1
    service = EditService(store=library)
    service.execute(Command(type="track.add", project_id="one", expected_revision="0", payload={"kind": "audio"}))
    assert library.peek_history("one", 0) is not None
    revision = library.load("one").revision
    service.close()
    library.close()
    moved = tmp_path / "another disk" / "我的工程"
    moved.parent.mkdir()
    shutil.move(str(first), moved)
    source.unlink()
    reopened = DirectoryProjectStore(moved)
    loaded = reopened.load("one")
    assert loaded.revision == revision
    asset = Path(loaded.sequence.tracks[0].clips[0].asset_ref.source_path)
    assert asset.is_relative_to(moved / "media") and asset.is_file()
    service = EditService(store=reopened)
    service.execute(Command(type="history.undo", project_id="one", expected_revision=revision, payload={}))
    restored = reopened.load("one")
    assert len(restored.sequence.tracks) == 1
    assert Path(restored.sequence.tracks[0].clips[0].asset_ref.source_path).is_file()
    service.close()
    reopened.close()


def test_legacy_stays_readable_and_new_directory_is_indexed(tmp_path):
    db = tmp_path / "library.sqlite"
    legacy = ProjectStore(str(db))
    service = EditService(store=legacy)
    service.create_project("old")
    service.close()
    legacy.close()
    library = ProjectLibraryStore(str(db))
    assert library.load("old").project_id == "old"
    service = EditService(store=library)
    service.create_project("new")
    assert library.directory("old") is None
    assert library.directory("new") is not None
    assert {p["storageKind"] for p in library.list_projects()} == {"legacy", "directory"}
    service.close()
    library.close()


def test_missing_media_and_external_export_are_rejected(tmp_path):
    store = DirectoryProjectStore(tmp_path / "project", create_id="one")
    with pytest.raises(ValueError, match="素材缺失"):
        store.commit(project_with_media("one", tmp_path / "missing.mp4"))
    assert store.load("one") is None
    with pytest.raises(ValueError, match="exports"):
        store.export_path(str(tmp_path / "outside.mp4"))
    assert Path(store.export_path("final.mp4")) == store.root / "exports" / "final.mp4"
    store.close()


def legacy_library(tmp_path):
    from PIL import Image
    source = tmp_path / "original.png"
    Image.new("RGB", (128, 72), "red").save(source)
    database = tmp_path / "legacy.sqlite"
    store = ProjectStore(str(database))
    store.save(project_with_media("old", source))
    service = EditService(store=store)
    service.execute(Command(type="track.add", project_id="old", expected_revision="0", payload={"kind": "audio"}))
    service.close(); store.close()
    return ProjectLibraryStore(str(database)), source


def test_migration_keeps_history_references_and_rolls_back_without_deleting_files(tmp_path):
    library, source = legacy_library(tmp_path)
    before = library.load_for_sync("old")
    plan = plan_migration(library, "old")
    assert plan["canMigrate"] and plan["fileCount"] == 1 and plan["historySnapshots"] == 1
    result = migrate_project(library, "old", token=plan["token"], directory=str(tmp_path / "我的工程"))
    root = Path(result["directory"])
    child = library._target("old")
    assert source.is_file() and Path(result["backupPath"]).is_file()
    assert library.load_for_sync("old") != before
    assert library.load("old").revision == "1"
    assert child.get_asset("test-source")["path"].startswith(str(root / "media"))
    history = library.peek_history("old", 0)
    assert str(source) not in json.dumps(history)
    rollback_migration(library, "old")
    assert library.directory("old") is None and source.is_file() and (root / ENTRY).is_file()
    assert library.load("old").sequence.tracks[0].clips[0].asset_ref.source_path == str(source)
    library.close()


def test_migrated_folder_moves_without_original_library_and_can_undo_and_render(tmp_path):
    from cutvoke.rendering.render import RenderService
    import subprocess
    library, source = legacy_library(tmp_path)
    plan = plan_migration(library, "old")
    result = migrate_project(library, "old", token=plan["token"])
    original_root = Path(result["directory"])
    library.close(); source.unlink()
    moved = tmp_path / "moved-project"
    shutil.move(str(original_root), moved)
    independent = DirectoryProjectStore(moved)
    service = EditService(store=independent)
    service.execute(Command(type="history.undo", project_id="old", expected_revision="1", payload={}))
    project = independent.load("old")
    assert len(project.sequence.tracks) == 1
    output = independent.export_path("portable.mp4")
    RenderService().render(project, output)
    subprocess.run(["ffmpeg", "-v", "error", "-i", output, "-f", "null", "-"], check=True, capture_output=True)
    assert Path(output).is_relative_to(moved / "exports") and Path(output).stat().st_size > 100
    service.close(); independent.close()


def test_stale_migration_and_edited_rollback_are_rejected(tmp_path):
    library, source = legacy_library(tmp_path)
    plan = plan_migration(library, "old")
    source.write_bytes(source.read_bytes() + b"change")
    with pytest.raises(RevisionConflict):
        migrate_project(library, "old", token=plan["token"])
    plan = plan_migration(library, "old")
    migrate_project(library, "old", token=plan["token"])
    service = EditService(store=library)
    service.execute(Command(type="track.add", project_id="old", expected_revision="1", payload={"kind": "audio"}))
    with pytest.raises(ValueError, match="已修改"):
        rollback_migration(library, "old")
    assert library.directory("old") and library.load("old").revision == "2"
    service.close(); library.close()


def test_offline_directory_never_falls_back_to_old_snapshot(tmp_path):
    library, _ = legacy_library(tmp_path)
    plan = plan_migration(library, "old")
    result = migrate_project(library, "old", token=plan["token"])
    database = library.path
    library.close()
    moved = tmp_path / "relocated"
    shutil.move(result["directory"], moved)
    library = ProjectLibraryStore(database)
    assert library.list_projects()[0]["available"] is False
    with pytest.raises(ValueError, match="离线或已移动"):
        library.load("old")
    library.open_directory(moved / ENTRY)
    assert library.load("old").revision == "1" and library.list_projects()[0]["available"]
    library.close()


def test_failed_copy_never_switches_and_blocks_opening_partial_folder(tmp_path, monkeypatch):
    library, _ = legacy_library(tmp_path)
    plan = plan_migration(library, "old")
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(DirectoryProjectStore, "collect_file", fail)
    with pytest.raises(OSError, match="disk full"):
        migrate_project(library, "old", token=plan["token"])
    assert library.directory("old") is None
    with pytest.raises(ValueError):
        library.open_directory(plan["suggestedDirectory"])
    assert library.load("old").revision == "1"
    library.close()


def test_whole_library_move_relocates_internal_folders_and_preserves_external_ones(tmp_path):
    from cutvoke.storage.storage_migration import migrate_library
    from PIL import Image
    base = tmp_path / "library"; base.mkdir()
    source = tmp_path / "source.png"; Image.new("RGB", (128, 72), "green").save(source)
    library = ProjectLibraryStore(str(base / "library.sqlite"))
    library.commit(project_with_media("internal", source))
    external = tmp_path / "custom-project"
    library.create_in_directory(project_with_media("external", source), str(external))
    previous = library.directory("internal")
    library.close()
    moved = tmp_path / "new-library"
    new_database = migrate_library(str(base / "library.sqlite"), str(moved))
    base.rename(tmp_path / "old-library-backup")
    library = ProjectLibraryStore(new_database)
    assert library.directory("internal") == moved / previous.relative_to(base)
    assert library.directory("external") == external
    project = library.load("internal")
    assert Path(project.sequence.tracks[0].clips[0].asset_ref.source_path).is_file()
    assert str(base) not in project.sequence.tracks[0].clips[0].asset_ref.source_path
    assert library.load("external").project_id == "external"
    library.close()


def test_conflicting_commit_does_not_change_asset_ledger(tmp_path):
    from PIL import Image
    first = tmp_path / "first.png"; Image.new("RGB", (128, 72), "green").save(first)
    second = tmp_path / "second.png"; Image.new("RGB", (128, 72), "red").save(second)
    store = DirectoryProjectStore(tmp_path / "project", create_id="one")
    store.commit(project_with_media("one", first))
    original_asset = store.get_asset("test-source")
    project = project_with_media("one", second)
    project.revision = "2"
    with pytest.raises(RevisionConflict):
        store.commit(project, expected_prev_revision="1")
    assert store.get_asset("test-source") == original_asset
    assert store.load("one").revision == "0"
    store.close()
