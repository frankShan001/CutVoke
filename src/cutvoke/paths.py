"""Package data paths, independent of cwd and installed/source layout."""
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
CATALOG_ROOT = PACKAGE_ROOT / "catalogs"


def catalog_path(name: str, package_root: Path | None = None) -> Path:
    root = package_root if package_root is not None else PACKAGE_ROOT
    current = root / "catalogs" / name
    # Released resource archives used core/ before the source reorganization.
    # Keep those archives readable; new source and packages use catalogs/.
    legacy = root / "core" / name
    return legacy if not current.is_file() and legacy.is_file() else current
