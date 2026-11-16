"""Guard the shared browser fixture against accidental legacy-format seeding."""
from pathlib import Path
import runpy
import shutil
import pytest
from cutvoke.storage.project_directory import ProjectLibraryStore
from cutvoke.editing.service import EditService
from cutvoke.interfaces.httpapi import HttpApi
from cutvoke.rendering.render import RenderService


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='fixture generates real FFmpeg media')
def test_shared_browser_fixture_is_current_and_http_readable(tmp_path):
    seed = runpy.run_path(str(Path(__file__).parents[1]/'support'/'e2e_server.py'))['seed_fixture']
    with ProjectLibraryStore(str(tmp_path/'projects.sqlite')) as store:
        service = EditService(store)
        seed(service, str(tmp_path))
        api = HttpApi(service, RenderService(), media_dir=str(tmp_path/'media'))
        try:
            status, project = api.handle('GET', '/api/v1/projects/e2e-fixture', {})
            assert status == 200, project
            assert project['schemaVersion'] == '2'
            assert store.directory('e2e-fixture') is not None
            clips = project['sequence']['tracks'][0]['clips']
            assert len(clips) == 3
            for clip, color in zip(clips, ("red", "green", "blue")):
                source = Path(clip['assetRef']['sourcePath'])
                assert source.name == color + ".mp4"
                assert source.is_file() and source.stat().st_size > 0
            # Ordinary E2Es create another project by sourcePath only. Ensure
            # its ledger keeps semantic labels for UI filtering/clip selection.
            status, created = api.handle('POST', '/api/v1/projects', {'projectId':'consumer'})
            assert status == 201, created
            for index, clip in enumerate(clips):
                status, current = api.handle('GET', '/api/v1/projects/consumer', {})
                assert status == 200
                status, result = api.handle('POST', '/api/v1/projects/consumer/commands', {
                    'type':'clip.insert', 'expectedRevision':current['revision'],
                    'payload':{'clipId':f'c{index}', 'trackId':'v', **({'createTrackKind':'video'} if index == 0 else {}),
                               'sourcePath':clip['assetRef']['sourcePath'],
                               'timelineStart':{'num':str(index*5),'den':'1'}, 'timelineEnd':{'num':str((index+1)*5),'den':'1'}}})
                assert status == 200, result
            status, assets = api.handle('GET', '/api/v1/assets', {'projectId':'consumer'})
            assert status == 200, assets
            assert {'red.mp4','green.mp4','blue.mp4'} <= {a['name'] for a in assets['assets']}
        finally:
            api.close()
