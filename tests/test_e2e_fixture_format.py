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
    seed = runpy.run_path(str(Path(__file__).parent/'support'/'e2e_server.py'))['seed_fixture']
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
            for clip in clips:
                source = Path(clip['assetRef']['sourcePath'])
                assert source.is_file() and source.stat().st_size > 0
        finally:
            api.close()
