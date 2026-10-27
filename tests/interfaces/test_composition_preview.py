import copy

from cutvoke.interfaces.httpapi import HttpApi
from cutvoke.domain.project_health import project_preflight
from cutvoke.domain.rational import Rational as R
from tests.support.composition_fixtures import setup, frames


def test_http_window_does_not_drop_a_matte_reference_outside_its_interval(tmp_path):
    service, store, renderer, command = setup(tmp_path)
    api = HttpApi(service, renderer, media_dir=str(tmp_path / 'cache'))
    try:
        command('clip.insert', clipId='matte', trackId='v2', createTrackKind='video', sourcePath=str(tmp_path/'red.png'), timelineStart=R.of(3).to_json(), timelineEnd=R.of(6).to_json())
        command('clip.composite', clipId='base', compositing={'matte': {'clipId': 'matte', 'invert': True}})
        output, _, _ = api._preview_window_file(service.get_project('composition'), 0)
        assert frames(output)[3, 20, 20, 0] > 160
    finally:
        api.close(); service.close(); store.close()


def test_hidden_matte_is_a_required_dependency_and_still_drives_pixels(tmp_path):
    service, store, renderer, command = setup(tmp_path)
    try:
        command('clip.insert', clipId='matte', trackId='v2', createTrackKind='video', sourcePath=str(tmp_path/'red.png'), timelineEnd=R.of(3).to_json())
        command('clip.composite', clipId='base', compositing={'matte': {'clipId': 'matte'}})
        command('track.update', trackId='v2', visible=False)
        project = service.get_project('composition')
        report = project_preflight(project)
        assert any(ref['clipId']=='matte' and ref['required'] for item in report['files'] for ref in item['references'])
        output = tmp_path/'hidden.mp4'; renderer.render(project,str(output))
        assert frames(output)[3,20,20,0] > 160
    finally:
        service.close(); store.close()


def test_profile_is_read_only_and_prefetch_cancellation_has_a_scoped_token(tmp_path):
    service, store, renderer, command = setup(tmp_path)
    api = HttpApi(service, renderer, media_dir=str(tmp_path/'cache'))
    try:
        command('effect.add',clipId='base',effectId='cutvoke.fx.invert',params={})
        before = copy.deepcopy(store.load('composition').to_dict())
        result = command('composition.profile',start=0,duration=.3,maximum=320)
        report = result.changed_entities[0]
        assert len(report['runs']) == 2 and report['runs'][0]['effects'][0]['samples'] > 0
        assert store.load('composition').to_dict() == before
        token = 'b'*32
        status, result = api.handle('POST','/api/v1/projects/composition/preview-prefetch-cancel',{'token':token})
        assert status == 200 and result['cancelled']
        assert api._prefetch_tokens[token].is_set()
    finally:
        api.close(); service.close(); store.close()
