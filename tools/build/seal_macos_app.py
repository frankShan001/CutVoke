"""Seal the newly built local app even when electron-builder skips PR signing."""
from pathlib import Path
import platform
import subprocess
import sys


def seal_app(app: Path) -> None:
    if not (app / "Contents/MacOS/CutVoke").is_file():
        raise RuntimeError(f"Missing newly built application: {app}")
    # '-' is a local integrity seal, with no certificate, keychain or publisher
    # identity. Keep strict validation on both this tree and the shipped archive.
    subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)], check=True)


if __name__ == "__main__":
    if sys.platform != "darwin":
        raise SystemExit("The local macOS integrity seal requires macOS")
    root = Path(__file__).resolve().parents[2]
    target = "mac-arm64" if platform.machine() in {"arm64", "aarch64"} else "mac"
    seal_app(root / ".artifacts/desktop" / target / "CutVoke.app")
