"""Shared paths for development, build and verification tools."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / ".artifacts"


def artifact_directory(name: str) -> Path:
    path = (ARTIFACTS / name).resolve()
    if not path.is_relative_to(ARTIFACTS.resolve()):
        raise ValueError("artifact path must stay inside .artifacts")
    path.mkdir(parents=True, exist_ok=True)
    return path
