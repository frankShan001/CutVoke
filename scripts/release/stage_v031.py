"""Stage only the four approved v0.3.1 archives; never execute their contents.

Manual release transfer only. Tag creation and final publication use the
maintainer's existing local login, outside this job's temporary token.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = "frankShan001/CutVoke"
TAG = "v0.3.1"
TARGET = "58f890090463300c007f541ac36be8bca71bb4e9"
BUILD = "fdec479c4acbc3c685a5c8adbe8ec91621a7c194"
RELEASE_ID = 415558347
DRAFT_BODY_SHA256 = "21543359d49de9fc03edadcf802a249e264e9948403b1ac330ebf974d13e2335"
PACKAGES = (
    {"artifact": 12022962244, "run": 38845496409, "artifact_name": "CutVoke-macos-arm64", "name": "CutVoke-0.3.1-macos-arm64.zip", "size": 352156692, "sha256": "24b4c0615618cabf5a641d0aa5abc0b76a5bf768b9291001c0b90fd557b0576b", "outer_size": 352287687, "outer_sha256": "b6c4fd6a3b10200b8ad6a5fee2a1155514aa9b36b2e3dd931ba327b3979bd777"},
    {"artifact": 12023640599, "run": 38845496409, "artifact_name": "CutVoke-macos-x64", "name": "CutVoke-0.3.1-macos-x64.zip", "size": 422388587, "sha256": "93116f49ca982398766abb20efc580bc1f6282581885b1cbb6b95a1a4f7d1854", "outer_size": 422522322, "outer_sha256": "282deb423262b85402c0d2525e6d030e754b927ed71177a54b490a811f014fae"},
    {"artifact": 12023097854, "run": 38845496409, "artifact_name": "CutVoke-linux-x64", "name": "CutVoke-0.3.1-linux-x64.tar.gz", "size": 359522533, "sha256": "c8df79f6a184b9dcf5d12506516db4ef2fca7073d6e091de640b532023010662", "outer_size": 359630760, "outer_sha256": "5d9197cb0d6952d3d465d152b69556f96429725dba415b539d573244fa593dfd"},
    {"artifact": 12022894966, "run": 38845496400, "artifact_name": "CutVoke-windows-x64-portable", "name": "CutVoke-0.3.1-windows-x64-portable.zip", "size": 380760157, "sha256": "2aab6699ee1644f1afe7d6daa4afa9d1fd99311838c46fc4bb4c6ad202cc1dbc", "outer_size": 380774452, "outer_sha256": "47e8c0a450846e4ca6db8b2ce61a1858efc9a94c679834a3aba88429dc523aac"},
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    def __init__(self):
        self.token = os.environ["GH_TOKEN"]
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, path, method="GET", data=None, headers=None):
        url = path if path.startswith("https://") else "https://api.github.com" + path
        require(urllib.parse.urlsplit(url).hostname in {"api.github.com", "uploads.github.com"}, "Unexpected authenticated host")
        h = {"Authorization": "Bearer " + self.token, "User-Agent": "CutVoke-v031-staging", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        h.update(headers or {})
        try:
            return self.opener.open(urllib.request.Request(url, data=data, headers=h, method=method), timeout=90)
        except urllib.error.HTTPError as error:
            if error.code == 302:
                return error
            raise RuntimeError(f"GitHub HTTP {error.code}; stopping without replacement or permission changes") from None

    def json(self, path):
        with self.request(path) as response:
            require(response.status == 200, "Unexpected metadata response")
            return json.load(response)


def check_draft(gh, *, empty=False):
    ref = gh.json(f"/repos/{REPOSITORY}/git/ref/tags/{TAG}")
    require(ref["object"]["type"] == "commit" and ref["object"]["sha"] == TARGET, "Tag differs from approved target")
    release = gh.json(f"/repos/{REPOSITORY}/releases/{RELEASE_ID}")
    require(release["tag_name"] == TAG and release["target_commitish"] == TARGET and release["draft"] and release["prerelease"], "Task draft identity/status mismatch")
    require(hashlib.sha256(release["body"].encode()).hexdigest() == DRAFT_BODY_SHA256, "Draft notes were changed; do not overwrite")
    require(release["author"]["login"].lower() == "frankshan001", "Unexpected draft owner")
    if empty:
        require(not release["assets"], "Draft already has assets; refusing to overwrite or duplicate")
    return release


def download_one(package, root):
    gh = GitHub()
    meta = gh.json(f"/repos/{REPOSITORY}/actions/artifacts/{package['artifact']}")
    require(meta["id"] == package["artifact"] and meta["name"] == package["artifact_name"] and not meta["expired"], "Artifact identity/expiry mismatch")
    require(meta["workflow_run"]["id"] == package["run"] and meta["workflow_run"]["head_sha"] == BUILD, "Artifact source build mismatch")
    require(meta["size_in_bytes"] == package["outer_size"] and meta["digest"] == "sha256:" + package["outer_sha256"], "Outer artifact metadata mismatch")
    outer = root / f"{package['artifact']}.zip"
    with gh.request(f"/repos/{REPOSITORY}/actions/artifacts/{package['artifact']}/zip") as redirect:
        require(redirect.status == 302, "Expected artifact download redirect")
        location = redirect.headers["Location"]
    parsed = urllib.parse.urlsplit(location)
    require(parsed.scheme == "https" and parsed.hostname and (parsed.hostname.endswith(".blob.core.windows.net") or parsed.hostname.endswith(".githubusercontent.com")), "Unexpected artifact download host")
    # No GitHub authorization header is forwarded to the storage host.
    with gh.opener.open(urllib.request.Request(location, headers={"User-Agent": "CutVoke-v031-staging"}), timeout=90) as source, outer.open("xb") as dest:
        require(source.status == 200, "Artifact download was not successful")
        shutil.copyfileobj(source, dest, length=1024 * 1024)
    require(outer.stat().st_size == package["outer_size"] and sha256(outer) == package["outer_sha256"], "Actual outer ZIP hash/size mismatch")
    target = root / package["name"]
    # Read one exact member only. Never extract arbitrary paths, symlinks or code.
    with zipfile.ZipFile(outer) as archive:
        require(archive.namelist().count(package["name"]) == 1, "Inner package missing/ambiguous")
        info = archive.getinfo(package["name"])
        require(info.file_size == package["size"], "Inner package declared size mismatch")
        with archive.open(info) as source, target.open("xb") as dest:
            shutil.copyfileobj(source, dest, length=1024 * 1024)
    actual = sha256(target)
    require(target.stat().st_size == package["size"] and actual == package["sha256"], "Actual inner package hash/size mismatch")
    result = {"name": target.name, "size": target.stat().st_size, "sha256": actual, "artifact_id": package["artifact"], "run": package["run"], "outer_sha256": package["outer_sha256"]}
    print(json.dumps({"verified": result}), flush=True)
    return result


def verify_all(gh, root):
    check_draft(gh, empty=True)
    for run_id in {p["run"] for p in PACKAGES}:
        run = gh.json(f"/repos/{REPOSITORY}/actions/runs/{run_id}")
        require(run["head_sha"] == BUILD and run["status"] == "completed" and run["conclusion"] == "success", "Original build run not successful at approved commit")
    root.mkdir(exist_ok=False)
    with ThreadPoolExecutor(max_workers=4) as pool:
        actual = list(pool.map(lambda item: download_one(item, root), PACKAGES))
    # No upload happens until every future above has passed full-byte verification.
    sums = root / "SHA256SUMS"
    sums.write_text("".join(f"{item['sha256']}  {item['name']}\n" for item in actual), encoding="ascii", newline="\n")
    actual.append({"name": "SHA256SUMS", "size": sums.stat().st_size, "sha256": sha256(sums)})
    manifest = {"repository": REPOSITORY, "tag": TAG, "target": TARGET, "build": BUILD, "release_id": RELEASE_ID, "all_four_actual_archives_verified": True, "assets": actual}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("All four original inner archives verified; upload is now eligible.", flush=True)


def upload_all(gh, root):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    require(manifest["all_four_actual_archives_verified"] and manifest["release_id"] == RELEASE_ID and manifest["target"] == TARGET and manifest["build"] == BUILD, "Verification manifest mismatch")
    expected = list(PACKAGES) + [{"name": "SHA256SUMS", "size": (root / "SHA256SUMS").stat().st_size, "sha256": sha256(root / "SHA256SUMS")}]
    require(len(manifest["assets"]) == 5, "Expected five verified assets")
    for item, reference in zip(manifest["assets"], expected):
        require(all(item[key] == reference[key] for key in ("name", "size", "sha256")), "Manifest package differs from approved set")
        path = root / item["name"]
        require(path.stat().st_size == item["size"] and sha256(path) == item["sha256"], "Verified local bytes changed before upload")
    release = check_draft(gh, empty=True)
    for item in manifest["assets"]:
        # Re-check identity/body and reject each name if anything appeared meanwhile.
        current = check_draft(gh)
        require(not any(asset["name"] == item["name"] for asset in current["assets"]), "Asset conflict; never clobber")
        url = release["upload_url"].split("{", 1)[0] + "?" + urllib.parse.urlencode({"name": item["name"]})
        with (root / item["name"]).open("rb") as source:
            with gh.request(url, method="POST", data=source, headers={"Content-Type": "application/octet-stream", "Content-Length": str(item["size"])}) as response:
                require(response.status == 201, "Upload was not confirmed")
                uploaded = json.load(response)
        require(uploaded["state"] == "uploaded" and uploaded["size"] == item["size"] and uploaded.get("digest") == "sha256:" + item["sha256"], "Server upload digest/size mismatch")
        print(json.dumps({"uploaded": {key: uploaded[key] for key in ("id", "name", "size", "state", "digest")}}), flush=True)
    final = check_draft(gh)
    require(len(final["assets"]) == 5, "Draft must contain exactly five assets")
    remote = {asset["name"]: asset for asset in final["assets"]}
    for item in manifest["assets"]:
        asset = remote[item["name"]]
        require(asset["state"] == "uploaded" and asset["size"] == item["size"] and asset.get("digest") == "sha256:" + item["sha256"], "Final draft server verification failed")
    manifest["all_five_remote_digests_verified"] = True
    manifest["workflow_run"] = int(os.environ["GITHUB_RUN_ID"])
    (root / "staged-release.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write(f"## Verified v0.3.1 draft {RELEASE_ID}\n\nAll four original archives were verified before any upload. All five server digests match. Final publication remains with the maintainer's existing local login.\n\n| Asset | Bytes | SHA256 |\n| --- | ---: | --- |\n")
        for item in manifest["assets"]:
            summary.write(f"| {item['name']} | {item['size']} | `{item['sha256']}` |\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("verify", "upload"))
    args = parser.parse_args()
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "Manual workflow in the exact repository required")
    require(RELEASE_ID > 0 and len(DRAFT_BODY_SHA256) == 64, "Draft has not been bound")
    root = Path(os.environ["RUNNER_TEMP"]) / "cutvoke-v031-staging"
    gh = GitHub()
    if args.operation == "verify":
        verify_all(gh, root)
    else:
        upload_all(gh, root)


if __name__ == "__main__":
    main()
