"""Copy-and-switch migration. Never delete original media or legacy records."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

from cutvoke.storage.project_directory import (
    DATABASE, ENTRY, DirectoryProjectStore, _atomic_json, _map_paths, _walk_paths, _refs)
from cutvoke.storage.store import ProjectStore, RevisionConflict


def _state_token(store, pid):
    return hashlib.sha256(json.dumps([_snapshot(store, pid), ProjectStore.list_assets(store)],
                                    sort_keys=True).encode()).hexdigest()


def _snapshot(store, pid):
    result = {}
    with store._lock, store._transaction(immediate=False):
        for table in ("projects", "history", "events", "idempotency", "export_jobs", "edit_leases", "asr_jobs"):
            columns = [r[1] for r in store._conn.execute(f"PRAGMA table_info({table})")]
            if "project_id" not in columns:
                continue
            rows = store._conn.execute(f"SELECT * FROM {table} WHERE project_id=? ORDER BY rowid", (pid,)).fetchall()
            result[table] = {"columns": columns, "rows": [list(row) for row in rows],
                             "schema": store._conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]}
        if not result.get("projects", {}).get("rows"):
            raise ValueError("工程不存在")
        for row in result.get("edit_leases", {}).get("rows", []):
            if dict(zip(result["edit_leases"]["columns"], row))["expires_at"] > time.time():
                raise ValueError("请先结束 Agent 编辑租约，再迁移工程")
        for table in ("export_jobs", "asr_jobs"):
            for row in result.get(table, {}).get("rows", []):
                if dict(zip(result[table]["columns"], row)).get("status") in ("queued", "running"):
                    raise ValueError("请先完成或取消后台任务，再迁移工程")
    return result


def _inventory(store, snapshot):
    paths, ids, exports = set(), set(), set()
    for table, content in snapshot.items():
        for row in content["rows"]:
            fields = dict(zip(content["columns"], row))
            if fields.get("out_path"):
                exports.add(fields["out_path"])
            for key, raw in fields.items():
                if key.endswith("_json") and raw:
                    value = json.loads(raw)
                    paths.update(p for p in _walk_paths(value) if p and Path(p).is_absolute())
                    ids.update(ref["assetId"] for ref in _refs(value))
    assets = [asset for aid in ids if (asset := store.get_asset(aid))]
    paths.update(asset["path"] for asset in assets if asset.get("path"))
    files, missing = [], []
    for name in sorted(paths | exports):
        p = Path(name)
        if p.is_dir():
            continue
        category = "exports" if name in exports else "media"
        if not p.is_file():
            if category == "media":
                missing.append(name)
            continue
        stat = p.stat()
        files.append({"source": name, "bytes": stat.st_size, "mtimeNs": stat.st_mtime_ns, "category": category})
    return files, missing, assets


def plan_migration(library, pid):
    if library.directory(pid):
        raise ValueError("工程已经使用独立目录")
    snapshot = _snapshot(library, pid)
    files, missing, assets = _inventory(library, snapshot)
    token = hashlib.sha256(json.dumps([snapshot, files, missing], sort_keys=True).encode()).hexdigest()
    project = library.load(pid)
    return {"projectId": pid, "revision": project.revision, "token": token,
            "fileCount": len(files), "copyBytes": sum(f["bytes"] for f in files),
            "missing": missing, "canMigrate": not missing,
            "historySnapshots": len(snapshot.get("history", {}).get("rows", [])),
            "originalFilesRetained": True,
            "suggestedDirectory": str(library.projects_root / (
                "project-" + hashlib.sha256(pid.encode()).hexdigest()[:12]))}


def migrate_project(library, pid, *, token, directory=None):
    # Rebuild the exact inventory; an old plan never silently migrates new edits.
    plan = plan_migration(library, pid)
    if token != plan["token"]:
        raise RevisionConflict("工程或素材已变化，请重新预览迁移")
    if plan["missing"]:
        raise ValueError("存在缺失素材，请先重新链接后再迁移")
    root = Path(directory or plan["suggestedDirectory"]).expanduser()
    if not root.is_absolute():
        raise ValueError("请选择绝对路径作为工程目录")
    root = root.resolve()
    if root.exists():
        raise ValueError("目标目录已存在，请选择一个新的工程目录")
    root.parent.mkdir(parents=True, exist_ok=True)
    snapshot = _snapshot(library, pid)
    files, _, assets = _inventory(library, snapshot)
    project = library.load(pid)
    # A whole-library backup protects the original catalogue and old records.
    backup_root = Path(library.path).resolve().parent / "backups"
    backup_root.mkdir(exist_ok=True)
    backup = backup_root / ("before-project-directory-" + str(time.time_ns()) + ".sqlite")
    library.backup_to(str(backup))
    child = DirectoryProjectStore(root, shared=library, create_id=pid, name=project.name)
    child.manifest["state"] = "preparing"
    _atomic_json(root / ENTRY, child.manifest)
    try:
        mapping = {}
        for item in files:
            mapping[item["source"]] = child.collect_file(item["source"], folder=item["category"])
        with child._lock, child._transaction():
            for table, content in snapshot.items():
                if table == "edit_leases":
                    continue
                child._conn.execute(content["schema"].replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))
                columns = content["columns"]
                placeholders = ",".join("?" for _ in columns)
                for row in content["rows"]:
                    child._conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})", row)
            # Preserve the original asset metadata; only the path and ownership change.
            for asset in assets:
                child._conn.execute("INSERT OR REPLACE INTO assets "
                    "(asset_id,name,path,size,kind,duration,has_video,has_audio,width,height,created_at,builtin,audio_role,sha256,relative_path) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    asset["assetId"], asset["name"], mapping.get(asset["path"], asset["path"]), asset["size"],
                    asset["kind"], asset.get("duration"), int(asset.get("hasVideo", False)), int(asset.get("hasAudio", False)),
                    asset.get("width"), asset.get("height"), time.time(), 0, asset.get("audioRole", "unclassified"),
                    asset.get("sha256"), asset.get("relativePath")))
        # Register from original paths so path-only media keeps its imported
        # display name before paths become content-hash basenames.
        # Also register references that entered through Agent edits rather than
        # the shared media ledger, including media retained only in undo states.
        for table in ("projects", "history"):
            column = "project_json" if table == "projects" else "snapshot_json"
            for (raw,) in child._conn.execute(f"SELECT {column} FROM {table}").fetchall():
                if raw:
                    from cutvoke.domain.model import Project
                    child._own_project(Project.from_dict(json.loads(raw)))
        child.rewrite_paths(lambda value: mapping.get(value, value))
        child._conn.execute("UPDATE projects SET updated_at=? WHERE project_id=?", (time.time(), pid))
        child.backup_to(str(root / "backups" / "initial.sqlite"))
        # Check again after long file copies. The old project is still authoritative.
        current = plan_migration(library, pid)
        if current["token"] != token:
            raise RevisionConflict("复制期间工程或素材发生变化，原工程未切换；请重新预览迁移")
        with library._directory_lock, library._lock, library._transaction():
            if _snapshot(library, pid) != snapshot:
                raise RevisionConflict("工程已变化，未切换存储位置")
            child.manifest["state"] = "ready"
            _atomic_json(root / ENTRY, child.manifest)
            migrated_token = _state_token(child, pid)
            library._conn.execute("INSERT INTO project_directories(project_id,root,migrated_token) VALUES (?,?,?)",
                                  (pid, str(root), migrated_token))
            library._directories[pid] = child
        return {"projectId": pid, "directory": str(root), "entryPath": str(root / ENTRY),
                "copiedFiles": len(mapping), "copiedBytes": plan["copyBytes"],
                "backupPath": str(backup), "originalFilesRetained": True,
                "historySnapshots": plan["historySnapshots"]}
    except BaseException:
        # Failed migration folders remain explicitly marked as incomplete for
        # inspection. No deletion, source move, or partial catalogue switch.
        child.close()
        raise


def rollback_migration(library, pid):
    child = library._target(pid)
    if child is None:
        raise ValueError("工程尚未迁移")
    with library._directory_lock, library._lock:
        row = library._conn.execute("SELECT migrated_token FROM project_directories WHERE project_id=?", (pid,)).fetchone()
        current = _state_token(child, pid)
        if not row or not row[0] or current != row[0]:
            raise ValueError("迁移后工程已修改，请先备份当前工程；不能回退到旧快照")
        with library._transaction():
            library._conn.execute("DELETE FROM project_directories WHERE project_id=?", (pid,))
        child.close()
        library._directories.pop(pid, None)
    return {"projectId": pid, "restoredLegacyLibrary": True, "directoryRetained": True}
