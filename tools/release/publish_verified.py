"""Publish only newly built, accepted archives from the exact tagged main commit.

No asset replacement, historical build reuse, or test execution from artifacts.
An interrupted upload leaves a draft for inspection; reruns never overwrite it.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 is a supported development runtime.
    import tomli as tomllib
import urllib.error
import urllib.parse
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


def releases(repo):
    pages = json.loads(gh('api', '--paginate', '--slurp', f'repos/{repo}/releases?per_page=100'))
    return [release for page in pages for release in page]


def created_draft_id(repo, tag, sha):
    # The tag endpoint only returns published releases. Resolve the fresh draft
    # through the authenticated listing and bind subsequent reads/writes to ID.
    matches = [release for release in releases(repo) if release['tag_name'] == tag]
    require(len(matches) == 1, 'Missing or ambiguous newly created draft')
    release = matches[0]
    require(release['draft'] and release['prerelease'] and release['target_commitish'] == sha,
            'New draft identity mismatch')
    require(isinstance(release['id'], int) and release['id'] > 0, 'Invalid new draft ID')
    return release['id']


def stream_digest(stream):
    checksum = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        checksum.update(block)
    return checksum.hexdigest()


def digest(path):
    with path.open('rb') as file:
        return stream_digest(file)


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


# Explicit recovery is limited to the draft created by the accepted v0.3.4 run.
# The recovery tool's branch SHA is deliberately separate from package provenance.
RECOVERY_REPO = 'frankShan001/CutVoke'
RECOVERY_ID = 426210812
RECOVERY_SHA = '56daa1aac5327142ba0f271cc7490a1690c68bcb'
RECOVERY_TAG = 'v0.3.4'
RECOVERY_MANIFEST_ARTIFACT = 12787957504
RECOVERY_PUBLICATION_RUN = 40623230980
RECOVERY_MANIFEST_SHA256 = '96d3aeb0285b6f25d88b66ef832cc8349438a67532dc72b781baed4572465689'
RECOVERY_BODY_SHA256 = 'c3681a69f917efd3301e88aca5ccf7dbcd55f7cc88cadbff99918e1c9546ba99'
RECOVERY_RUNS = {'ci.yml': 40619269603, 'windows-portable.yml': 40619269502,
                 'unix-packages.yml': 40619269546}
RECOVERY_JOBS = {
    'ci.yml': {'Python 3.10', 'Python 3.12', 'Python 3.13', 'Browser editing workflow'},
    'windows-portable.yml': {'Build redistributable FFmpeg x64', 'Package and run Windows x64 portable'},
    'unix-packages.yml': {'Build and accept macos-arm64', 'Build and accept macos-x64', 'Build and accept linux-x64'},
}
RECOVERY_OLD = {'v0.3.1': 415558347, 'v0.3.2': 420271003, 'v0.3.3': 424509643}


def recovery_synchronized(*, publishing):
    prefix = f'repos/{RECOVERY_REPO}'
    ref = api(f'{prefix}/git/ref/tags/{RECOVERY_TAG}')['object']
    require(ref['type'] == 'commit' and ref['sha'] == RECOVERY_SHA, 'Recovery tag changed')
    if publishing:
        require(api(f'{prefix}/branches/main')['commit']['sha'] == RECOVERY_SHA,
                'main advanced; refusing draft publication')
    for workflow, run_id in RECOVERY_RUNS.items():
        run = api(f'{prefix}/actions/runs/{run_id}')
        require(run['head_sha'] == RECOVERY_SHA and run['head_branch'] == 'main'
                and run['event'] == 'push' and run['path'] == '.github/workflows/' + workflow,
                'Recovery workflow/source mismatch')
        require(run['status'] == 'completed' and run['conclusion'] == 'success',
                'Recovery workflow not successful')
        jobs = api(f'{prefix}/actions/runs/{run_id}/jobs?per_page=100')['jobs']
        require(len(jobs) == len(RECOVERY_JOBS[workflow])
                and {job['name'] for job in jobs} == RECOVERY_JOBS[workflow]
                and all(job['status'] == 'completed' and job['conclusion'] == 'success' for job in jobs),
                'Recovery job missing, skipped or unsuccessful')


def recovery_release():
    release = api(f'repos/{RECOVERY_REPO}/releases/{RECOVERY_ID}')
    require(release['id'] == RECOVERY_ID and release['tag_name'] == RECOVERY_TAG
            and release['target_commitish'] == RECOVERY_SHA and release['prerelease'],
            'Recovery release ID/tag/source mismatch')
    require(hashlib.sha256(release['body'].encode('utf-8')).hexdigest() == RECOVERY_BODY_SHA256,
            'Recovery release notes changed')
    return release


def recovery_old_state():
    state = {}
    for tag, release_id in RECOVERY_OLD.items():
        release = api(f'repos/{RECOVERY_REPO}/releases/{release_id}')
        require(release['id'] == release_id and release['tag_name'] == tag and not release['draft'],
                'Historical release identity changed')
        state[tag] = {
            'ref': api(f'repos/{RECOVERY_REPO}/git/ref/tags/{tag}')['object'],
            'source': release['target_commitish'], 'prerelease': release['prerelease'],
            'body': hashlib.sha256(release['body'].encode('utf-8')).hexdigest(),
            'assets': sorted((a['id'], a['name'], a['size'], a['digest']) for a in release['assets']),
        }
    return state


def recovery_assets(release, expected, *, complete=False):
    assets = release['assets']
    require(len({a['name'] for a in assets}) == len(assets), 'Duplicate recovery asset name')
    require(set(a['name'] for a in assets) <= set(expected), 'Unexpected recovery asset')
    for asset in assets:
        require(asset['state'] == 'uploaded'
                and (asset['size'], asset['digest']) == expected[asset['name']],
                'Existing recovery asset hash/state mismatch')
    if complete:
        require(len(assets) == len(expected), 'Recovery assets incomplete')
    return {a['name']: a for a in assets}


def upload_recovery_asset(release, path):
    # Use the draft's official upload_url and the existing Actions job token.
    # Never change hosts, follow authenticated redirects, or replace an asset.
    url = release['upload_url'].split('{', 1)[0]
    require(url == f'https://uploads.github.com/repos/{RECOVERY_REPO}/releases/{RECOVERY_ID}/assets',
            'Unexpected recovery upload URL')
    url += '?' + urllib.parse.urlencode({'name': path.name})
    with path.open('rb') as source:
        request = urllib.request.Request(url, method='POST', data=source, headers={
            'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'User-Agent': 'CutVoke-release',
            'Content-Type': 'application/octet-stream', 'Content-Length': str(path.stat().st_size),
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
        })
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=180) as response:
                require(response.status == 201, 'Recovery upload not confirmed')
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'Recovery upload HTTP {error.code}; stopping without replacement') from None


def resume_release(release_id):
    require(release_id == RECOVERY_ID, 'Only the bound v0.3.4 draft can be resumed')
    require(os.environ.get('GITHUB_REPOSITORY') == RECOVERY_REPO
            and os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
            and os.environ.get('GITHUB_REF', '').startswith('refs/heads/'),
            'Recovery requires a manual repository Actions branch run')
    release = recovery_release()
    recovery_synchronized(publishing=release['draft'])
    old_state = recovery_old_state()
    output = ROOT / '.artifacts/release'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='resume-v034-', dir=output) as work_dir:
        work = Path(work_dir)
        manifest_path = output / 'BUILD-INFO.json'
        if release['draft']:
            artifact = api(f'repos/{RECOVERY_REPO}/actions/artifacts/{RECOVERY_MANIFEST_ARTIFACT}')
            require(artifact['name'] == 'release-verification' and not artifact['expired']
                    and artifact['workflow_run']['id'] == RECOVERY_PUBLICATION_RUN
                    and artifact['workflow_run']['head_sha'] == RECOVERY_SHA,
                    'Recovery manifest artifact source mismatch')
            outer = work / 'manifest.zip'
            download_artifact(RECOVERY_REPO, artifact, outer)
            with zipfile.ZipFile(outer) as archive:
                require(archive.namelist() == ['BUILD-INFO.json'], 'Unexpected recovery manifest archive')
                manifest_path.write_bytes(archive.read('BUILD-INFO.json'))
        else:
            with urllib.request.urlopen(f'https://github.com/{RECOVERY_REPO}/releases/download/{RECOVERY_TAG}/BUILD-INFO.json', timeout=180) as source:
                manifest_path.write_bytes(source.read())
        require(digest(manifest_path) == RECOVERY_MANIFEST_SHA256, 'Recovery manifest hash mismatch')
        manifest = json.loads(manifest_path.read_text('utf-8'))
        checks = {key: f'https://github.com/{RECOVERY_REPO}/actions/runs/{value}' for key, value in RECOVERY_RUNS.items()}
        require(manifest['version'] == '0.3.4' and manifest['tag'] == RECOVERY_TAG
                and manifest['commit'] == RECOVERY_SHA and manifest['prerelease'] is True
                and manifest['checks'] == checks and len(manifest['assets']) == 4,
                'Recovery manifest binding mismatch')
        sums = output / 'SHA256SUMS'
        sums.write_text(''.join(f'{a["sha256"]}  {a["name"]}\n' for a in manifest['assets']), encoding='ascii')
        expected = {'BUILD-INFO.json': (manifest_path.stat().st_size, 'sha256:' + digest(manifest_path)),
                    'SHA256SUMS': (sums.stat().st_size, 'sha256:' + digest(sums))}
        paths = []
        for (platform, (workflow, artifact_name, suffix)), item in zip(PACKAGES.items(), manifest['assets']):
            filename = f'CutVoke-0.3.4-{platform}{suffix}'
            require(item['name'] == filename and item['run'] == checks[workflow], 'Recovery package run/name mismatch')
            artifact = api(f'repos/{RECOVERY_REPO}/actions/artifacts/{item["artifactId"]}')
            require(artifact['name'] == artifact_name and artifact['workflow_run']['head_sha'] == RECOVERY_SHA
                    and artifact['workflow_run']['id'] == RECOVERY_RUNS[workflow], 'Recovery package artifact source mismatch')
            expected[filename] = (item['bytes'], 'sha256:' + item['sha256'])
            if release['draft']:
                require(not artifact['expired'], 'Recovery package artifact expired')
                outer = work / (platform + '-artifact.zip')
                download_artifact(RECOVERY_REPO, artifact, outer)
                with zipfile.ZipFile(outer) as archive:
                    require(archive.namelist().count(filename) == 1, 'Recovery archive filename mismatch')
                    info = json.loads(archive.read('download-info.json'))
                    verify_info(info, '0.3.4', RECOVERY_SHA, platform)
                    require(info['filename'] == filename and info['runUrl'] == checks[workflow]
                            and info['bytes'] == item['bytes'] and info['sha256'] == item['sha256'],
                            'Recovery build metadata mismatch')
                    path = work / filename
                    with archive.open(filename) as source, path.open('xb') as target:
                        shutil.copyfileobj(source, target, 1024 * 1024)
                require((path.stat().st_size, 'sha256:' + digest(path)) == expected[filename],
                        'Recovery package digest mismatch')
                verify_info(build_info(path), '0.3.4', RECOVERY_SHA, platform)
                paths.append(path)
                outer.unlink()
        initial = recovery_assets(release, expected, complete=not release['draft'])
        initial_ids = {name: asset['id'] for name, asset in initial.items()}
        if release['draft']:
            # All four actual archives have been checked before the first write.
            for path in paths + [manifest_path, sums]:
                recovery_synchronized(publishing=True)
                current = recovery_release()
                require(current['draft'], 'Recovery draft state changed during upload')
                existing = recovery_assets(current, expected)
                require(all(existing.get(name, {}).get('id') == asset_id for name, asset_id in initial_ids.items()),
                        'Existing recovery asset ID changed')
                if path.name not in existing:
                    uploaded = upload_recovery_asset(current, path)
                    require(uploaded['name'] == path.name and uploaded['state'] == 'uploaded'
                            and (uploaded['size'], uploaded['digest']) == expected[path.name],
                            'Recovery upload digest mismatch')
            current = recovery_release()
            require(current['draft'], 'Recovery draft state changed before publication')
            recovery_assets(current, expected, complete=True)
            recovery_synchronized(publishing=True)
            gh('api', '--method', 'PATCH', f'repos/{RECOVERY_REPO}/releases/{RECOVERY_ID}',
               '-F', 'draft=false', '-F', 'prerelease=true')
        published = recovery_release()
        require(not published['draft'], 'Recovery publication not confirmed')
        final = recovery_assets(published, expected, complete=True)
        require(all(final[name]['id'] == asset_id for name, asset_id in initial_ids.items()),
                'Existing recovery assets replaced')
        downloads = []
        for name, asset in final.items():
            url = f'https://github.com/{RECOVERY_REPO}/releases/download/{RECOVERY_TAG}/{name}'
            require(asset['browser_download_url'] == url, 'Recovery public URL mismatch')
            path = work / ('public-' + name)
            with urllib.request.urlopen(url, timeout=180) as source, path.open('xb') as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
            require((path.stat().st_size, 'sha256:' + digest(path)) == expected[name],
                    'Recovery public download mismatch')
            if name.startswith('CutVoke-'):
                platform = next(platform for platform, (_, _, suffix) in PACKAGES.items()
                                if name == f'CutVoke-0.3.4-{platform}{suffix}')
                verify_info(build_info(path), '0.3.4', RECOVERY_SHA, platform)
            downloads.append({'name': name, 'bytes': path.stat().st_size, 'sha256': digest(path), 'url': url})
            path.unlink()
        require(recovery_old_state() == old_state, 'Historical tags/releases/assets changed')
        recovery_synchronized(publishing=False)
        report = {'passed': True, 'releaseId': RECOVERY_ID, 'tag': RECOVERY_TAG,
                  'source': RECOVERY_SHA, 'recoveryToolCommit': os.environ.get('GITHUB_SHA'),
                  'checks': checks, 'verifiedPublicDownloads': downloads,
                  'preservedAssetIds': initial_ids, 'oldReleasesUnchanged': True}
        (output / 'recovery-verification.json').write_text(json.dumps(report, indent=2) + '\n')
        print(f'Published and verified six public files at {published["html_url"]}', flush=True)
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as summary:
            summary.write(f'Recovered existing release {RECOVERY_ID}; source `{RECOVERY_SHA}`; six public downloads verified.\n')


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
    require(not any(r['tag_name'] == tag for r in releases(repo)), 'Release already exists; refusing replacement')
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
    release_id = created_draft_id(repo, tag, sha)
    gh('release', 'upload', tag, '--repo', repo, *(str(p) for p in paths))
    release = api(f'repos/{repo}/releases/{release_id}')
    require(release['id'] == release_id and release['tag_name'] == tag
            and release['target_commitish'] == sha, 'Draft identity changed')
    require(release['draft'] and release['prerelease'], 'Unexpected release state')
    require(len(release['assets']) == len(expected), 'Unexpected asset count')
    for asset in release['assets']:
        require((asset['size'], asset['digest']) == expected.get(asset['name']), 'Uploaded asset digest mismatch')
    synchronized()
    gh('api', '--method', 'PATCH', f'repos/{repo}/releases/{release_id}',
       '-F', 'draft=false', '-F', 'prerelease=true')
    published = api(f'repos/{repo}/releases/{release_id}')
    require(published['id'] == release_id and published['tag_name'] == tag
            and published['target_commitish'] == sha, 'Published release identity mismatch')
    require(not published['draft'] and published['prerelease'], 'Publication not confirmed')
    # Verify public download bytes, not only API upload receipts.
    for asset in published['assets']:
        expected_url = f'https://github.com/{repo}/releases/download/{tag}/{asset["name"]}'
        require(asset['browser_download_url'] == expected_url, 'Download URL mismatch')
        with urllib.request.urlopen(expected_url, timeout=180) as response:
            actual = stream_digest(response)
        require('sha256:' + actual == expected[asset['name']][1], 'Public download digest mismatch')
    print(published['html_url'], flush=True)
    with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a', encoding='utf-8') as summary:
        summary.write(f'Published and downloaded all six verified assets: {published["html_url"]}\n\nSource: `{sha}`\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume-release-id', type=int)
    arguments = parser.parse_args()
    if arguments.resume_release_id is None:
        main()
    else:
        resume_release(arguments.resume_release_id)
