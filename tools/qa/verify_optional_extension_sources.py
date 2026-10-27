"""Download pinned optional components from each origin; distinguish cache hits.

Example (run each in the actual network being accepted):
  python tools/qa/verify_optional_extension_sources.py --region cn --direct --fresh
  python tools/qa/verify_optional_extension_sources.py --region global --fresh

This is an explicit, potentially multi-gigabyte acceptance check, not a startup
probe or CI unit test. It does not install or execute downloaded packages.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cutvoke.rendering.extension_downloads import Downloader, verified
from cutvoke.storage.extension_paths import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", choices=["cn", "global", "all"], default="all")
    parser.add_argument("--direct", action="store_true", help="Bypass configured proxies")
    parser.add_argument("--fresh", action="store_true", help="Use a new empty cache directory")
    parser.add_argument("--profile", help="Check one profile from the shipped catalog")
    parser.add_argument("--cache", type=Path, default=ROOT / ".artifacts/extension-source-audit")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    catalog_path = ROOT / "src/cutvoke/catalogs/optional_extensions.json"
    catalog = json.loads(catalog_path.read_text("utf8"))
    cache = args.cache.resolve()
    if args.fresh:
        cache /= str(time.time_ns())
    report_path = args.report or cache / "verification.json"
    allowed = set(catalog["artifacts"])
    if args.profile:
        profile = next((p for p in catalog["profiles"] if p["id"] == args.profile), None)
        if not profile:
            parser.error("profile is not in the shipped catalog")
        allowed = set(profile["artifacts"])
    pairs = [(a, s) for key, a in catalog["artifacts"].items() if key in allowed and not a.get("bundled")
             for s in a["sources"] if args.region == "all" or s["region"] == args.region]

    def check(artifact, source):
        origin_id = hashlib.sha256(source["url"].encode()).hexdigest()[:16]
        opener = build_opener(ProxyHandler({})) if args.direct else build_opener()
        downloader = Downloader(cache / origin_id, opener=opener, source_filter=lambda s: s == source)
        target = downloader.path(artifact)
        hit = verified(target, artifact)
        partial = target.with_suffix(target.suffix + ".part")
        resumed = partial.stat().st_size if partial.is_file() else 0
        started = time.monotonic()
        result = {"filename": artifact["filename"], "url": source["url"], "source": source["label"],
                  "region": source["region"], "sha256": artifact["sha256"], "sizeBytes": artifact["sizeBytes"],
                  "cacheHit": hit, "resumedBytes": resumed,
                  "verificationKind": "cache" if hit else "resumed_network" if resumed else "full_network"}
        try:
            downloader.fetch(artifact)
            result["verified"] = verified(target, artifact)
        except Exception as error:
            result.update(verified=False, error=str(error))
        return {**result, "seconds": round(time.monotonic() - started, 3)}

    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(check, a, s) for a, s in pairs]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            report = {"catalogVersion": catalog["version"],
                      "proxyMode": "direct" if args.direct else "configured; routing policy belongs to the host",
                      "geographyNotInferred": True, "artifacts": results}
            atomic_json(report_path, report)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if all(r["verified"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
