"""Archived, read-only v0.3.2 verification; historical releases are immutable.

The original source/main/tag bindings intentionally fail if history has moved.
This audit cannot repair tags, create releases, publish drafts or upload assets.
"""
import hashlib
import json
import os
from pathlib import Path
import urllib.request

from publish_verified import ROOT, api, build_info, digest, gh, require, verify_info


def main():
    repo = 'frankShan001/CutVoke'
    expected = json.loads(Path(__file__).with_name('recover_v032_expected.json').read_text())
    tag, sha = expected['tag'], expected['commit']
    require(os.environ['GITHUB_REPOSITORY'] == repo, 'Repository mismatch')
    endpoint = f'repos/{repo}/releases/{expected["releaseId"]}'

    def synchronized():
        require(api(f'repos/{repo}/branches/main')['commit']['sha'] == sha, 'main advanced')
        ref = api(f'repos/{repo}/git/ref/tags/{tag}')['object']
        require(ref['type'] == 'commit' and ref['sha'] == sha, 'Tag changed')

    def assets_match(release):
        actual = [{key: a[key] for key in ('id', 'name', 'size', 'digest')} for a in release['assets']]
        require(sorted(actual, key=lambda a: a['name']) == sorted(expected['assets'], key=lambda a: a['name']), 'Assets changed')
        require(release['tag_name'] == tag and release['target_commitish'] == sha and release['prerelease'], 'Release metadata mismatch')

    synchronized()
    release = api(endpoint)
    assets_match(release)
    require(not release['draft'], 'Historical recovery is retired; refusing draft publication')
    old = api(f'repos/{repo}/releases/tags/v0.3.1')
    old_assets = [(a['id'], a['name'], a['size'], a['digest']) for a in old['assets']]
    output = ROOT / '.artifacts/recovery'
    output.mkdir(parents=True, exist_ok=False)
    # Download only the existing public release; never upload or replace bytes.
    gh('release', 'download', tag, '--repo', repo, '--dir', str(output))
    for asset in expected['assets']:
        path = output / asset['name']
        require(path.stat().st_size == asset['size'] and 'sha256:' + digest(path) == asset['digest'], 'Draft download mismatch')
    manifest = json.loads((output / 'BUILD-INFO.json').read_text())
    require(manifest['version'] == '0.3.2' and manifest['tag'] == tag and manifest['commit'] == sha and manifest['prerelease'], 'Manifest mismatch')
    require(set(manifest['checks']) == {'ci.yml', 'windows-portable.yml', 'unix-packages.yml'}, 'Missing workflow gate')
    for workflow, url in manifest['checks'].items():
        run = api(f'repos/{repo}/actions/runs/{int(url.rsplit("/", 1)[1])}')
        require(run['head_sha'] == sha and run['head_branch'] == 'main' and run['event'] == 'push', 'Run source mismatch')
        require(run['path'] == '.github/workflows/' + workflow and run['status'] == 'completed' and run['conclusion'] == 'success', 'Run not successful')
    sums = ''.join(f'{a["sha256"]}  {a["name"]}\n' for a in manifest['assets'])
    require((output / 'SHA256SUMS').read_bytes() == sums.encode('ascii'), 'Checksum list mismatch')
    require(len(manifest['assets']) == 4, 'Expected four platform packages')
    for asset, platform in zip(manifest['assets'], ('windows-x64', 'macos-arm64', 'macos-x64', 'linux-x64')):
        path = output / asset['name']
        require(path.stat().st_size == asset['bytes'] and digest(path) == asset['sha256'], 'Manifest asset mismatch')
        verify_info(build_info(path), '0.3.2', sha, platform)
        artifact = api(f'repos/{repo}/actions/artifacts/{asset["artifactId"]}')
        require(artifact['workflow_run']['head_sha'] == sha, 'Original artifact source mismatch')
    synchronized()
    assets_match(api(endpoint))
    published = api(f'repos/{repo}/releases/tags/{tag}')
    require(published['id'] == expected['releaseId'] and not published['draft'], 'Publication not confirmed')
    assets_match(published)
    verified = []
    for asset in published['assets']:
        url = f'https://github.com/{repo}/releases/download/{tag}/{asset["name"]}'
        require(asset['browser_download_url'] == url, 'Canonical download URL mismatch')
        size, h = 0, hashlib.sha256()
        with urllib.request.urlopen(url, timeout=180) as response:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                h.update(chunk)
        require(size == asset['size'] and 'sha256:' + h.hexdigest() == asset['digest'], 'Public download mismatch')
        verified.append({'name': asset['name'], 'bytes': size, 'sha256': h.hexdigest(), 'url': url})
        print('Verified public download:', asset['name'], flush=True)
    old_after = api(f'repos/{repo}/releases/tags/v0.3.1')
    require(old_after['id'] == old['id'] and old_assets == [(a['id'], a['name'], a['size'], a['digest']) for a in old_after['assets']], 'Old release changed')
    synchronized()
    report = {'release': published['html_url'], 'releaseId': published['id'], 'commit': sha,
              'publishedAt': published['published_at'], 'prerelease': True, 'checks': manifest['checks'],
              'verifiedPublicDownloads': verified, 'oldReleaseUnchanged': True}
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as summary:
        summary.write(f'Audited six existing public downloads: {published["html_url"]}\n\nSource: `{sha}`. v0.3.1 preserved.\n')


if __name__ == '__main__':
    main()
