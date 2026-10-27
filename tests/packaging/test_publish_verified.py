"""Exercise draft publication without GitHub writes or executable artifacts."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import zipfile

import pytest


@pytest.fixture
def publication(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "tools/release/publish_verified.py"
    spec = importlib.util.spec_from_file_location("verified_publication", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repo, sha, tag = "frankShan001/CutVoke", "a" * 40, "v0.3.3"
    monkeypatch.setattr(module, "ROOT", tmp_path)
    for key, value in {"GITHUB_REPOSITORY": repo, "GITHUB_SHA": sha,
                       "GITHUB_REF_NAME": tag, "GITHUB_STEP_SUMMARY": str(tmp_path / "summary")}.items():
        monkeypatch.setenv(key, value)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="0.3.3"\n')
    (tmp_path / "apps/desktop").mkdir(parents=True)
    (tmp_path / "apps/desktop/package.json").write_text('{"version":"0.3.3"}')
    (tmp_path / "docs/development").mkdir(parents=True)
    (tmp_path / f"docs/development/release-notes-{tag}.md").write_text("Preview")
    state = {"created": False, "uploaded": False, "published": False, "mode": None,
             "by_id_reads": 0, "downloads": []}
    packages, artifacts = {}, []
    for index, (platform, (workflow, artifact_name, suffix)) in enumerate(module.PACKAGES.items()):
        filename = f"CutVoke-0.3.3-{platform}{suffix}"
        info = {"version": "0.3.3", "commit": sha, "platform": platform}
        payload = io.BytesIO()
        if suffix.endswith(".zip"):
            with zipfile.ZipFile(payload, "w") as archive:
                archive.writestr("app/build-info.json", json.dumps(info))
        else:
            data = json.dumps(info).encode()
            with tarfile.open(fileobj=payload, mode="w:gz") as archive:
                member = tarfile.TarInfo("app/build-info.json")
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        binary = payload.getvalue()
        info.update(filename=filename, bytes=len(binary), sha256=hashlib.sha256(binary).hexdigest(),
                    runUrl=f"https://github.com/{repo}/actions/runs/{workflow}")
        packages[index] = (filename, binary, info)
        artifacts.append({"id": index, "name": artifact_name, "expired": False,
                          "workflow_run": {"head_sha": sha}})

    def release():
        assets = []
        if state["uploaded"]:
            output = tmp_path / ".artifacts/release"
            for name in [p[0] for p in packages.values()] + ["BUILD-INFO.json", "SHA256SUMS"]:
                data = (output / name).read_bytes()
                assets.append({"name": name, "size": len(data),
                               "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                               "browser_download_url": f"https://github.com/{repo}/releases/download/{tag}/{name}"})
            if state["mode"] == "bad_digest":
                assets[0]["digest"] = "sha256:" + "0" * 64
        return {"id": 77, "tag_name": tag, "target_commitish": sha,
                "draft": not state["published"], "prerelease": True, "assets": assets,
                "html_url": f"https://github.com/{repo}/releases/tag/{tag}"}

    def fake_api(endpoint):
        if endpoint.endswith("branches/main"):
            value = "b" * 40 if state["mode"] == "main_advanced" and state["uploaded"] else sha
            return {"commit": {"sha": value}}
        if "/git/ref/tags/" in endpoint:
            return {"object": {"type": "commit", "sha": sha}}
        if "/actions/workflows/" in endpoint:
            workflow = endpoint.split("/workflows/")[1].split("/")[0]
            return {"workflow_runs": [{"id": workflow, "head_sha": sha, "head_branch": "main",
                    "status": "completed", "conclusion": "success",
                    "html_url": f"https://github.com/{repo}/actions/runs/{workflow}"}]}
        if "/artifacts?" in endpoint:
            return {"artifacts": artifacts}
        if "/releases/tags/" in endpoint:
            raise RuntimeError("404: tag endpoint does not expose the draft")
        if endpoint.endswith("releases/77"):
            state["by_id_reads"] += 1
            return release()
        raise AssertionError(endpoint)

    def fake_gh(*args):
        if args[:3] == ("api", "--paginate", "--slurp"):
            items = [release()] if state["created"] else []
            if not state["created"] and state["mode"] in {"existing_draft", "existing_published"}:
                items = [{**release(), "draft": state["mode"] == "existing_draft"}]
            if state["created"] and state["mode"] == "duplicate":
                items.append({**release(), "id": 88})
            if state["created"] and state["mode"] == "wrong_source":
                items[0]["target_commitish"] = "b" * 40
            return json.dumps([[], items])  # Also exercise pagination.
        if args[:2] == ("release", "create"):
            state["created"] = True
        elif args[:2] == ("release", "upload"):
            assert state["created"]
            state["uploaded"] = True
        elif args[:3] == ("api", "--method", "PATCH"):
            assert args[3] == f"repos/{repo}/releases/77"
            assert args[4:] == ("-F", "draft=false", "-F", "prerelease=true")
            state["published"] = True
        else:
            raise AssertionError(args)
        return ""

    def download(_repo, artifact, destination):
        name, data, info = packages[artifact["id"]]
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr(name, data)
            archive.writestr("download-info.json", json.dumps(info))

    def public_download(url, timeout):
        assert state["published"]
        state["downloads"].append(url)
        return io.BytesIO((tmp_path / ".artifacts/release" / url.rsplit("/", 1)[1]).read_bytes())

    monkeypatch.setattr(module, "api", fake_api)
    monkeypatch.setattr(module, "gh", fake_gh)
    monkeypatch.setattr(module, "download_artifact", download)
    monkeypatch.setattr(module.urllib.request, "urlopen", public_download)
    return module, state


def test_draft_resolves_by_id_and_all_six_public_assets_are_verified(publication):
    module, state = publication
    module.main()
    assert state["published"] and state["by_id_reads"] == 2
    assert len(state["downloads"]) == 6


@pytest.mark.parametrize("mode,message", [
    ("bad_digest", "Uploaded asset digest mismatch"),
    ("main_advanced", "main advanced"),
    ("duplicate", "ambiguous newly created draft"),
    ("existing_draft", "Release already exists"),
    ("existing_published", "Release already exists"),
    ("wrong_source", "New draft identity mismatch"),
])
def test_bad_upload_stale_source_or_ambiguous_draft_cannot_publish(publication, mode, message):
    module, state = publication
    state["mode"] = mode
    with pytest.raises(RuntimeError, match=message):
        module.main()
    assert not state["published"] and not state["downloads"]
