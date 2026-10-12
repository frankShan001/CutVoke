"""Copy legacy workspace media, then atomically relocate persisted references.

Default is a read-only plan. --apply makes a SQLite backup, verifies copied
bytes and commits through the editor command service. Original folders stay
in place until a maintainer relocates them using archive-plan.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore
from cutvoke.core.workspace_paths import JSON_COLUMNS, path_key, path_values, replace_paths


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_database(path):
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as connection:
        projects = {pid: {"revision": rev, "project": json.loads(raw) if raw else
                          {"revision": rev, "sequence": json.loads(sequence)}}
                    for pid, rev, raw, sequence in connection.execute(
                        "SELECT project_id,revision,project_json,sequence_json FROM projects")}
        values = set()
        for table, columns in JSON_COLUMNS.items():
            for column in columns:
                for (raw,) in connection.execute(f"SELECT {column} FROM {table}"):
                    if raw:
                        values.update(path_values(json.loads(raw)))
        for table, column in (("assets", "path"), ("export_jobs", "out_path")):
            values.update(raw for (raw,) in connection.execute(f"SELECT {column} FROM {table}") if raw)
    return projects, values


def legacy_path(value, workspace):
    path = Path(value)
    if not path.is_absolute():
        return None
    path = path.resolve()
    for name in ("output", "本地开发"):
        if path.is_relative_to(workspace / name):
            return path
    return None


def make_moves(values, workspace, destination):
    moves, seen = [], set()
    for value in sorted(values):
        source = legacy_path(value, workspace)
        if source is None or path_key(str(source)) in seen:
            continue
        seen.add(path_key(str(source)))
        if source.is_file():
            digest = sha256(source)
            if source.name == "Tidal_Museum_1080p.mp4":
                target = destination / "exports" / "tide-gallery-20260927" / source.name
            else:
                target = destination / "data" / "media" / digest[:2] / (digest + source.suffix.lower())
            moves.append({"from": str(source), "to": str(target), "sha256": digest})
        elif not source.exists():
            target = destination / "data" / "missing" / source.relative_to(workspace)
            moves.append({"from": str(source), "to": str(target), "missing": True})
        else:
            raise ValueError(f"unsupported directory reference: {source}")
    return moves


def copy_verified(source, target, digest=None):
    source, target = Path(source), Path(target)
    if source.is_symlink() or target.is_symlink():
        raise ValueError("symlinks are not supported by this migration")
    digest = digest or sha256(source)
    if target.exists() and (not target.is_file() or sha256(target) != digest):
        raise ValueError(f"target already contains different data: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(source, target)
    if sha256(target) != digest:
        raise ValueError(f"copy verification failed: {target}")


def verify_semantics(before, after, moves):
    if before.keys() != after.keys():
        raise AssertionError("project list changed during migration")
    mapping = {path_key(move["from"]): move["to"] for move in moves}
    for pid, original in before.items():
        expected, _ = replace_paths(original["project"], mapping)
        actual = after[pid]["project"].copy()
        actual["revision"] = expected["revision"]
        if actual != expected:
            raise AssertionError(f"project edit semantics changed: {pid}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--data", type=Path, default=Path.home() / ".cutvoke/data/projects.sqlite")
    parser.add_argument("--destination", type=Path, default=Path.home() / ".cutvoke")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    workspace, data, destination = args.workspace.resolve(), args.data.resolve(), args.destination.resolve()
    if destination.is_relative_to(workspace) or workspace.is_relative_to(destination):
        raise ValueError("destination must be separate from the source checkout")
    before, values = inspect_database(data)
    moves = make_moves(values, workspace, destination)
    report = {"apply": args.apply, "projectCount": len(before), "referenceCount": len(moves),
              "preexistingMissing": [move["from"] for move in moves if move.get("missing")],
              "destination": str(destination), "moves": moves}
    if not args.apply or not moves:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    stamp = time.strftime("layout-%Y%m%d-%H%M%S")
    backup = destination / "backups" / stamp
    backup.mkdir(parents=True, exist_ok=False)
    store = ProjectStore(str(data))
    service = EditService(store=store)
    try:
        store.backup_to(str(backup / "projects.sqlite"))
        before, values = inspect_database(backup / "projects.sqlite")
        moves = make_moves(values, workspace, destination)
        expected = {pid: item["revision"] for pid, item in before.items()}
        for move in moves:
            if not move.get("missing"):
                copy_verified(move["from"], move["to"], move["sha256"])
        # Keep delivery files and authoring notes accessible outside the checkout.
        output = workspace / "output/tide-gallery-20260927"
        if output.is_dir():
            for source in output.iterdir():
                if source.suffix in {".cvkpkg", ".md"}:
                    group = "packages" if source.suffix == ".cvkpkg" else "projects"
                    copy_verified(source, destination / group / output.name / source.name)
            if (output / "source").is_dir():
                for source in (output / "source").rglob("*"):
                    if source.is_file():
                        copy_verified(source, destination / "projects" / output.name / "source" / source.relative_to(output / "source"))
        command = Command(type="workspace.relocatePaths", command_id=stamp,
                          actor=Actor(kind="cli", id="workspace-layout"),
                          payload={"expectedProjects": expected, "moves": moves})
        (backup / "command.json").write_text(json.dumps(command.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        receipt = service.execute(command).to_dict()
        after, remaining = inspect_database(data)
        verify_semantics(before, after, moves)
        if any(legacy_path(value, workspace) for value in remaining):
            raise AssertionError("legacy references remain; original folders must stay in place")
        for move in moves:
            if not move.get("missing") and sha256(move["to"]) != move["sha256"]:
                raise AssertionError("relocated file content changed")
        archive = [{"from": str(workspace / name), "to": str(backup / "workspace-originals" / name)}
                   for name in ("output", "本地开发") if (workspace / name).exists()]
        (backup / "archive-plan.json").write_text(json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")
        report.update(moves=moves, receipt=receipt, backup=str(backup), verified=True,
                      referenceCount=len(moves), projectCount=len(after))
        (backup / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "moves"}, ensure_ascii=False, indent=2))
    finally:
        service.close()
        store.close()


if __name__ == "__main__":
    main()
