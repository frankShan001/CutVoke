"""Read Windows path capabilities and probe host Python without policy changes."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET


def manifest_capability(executable):
    import pefile  # Already pinned by the Windows PyInstaller build lock.
    with pefile.PE(str(executable), fast_load=True) as image:
        image.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        for entry in image.DIRECTORY_ENTRY_RESOURCE.entries:
            if entry.id != 24:  # RT_MANIFEST
                continue
            for resource in entry.directory.entries:
                for language in resource.directory.entries:
                    data = language.data.struct
                    root = ET.fromstring(image.get_data(data.OffsetToData, data.Size))
                    value = next((node.text for node in root.iter() if node.tag.endswith("longPathAware")), None)
                    return {"longPathAware": value.strip().lower() == "true" if value else None}
    return {"longPathAware": None}


def attempt(path):
    created = False
    result = {"pathLength": len(str(path)), "created": False}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        created = result["created"] = True
    except OSError as error:
        result.update(errno=error.errno, winerror=getattr(error, "winerror", None), detail=str(error))
    finally:
        if created:
            path.unlink()
    return result


def collect(engine, package_root, legacy_home, output):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            policy = winreg.QueryValueEx(key, "LongPathsEnabled")[0]
    except OSError:
        policy = None
    manifest = json.loads((package_root / "catalogs/builtin_resource_pack.json").read_text("utf-8"))
    legacy = []
    for item in manifest["files"]:
        if not item["path"].startswith("assets/stickers/") or not item["path"].endswith(".png"):
            continue
        target = legacy_home / "data/media/.builtin-resources" / manifest["packId"] / manifest["version"] / item["sha256"] / item["path"]
        temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
        legacy.append({"resource": item["path"], **attempt(temporary)})
    with tempfile.TemporaryDirectory(prefix="probe-", dir=output.parent) as temporary:
        boundaries = []
        for length in (259, 260, 261):
            base = Path(temporary)
            parent = base / ("d" * (length - len(str(base)) - 1 - 81))
            boundaries.append(attempt(parent / ("f" * 80)))
    result = {"python": sys.executable, "pythonVersion": sys.version,
              "registryLongPathsEnabled": policy,
              "pythonManifest": manifest_capability(sys.executable),
              "frozenManifest": manifest_capability(engine),
              "hostPythonCreateBoundaries": boundaries,
              "legacyStaging": {"attempted": len(legacy), "atOrAbove260": sum(x["pathLength"] >= 260 for x in legacy),
                                "failed": [x for x in legacy if not x["created"]]},
              "scope": "Read-only registry/PE inspection and isolated host-Python file creates; frozen behavior is measured separately through packaged HTTP resource installs."}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("engine", "package-root", "legacy-home", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("This capability probe requires real Windows")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    collect(args.engine, args.package_root, args.legacy_home, args.output)
