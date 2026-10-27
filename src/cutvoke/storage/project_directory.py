"""Self-contained project directories and a backwards-compatible library index.

The library database remains the home of legacy projects and application-wide
resources. Directory projects keep their authoritative SQLite database, including
undo/redo and jobs, beside their media. The index never substitutes an old legacy
snapshot when a registered directory is disconnected.
"""
from __future__ import annotations

from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid

from cutvoke.domain.model import Project
from cutvoke.domain.effect_files import effect_file_paths, map_effect_files
from cutvoke.storage.store import ProjectStore, RevisionConflict
from cutvoke.storage.workspace_paths import is_path_field

ENTRY = "project.cutvoke"
DATABASE = "project.sqlite"
FORMAT = "cutvoke.project-directory"
FOLDERS = ("media", "cache", "exports", "backups")


def _atomic_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".part")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _walk_paths(value, field=""):
    if isinstance(value, dict):
        if 'effectId' in value:
            yield from (path for path, _kind in effect_file_paths(value))
        for key, item in value.items():
            if key != 'params' or 'effectId' not in value:
                yield from _walk_paths(item, key)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_paths(item, field)
    elif isinstance(value, str) and is_path_field(field):
        yield value


def _map_paths(value, replace, field=""):
    if isinstance(value, dict):
        if 'effectId' in value:
            mapped = map_effect_files(value, replace)
            return {key: item if key == 'params' else _map_paths(item, replace, key)
                    for key, item in mapped.items()}
        return {key: _map_paths(item, replace, key) for key, item in value.items()}
    if isinstance(value, list):
        return [_map_paths(item, replace, field) for item in value]
    return replace(value) if isinstance(value, str) and is_path_field(field) else value


def _refs(value):
    if isinstance(value, dict):
        if "assetId" in value and value.get("sourcePath"):
            yield value
        for item in value.values():
            yield from _refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _refs(item)


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _entry(root):
    path = Path(root).expanduser().resolve()
    root = path.parent if path.name in (ENTRY, DATABASE) else path
    if (root / ENTRY).is_symlink():
        raise ValueError("工程入口不能是符号链接")
    data = json.loads((root / ENTRY).read_text("utf-8"))
    if (data.get("format") != FORMAT or data.get("version") != 1 or
            not isinstance(data.get("projectId"), str) or not data["projectId"] or
            data.get("database") != DATABASE or data.get("state", "ready") != "ready"):
        raise ValueError("不是受支持的 CutVoke 工程目录")
    database = root / DATABASE
    if not database.is_file() or database.is_symlink():
        raise ValueError("工程数据库不存在或不是普通文件")
    return root, data


class DirectoryProjectStore(ProjectStore):
    """An ordinary ProjectStore whose complete durable state lives in one folder."""

    def __init__(self, root, *, shared=None, create_id=None, name=""):
        self.root = Path(root).expanduser().resolve()
        self.shared = shared
        self._copy_lock = threading.RLock()
        self._fingerprints = {}
        if create_id is not None:
            self.root.mkdir(parents=True, exist_ok=False)
            for folder in FOLDERS:
                (self.root / folder).mkdir()
            self.manifest = {"format": FORMAT, "version": 1, "projectId": create_id,
                             "name": name, "database": DATABASE,
                             "rootPath": str(self.root), "createdAt": time.time()}
            _atomic_json(self.root / ENTRY, self.manifest)
        else:
            self.root, self.manifest = _entry(self.root)
        super().__init__(str(self.root / DATABASE))
        self._rebase()

    def _rebase(self):
        old = str(self.manifest.get("rootPath", self.root)).replace("\\", "/").rstrip("/")
        if old == str(self.root).replace("\\", "/"):
            return
        def replace(value):
            normalized = value.replace("\\", "/")
            if normalized.casefold().startswith(old.casefold() + "/"):
                relative = normalized[len(old) + 1:]
                target = (self.root / relative).resolve()
                if not target.is_relative_to(self.root):
                    raise ValueError("工程文件路径超出工程目录")
                return str(target)
            return value
        self.rewrite_paths(replace)
        self.manifest["rootPath"] = str(self.root)
        _atomic_json(self.root / ENTRY, self.manifest)

    def rewrite_paths(self, replace):
        """Rebase references, retaining edits, events and signed snapshot structure."""
        json_columns = {
            "projects": ("project_json", "sequence_json"), "history": ("snapshot_json",),
            "events": ("payload_json",), "idempotency": ("result_json",),
            "export_jobs": ("project_json", "request_json", "result_json", "error_json"),
            "asr_jobs": ("payload_json", "snapshot_json"),
        }
        with self._lock, self._transaction():
            for table, columns in json_columns.items():
                fields = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
                for column in columns:
                    if column not in fields:
                        continue
                    for rowid, raw in self._conn.execute(f"SELECT rowid,{column} FROM {table}").fetchall():
                        if raw:
                            value = json.loads(raw)
                            updated = _map_paths(value, replace)
                            if updated != value:
                                self._conn.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?",
                                                   (json.dumps(updated, ensure_ascii=False), rowid))
            for table, column in (("assets", "path"), ("export_jobs", "out_path")):
                for rowid, value in self._conn.execute(f"SELECT rowid,{column} FROM {table}").fetchall():
                    if value:
                        updated = replace(value)
                        if updated != value:
                            self._conn.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (updated, rowid))

    def collect_file(self, source, *, folder="media"):
        source = Path(source).expanduser().resolve()
        if source.is_relative_to(self.root):
            if not source.is_file() or source.is_symlink():
                raise ValueError(f"工程素材不可访问：{source}")
            return str(source)
        if not source.is_file():
            raise ValueError(f"素材缺失，请先重新链接：{source}")
        before = source.stat()
        stamp = (str(source), before.st_size, before.st_mtime_ns)
        digest = self._fingerprints.get(stamp)
        if digest is None:
            digest = _digest(source)
            self._fingerprints[stamp] = digest
        extension = source.suffix.lower()[:16]
        destination = self.root / folder / (digest + extension)
        if destination.is_symlink() or not destination.resolve().is_relative_to(self.root):
            raise ValueError("工程素材目录包含外部链接")
        if destination.exists() and (not destination.is_file() or _digest(destination) != digest):
            raise ValueError(f"工程中已有同名但内容不同的文件，请先检查：{destination}")
        if not destination.is_file():
            temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".part")
            try:
                shutil.copy2(source, temporary)
                after = source.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or _digest(temporary) != digest:
                    raise ValueError("复制过程中源文件发生变化，请重试")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        return str(destination)

    def _own_project(self, project):
        with self._copy_lock:
            data = project.to_dict(compact=True)
            copied = {}
            def own(value):
                if value:
                    if value not in copied:
                        source = Path(value).expanduser()
                        if not source.is_absolute() and (self.root / source).exists():
                            source = self.root / source
                        copied[value] = self.collect_file(source)
                    return copied[value]
                return value
            updated = _map_paths(data, own)
            original_names = {owned: Path(original).name for original, owned in copied.items()}
            seen = set()
            for ref in _refs(updated):
                # sourcePath is a valid public insertion contract. Give these
                # references an identity before registering copied media; an
                # empty ID previously left the project ledger entirely empty.
                if not ref["assetId"]:
                    ref["assetId"] = "asset_" + _digest(ref["sourcePath"])
                if ref["assetId"] in seen:
                    continue
                seen.add(ref["assetId"])
                path = Path(ref["sourcePath"])
                if not path.is_file():
                    continue
                existing = super().get_asset(ref["assetId"])
                if existing and existing["path"] == str(path):
                    continue
                original = self.shared.get_asset(ref["assetId"]) if self.shared else None
                kind = (original or {}).get("kind") or (
                    "image" if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".svg") else
                    "audio" if path.suffix.lower() in (".mp3", ".wav", ".m4a", ".flac", ".ogg") else "video")
                super().add_asset(asset_id=ref["assetId"], name=(original or {}).get("name", original_names.get(str(path), path.name)),
                    path=str(path), size=path.stat().st_size, kind=kind,
                    duration=(original or {}).get("duration"), has_video=(original or {}).get("hasVideo", kind == "video"),
                    has_audio=(original or {}).get("hasAudio", kind == "audio"),
                    width=(original or {}).get("width"), height=(original or {}).get("height"),
                    sha256=_digest(path), builtin=False)
            if updated != data:
                owned = Project.from_dict(updated)
                project.__dict__.update(owned.__dict__)
            return copied

    def commit(self, project, **kwargs):
        if project.project_id != self.manifest["projectId"]:
            raise ValueError("一个工程目录只能包含一个工程")
        project._directory_root = str(self.root)
        with self._copy_lock, self._lock, self._transaction():
            mapping = self._own_project(project)
            for key in ("event_payload", "history_push"):
                if kwargs.get(key) is not None:
                    kwargs[key] = _map_paths(kwargs[key], lambda value: mapping.get(value, value))
            if kwargs.get("idem_entry"):
                cid, request_hash, result = kwargs["idem_entry"]
                kwargs["idem_entry"] = (cid, request_hash, json.dumps(
                    _map_paths(json.loads(result), lambda value: mapping.get(value, value)), ensure_ascii=False))
            return super().commit(project, **kwargs)

    def save(self, project):
        if project.project_id != self.manifest["projectId"]:
            raise ValueError("一个工程目录只能包含一个工程")
        project._directory_root = str(self.root)
        with self._copy_lock, self._lock, self._transaction():
            self._own_project(project)
            return super().save(project)

    def get_asset(self, asset_id):
        local = super().get_asset(asset_id)
        if local:
            return local
        shared = self.shared.get_asset(asset_id) if self.shared else None
        return shared if shared and shared.get("builtin") else None

    def load(self, project_id):
        project=super().load(project_id)
        if project is not None: project._directory_root=str(self.root)
        return project

    def list_assets(self):
        local = super().list_assets()
        ids = {asset["assetId"] for asset in local}
        return local + ([asset for asset in self.shared.list_assets()
                         if asset.get("builtin") and asset["assetId"] not in ids] if self.shared else [])

    def export_path(self, value="", *, suffix=".mp4"):
        target = Path(value).expanduser() if value else Path("exports") / (self.manifest["projectId"] + suffix)
        if not target.is_absolute():
            if not target.parts or target.parts[0] not in ("exports", "backups"):
                target = Path("exports") / target
            target = self.root / target
        target = target.resolve()
        if not target.is_relative_to(self.root / "exports") and not target.is_relative_to(self.root / "backups"):
            raise ValueError("请将导出文件保存到工程目录的 exports 或 backups 中")
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(target)


class ProjectLibraryStore(ProjectStore):
    """Route directory projects while keeping old libraries readable until migrated."""

    def __init__(self, database, *, projects_root=None):
        super().__init__(database)
        self.projects_root = Path(projects_root or Path(database).resolve().parent / "projects").resolve()
        self._directories = {}
        self._directory_lock = threading.RLock()
        self._conn.execute("CREATE TABLE IF NOT EXISTS project_directories ("
                           "project_id TEXT PRIMARY KEY, root TEXT NOT NULL UNIQUE, migrated_token TEXT)")

    def directory(self, project_id):
        with self._lock:
            row = self._conn.execute("SELECT root FROM project_directories WHERE project_id=?", (project_id,)).fetchone()
        return Path(row[0]) if row else None

    def _target(self, project_id):
        root = self.directory(project_id)
        if root is None:
            return None
        with self._directory_lock:
            if not (root / ENTRY).is_file():
                raise ValueError(f"工程目录离线或已移动，请重新打开：{root}")
            existing = self._directories.get(project_id)
            if existing is not None:
                return existing
            store = DirectoryProjectStore(root, shared=self)
            if store.manifest["projectId"] != project_id:
                store.close()
                raise ValueError("工程入口与工程列表不匹配")
            self._directories[project_id] = store
            return store

    def _create_directory(self, project, directory=None):
        slug = re.sub(r"[^\w .-]", "_", project.project_id, flags=re.UNICODE).strip(" .")[:48] or "project"
        slug += "-" + hashlib.sha256(project.project_id.encode()).hexdigest()[:10]
        self.projects_root.mkdir(parents=True, exist_ok=True)
        root = Path(directory).expanduser() if directory else self.projects_root / slug
        if not root.is_absolute():
            raise ValueError("请选择绝对路径作为工程目录")
        root = root.resolve()
        child = DirectoryProjectStore(root, shared=self, create_id=project.project_id, name=project.name)
        try:
            child.commit(project, expected_prev_revision="-1")
            with self._lock, self._transaction():
                self._conn.execute("INSERT INTO project_directories(project_id,root) VALUES (?,?)", (project.project_id, str(root)))
        except BaseException:
            child.close()
            raise
        self._directories[project.project_id] = child
        return child

    def create_in_directory(self, project, directory):
        with self._directory_lock:
            if self.exists(project.project_id):
                raise ValueError("工程已经存在")
            return self._create_directory(project, directory)

    def commit(self, project, **kwargs):
        with self._directory_lock:
            child = self._target(project.project_id)
            if child:
                return child.commit(project, **kwargs)
            if super().exists(project.project_id):
                return super().commit(project, **kwargs)
            # Creation has no history/event arguments in EditService.
            if any(value for value in kwargs.values() if value not in ("-1", None, False)):
                raise ValueError("工程必须先创建再提交编辑")
            self._create_directory(project)

    def save(self, project):
        child = self._target(project.project_id)
        if child:
            return child.save(project)
        if super().exists(project.project_id):
            return super().save(project)
        self._create_directory(project)

    def list_all(self):
        with self._lock:
            directories = [r[0] for r in self._conn.execute("SELECT project_id FROM project_directories")]
        return sorted(set(super().list_all()) | set(directories))

    def list_projects(self):
        rows = {p["id"]: {**p, "storageKind": "legacy"} for p in super().list_projects()}
        with self._lock:
            directories = list(self._conn.execute("SELECT project_id,root FROM project_directories"))
        for pid, root in directories:
            try:
                child = self._target(pid)
                item = next(p for p in child.list_projects() if p["id"] == pid)
                rows[pid] = {**item, "storageKind": "directory", "directory": root, "available": True}
            except (OSError, ValueError, StopIteration):
                previous = rows.get(pid, {"id": pid, "name": pid, "revision": "", "updatedAt": ""})
                rows[pid] = {**previous, "storageKind": "directory", "directory": root, "available": False}
        return sorted(rows.values(), key=lambda p: p["updatedAt"], reverse=True)

    def open_directory(self, path):
        root, manifest = _entry(path)
        pid = manifest["projectId"]
        with self._directory_lock:
            previous = self.directory(pid)
            if previous and previous.resolve() != root and (previous / ENTRY).is_file():
                raise ValueError("此工程的另一个副本仍在工程列表中，请先关闭原副本后再移动")
            child = DirectoryProjectStore(root, shared=self)
            project = child.load(pid)
            if project is None:
                child.close()
                raise ValueError("工程入口与数据库不匹配")
            with self._lock, self._transaction():
                self._conn.execute("INSERT INTO project_directories(project_id,root) VALUES (?,?) "
                                   "ON CONFLICT(project_id) DO UPDATE SET root=excluded.root", (pid, str(root)))
            old = self._directories.pop(pid, None)
            if old:
                old.close()
            self._directories[pid] = child
            return project

    def get_asset(self, asset_id):
        # Use the base implementation for shared assets to avoid recursion from
        # a child's fallback; HTTP routes project-owned assets to that child.
        return super().get_asset(asset_id)

    def owner_of_asset(self, asset_id):
        """Find an owner only for old, unscoped asset inspection requests."""
        for pid in self.list_all():
            try:
                child = self._target(pid)
            except (OSError, ValueError):
                continue
            if child and ProjectStore.get_asset(child, asset_id):
                return pid
        return None

    def get_project_asset(self, project_id, asset_id):
        child = self._target(project_id)
        return child.get_asset(asset_id) if child else super().get_asset(asset_id)

    def _job_target(self, job_id):
        legacy = super().get_export_job(job_id)
        if legacy:
            return self._target(legacy["projectId"])
        for pid in self.list_all():
            try:
                child = self._target(pid)
            except (OSError, ValueError):
                continue
            if child and child.get_export_job(job_id):
                return child
        return None

    def list_export_jobs(self, project_id=None, limit=50):
        if project_id:
            child = self._target(project_id)
            return child.list_export_jobs(project_id, limit) if child else super().list_export_jobs(project_id, limit)
        items = [j for j in super().list_export_jobs(limit=limit) if self.directory(j["projectId"]) is None]
        for pid in self.list_all():
            try:
                child = self._target(pid)
            except (OSError, ValueError):
                continue
            if child:
                items.extend(child.list_export_jobs(limit=limit))
        return sorted(items, key=lambda j: j.get("createdAt", 0), reverse=True)[:limit]

    def close(self):
        for child in self._directories.values():
            child.close()
        self._directories.clear()
        super().close()


def _project_method(name, *, keyword=False):
    original = getattr(ProjectStore, name)
    @wraps(original)
    def routed(self, *args, **kwargs):
        pid = kwargs.get("project_id") if keyword else (args[0] if args else kwargs.get("project_id"))
        child = self._target(pid) if pid else None
        return getattr(child, name)(*args, **kwargs) if child else original(self, *args, **kwargs)
    return routed


for _name in ("db_revision", "get_idempotent", "history_depth", "redo_depth", "get_edit_lease",
              "peek_history", "pop_history", "load", "exists", "load_for_sync", "rename_project",
              "append_event", "events_since", "production_snapshot"):
    setattr(ProjectLibraryStore, _name, _project_method(_name))
for _name in ("acquire_edit_lease", "renew_edit_lease", "release_edit_lease", "release_edit_lease_status", "create_export_job", "claim_export_job"):
    setattr(ProjectLibraryStore, _name, _project_method(_name, keyword=True))
for _name in ("finish_export_job", "get_export_job", "update_export_job_progress", "get_export_project_snapshot"):
    def _job_method(name):
        original = getattr(ProjectStore, name)
        @wraps(original)
        def routed(self, job_id, *args, **kwargs):
            child = self._job_target(job_id)
            return getattr(child, name)(job_id, *args, **kwargs) if child else original(self, job_id, *args, **kwargs)
        return routed
    setattr(ProjectLibraryStore, _name, _job_method(_name))


def open_project_store(database):
    path = Path(database).expanduser().resolve()
    if path.name in (ENTRY, DATABASE) and (path.parent / ENTRY).is_file():
        return DirectoryProjectStore(path.parent)
    return ProjectLibraryStore(str(path))
