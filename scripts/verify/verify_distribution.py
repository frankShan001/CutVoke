"""Inspect a local wheel + source candidate without installing or publishing it."""
from __future__ import annotations

import argparse
import ast
from email.parser import Parser
import json
from pathlib import Path, PurePosixPath
import tarfile
import zipfile


def check_names(names):
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe archive path: {name}")
        if any(part in {"__pycache__", "node_modules", ".venv", ".git"} for part in path.parts):
            raise ValueError(f"local dependency/cache leaked into archive: {name}")
        if path.suffix in {".pyc", ".pyo", ".sqlite", ".db", ".part"}:
            raise ValueError(f"runtime file leaked into archive: {name}")


def require(names, required):
    missing = sorted(set(required) - set(names))
    if missing:
        raise ValueError("missing distribution files: " + ", ".join(missing))


def verify(directory: Path) -> dict:
    wheels = list(directory.glob("*.whl"))
    sources = list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("candidate directory must contain exactly one wheel and one source archive")
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        check_names(names)
        require(names, ["cutvoke/__init__.py", "cutvoke/main.py", "cutvoke/core/mcp_server.py",
                        "cutvoke/web/index.html", "cutvoke/core/builtin_presets.json",
                        "cutvoke/core/builtin_resource_pack.json",
                        "cutvoke/assets/fonts/NotoSansSC-VF.ttf",
                        "cutvoke/core/vlog_fx.py", "cutvoke/assets/effects/vlog-labels.json"])
        if not any(n.startswith("cutvoke/web/assets/") and n.endswith(".js") for n in names):
            raise ValueError("wheel has no compiled editor JavaScript")
        metadata_name, = [n for n in names if n.endswith(".dist-info/METADATA")]
        metadata = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
        version = metadata["Version"]
        module = ast.parse(archive.read("cutvoke/__init__.py").decode("utf-8"))
        runtime_version = next(ast.literal_eval(n.value) for n in module.body
                               if isinstance(n, ast.Assign) and any(
                                   isinstance(t, ast.Name) and t.id == "__version__" for t in n.targets))
        if runtime_version != version:
            raise ValueError("wheel metadata and runtime versions disagree")
        wheel_count = len(names)
    with tarfile.open(sources[0], "r:gz") as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        check_names(names)
        if any(m.issym() or m.islnk() or m.isdev() for m in members):
            raise ValueError("source archive contains a link or device")
        root = f"cutvoke-{version}"
        require(names, [f"{root}/{p}" for p in (
            "PKG-INFO", "pyproject.toml", "setup.py", "LICENSE", "README.md", "README.zh-CN.md",
            "CONTRIBUTING.md", "CONTRIBUTING.zh-CN.md", "SECURITY.md", ".gitattributes",
            ".artifacts/web/index.html", "web/app/package.json", "web/app/src/main.tsx",
            "skills/cutvoke-editor/SKILL.md", "skills/cutvoke-editor/scripts/demo.py",
            "effects/transitions/cutvoke.transition.community.soft-reveal/manifest.json",
            "effects/transitions/cutvoke.transition.community.luma-ink/manifest.json",
            "effects/transitions/cutvoke.transition.community.luma-ink/LICENSE",
            "docs/user/vlog-effects.md", "docs/user/vlog-effects.zh-CN.md",
            "scripts/verify/demo_vlog_effects.py",
            "scripts/verify/check_layering.py", "scripts/verify/verify_distribution.py",
            "desktop/package.json", "desktop/engine.spec", ".github/workflows/ci.yml")])
        pkg = archive.extractfile(f"{root}/PKG-INFO")
        if pkg is None or Parser().parsestr(pkg.read().decode("utf-8"))["Version"] != version:
            raise ValueError("source archive and wheel versions disagree")
    return {"ok": True, "version": version, "wheel": wheels[0].name,
            "source": sources[0].name, "wheelEntries": wheel_count,
            "sourceEntries": len(names), "checks": ["editor bundled", "runtime version matches",
            "source workflow included", "no caches/dependencies/runtime databases", "safe archive paths"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = verify(args.directory.resolve())
    encoded = json.dumps(result, indent=2) + "\n"
    if args.report:
        args.report.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
