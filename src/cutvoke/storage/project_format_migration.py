"""Read-only discovery and explicit copy-only v1 to v2 migration."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import tempfile
import uuid

from cutvoke.storage.project_format import ProjectFormatError
from cutvoke.domain.model import LEGACY_PROJECT_SCHEMAS
from cutvoke.storage.v2_connection import TABLES

ENTRY = 'project.cutvoke'
V2_DATABASE = 'project-v2.sqlite'


class _SnapshotConnection(sqlite3.Connection):
    """SQLite may write WAL bookkeeping only inside this owned snapshot."""
    def close(self):
        try:
            super().close()
        finally:
            self._snapshot_directory.cleanup()


def readonly(database):
    # mode=ro still permits SQLite to create/update -wal/-shm beside the source.
    # Copy DB + WAL as one stable observation instead. Never use immutable=1:
    # it would silently discard transactions committed only in the WAL.
    path = Path(database).expanduser().absolute()
    temporary = tempfile.TemporaryDirectory(prefix='cutvoke-readonly-')
    try:
        paths = [path, Path(str(path) + '-wal'), Path(str(path) + '-journal')]
        def signature():
            result = []
            for item in paths:
                if item.is_symlink():
                    raise ProjectFormatError('Project database is missing or unsafe')
                if not item.exists():
                    result.append(None)
                    continue
                if not item.is_file():
                    raise ProjectFormatError('Project database is missing or unsafe')
                stat = item.stat()
                result.append((stat.st_dev, stat.st_ino, stat.st_size,
                               stat.st_mtime_ns, stat.st_ctime_ns))
            return result
        for attempt in range(3):
            before = signature()
            if before[0] is None:
                raise ProjectFormatError('Project database is missing or unsafe')
            if before[2] is not None and before[2][2]:
                raise ProjectFormatError('Project has a rollback journal; close its writer before inspection')
            try:
                contents = [item.read_bytes() if stamp is not None else None
                            for item, stamp in zip(paths, before)]
                if signature() != before:
                    continue
                if any(item.read_bytes() != content for item, content in zip(paths, contents)
                       if content is not None):
                    continue
                if signature() != before:
                    continue
            except FileNotFoundError:
                continue
            break
        else:
            raise ProjectFormatError('Project changed while reading; close its writer and try again')
        copy = Path(temporary.name) / 'snapshot.sqlite'
        copy.write_bytes(contents[0])
        if contents[1] is not None:
            Path(str(copy) + '-wal').write_bytes(contents[1])
        conn = sqlite3.connect(str(copy), isolation_level=None, factory=_SnapshotConnection)
        conn._snapshot_directory = temporary
        conn.execute('PRAGMA query_only=ON')
        return conn
    except BaseException:
        temporary.cleanup()
        raise


def state_token(conn, pid):
    digest = hashlib.sha256(pid.encode())
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        if not table.replace('_', '').isalnum():
            raise ProjectFormatError('Unsupported table identifier')
        digest.update(table.encode())
        for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
            digest.update(repr(row).encode())
    return digest.hexdigest()



def source_manifest(database, pid):
    """Read a directory's identity without opening or rebasing its writable store."""
    database = Path(database).resolve()
    if database.name != 'project.sqlite' or not (database.parent / ENTRY).exists():
        return None
    entry = database.parent / ENTRY
    if entry.is_symlink():
        raise ProjectFormatError('Unsafe source entry')
    raw = entry.read_bytes()
    manifest = json.loads(raw)
    if (manifest.get('format') != 'cutvoke.project-directory' or
            type(manifest.get('version')) is not int or manifest['version'] != 1 or
            manifest.get('database') != database.name or
            manifest.get('projectId') != pid or manifest.get('state', 'ready') != 'ready'):
        raise ProjectFormatError('Source entry/database identity mismatch')
    return manifest, raw


def database_token(conn, pid, database):
    token = state_token(conn, pid)
    manifest = source_manifest(database, pid)
    info = manifest[0] if manifest else None
    from cutvoke.storage.project_directory import _walk_paths, _digest
    names, ids = set(), set()
    prefix = 'cv2_' if conn.execute("SELECT 1 FROM sqlite_master WHERE name='cv2_projects'").fetchone() else ''
    for table, column in [('projects','project_json'),('projects','sequence_json'),('history','snapshot_json')]:
        for (raw,) in conn.execute(f'SELECT {column} FROM {prefix}{table} WHERE project_id=?', (pid,)):
            if raw:
                document = json.loads(raw)
                names.update(name for name in _walk_paths(document) if name)
                ids.update(asset_refs(document))
    for aid, name in conn.execute(f'SELECT asset_id,path FROM {prefix}assets'):
        if aid in ids and name:
            names.add(name)
    files = []
    directories, hashes = set(), set()
    for name in sorted(names):
        source = resolve_source_file(name, database, info)
        digest = _digest(source) if source.is_file() else None
        files.append((name, str(source), digest))
        if digest:
            hashes.add(digest)
            if (source.parent / '.generated').exists():
                directories.add(source.parent / '.generated')
    from cutvoke.storage.generated_sources import referenced_sources
    companions = [(item['path'], item['sha256']) for item in referenced_sources(hashes, directories)]
    payload = token.encode() + (manifest[1] if manifest else b'') + json.dumps([files, companions],sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def resolve_source_file(name, database, manifest):
    """The selected directory wins over an obsolete root, even if it still exists."""
    root = Path(database).resolve().parent
    normalized = name.replace('\\', '/')
    if manifest:
        old = str(manifest.get('rootPath', root)).replace('\\', '/').rstrip('/')
        windows = len(old) > 1 and old[1] == ':'
        probe, prefix = (normalized.casefold(), old.casefold()) if windows else (normalized, old)
        if probe.startswith(prefix + '/'):
            target = (root / normalized[len(old) + 1:]).resolve()
            if not target.is_relative_to(root):
                raise ProjectFormatError('Source media escapes selected directory')
            return target
    source = Path(normalized).expanduser()
    if not source.is_absolute():
        source = (root / source).resolve()
        if not source.is_relative_to(root):
            raise ProjectFormatError('Relative media escapes selected directory')
    return source


def asset_refs(value):
    if isinstance(value, dict):
        if isinstance(value.get('assetId'), str) and value['assetId']:
            yield value['assetId']
        for item in value.values():
            yield from asset_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from asset_refs(item)


def inspect_database(database, pid):
    conn = readonly(database)
    try:
        conn.execute('BEGIN')
        current = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cv2_projects'").fetchone()
        table = 'cv2_projects' if current else 'projects'
        row = conn.execute(f'SELECT schema_version,project_id,name FROM {table} WHERE project_id=?', (pid,)).fetchone()
        if row is None:
            raise ProjectFormatError('Project not found')
        expected = '2' if current else '1'
        if row[0] not in (('2',) if current else LEGACY_PROJECT_SCHEMAS):
            raise ProjectFormatError('Unsupported project schema')
        return {'status': 'current' if current else 'legacy', 'sourceVersion': int(expected),
                'sourceSchemaVersion': row[0], 'targetVersion': 2, 'projectId': row[1], 'name': row[2] or row[1],
                'token': database_token(conn, pid, database)}
    finally:
        conn.close()


def inspect_directory(path):
    root = Path(path).expanduser().resolve()
    if root.name in (ENTRY, 'project.sqlite', V2_DATABASE):
        root = root.parent
    entry = root / ENTRY
    if not entry.is_file() or entry.is_symlink():
        raise ProjectFormatError('Project entry is missing or unsafe')
    manifest = json.loads(entry.read_text('utf-8'))
    version = manifest.get('version')
    if manifest.get('format') != 'cutvoke.project-directory' or type(version) is not int or version not in (1,2):
        raise ProjectFormatError('Unsupported project directory version')
    database = 'project.sqlite' if version == 1 else V2_DATABASE
    if manifest.get('database') != database or manifest.get('state', 'ready') != 'ready':
        raise ProjectFormatError('Project directory is not ready')
    result = inspect_database(root / database, manifest.get('projectId', ''))
    if result['sourceVersion'] != version:
        raise ProjectFormatError('Project entry/database version mismatch')
    return result | {'directory': str(root), 'database': str(root / database)}


def _convert_json(value, old_id, new_id):
    if isinstance(value, list):
        return [_convert_json(item, old_id, new_id) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _convert_json(item, old_id, new_id) for key, item in value.items()}
    if result.get('projectId') == old_id:
        result['projectId'] = new_id
    if result.get('projectId') == new_id and 'revision' in result and 'schemaVersion' in result and ('sequence' in result or 'sequences' in result):
        if result['schemaVersion'] not in LEGACY_PROJECT_SCHEMAS:
            raise ProjectFormatError('Unsupported historical schema')
        result['schemaVersion'] = '2'
    return result


def convert_copy(database, pid, token, destination, *, shared=None):
    """Call only after explicit user choice. Never modify or register the source."""
    from cutvoke.storage.project_directory import DirectoryProjectStore, _atomic_json
    from cutvoke.domain.model import Project
    before = inspect_database(database, pid)
    if before['status'] != 'legacy' or before['token'] != token:
        raise ProjectFormatError('Project changed; inspect and confirm conversion again')
    source_entry = source_manifest(database, pid)
    source_info = source_entry[0] if source_entry else None
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=False)
    new_id = pid + '-v2-' + uuid.uuid4().hex[:8]
    manifest = {'format':'cutvoke.project-directory','version':2,'database':V2_DATABASE,
                'projectId':new_id,'name':before['name'],'rootPath':str(root),
                'state':'preparing','createdAt':time.time()}
    _atomic_json(root / ENTRY, manifest)
    for folder in ('media','cache','exports','backups'):
        (root / folder).mkdir()
    source = readonly(database)
    target = sqlite3.connect(str(root / V2_DATABASE), isolation_level=None)
    try:
        source.execute('BEGIN')
        if database_token(source, pid, database) != token:
            raise ProjectFormatError('Source changed before copying')
        source.backup(target)
    finally:
        source.close()
        target.close()
    conn = sqlite3.connect(str(root / V2_DATABASE), isolation_level=None)
    try:
        conn.execute('BEGIN IMMEDIATE')
        for (table,) in list(conn.execute("SELECT name FROM sqlite_master WHERE type='table'")):
            if not table.replace('_','').isalnum():
                raise ProjectFormatError('Unsupported table identifier')
            columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
            if 'project_id' in columns:
                conn.execute(f'DELETE FROM "{table}" WHERE project_id != ?', (pid,))
                if table in ('edit_leases','edit_lease_tokens','export_jobs','asr_jobs','idempotency'):
                    # Active work may not be silently copied into a new identity.
                    if table in ('export_jobs','asr_jobs') and 'status' in columns and conn.execute(f'SELECT 1 FROM "{table}" WHERE status IN (\'queued\',\'running\')').fetchone():
                        raise ProjectFormatError('Finish or cancel background jobs before conversion')
                    if table == 'edit_leases' and conn.execute('SELECT 1 FROM edit_leases WHERE expires_at > ?', (time.time(),)).fetchone():
                        raise ProjectFormatError('End the active edit lease before conversion')
                    conn.execute(f'DELETE FROM "{table}"')
                conn.execute(f'UPDATE "{table}" SET project_id=?', (new_id,))
            for column in columns:
                if column.endswith('_json'):
                    for rowid, raw in list(conn.execute(f'SELECT rowid,"{column}" FROM "{table}"')):
                        if raw:
                            data = _convert_json(json.loads(raw), pid, new_id)
                            conn.execute(f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?', (json.dumps(data,ensure_ascii=False), rowid))
            if table == 'projects':
                conn.execute("UPDATE projects SET schema_version='2'")
            if table in TABLES:
                conn.execute(f'ALTER TABLE "{table}" RENAME TO "cv2_{table}"')
        conn.execute('COMMIT')
    except BaseException:
        if conn.in_transaction:
            conn.execute('ROLLBACK')
        raise
    finally:
        conn.close()
    # Open only our new copy. Legacy data was read with mode=ro throughout.
    child = DirectoryProjectStore(root, shared=shared, allow_preparing=True)
    try:
        project = child.load(new_id)
        if project is None or project.schema_version != '2':
            raise ProjectFormatError('Converted snapshot failed validation')
        mapping = {}
        required_assets = set()
        from cutvoke.storage.project_directory import _walk_paths
        documents = []
        for table, column in [('projects','project_json'),('projects','sequence_json'),
                              ('history','snapshot_json')]:
            for (raw,) in child._conn.execute(f'SELECT {column} FROM {table}'):
                if raw:
                    documents.append(json.loads(raw))
        for document in documents:
            required_assets.update(asset_refs(document))
            for name in _walk_paths(document):
                if name and name not in mapping:
                    mapping[name] = child.collect_file(str(resolve_source_file(name, database, source_info)))
        # Asset-ID-only references have no path in the document. Include both
        # current and undo/redo dependencies, but not another project's ledger.
        for asset_id, path in list(child._conn.execute('SELECT asset_id,path FROM assets')):
            if asset_id not in required_assets:
                child._conn.execute('DELETE FROM assets WHERE asset_id=?', (asset_id,))
            elif path and path not in mapping:
                mapping[path] = child.collect_file(str(resolve_source_file(path, database, source_info)))
        for asset_id in required_assets:
            if child.get_asset(asset_id) is None:
                # An explicit sourcePath remains a supported reference without
                # a ledger entry; a bare unknown ID cannot become a ready copy.
                def has_path(value):
                    if isinstance(value, dict):
                        return (value.get('assetId') == asset_id and bool(value.get('sourcePath'))) or any(has_path(v) for v in value.values())
                    return isinstance(value, list) and any(has_path(v) for v in value)
                if not any(has_path(document) for document in documents):
                    raise ProjectFormatError('Missing asset dependency: ' + asset_id)
        # Preserve declared generated source companions inertly; never execute
        # or extract source code while converting a project.
        from cutvoke.storage.generated_sources import referenced_sources, retain_source_archive
        from cutvoke.storage.project_directory import _digest
        directories = {resolve_source_file(name, database, source_info).parent / '.generated' for name in mapping}
        directories = {path for path in directories if path.exists()}
        hashes = {_digest(Path(name)) for name in mapping.values()}
        for archive in referenced_sources(hashes, directories):
            retain_source_archive(archive, child.root / 'media' / '.generated')
        child.rewrite_paths(lambda value: mapping.get(value,value))
        # Register sourcePath-only media in both the live document and undo
        # snapshots, preserving human-readable names rather than hash basenames.
        original_names = {owned: Path(name.replace('\\','/')).name for name, owned in mapping.items()}
        current = child.load(new_id)
        child._own_project(current)
        child.save(current)
        for rowid, raw in list(child._conn.execute('SELECT rowid,snapshot_json FROM history')):
            if raw:
                historical = Project.from_dict(json.loads(raw))
                child._own_project(historical)
                child._conn.execute('UPDATE history SET snapshot_json=? WHERE rowid=?',
                                    (json.dumps(historical.to_dict(),ensure_ascii=False), rowid))
        for aid, path in child._conn.execute('SELECT asset_id,path FROM assets').fetchall():
            if path in original_names:
                child._conn.execute('UPDATE assets SET name=? WHERE asset_id=?', (original_names[path],aid))

        from cutvoke.domain.invariants import validate_project
        validation = validate_project(child.load(new_id))
        if not validation.structurally_valid:
            raise ProjectFormatError("Converted project is invalid: " + "; ".join(validation.errors))
        if inspect_database(database,pid)['token'] != token:
            raise ProjectFormatError('Source changed during copying; original retained')
        child.manifest['state']='ready'
        _atomic_json(root / ENTRY,child.manifest)
        return {'projectId':new_id,'name':project.name,'directory':str(root),
                'originalFilesRetained':True}
    finally:
        child.close()
