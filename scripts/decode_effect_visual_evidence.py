"""Strictly decode the retained default cases and refreshed library previews."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess

from prepare_effect_visual_review import ROOT, OUT, save, sha
from cutvoke.core.builtin_stickers import load_builtin_stickers


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def decode(path):
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-threads", "1",
         "-i", str(path), "-map", "0:v:0", "-map", "0:a?", "-threads", "1",
         "-f", "null", "NUL"],
        capture_output=True, text=True, timeout=180,
    )
    return {"path": str(path), "sha256": sha(path), "exitCode": result.returncode,
            "passed": result.returncode == 0, "error": result.stderr[-2000:]}


def main():
    paths = {Path(r["videoPath"]) for r in read(OUT / "manifest.json")}
    paths.update(Path(r["videoPath"]) for r in read(OUT / "dynamic-stickers/manifest.json"))
    core = ROOT / "src/cutvoke/core"
    updated = set(read(OUT / "library-approval-renewal.json")["presetIds"])
    for preset in read(core / "builtin_presets.json")["presets"]:
        if preset["presetId"] in updated:
            reference = preset["motionPreview"]
            paths.add(core / reference)
    for sticker in load_builtin_stickers():
        if sticker.get("kind") == "dynamic":
            paths.add(Path(sticker["previewPath"]))
    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(decode, path): path for path in sorted(paths)}
        for future in as_completed(futures):
            results.append(future.result())
    report = {"count": len(results), "passed": sum(r["passed"] for r in results),
              "failed": sum(not r["passed"] for r in results), "results": results}
    save(OUT / "strict-decode.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "results"}))
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
