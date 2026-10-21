"""Verify application/engine locations and prevent obsolete executable imports."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.verify.check_asset_layout import check as check_assets


def check(root: Path = ROOT) -> list[str]:
    errors = check_assets(root)
    files = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root
    ).decode("utf-8").split("\0")
    files = sorted({name for name in files if name and (root / name).is_file()})
    for name in files:
        if name.startswith(("web/app/", "desktop/", "scripts/", "src/cutvoke/core/")):
            errors.append(f"Retired source directory: {name}")
        if name.startswith("tests/") and Path(name).parent == Path("tests") and name.endswith(".py"):
            errors.append(f"Group engine tests by responsibility: {name}")
        path = root / name
        if path.suffix == ".py":
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=name)
            except SyntaxError as exc:
                errors.append(f"Invalid Python source: {name}: {exc}")
                continue
            for node in ast.walk(tree):
                sources = [node.module or ""] if isinstance(node, ast.ImportFrom) else (
                    [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
                for source in sources:
                    if source == "cutvoke.core" or source.startswith("cutvoke.core.") or source == "scripts" or source.startswith("scripts."):
                        errors.append(f"Obsolete Python import in {name}:{node.lineno}: {source}")
        if name.startswith(("tools/", ".github/", "apps/desktop/")) and path.suffix in {
            ".py", ".cmd", ".ps1", ".cjs", ".mjs", ".yml", ".spec", ".sh"
        } and path.name != "check_repository_layout.py":
            text = path.read_text(encoding="utf-8")
            if re.search(r"web[/\\]app|src[/\\]cutvoke[/\\]core|apps[/\\]apps[/\\]desktop", text):
                errors.append(f"Obsolete or duplicated build path: {name}")
    required = ["apps/editor/package.json", "apps/desktop/package.json", "apps/desktop/engine.spec",
                "tools/build/build-cutvoke-desktop.cmd", "tests/support/e2e_server.py"]
    required += [f"src/cutvoke/{area}/__init__.py" for area in (
        "domain", "editing", "rendering", "storage", "interfaces")]
    required += [f"src/cutvoke/catalogs/builtin_{kind}.json" for kind in ("presets", "stickers", "resource_pack")]
    for name in required:
        if name not in files:
            errors.append(f"Missing versioned source/package entry: {name}")
    return sorted(set(errors))


if __name__ == "__main__":
    problems = check()
    print(json.dumps({"ok": not problems, "errors": problems}, ensure_ascii=False, indent=2))
    raise SystemExit(bool(problems))
