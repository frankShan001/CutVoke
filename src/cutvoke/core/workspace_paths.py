"""Atomic relocation of persisted file references without changing edit history."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from .protocol import Actor, CommandResult, Event, bump_revision


def path_key(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


def is_path_field(name: str) -> bool:
    lowered = name.lower().replace("_", "")
    return lowered in {"path", "file", "directory", "root", "paths"} or lowered.endswith(
        ("path", "paths", "file", "directory", "dir", "root"))


def path_values(value, field=""):
    """Find file fields while leaving captions, names and diagnostic prose alone."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from path_values(item, key)
    elif isinstance(value, list):
        for item in value:
            yield from path_values(item, field)
    elif isinstance(value, str) and is_path_field(field):
        yield value


def replace_paths(value, mapping, field=""):
    count = 0
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result[key], changed = replace_paths(item, mapping, key)
            count += changed
        return result, count
    if isinstance(value, list):
        result = []
        for item in value:
            rewritten, changed = replace_paths(item, mapping, field)
            result.append(rewritten)
            count += changed
        return result, count
    if isinstance(value, str) and is_path_field(field):
        replacement = mapping.get(path_key(value))
        if replacement is not None and replacement != value:
            return replacement, 1
    return value, 0


def validate_moves(moves):
    if not isinstance(moves, list) or not moves or len(moves) > 10000:
        raise ValueError("moves must contain 1..10000 file relocations")
    mapping = {}
    for move in moves:
        if not isinstance(move, dict) or set(move) - {"from", "to", "sha256", "missing"}:
            raise ValueError("invalid relocation fields")
        source, target = Path(move["from"]), Path(move["to"])
        if not source.is_absolute() or not target.is_absolute():
            raise ValueError("relocation paths must be absolute")
        key = path_key(str(source))
        if key in mapping or key == path_key(str(target)):
            raise ValueError("duplicate or unchanged relocation source")
        if "missing" in move and not isinstance(move["missing"], bool):
            raise ValueError("missing must be a boolean")
        if move.get("missing"):
            if source.exists() or target.exists():
                raise ValueError("a missing reference must remain missing at both paths")
        else:
            expected = move.get("sha256", "")
            if len(expected) != 64:
                raise ValueError("SHA256 is required for an existing file")
            for path in (source, target):
                if not path.is_file():
                    raise ValueError(f"file identity check failed: {path}")
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != expected:
                    raise ValueError(f"file identity check failed: {path}")
        mapping[key] = str(target)
    return mapping


JSON_COLUMNS = {
    "projects": ("sequence_json", "project_json"),
    "history": ("snapshot_json",),
    "events": ("payload_json",),
    "idempotency": ("result_json",),
    "export_jobs": ("project_json", "result_json", "error_json"),
}


def relocate_connection(connection, *, moves, expected_projects, command_id, request_hash, actor=None):
    """Called by ProjectStore inside one IMMEDIATE transaction."""
    cached = connection.execute(
        "SELECT request_hash,result_json FROM idempotency WHERE project_id=? AND command_id=?",
        ("__workspace__", command_id)).fetchone()
    if cached:
        if cached[0] != request_hash:
            raise ValueError("commandId already used with different relocation payload")
        return json.loads(cached[1])
    current = dict(connection.execute("SELECT project_id,revision FROM projects"))
    if not isinstance(expected_projects, dict) or current != expected_projects:
        from .store import RevisionConflict
        raise RevisionConflict("workspace projects changed; rebuild the relocation plan")
    now = time.time()
    if connection.execute("SELECT 1 FROM edit_leases WHERE expires_at>? LIMIT 1", (now,)).fetchone():
        raise ValueError("release active edit leases before relocating workspace files")
    if connection.execute("SELECT 1 FROM export_jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
        raise ValueError("finish or cancel active exports before relocating workspace files")
    mapping = validate_moves(moves)
    affected = set()
    changed_rows = 0
    for table, columns in JSON_COLUMNS.items():
        fields = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for column in columns:
            if column not in fields:
                continue
            rows = connection.execute(f"SELECT rowid,project_id,{column} FROM {table}").fetchall()
            for rowid, pid, raw in rows:
                if not raw:
                    continue
                rewritten, count = replace_paths(json.loads(raw), mapping)
                if count:
                    connection.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?",
                                       (json.dumps(rewritten, ensure_ascii=False), rowid))
                    if pid in current:
                        affected.add(pid)
                    changed_rows += 1
    for table, column in (("assets", "path"), ("export_jobs", "out_path")):
        for rowid, raw in connection.execute(f"SELECT rowid,{column} FROM {table}").fetchall():
            replacement = mapping.get(path_key(raw)) if raw else None
            if replacement and replacement != raw:
                connection.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (replacement, rowid))
                if table == "export_jobs":
                    pid = connection.execute("SELECT project_id FROM export_jobs WHERE rowid=?", (rowid,)).fetchone()[0]
                    if pid in current:
                        affected.add(pid)
                changed_rows += 1
    transaction_id = "txn_" + uuid.uuid4().hex
    revisions = {}
    for pid in sorted(affected):
        revision = bump_revision(current[pid])
        row = connection.execute("SELECT project_json FROM projects WHERE project_id=?", (pid,)).fetchone()
        project = json.loads(row[0]) if row[0] else None
        if project is not None:
            project["revision"] = revision
        connection.execute("UPDATE projects SET revision=?,project_json=?,updated_at=? WHERE project_id=?",
                           (revision, json.dumps(project, ensure_ascii=False) if project is not None else "", now, pid))
        event = Event(event_id="ev_" + uuid.uuid4().hex, project_id=pid, previous_revision=current[pid],
                      revision=revision, command_id=command_id, transaction_id=transaction_id,
                      actor=Actor(**actor) if actor else None, changed_entities=[{"kind": "workspace.pathsRelocated"}])
        sequence = connection.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE project_id=?", (pid,)).fetchone()[0]
        connection.execute("INSERT INTO events(event_id,project_id,payload_json,seq) VALUES(?,?,?,?)",
                           (event.event_id, pid, json.dumps(event.to_dict(), ensure_ascii=False), sequence))
        revisions[pid] = {"previousRevision": current[pid], "revision": revision}
    result = CommandResult(command_id=command_id, previous_revision="", revision="",
                           transaction_id=transaction_id, changed_entities=[{
                               "kind": "workspace.pathsRelocated", "paths": len(mapping),
                               "changedRows": changed_rows, "projects": revisions}]).to_dict()
    connection.execute("INSERT INTO idempotency(command_id,project_id,request_hash,result_json,committed_at) VALUES(?,?,?,?,?)",
                       (command_id, "__workspace__", request_hash, json.dumps(result, ensure_ascii=False), now))
    return result
