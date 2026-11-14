"""Repeat the unchanged 45s title flow with a fresh server and font cache per run."""
import hashlib
import json
import os
from pathlib import Path
import socket
import signal
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = "title edits preview immediately, save without applying, and share canvas selection$"


def available_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def verify_cache(cache, assets):
    manifest = json.loads((assets / "manifest.json").read_text("utf-8"))
    entry = manifest["instances"]["700"]
    expected = entry["sha256"]
    packaged = assets / "sans-700-v1.ttf"
    if hashlib.sha256(packaged.read_bytes()).hexdigest() != expected:
        raise RuntimeError("packaged font hash mismatch")
    name = f'{manifest["source_sha256"]}-700-v2-{expected}.ttf'
    actual = cache / name
    if not actual.is_file() or hashlib.sha256(actual.read_bytes()).hexdigest() != expected:
        raise RuntimeError("scenario did not produce the expected packaged bold cache")
    if list(cache.glob("*-v1.ttf")):
        raise RuntimeError("unexpected runtime font fallback")
    return {"weight": 700, "sha256": expected, "cache": name}


def run_bounded(command, *, timeout=180, grace=5, **kwargs):
    # This hosted acceptance helper is POSIX-only; own the entire process group.
    if os.name != "posix":
        raise RuntimeError("cold title acceptance currently requires POSIX")
    process = subprocess.Popen(command, start_new_session=True, **kwargs)
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
        # Kill remaining members of our group, including detached npx children.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        return 124


def main():
    out = ROOT / ".artifacts/cold-title"
    out.mkdir(parents=True, exist_ok=True)
    assets = ROOT / "src/cutvoke/assets/caption_fonts"
    reports = []
    for run in range(1, 4):
        port = available_port()
        with tempfile.TemporaryDirectory(prefix="cutvoke-title-font-") as folder:
            cache = Path(folder)
            if list(cache.iterdir()):
                raise RuntimeError("cache not empty before scenario")
            env = dict(os.environ, CI="1", CUTVOKE_E2E_REUSE="0",
                       CUTVOKE_E2E_PORT=str(port), CUTVOKE_FONT_CACHE=str(cache),
                       CUTVOKE_E2E_PREVIEW_DIAGNOSTICS="1")
            env.pop("CUTVOKE_FONT", None)
            screenshots = [ROOT / ".artifacts" / name for name in (
                "title-live-edit.png", "title-live-canvas.png", "title-render-reference.png")]
            before = {p: p.stat().st_mtime_ns if p.exists() else None for p in screenshots}
            started = time.monotonic()
            exit_code = run_bounded(["npx", "playwright", "test", "e2e/editor.spec.ts",
                "--grep", SCENARIO, "--workers=1", "--retries=0", "--trace=on",
                f"--output={out / str(run)}"], cwd=ROOT / "apps/editor", env=env)
            evidence = out / str(run)
            evidence.mkdir(parents=True, exist_ok=True)
            for screenshot in screenshots:
                if screenshot.exists() and screenshot.stat().st_mtime_ns != before[screenshot]:
                    shutil.copy2(screenshot, evidence / screenshot.name)
            record = {"run": run, "port": port, "freshServer": True, "initialCacheEntries": 0,
                      "testTimeoutMs": 45000, "processSeconds": time.monotonic() - started,
                      "exitCode": exit_code,
                      "scope": "processSeconds includes server startup; test duration is in Playwright trace"}
            reports.append(record)
            (out / "verification.json").write_text(json.dumps(reports, indent=2) + "\n", "utf-8")
            if exit_code:
                raise RuntimeError(f"title flow failed in independent cold run {run}")
            with socket.socket() as sock:
                sock.settimeout(1)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    raise RuntimeError("Playwright server still listening after scenario")
            record["packagedBoldCache"] = verify_cache(cache, assets)
            record["serverPortClosed"] = True
            (out / "verification.json").write_text(json.dumps(reports, indent=2) + "\n", "utf-8")


if __name__ == "__main__":
    main()
