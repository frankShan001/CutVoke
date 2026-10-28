"""Exercise the bound Actions recovery without credentials or network writes."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import zipfile

import pytest


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'tools/release/publish_verified.py'
    spec = importlib.util.spec_from_file_location('resume_publication', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    for key, value in {'GITHUB_REPOSITORY': m.RECOVERY_REPO, 'GITHUB_ACTIONS': 'true',
                       'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/recovery',
                       'GITHUB_SHA': 'b' * 40, 'GITHUB_STEP_SUMMARY': str(tmp_path / 'summary')}.items():
        monkeypatch.setenv(key, value)
    state = {'draft': True, 'uploads': [], 'patches': [], 'assets': [], 'downloads': [],
             'mode': None, 'packageDownloads': 0, 'api': []}
    checks = {k: f'https://github.com/{m.RECOVERY_REPO}/actions/runs/{v}' for k, v in m.RECOVERY_RUNS.items()}
    packages, outers, artifacts, items = {}, {}, {}, []
    for index, (platform, (workflow, artifact_name, suffix)) in enumerate(m.PACKAGES.items()):
        filename = f'CutVoke-0.3.4-{platform}{suffix}'
        info = {'version': '0.3.4', 'commit': m.RECOVERY_SHA, 'platform': platform}
        payload = io.BytesIO()
        if suffix.endswith('.zip'):
            with zipfile.ZipFile(payload, 'w') as z:
                z.writestr('app/build-info.json', json.dumps(info))
        else:
            data = json.dumps(info).encode()
            with tarfile.open(fileobj=payload, mode='w:gz') as z:
                entry = tarfile.TarInfo('app/build-info.json'); entry.size = len(data)
                z.addfile(entry, io.BytesIO(data))
        binary = payload.getvalue()
        packages[filename] = binary
        info.update(filename=filename, bytes=len(binary), sha256=sha(binary), runUrl=checks[workflow])
        artifact_id = 100 + index
        outer = io.BytesIO()
        with zipfile.ZipFile(outer, 'w') as z:
            z.writestr(filename, binary); z.writestr('download-info.json', json.dumps(info))
        outers[artifact_id] = outer.getvalue()
        artifacts[artifact_id] = {'id': artifact_id, 'name': artifact_name, 'expired': False,
            'size_in_bytes': len(outer.getvalue()), 'digest': 'sha256:' + sha(outer.getvalue()),
            'workflow_run': {'id': m.RECOVERY_RUNS[workflow], 'head_sha': m.RECOVERY_SHA}}
        items.append({'name': filename, 'bytes': len(binary), 'sha256': sha(binary),
                      'run': checks[workflow], 'artifactId': artifact_id})
    manifest = {'version': '0.3.4', 'tag': m.RECOVERY_TAG, 'commit': m.RECOVERY_SHA,
                'prerelease': True, 'checks': checks, 'assets': items}
    raw_manifest = (json.dumps(manifest, indent=2) + '\n').encode()
    packages['BUILD-INFO.json'] = raw_manifest
    packages['SHA256SUMS'] = ''.join(f'{a["sha256"]}  {a["name"]}\n' for a in items).encode('ascii')
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, 'w') as z:
        z.writestr('BUILD-INFO.json', raw_manifest)
    outers[m.RECOVERY_MANIFEST_ARTIFACT] = outer.getvalue()
    artifacts[m.RECOVERY_MANIFEST_ARTIFACT] = {'id': m.RECOVERY_MANIFEST_ARTIFACT,
        'name': 'release-verification', 'expired': False,
        'size_in_bytes': len(outer.getvalue()), 'digest': 'sha256:' + sha(outer.getvalue()),
        'workflow_run': {'id': m.RECOVERY_PUBLICATION_RUN, 'head_sha': m.RECOVERY_SHA}}
    monkeypatch.setattr(m, 'RECOVERY_MANIFEST_SHA256', sha(raw_manifest))
    monkeypatch.setattr(m, 'RECOVERY_BODY_SHA256', sha(b'Original bilingual notes'))

    def asset(name, asset_id):
        data = packages[name]
        return {'id': asset_id, 'name': name, 'size': len(data), 'digest': 'sha256:' + sha(data),
                'state': 'uploaded',
                'browser_download_url': f'https://github.com/{m.RECOVERY_REPO}/releases/download/{m.RECOVERY_TAG}/{name}'}

    def release():
        return {'id': m.RECOVERY_ID, 'tag_name': m.RECOVERY_TAG, 'target_commitish': m.RECOVERY_SHA,
                'body': 'Original bilingual notes', 'draft': state['draft'], 'prerelease': True,
                'assets': [dict(a) for a in state['assets']],
                'upload_url': f'https://uploads.github.com/repos/{m.RECOVERY_REPO}/releases/{m.RECOVERY_ID}/assets{{?name,label}}',
                'html_url': f'https://github.com/{m.RECOVERY_REPO}/releases/tag/{m.RECOVERY_TAG}'}

    def api(endpoint):
        state['api'].append(endpoint)
        if endpoint.endswith(f'releases/{m.RECOVERY_ID}'):
            r = release()
            field = {'wrong_id': 'id', 'wrong_tag': 'tag_name', 'wrong_source': 'target_commitish',
                     'changed_notes': 'body'}.get(state['mode'])
            if field: r[field] = 99 if field == 'id' else 'wrong'
            return r
        for tag, release_id in m.RECOVERY_OLD.items():
            if endpoint.endswith(f'releases/{release_id}'):
                h = 'changed' if state['mode'] == 'old_changed' and not state['draft'] else 'old'
                return {'id': release_id, 'tag_name': tag, 'target_commitish': 'old-sha', 'draft': False,
                        'prerelease': True, 'body': 'Old notes', 'assets': [{'id': 9, 'name': 'old.zip', 'size': 1, 'digest': h}]}
        if '/git/ref/tags/' in endpoint:
            value = 'c' * 40 if state['mode'] == 'tag_changed' and endpoint.endswith(m.RECOVERY_TAG) else m.RECOVERY_SHA
            return {'object': {'type': 'commit', 'sha': value}}
        if endpoint.endswith('branches/main'):
            return {'commit': {'sha': 'c' * 40 if state['mode'] == 'main_advanced' else m.RECOVERY_SHA}}
        if '/actions/runs/' in endpoint:
            number = int(endpoint.split('/runs/')[1].split('/')[0])
            workflow = next(k for k, v in m.RECOVERY_RUNS.items() if v == number)
            if '/jobs?' in endpoint:
                jobs = [{'name': n, 'status': 'completed', 'conclusion': 'success'} for n in m.RECOVERY_JOBS[workflow]]
                if state['mode'] == 'skipped_job': jobs[0]['conclusion'] = 'skipped'
                return {'jobs': jobs}
            r = {'head_sha': m.RECOVERY_SHA, 'head_branch': 'main', 'event': 'push',
                 'path': '.github/workflows/' + workflow, 'status': 'completed', 'conclusion': 'success'}
            if state['mode'] == 'wrong_workflow': r['path'] = '.github/workflows/unrelated.yml'
            if state['mode'] == 'wrong_run_source': r['head_sha'] = 'c' * 40
            return r
        if '/actions/artifacts/' in endpoint:
            number = int(endpoint.rsplit('/', 1)[1]); r = dict(artifacts[number]); r['workflow_run'] = dict(r['workflow_run'])
            if state['mode'] == 'wrong_artifact_source' and number != m.RECOVERY_MANIFEST_ARTIFACT:
                r['workflow_run']['head_sha'] = 'c' * 40
            if state['mode'] == 'wrong_artifact_run' and number != m.RECOVERY_MANIFEST_ARTIFACT:
                r['workflow_run']['id'] = 999
            return r
        raise AssertionError(endpoint)

    def download(_repo, a, destination):
        data = outers[a['id']]
        if a['id'] != m.RECOVERY_MANIFEST_ARTIFACT: state['packageDownloads'] += 1
        if state['mode'] == 'bad_manifest' and a['id'] == m.RECOVERY_MANIFEST_ARTIFACT:
            replacement = io.BytesIO()
            with zipfile.ZipFile(replacement, 'w') as z: z.writestr('BUILD-INFO.json', b'{}')
            data = replacement.getvalue()
        destination.write_bytes(data)

    def upload(r, path):
        assert r['id'] == m.RECOVERY_ID and state['packageDownloads'] == 4
        assert not any(a['name'] == path.name for a in state['assets']), 'must not clobber'
        assert path.read_bytes() == packages[path.name]
        state['uploads'].append(path.name)
        result = asset(path.name, 1000 + len(state['uploads']))
        if state['mode'] == 'bad_upload': result['digest'] = 'sha256:' + '0' * 64
        state['assets'].append(result)
        return result

    def gh(*args):
        assert args == ('api', '--method', 'PATCH', f'repos/{m.RECOVERY_REPO}/releases/{m.RECOVERY_ID}',
                        '-F', 'draft=false', '-F', 'prerelease=true')
        assert len(state['assets']) == 6
        state['patches'].append(m.RECOVERY_ID); state['draft'] = False
        return '{}'

    def public(url, timeout):
        assert not state['draft'] and timeout == 180
        state['downloads'].append(url.rsplit('/', 1)[1])
        data = packages[url.rsplit('/', 1)[1]]
        if state['mode'] == 'bad_public' and url.endswith('.zip'): data += b'corrupt'
        return io.BytesIO(data)

    monkeypatch.setattr(m, 'api', api); monkeypatch.setattr(m, 'gh', gh)
    monkeypatch.setattr(m, 'download_artifact', download)
    monkeypatch.setattr(m, 'upload_recovery_asset', upload)
    monkeypatch.setattr(m.urllib.request, 'urlopen', public)
    return m, state, asset, packages, artifacts, outers


def test_empty_draft_uploads_only_verified_packages_and_downloads_six(recovery):
    m, s, *_ = recovery
    m.resume_release(m.RECOVERY_ID)
    assert len(s['uploads']) == 6 and s['patches'] == [m.RECOVERY_ID]
    assert len(s['downloads']) == 6
    report = json.loads((m.ROOT / '.artifacts/release/recovery-verification.json').read_text())
    assert report['passed'] and report['source'] == m.RECOVERY_SHA
    assert report['recoveryToolCommit'] == 'b' * 40


def test_partial_and_complete_drafts_preserve_asset_ids_and_upload_missing_only(recovery):
    m, s, asset, packages, *_ = recovery
    initial = list(packages)[:2]
    s['assets'] = [asset(n, 71 + i) for i, n in enumerate(initial)]
    m.resume_release(m.RECOVERY_ID)
    assert len(s['uploads']) == 4 and not set(initial) & set(s['uploads'])
    assert [(a['name'], a['id']) for a in s['assets'][:2]] == list(zip(initial, [71, 72]))
    # Already-public rerun audits bytes but cannot upload or publish again.
    s['uploads'].clear(); s['patches'].clear(); s['downloads'].clear()
    m.resume_release(m.RECOVERY_ID)
    assert s['uploads'] == [] and s['patches'] == [] and len(s['downloads']) == 7


def test_complete_unpublished_draft_publishes_without_upload(recovery):
    m, s, asset, packages, *_ = recovery
    s['assets'] = [asset(n, 71 + i) for i, n in enumerate(packages)]
    m.resume_release(m.RECOVERY_ID)
    assert s['uploads'] == [] and s['patches'] == [m.RECOVERY_ID]


@pytest.mark.parametrize('mode,message', [
    ('wrong_id', 'release ID/tag/source'), ('wrong_tag', 'release ID/tag/source'),
    ('wrong_source', 'release ID/tag/source'), ('changed_notes', 'notes changed'),
    ('tag_changed', 'tag changed'), ('main_advanced', 'main advanced'),
    ('wrong_workflow', 'workflow/source'), ('wrong_run_source', 'workflow/source'),
    ('skipped_job', 'job missing'), ('bad_manifest', 'manifest hash'),
    ('wrong_artifact_source', 'artifact source'), ('wrong_artifact_run', 'artifact source'),
])
def test_source_workflow_and_manifest_conflicts_stop_before_writes(recovery, mode, message):
    m, s, *_ = recovery; s['mode'] = mode
    with pytest.raises(RuntimeError, match=message): m.resume_release(m.RECOVERY_ID)
    assert s['uploads'] == [] and s['patches'] == []


@pytest.mark.parametrize('conflict', ['hash', 'extra', 'duplicate', 'starter'])
def test_existing_asset_conflicts_cannot_be_replaced_or_published(recovery, conflict):
    m, s, asset, packages, *_ = recovery
    a = asset(next(iter(packages)), 71); s['assets'] = [a]
    if conflict == 'hash': a['digest'] = 'sha256:' + '0' * 64
    if conflict == 'extra': a['name'] = 'unapproved.zip'
    if conflict == 'duplicate': s['assets'].append(dict(a, id=72))
    if conflict == 'starter': a['state'] = 'starter'
    with pytest.raises(RuntimeError): m.resume_release(m.RECOVERY_ID)
    assert s['uploads'] == [] and s['patches'] == []


@pytest.mark.parametrize('mode,message', [('bad_upload', 'upload digest'), ('bad_public', 'public download'), ('old_changed', 'Historical tags/releases/assets')])
def test_upload_public_bytes_and_historical_audit_fail_closed(recovery, mode, message):
    m, s, *_ = recovery; s['mode'] = mode
    with pytest.raises(RuntimeError, match=message): m.resume_release(m.RECOVERY_ID)
    assert not (m.ROOT / '.artifacts/release/recovery-verification.json').exists()
    if mode == 'bad_upload': assert s['patches'] == []


def test_wrong_recovery_target_or_non_actions_invocation_is_rejected(recovery, monkeypatch):
    m, s, *_ = recovery
    with pytest.raises(RuntimeError, match='bound'): m.resume_release(1)
    monkeypatch.setenv('GITHUB_ACTIONS', 'false')
    with pytest.raises(RuntimeError, match='manual repository Actions'): m.resume_release(m.RECOVERY_ID)
    assert s['api'] == [] and s['uploads'] == []


@pytest.mark.parametrize('field,value,message', [
    ('commit', 'c' * 40, 'source commit'), ('version', '0.3.3', 'version'),
    ('platform', 'unapproved', 'architecture'),
])
def test_embedded_package_identity_is_checked_before_upload(recovery, monkeypatch, field, value, message):
    m, s, *_ = recovery
    original = m.build_info
    monkeypatch.setattr(m, 'build_info', lambda path: {**original(path), field: value})
    with pytest.raises(RuntimeError, match=message): m.resume_release(m.RECOVERY_ID)
    assert s['uploads'] == [] and s['patches'] == []


def test_upload_uses_only_bound_official_url_and_streams_with_runtime_token(monkeypatch, tmp_path):
    script = Path(__file__).resolve().parents[2] / 'tools/release/publish_verified.py'
    spec = importlib.util.spec_from_file_location('upload_transport', script)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    path = tmp_path / 'asset.zip'; path.write_bytes(b'accepted binary')
    monkeypatch.setenv('GH_TOKEN', 'unit-test-runtime-token')
    seen = []
    class Response(io.BytesIO):
        status = 201
    class Opener:
        def open(self, request, timeout):
            seen.append(request.full_url)
            assert request.full_url == f'https://uploads.github.com/repos/{m.RECOVERY_REPO}/releases/{m.RECOVERY_ID}/assets?name=asset.zip'
            assert request.data.read() == b'accepted binary' and timeout == 180
            assert request.get_header('Authorization') == 'Bearer unit-test-runtime-token'
            assert request.get_header('Content-length') == str(path.stat().st_size)
            return Response(b'{"id":123}')
    monkeypatch.setattr(m.urllib.request, 'build_opener', lambda handler: Opener())
    with pytest.raises(RuntimeError, match='upload URL'):
        m.upload_recovery_asset({'upload_url': 'https://example.com/not-the-draft'}, path)
    assert seen == []
    base = f'https://uploads.github.com/repos/{m.RECOVERY_REPO}/releases/{m.RECOVERY_ID}/assets'
    assert m.upload_recovery_asset({'upload_url': base + '{?name,label}'}, path) == {'id': 123}
    assert len(seen) == 1
