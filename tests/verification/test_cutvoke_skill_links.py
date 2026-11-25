"""Check inline Markdown file links in a standalone copy of the editor Skill."""

import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlsplit


SKILL = Path(__file__).resolve().parents[2] / "skills" / "cutvoke-editor"


def test_copied_skill_local_markdown_links_resolve_inside_package(tmp_path):
    installed = shutil.copytree(SKILL, tmp_path / "cutvoke-editor").resolve()
    broken = []
    for doc in sorted(installed.rglob("*.md")):
        for match in re.finditer(r"\[[^\]\n]+\]\(([^\s)]+)\)", doc.read_text(encoding="utf-8")):
            link = urlsplit(match[1])
            if link.scheme or link.netloc or not link.path:
                continue
            target = (doc.parent / unquote(link.path)).resolve()
            if not target.is_relative_to(installed) or not target.exists():
                broken.append(f"{doc.relative_to(installed)}: {match[1]}")

    assert not broken, "\n".join(broken)
