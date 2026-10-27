"""Merging historical evidence must never resurrect a release mutation."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize('state, message', [('draft', 'retired'), ('main_advanced', 'main advanced'), ('tag_changed', 'Tag changed')])
def test_archived_recovery_refuses_stale_or_unpublished_history(tmp_path, monkeypatch, state, message):
    directory = Path(__file__).resolve().parents[2] / 'tools/release'
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location('archived_recovery', directory / 'recover_v032.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected = json.loads((directory / 'recover_v032_expected.json').read_text())
    monkeypatch.setenv('GITHUB_REPOSITORY', 'frankShan001/CutVoke')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    calls = []

    def read(endpoint):
        calls.append(endpoint)
        if endpoint.endswith('branches/main'):
            return {'commit': {'sha': 'a' * 40 if state == 'main_advanced' else expected['commit']}}
        if '/git/ref/tags/' in endpoint:
            return {'object': {'type': 'commit', 'sha': 'b' * 40 if state == 'tag_changed' else expected['commit']}}
        assert endpoint.endswith('/releases/' + str(expected['releaseId']))
        return {'assets': expected['assets'], 'tag_name': expected['tag'],
                'target_commitish': expected['commit'], 'prerelease': True, 'draft': True}

    def forbidden(*args, **kwargs):
        raise AssertionError('Historical audit attempted external execution/download: ' + repr(args))

    monkeypatch.setattr(module, 'api', read)
    monkeypatch.setattr(module, 'gh', forbidden)
    monkeypatch.setattr(module.urllib.request, 'urlopen', forbidden)
    with pytest.raises(RuntimeError, match=message):
        module.main()
    assert calls and not (tmp_path / '.artifacts').exists()
