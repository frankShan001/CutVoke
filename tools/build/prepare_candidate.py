"""Build a source/wheel candidate, local build facts and checksums; never publish."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def capture(command, root):
    return subprocess.check_output(command, cwd=root, text=True, encoding="utf-8").strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="a NEW candidate directory; existing artifacts are never replaced")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    if output.exists():
        parser.error("--output must be new; retain earlier candidates for comparison")
    if not (root / ".artifacts/web/index.html").is_file():
        parser.error("build the editor with npm ci and npm run build in apps/editor first")
    # Fail before building if this is not an identifiable Git checkout.
    commit = capture(["git", "rev-parse", "HEAD"], root)
    dirty = bool(capture(["git", "status", "--porcelain"], root))
    output.mkdir(parents=True)
    subprocess.run([sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(output)],
                   cwd=root, check=True)
    report = output / "VERIFICATION.json"
    subprocess.run([sys.executable, str(root / "tools/verify/verify_distribution.py"),
                    str(output), "--report", str(report)], check=True)
    facts = {"sourceCommit": commit, "dirty": dirty,
             "createdUtc": datetime.now(timezone.utc).isoformat(),
             "python": sys.version.split()[0], "platform": sys.platform,
             "scope": "source/wheel candidate; no installer, signature, publication or deployment"}
    for executable in ("ffmpeg", "ffprobe", "node"):
        if shutil.which(executable):
            flag = "--version" if executable == "node" else "-version"
            facts[executable] = capture([executable, flag], root).splitlines()[0]
    (output / "BUILD-INFO.json").write_text(json.dumps(facts, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(root / "docs/development/release-notes-draft.md", output / "RELEASE_NOTES.md")
    sums = []
    for artifact in sorted(output.iterdir()):
        digest = hashlib.sha256()
        with artifact.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        sums.append(f"{digest.hexdigest()}  {artifact.name}\n")
    with (output / "SHA256SUMS").open("w", encoding="ascii", newline="\n") as stream:
        stream.write("".join(sums))
    print(json.dumps({"ok": True, "candidateDirectory": str(output),
                      "sourceCommit": commit, "dirty": dirty, "published": False}))


if __name__ == "__main__":
    main()
