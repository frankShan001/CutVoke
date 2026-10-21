"""Copy a stopped desktop library, preserving the source and relocating owned paths."""
from __future__ import annotations

import json
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import time

from .workspace_paths import JSON_COLUMNS, path_key, path_values, replace_paths


def migrate_library(source_file: str, destination: str, *, cache_directory=None, cache_bytes=None) -> str:
    source = Path(source_file).resolve(strict=True)
    root = source.parent
    target = Path(destination).resolve()
    if target == root or target.is_relative_to(root) or root.is_relative_to(target):
        raise ValueError("Choose a separate empty folder / 请选择与原目录分开的空文件夹")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError("Destination must be empty / 目标文件夹必须为空")
    cache = None
    if cache_directory is not None:
        cache = Path(cache_directory)
        if not cache.is_absolute() or not isinstance(cache_bytes, int) or not 256 * 1024**2 <= cache_bytes <= 1024**4:
            raise ValueError("Invalid cache settings / 缓存设置无效")
        cache = cache.resolve()
        if cache.is_relative_to(root):
            cache = target / cache.relative_to(root)
        if cache.name not in (".preview-cache-v2", ".cutvoke-preview-cache"):
            cache = cache / ".cutvoke-preview-cache"
        if not cache.is_relative_to(target):
            cache.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=cache):
                pass
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".cutvoke-migration-", dir=target.parent))
    try:
        # Hold the writer lock through snapshot and publication. Separate reader
        # includes committed WAL pages without backing up an active write txn.
        with closing(sqlite3.connect(source, timeout=1)) as guard:
            guard.execute("BEGIN IMMEDIATE")
            if guard.execute("SELECT 1 FROM edit_leases WHERE expires_at>? LIMIT 1", (time.time(),)).fetchone():
                raise ValueError("Finish Agent editing before moving / 请等待 Agent 编辑完成")
            if guard.execute("SELECT 1 FROM export_jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
                raise ValueError("Finish exports before moving / 请等待导出完成")
            caches = {".preview-cache", ".preview-cache-v2", ".cutvoke-preview-cache"}
            def ignore(folder, names):
                skip = caches | ({source.name, source.name + "-wal", source.name + "-shm", "logs"}
                                 if Path(folder) == root else set())
                return [name for name in names if name in skip]
            for item in root.rglob("*"):
                if item.is_symlink() or getattr(item.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError("Linked folders cannot be migrated automatically / 含链接目录，请先移除链接再迁移")
            shutil.copytree(root, stage, dirs_exist_ok=True, ignore=ignore)
            database = stage / source.name
            with closing(sqlite3.connect(source)) as reader, closing(sqlite3.connect(database)) as output:
                reader.backup(output)
                mapping = {}
                def add(value):
                    p = Path(value)
                    if p.is_absolute() and p.is_relative_to(root):
                        mapping[path_key(value)] = str(target / p.relative_to(root))
                for table, columns in JSON_COLUMNS.items():
                    fields = {row[1] for row in output.execute(f"PRAGMA table_info({table})")}
                    for column in columns:
                        if column not in fields:
                            continue
                        for rowid, raw in output.execute(f"SELECT rowid,{column} FROM {table}").fetchall():
                            if not raw:
                                continue
                            value = json.loads(raw)
                            for item in path_values(value):
                                add(item)
                            value, count = replace_paths(value, mapping)
                            if count:
                                output.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?",
                                               (json.dumps(value, ensure_ascii=False), rowid))
                for table, column in (("assets", "path"), ("export_jobs", "out_path")):
                    for rowid, raw in output.execute(f"SELECT rowid,{column} FROM {table}").fetchall():
                        if raw:
                            add(raw)
                            if path_key(raw) in mapping:
                                output.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?",
                                               (mapping[path_key(raw)], rowid))
                output.commit()
                if output.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Copied project database failed integrity check")
            if cache:
                (stage / "preview-cache.json").write_text(json.dumps({
                    "directory": str(cache), "maxBytes": cache_bytes,
                }), encoding="utf-8")
            # Never replace an existing library, even if it appeared during copy.
            if target.exists():
                target.rmdir()  # Only an empty directory may be removed.
            os.rename(stage, target)
        return str(target / source.name)
    finally:
        # Only our randomly allocated staging directory is disposable.
        if stage.exists() and stage.parent == target.parent and stage.name.startswith(".cutvoke-migration-"):
            shutil.rmtree(stage)
