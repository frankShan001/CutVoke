"""CI only: compare Linux's frozen build tree with the engine from the extracted archive."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys

PROGRAMS = ("cutvoke-engine", "_internal/bin/ffmpeg", "_internal/bin/ffprobe")


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _regular_entry(path, before):
    # This auditor runs after the build on a runner-owned tree, not on untrusted live paths.
    # Still refuse a substituted symlink/special file or any observed change while hashing.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if _stamp(os.fstat(stream.fileno())) != _stamp(before):
            raise ValueError(f"file changed before hashing: {path.name!a}")
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        if _stamp(os.fstat(stream.fileno())) != _stamp(before) or _stamp(path.lstat()) != _stamp(before):
            raise ValueError(f"file changed while hashing: {path.name!a}")
    # electron-builder deliberately widens owner execute to group/others. Check
    # the owner's ability to execute (as needed by the application's user).
    return ("file", digest.hexdigest(), bool(before.st_mode & stat.S_IXUSR))


def _inventory(root):
    entries = {}
    directories = {PurePosixPath(".")}
    pending = [root]
    while pending:
        for path in pending.pop().iterdir():
            rel = PurePosixPath(path.relative_to(root).as_posix())
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                directories.add(rel)
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                entries[rel] = _regular_entry(path, info)
            elif stat.S_ISLNK(info.st_mode):
                entries[rel] = ("link", os.readlink(path))
            else:
                raise ValueError(f"unsupported entry: {str(rel)!a}")

    # Resolve within the scanned tree without probing a symlink target on disk.
    # Replaying each path component also catches escape-and-return paths and
    # links traversing a file as though it were a directory.
    for rel, entry in entries.items():
        if entry[0] != "link":
            continue
        parts = list(reversed(rel.parts))
        current = PurePosixPath(".")
        followed = 0
        while parts:
            part = parts.pop()
            if part == "..":
                if current == PurePosixPath("."):
                    raise ValueError(f"escaping link: {str(rel)!a}")
                current = current.parent
                continue
            if part in ("", "."):
                continue
            dest = current / part
            node = entries.get(dest)
            if node and node[0] == "link":
                followed += 1
                target = node[1]
                if followed > 40 or target.startswith("/"):
                    raise ValueError(f"absolute or cyclic link: {str(rel)!a}")
                parts.extend(reversed(target.split("/")))
            elif dest in directories or (node and not parts):
                current = dest
            else:
                raise ValueError(f"unresolved link: {str(rel)!a}")

    for program in PROGRAMS:
        entry = entries.get(PurePosixPath(program))
        if not (entry and entry[0] == "file" and entry[2]):
            raise ValueError(f"missing regular owner-executable program: {program}")
    return entries


def verify(expected, extracted):
    for root in (expected, extracted):
        if not stat.S_ISDIR(root.lstat().st_mode):
            raise ValueError("engine root must be a real directory")
    expected, extracted = expected.resolve(strict=True), extracted.resolve(strict=True)
    if expected in extracted.parents or extracted in expected.parents or expected.samefile(extracted):
        raise ValueError("builder and archive trees must be independent roots")
    before, after = _inventory(expected), _inventory(extracted)
    # Electron's copy step omits empty directories. All file/link parent
    # directories are nonetheless implied by these relative entries.
    for rel in sorted(before.keys() | after.keys()):
        if rel not in before:
            raise ValueError(f"unexpected extracted entry: {str(rel)!a}")
        if rel not in after:
            raise ValueError(f"missing extracted entry: {str(rel)!a}")
        if before[rel] != after[rel]:
            raise ValueError(f"changed extracted entry: {str(rel)!a}")
    return {"files": sum(entry[0] == "file" for entry in before.values()),
            "relativeLinks": sum(entry[0] == "link" for entry in before.values())}


if __name__ == "__main__":
    try:
        print(json.dumps(verify(*(Path(arg) for arg in sys.argv[1:]))))
    except (OSError, ValueError) as error:
        sys.exit(f"Linux frozen engine inventory: {error}")
