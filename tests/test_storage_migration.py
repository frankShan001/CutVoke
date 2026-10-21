import json
from pathlib import Path
import sqlite3
import time

import pytest

from cutvoke.core.storage_migration import migrate_library
from cutvoke.core.store import ProjectStore


def library(tmp_path):
    root = tmp_path / "原 工程库"
    root.mkdir()
    source = root / "projects.sqlite"
    with ProjectStore(str(source)):
        pass
    media = root / "media" / "片段.png"
    media.parent.mkdir()
    media.write_bytes(b"media identity")
    return source, media


def test_copy_relocates_history_and_assets_preserving_caption_and_source(tmp_path):
    source, media = library(tmp_path)
    external = str(tmp_path / "external.wav")
    payload = json.dumps({"sourcePath": str(media), "text": str(media), "audioPath": external})
    # Keep this connection alive with committed WAL pages during the backup.
    connection = sqlite3.connect(source)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("INSERT INTO projects(project_id,schema_version,revision,sequence_json,updated_at) VALUES('p','1','r',?,0)", (payload,))
    connection.execute("INSERT INTO history(project_id,depth,snapshot_json,revision) VALUES('p',1,?,'r')", (payload,))
    connection.execute("INSERT INTO assets(asset_id,path,created_at) VALUES('a',?,0)", (str(media),))
    connection.commit()
    target = tmp_path / "新 工程库"
    result = migrate_library(str(source), str(target), cache_directory=str(source.parent / "media" / ".preview-cache-v2"), cache_bytes=2 * 1024**3)
    connection.close()
    with sqlite3.connect(result) as output:
        for table, column in (("projects", "sequence_json"), ("history", "snapshot_json")):
            value = json.loads(output.execute(f"SELECT {column} FROM {table}").fetchone()[0])
            assert value["sourcePath"] == str(target / "media" / media.name)
            assert value["text"] == str(media)
            assert value["audioPath"] == external
        assert Path(output.execute("SELECT path FROM assets").fetchone()[0]).read_bytes() == media.read_bytes()
        assert output.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with sqlite3.connect(source) as original:
        assert json.loads(original.execute("SELECT sequence_json FROM projects").fetchone()[0])["sourcePath"] == str(media)
    cache = json.loads((target / "preview-cache.json").read_text())
    assert cache["directory"] == str(target / "media" / ".preview-cache-v2")
    assert cache["maxBytes"] == 2 * 1024**3


@pytest.mark.parametrize("case", ["occupied", "nested", "ancestor", "lease", "export", "linked"])
def test_rejects_unsafe_or_busy_migration_without_changing_source(tmp_path, case):
    source, media = library(tmp_path)
    target = tmp_path / "target"
    if case == "occupied":
        target.mkdir()
        (target / "keep").write_text("existing")
    elif case == "nested":
        target = source.parent / "child"
    elif case == "ancestor":
        target = tmp_path
    elif case == "linked":
        try:
            (source.parent / "link").symlink_to(media.parent, target_is_directory=True)
        except OSError:
            # NTFS junctions do not require the Windows symlink privilege.
            import os
            import subprocess
            if os.name != "nt":
                raise
            subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                            "New-Item -ItemType Junction -Path $env:CUTVOKE_TEST_LINK -Value $env:CUTVOKE_TEST_TARGET -ErrorAction Stop | Out-Null"],
                           env={**os.environ, "CUTVOKE_TEST_LINK": str(source.parent / "link"),
                                "CUTVOKE_TEST_TARGET": str(media.parent)}, check=True, capture_output=True)
    else:
        with sqlite3.connect(source) as conn:
            if case == "lease":
                conn.execute("INSERT INTO edit_leases VALUES('p','lease','agent',?,0)", (time.time() + 60,))
            else:
                conn.execute("INSERT INTO export_jobs(job_id,project_id,revision,request_hash,out_path,quality,status,created_at,updated_at) VALUES('j','p','r','h','out','high','running',0,0)")
    with pytest.raises(ValueError):
        migrate_library(str(source), str(target))
    assert media.read_bytes() == b"media identity"
    assert source.exists()
    assert not list(tmp_path.glob(".cutvoke-migration-*"))
    if case == "occupied":
        assert (target / "keep").read_text() == "existing"


def test_copy_failure_leaves_original_and_no_published_library(tmp_path, monkeypatch):
    source, media = library(tmp_path)
    target = tmp_path / "target"
    import cutvoke.core.storage_migration as migration
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(migration.shutil, "copytree", fail)
    with pytest.raises(OSError, match="disk full"):
        migrate_library(str(source), str(target))
    assert source.exists() and media.read_bytes() == b"media identity"
    assert not target.exists()
    assert not list(tmp_path.glob(".cutvoke-migration-*"))


def test_desktop_settings_preflight_waits_for_background_work(tmp_path, monkeypatch):
    from cutvoke.core.httpapi import HttpApi
    from cutvoke.core.service import EditService
    from cutvoke.core.render import RenderService
    api = HttpApi(EditService(), RenderService(), media_dir=str(tmp_path / "media"))
    try:
        assert api.handle("GET", "/api/v1/settings/preflight", {})[0] == 200
        monkeypatch.setattr(api._preview_preparation, "busy", lambda: True)
        status, result = api.handle("GET", "/api/v1/settings/preflight", {})
        assert status == 409
        assert result["error"]["code"] == "SETTINGS_BUSY"
    finally:
        api.close()
