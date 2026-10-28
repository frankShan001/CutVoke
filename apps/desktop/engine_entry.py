"""The frozen CLI supports both the desktop service and external stdio agents."""
import os
from pathlib import Path
import sys

if getattr(sys, "frozen", False):
    # Native QA can capture a stalled worker before teardown destroys evidence.
    # Normal desktop launches do not install a diagnostic signal handler.
    if os.name == "posix" and os.environ.get("CUTVOKE_DESKTOP_QA") == "1":
        import faulthandler
        import json
        import signal
        import time
        faulthandler.register(signal.SIGUSR2, all_threads=True)
        def media_process_started(event, arguments):
            if event == "subprocess.Popen":
                executable, argv, _cwd, _env = arguments
                if os.path.basename(os.fsdecode(executable)) in {"ffmpeg", "ffprobe"}:
                    print("CUTVOKE_QA_MEDIA_PROCESS=" + json.dumps({
                        "time": time.time(), "monotonic": time.monotonic(), "argv": argv,
                    }, ensure_ascii=False, default=os.fsdecode), file=sys.stderr, flush=True)
        sys.addaudithook(media_process_started)
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
