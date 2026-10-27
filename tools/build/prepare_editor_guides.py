"""Bundle canonical Skill references for MCP-only, wheel and desktop installations."""
from pathlib import Path
import runpy


def prepare(root=None):
    root = Path(root or Path(__file__).resolve().parents[2]).resolve()
    package = root / "src" / "cutvoke"
    entries = runpy.run_path(str(package / "interfaces" / "creative_guides.py"))["GUIDE_FILES"]
    source_root = root / "skills" / "cutvoke-editor"
    destination = package / "assets" / "editor_guides"
    # Resolve all sources before writing, so an incomplete source distribution
    # fails explicitly rather than shipping a partially refreshed guide set.
    payloads = {key: (source_root / entry[0]).read_bytes() for key, entry in entries.items()}
    destination.mkdir(parents=True, exist_ok=True)
    for key, payload in payloads.items():
        (destination / f"{key}.md").write_bytes(payload)
    for obsolete in destination.glob("*.md"):
        if obsolete.stem not in entries:
            obsolete.unlink()
    return {"count": len(payloads), "directory": str(destination)}


if __name__ == "__main__":
    print(prepare())
