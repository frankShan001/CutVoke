"""The frozen CLI supports both the desktop service and external stdio agents."""
import os
from pathlib import Path
import sys

if getattr(sys, "frozen", False):
    binaries = Path(sys._MEIPASS) / "bin"
    os.environ["PATH"] = str(binaries) + os.pathsep + os.environ.get("PATH", "")
    # GUI child processes and stdio MCP must agree on Unicode on Windows,
    # including when launched by an agent that did not set Python env vars.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

from cutvoke.interfaces.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
