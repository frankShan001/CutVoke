"""The frozen CLI supports both the desktop service and external stdio agents."""
import os
from pathlib import Path
import sys

if getattr(sys, "frozen", False):
    binaries = Path(sys._MEIPASS) / "bin"
    os.environ["PATH"] = str(binaries) + os.pathsep + os.environ.get("PATH", "")

from cutvoke.main import main

if __name__ == "__main__":
    raise SystemExit(main())
