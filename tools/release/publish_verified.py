"""Publish only newly built, accepted archives from the exact tagged main commit.

No asset replacement, historical build reuse, or test execution from artifacts.
An interrupted upload leaves a draft for inspection; reruns never overwrite it.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
import tomllib
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ('ci.yml', 'windows-portable.yml', 'unix-packages.yml')
PACKAGES = {
    'windows-x64': ('windows-portable.yml', 'CutVoke-windows-x64-portable', '-portable.zip'),
    'macos-arm64': ('unix-packages.yml', 'CutVoke-macos-arm64', '.zip'),
    'macos-x64': ('unix-packages.yml', 'CutVoke-macos-x64', '.zip'),
    'linux-x64': ('unix-packages.yml', 'CutVoke-linux-x64', '.tar.gz'),
}


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def gh(*args):
    return subprocess.check_output(['gh', *args], text=True).strip()


def api(path):
    return json.loads(gh('api', path))


def digest(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def verify_info(info, version, sha, platform):
    require(info['version'] == version, 'Package version mismatch')
    require(info['commit'] == sha, 'Package source commit mismatch')
    require(info['platform'] == platform, 'Package architecture mismatch')


def build_info(path):
    if path.name.endswith('.zip'):
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.count('/') == 1 and n.endswith('/build-info.json')]
            require(len(names) == 1, 'Missing or ambiguous embedded build-info')
            return json.loads(archive.read(names[0]))
    with tarfile.open(path, 'r:gz') as archive:
        names = [m for m in archive.getmembers() if m.name.count('/') == 1 and m.name.endswith('/build-info.json') and m.isfile()]
        require(len(names) == 1, 'Missing or ambiguous embedded build-info')
        return json.load(archive.extractfile(names[0]))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_artifact(repo, artifact, destination):
    # Do not forward the API bearer token to the signed storage URL.
    request = urllib.request.Request(
        f'https://api.github.com/repos/{repo}/actions/artifacts/{artifact["id"]}/zip',
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'User-Agent': 'CutVoke-release'})
    try:
        urllib.request.build_opener(NoRedirect()).open(request, timeout=90)
        raise RuntimeError('Expected signed artifact redirect')
    except urllib.error.HTTPError as response:
        require(response.code == 302, f'Artifact HTTP {response.code}')
        location = response.headers['Location']
    require(location.startswith('https://'), 'Insecure artifact redirect')
    with urllib.request.urlopen(location, timeout=180) as source, destination.open('xb') as target:
        shutil.copyfileobj(source, target, 1024 * 1024)
    require(destination.stat().st_size == artifact['size_in_bytes'], 'Artifact size mismatch')
    require(artifact['digest'] == 'sha256:' + digest(destination), 'Artifact digest mismatch')


def main():
    repo = os.environ['GITHUB_REPOSITORY']
    sha = os.environ['GITHUB_SHA']
    tag = os.environ['GITHUB_REF_NAME']
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    require(repo == 'frankShan001/CutVoke' and tag == 'v' + version, 'Unexpected repository/tag/version')
    require(json.loads((ROOT / 'apps/desktop/package.json').read_text())['version'] == version, 'Desktop version mismatch')
    require(len(sha) == 40, 'Invalid source commit')

    def synchronized():
        require(api(f'repos/{repo}/branches/main')['commit']['sha'] == sha, 'main advanced; synchronize and rebuild before release')
        ref = api(f'repos/{repo}/git/ref/tags/{tag}')['object']
        require(ref['type'] == 'commit' and ref['sha'] == sha, 'Tag does not identify the source commit')

    synchronized()
    runs = {}
    deadline = time.monotonic() + 3600
    while len(runs) != len(WORKFLOWS):
        synchronized()
        for workflow in WORKFLOWS:
            result = api(f'repos/{repo}/actions/workflows/{workflow}/runs?head_sha={sha}&event=push&per_page=100')
            matching = [r for r in result['workflow_runs'] if r['head_sha'] == sha and r['head_branch'] == 'main']
            if not matching:
                continue
            run = max(matching, key=lambda r: r['id'])
            if run['status'] == 'completed':
                require(run['conclusion'] == 'success', f'{workflow} failed: {run["html_url"]}')
                runs[workflow] = run
        require(time.monotonic() < deadline, 'Timed out waiting for same-commit CI and packaging')
        if len(runs) != len(WORKFLOWS):
            print('Waiting for same-commit checks:', list(runs), flush=True)
            time.sleep(30)

    # A previous or independently created release is never overwritten.
    existing = json.loads(gh('api', '--paginate', '--slurp', f'repos/{repo}/releases?per_page=100'))
    require(not any(r['tag_name'] == tag for page in existing for r in page), 'Release already exists; refusing replacement')
    output = ROOT / '.artifacts/release'
    output.mkdir(parents=True, exist_ok=False)
    assets = []
    for platform, (workflow, artifact_name, suffix) in PACKAGES.items():
        run = runs[workflow]
        all_artifacts = api(f'repos/{repo}/actions/runs/{run["id"]}/artifacts?per_page=100')['artifacts']
        found = [a for a in all_artifacts if a['name'] == artifact_name and not a['expired']]
        require(len(found) == 1, f'Missing/ambiguous accepted artifact: {artifact_name}')
        artifact = found[0]
        require(artifact['workflow_run']['head_sha'] == sha, 'Artifact source mismatch')
        outer = output / f'{platform}-artifact.zip'
        download_artifact(repo, artifact, outer)
        filename = f'CutVoke-{version}-{platform}{suffix}'
        with zipfile.ZipFile(outer) as archive:
            require(archive.namelist().count(filename) == 1, 'Archive filename mismatch')
            info = json.loads(archive.read('download-info.json'))
            verify_info(info, version, sha, platform)
            require(info['filename'] == filename and info['runUrl'] == run['html_url'], 'Build metadata run/filename mismatch')
            target = output / filename
            with archive.open(filename) as source, target.open('xb') as dest:
                shutil.copyfileobj(source, dest, 1024 * 1024)
        require(target.stat().st_size == info['bytes'] and digest(target) == info['sha256'], 'Package checksum mismatch')
        verify_info(build_info(target), version, sha, platform)
        assets.append({'name': filename, 'bytes': info['bytes'], 'sha256': info['sha256'],
                       'run': run['html_url'], 'artifactId': artifact['id']})
        outer.unlink()
    manifest = {'version': version, 'tag': tag, 'commit': sha, 'prerelease': True,
                'checks': {key: value['html_url'] for key, value in runs.items()}, 'assets': assets}
    (output / 'BUILD-INFO.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (output / 'SHA256SUMS').write_text(''.join(f'{a["sha256"]}  {a["name"]}\n' for a in assets), encoding='ascii')
    paths = [output / a['name'] for a in assets] + [output / 'BUILD-INFO.json', output / 'SHA256SUMS']
    expected = {p.name: (p.stat().st_size, 'sha256:' + digest(p)) for p in paths}
    notes = (ROOT / f'docs/development/release-notes-{tag}.md').read_text('utf-8')
    notes += f'\n\nSource commit: `{sha}`\n\n' + '\n'.join(f'- {key}: {run["html_url"]}' for key, run in runs.items())
    notes += '\n\n| Asset | SHA256 |\n| --- | --- |\n' + ''.join(
        f'| [{a["name"]}](https://github.com/{repo}/releases/download/{tag}/{a["name"]}) | `{a["sha256"]}` |\n' for a in assets)
    note_file = output / 'release-notes.md'
    note_file.write_text(notes, encoding='utf-8')
    synchronized()
    gh('release', 'create', tag, '--repo', repo, '--target', sha, '--verify-tag', '--draft', '--prerelease',
       '--title', f'CutVoke {tag} — Cross-platform preview / 跨平台预览版', '--notes-file', str(note_file))
    gh('release', 'upload', tag, '--repo', repo, *(str(p) for p in paths))
    # The by-tag REST endpoint does not resolve an unpublished draft.
    pages = json.loads(gh('api', '--paginate', '--slurp', f'repos/{repo}/releases?per_page=100'))
    drafts = [r for page in pages for r in page if r['tag_name'] == tag]
    require(len(drafts) == 1, 'Missing/ambiguous newly created draft')
    release = api(f'repos/{repo}/releases/{drafts[0]["id"]}')
    require(release['draft'] and release['prerelease'], 'Unexpected release state')
    require(len(release['assets']) == len(expected), 'Unexpected asset count')
    for asset in release['assets']:
        require((asset['size'], asset['digest']) == expected.get(asset['name']), 'Uploaded asset digest mismatch')
    synchronized()
    gh('release', 'edit', tag, '--repo', repo, '--draft=false', '--prerelease')
    published = api(f'repos/{repo}/releases/tags/{tag}')
    require(not published['draft'] and published['prerelease'], 'Publication not confirmed')
    # Verify public download bytes, not only API upload receipts.
    for asset in published['assets']:
        expected_url = f'https://github.com/{repo}/releases/download/{tag}/{asset["name"]}'
        require(asset['browser_download_url'] == expected_url, 'Download URL mismatch')
        with urllib.request.urlopen(expected_url, timeout=180) as response:
            actual = hashlib.file_digest(response, 'sha256').hexdigest()
        require('sha256:' + actual == expected[asset['name']][1], 'Public download digest mismatch')
    print(published['html_url'], flush=True)
    with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a', encoding='utf-8') as summary:
        summary.write(f'Published and downloaded all six verified assets: {published["html_url"]}\n\nSource: `{sha}`\n')


if __name__ == '__main__':
    main()
