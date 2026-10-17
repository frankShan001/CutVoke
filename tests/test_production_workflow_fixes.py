"""Regressions found by editing the CHASE THE LIGHT showcase end to end."""
import copy
import hashlib
import json
import threading
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from cutvoke.core.httpapi import HttpApi
from cutvoke.core.mcp_server import MCPServer
from cutvoke.core.model import AssetReference, Clip, Sequence, Track, EFFECT_CROSSFADE
from cutvoke.core.protocol import Command
from cutvoke.core.rational import Rational as R
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore
from cutvoke.core.projectpack import unpack_project
from cutvoke.runtime import runtime_status
from cutvoke import runtime


@pytest.fixture
def editor(tmp_path):
    store = ProjectStore(str(tmp_path / 'projects.sqlite'))
    service = EditService(store)
    service.create_project('production', width=320, height=180, fps=R.of(24))
    server = MCPServer(service, media_dir=str(tmp_path / 'media'))
    yield server
    server.close()
    store.close()


def apply(server, command, payload, command_id=None):
    result = server.call_tool('command_apply', {
        'projectId': 'production', 'expectedRevision': server.service.get_project('production').revision,
        'type': command, 'payload': payload, **({'commandId': command_id} if command_id else {})})
    assert result['ok'], result
    return result


def title(server, clip_id='title', start=0, end=1, parent=None):
    payload = {'clipId': clip_id, 'trackId': 'titles', 'createTrackKind': 'text',
               'timelineStart': R.of(start).to_json(), 'timelineEnd': R.of(end).to_json(),
               'text': {'content': 'LIGHT', 'fontFamily': 'Barlow Condensed', 'fontSize': 180, 'bold': True,
                        'x': .5, 'y': .5, 'strokeWidth': 0}}
    if parent:
        payload['attachedToClipId'] = parent
    return apply(server, 'clip.insert', payload)


def test_text_only_preview_export_and_native_preparation(editor, tmp_path):
    title(editor, start=1, end=3)
    project = editor.service.get_project('production')
    renderer = editor.render
    assert renderer.extract_frame(project, .5, str(tmp_path / 'gap.png')) is None
    assert renderer.extract_frame(project, 3, str(tmp_path / 'end.png')) is None
    png = tmp_path / 'title.png'
    assert renderer.extract_frame(project, 2.5, str(png))
    pixels = np.asarray(Image.open(png).convert('RGB'))
    assert np.count_nonzero(pixels.max(axis=2) > 160) > 100
    renderer.render(project, str(tmp_path / 'title.mp4'), overwrite=True)
    renderer._verify(str(tmp_path / 'title.mp4'), 3, 320, 180)
    alpha = tmp_path / 'alpha.png'
    renderer.extract_still(project, 2.5, str(alpha), alpha=True)
    a = np.asarray(Image.open(alpha).convert('RGBA'))[:, :, 3]
    assert a.max() > 200 and np.count_nonzero(a == 0) > a.size * .5
    result = editor.call_tool('preview_prepare', {'projectId': 'production', 'expectedRevision': project.revision})
    assert result['ok'], result
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = editor.call_tool('preview_job', {'projectId': 'production', 'jobId': result['jobId']})
        if job['state'] in {'completed', 'failed', 'cancelled'}:
            break
        time.sleep(.05)
    assert job['state'] == 'completed', job
    renderer._verify(job['path'], 3, 320, 180)


def test_group_copy_remaps_children_and_is_one_persistent_undo(editor, tmp_path):
    image = tmp_path / 'source.png'
    Image.new('RGB', (320, 180), 'navy').save(image)
    apply(editor, 'clip.insert', {'clipId': 'bike', 'trackId': 'video', 'createTrackKind': 'video',
          'sourcePath': str(image), 'timelineStart': R.of(0).to_json(), 'timelineEnd': R.of(3).to_json()})
    title(editor, start=1, end=2, parent='bike')
    apply(editor, 'clip.keyframe', {'clipId': 'title', 'action': 'batch', 'keyframes': [
        {'param': 'scale', 'time': 0, 'value': 1}, {'param': 'scale', 'time': 1, 'value': 2}]})
    before = copy.deepcopy(editor.service.get_project('production').to_dict())
    payload = {'clipIds': ['bike'], 'timelineStart': R.of(4).to_json()}
    preview = editor.call_tool('command_preview', {'projectId': 'production', 'expectedRevision': before['revision'],
                                                  'type': 'clip.duplicateGroup', 'payload': payload})
    assert preview['ok'], preview
    assert editor.service.get_project('production').to_dict() == before
    result = apply(editor, 'clip.duplicateGroup', payload, 'copy-group')
    mapping = {e['sourceClipId']: e['id'] for e in result['changedEntities']}
    reloaded = editor.service.store.load('production')
    clips = {c.id: c for t in reloaded.sequence.tracks for c in t.clips}
    assert clips[mapping['title']].attached_to_clip_id == mapping['bike']
    assert clips[mapping['title']].timeline_start == R.of(5)
    assert clips[mapping['bike']].timeline_start == R.of(4)
    assert clips[mapping['title']].keyframes['scale'][1].value == 2
    assert clips[mapping['title']].keyframes['scale'][1].id != clips['title'].keyframes['scale'][1].id
    apply(editor, 'history.undo', {})
    after = editor.service.store.load('production').to_dict()
    after['revision'] = before['revision']
    assert after == before
    apply(editor, 'history.redo', {})
    assert len([c for t in editor.service.store.load('production').sequence.tracks for c in t.clips]) == 4


def test_archive_is_reopenable_and_evidence_matches_frozen_revision(editor, tmp_path):
    title(editor)
    revision = editor.service.get_project('production').revision
    path = tmp_path / 'production.cvkpkg'
    result = editor.call_tool('project_archive', {'projectId': 'production', 'expectedRevision': revision,
                              'outPath': str(path), 'frameTimes': [.5]})
    assert result['ok'], result
    assert result['eventCount'] and result['retainedHistoryCount']
    assert editor.service.get_project('production').revision == revision
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read('production/manifest.json'))
        assert manifest['revision'] == revision
        for name, info in manifest['files'].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == info['sha256']
        assert 'production/frames/00.png' in archive.namelist()
    reopened, _mapping, warnings = unpack_project(str(path), str(tmp_path / 'opened'))
    assert not warnings
    assert reopened.sequence.tracks[0].clips[0].effects[0]['params']['content'] == 'LIGHT'
    refusal = editor.call_tool('project_archive', {'projectId': 'production', 'expectedRevision': revision,
                               'outPath': str(path)})
    assert not refusal['ok']


def test_stale_services_refuse_old_schemas_and_writes_but_allow_recovery(editor, tmp_path):
    editor.runtime_info['sourceFingerprint'] = 'old-build'
    info = editor.call_tool('runtime', {})
    assert info['restartRequired'] and info['currentSourceFingerprint'] != 'old-build'
    for name, arguments in [('editor_help', {}), ('effects_list', {}), ('capabilities', {}),
                            ('create_project', {'projectId': 'should-not-exist'})]:
        result = editor.call_tool(name, arguments)
        assert result['error']['code'] == 'RUNTIME_RESTART_REQUIRED', result
        assert not result['error']['committed']
    resource = editor._handle({'jsonrpc': '2.0', 'id': 1, 'method': 'resources/read',
                               'params': {'uri': 'cutvoke://command/clip.insert'}})
    assert resource['error']['data']['code'] == 'RUNTIME_RESTART_REQUIRED'
    assert editor.call_tool('project_summary', {'projectId': 'production'})['ok']
    api = HttpApi(editor.service, editor.render, media_dir=str(tmp_path / 'http'))
    try:
        api.runtime_info['sourceFingerprint'] = 'old-build'
        assert api.handle('GET', '/api/v1/runtime', {})[1]['restartRequired']
        assert api.handle('GET', '/api/v1/capabilities', {})[0] == 409
        assert api.handle('POST', '/api/v1/projects', {'projectId': 'no-write'})[0] == 409
    finally:
        api.close()


def test_bounded_preparation_skips_distant_optics_and_keeps_transition_handle(tmp_path):
    service = EditService()
    project = service.create_project('bounded', width=320, height=180)
    def clip(cid, start, end):
        return Clip(cid, AssetReference('a', str(tmp_path / 'source.png')), R.of(start), R.of(end), R.of(0),
                    effects=[{'effectId': 'cutvoke.fx.opticalField', 'params': {'seed': start + 1}}])
    outgoing, current, distant = clip('out', 0, 2), clip('current', 2, 4), clip('late', 20, 22)
    current.effects.append({'effectId': EFFECT_CROSSFADE, 'params': {'duration': .5}})
    project.sequence.tracks = [Track('video', 'video', [outgoing, current, distant])]
    prepared = tmp_path / 'optical.mov'
    prepared.write_bytes(b'cached')
    renderer = RenderService()
    with patch('cutvoke.core.vfx_light.cached_source', return_value=str(prepared)) as cache:
        with renderer._procedural_sources(project, render_window=(2.1, 2.2)) as snapshot:
            assert cache.call_count == 2
            assert '_renderedLightPath' not in snapshot.sequence.tracks[0].clips[2].effects[0]['params']
    assert all('_renderedLightPath' not in c.effects[0]['params'] for c in project.sequence.tracks[0].clips)


def test_runtime_detects_real_source_and_web_file_updates(tmp_path):
    package = tmp_path / 'package'
    (package / 'core').mkdir(parents=True)
    source = package / 'runtime.py'
    source.write_text('version_one', encoding='utf-8')
    web = tmp_path / 'web'
    web.mkdir()
    (web / 'index.html').write_text('first build', encoding='utf-8')
    with patch.object(runtime, '__file__', str(source)), patch.object(runtime, 'web_directory', return_value=web), \
            patch.dict(runtime._freshness_cache, clear=True):
        loaded = runtime.runtime_identity()
        assert not runtime.runtime_status(loaded)['restartRequired']
        source.write_text('version_two_with_new_capabilities', encoding='utf-8')
        changed = runtime.runtime_status(loaded)
        assert changed['restartRequired']
        assert changed['sourceFingerprint'] == loaded['sourceFingerprint']
        assert changed['currentSourceFingerprint'] != loaded['sourceFingerprint']
        restarted = runtime.runtime_identity()
        assert not runtime.runtime_status(restarted)['restartRequired']
        (web / 'index.html').write_text('second build', encoding='utf-8')
        assert runtime.runtime_status(restarted)['restartRequired']


def test_archive_render_failure_preserves_existing_output(editor, tmp_path):
    from cutvoke.core.render import RenderError
    title(editor)
    revision = editor.service.get_project('production').revision
    target = tmp_path / 'keep.cvkpkg'
    target.write_bytes(b'keep previous archive')
    with patch.object(editor.render, 'extract_frame', side_effect=RenderError('frame failed')):
        result = editor.call_tool('project_archive', {'projectId': 'production', 'expectedRevision': revision,
                                   'outPath': str(target), 'overwrite': True, 'frameTimes': [.5]})
    assert not result['ok']
    assert target.read_bytes() == b'keep previous archive'
    assert not list(tmp_path.glob('cutvoke-archive-*'))
    assert editor.service.get_project('production').revision == revision


def test_nested_group_copy_and_archive_relink_every_child(editor, tmp_path):
    source = tmp_path / 'red.png'
    Image.new('RGB', (320, 180), 'red').save(source)
    for i in range(2):
        apply(editor, 'clip.insert', {'clipId': f'shot-{i}', 'trackId': 'video',
              **({'createTrackKind': 'video'} if i == 0 else {}),
              'sourcePath': str(source), 'timelineStart': R.of(i).to_json(), 'timelineEnd': R.of(i + 1).to_json()})
    apply(editor, 'clip.group', {'clipIds': ['shot-0', 'shot-1'], 'groupId': 'compound'})
    apply(editor, 'clip.duplicateGroup', {'clipIds': ['compound']})
    revision = editor.service.get_project('production').revision
    path = tmp_path / 'nested.cvkpkg'
    result = editor.call_tool('project_archive', {'projectId': 'production', 'expectedRevision': revision,
                              'outPath': str(path)})
    assert result['ok'], result
    reopened, _mapping, warnings = unpack_project(str(path), str(tmp_path / 'nested-open'))
    assert not warnings
    source.rename(tmp_path / 'original-no-longer-at-source-path.png')
    children = [c for compound in reopened.sequence.tracks[0].clips
                for track in compound.nested.tracks for c in track.clips]
    assert len({c.id for c in children}) == 4
    assert all(Path(c.asset_ref.source_path).is_file() for c in children)
    picture = tmp_path / 'reopened.png'
    editor.render.extract_frame(reopened, 3.5, str(picture))
    pixel = Image.open(picture).convert('RGB').getpixel((160, 90))
    assert pixel[0] > 200 and pixel[1] < 30 and pixel[2] < 30
