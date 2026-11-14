"""Synthetic cut-local audio inspection through real MCP content and export."""
import base64
import io
import json
import subprocess
import wave

import numpy as np
import pytest

from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditService
from cutvoke.interfaces.mcp_server import MCPServer


@pytest.fixture
def setup(tmp_path):
    rate = 48000
    t = np.arange(3 * rate) / rate
    samples = np.stack([.4*np.sin(2*np.pi*317*t), .2*np.sin(2*np.pi*701*t)], axis=1)
    source = tmp_path / 'tone.wav'
    with wave.open(str(source), 'wb') as wav:
        wav.setnchannels(2); wav.setsampwidth(2); wav.setframerate(rate)
        wav.writeframes((samples * 32767).astype('<i2').tobytes())
    service = EditService()
    project = service.create_project('inspect', width=160, height=90)
    project.sequence.tracks = [Track('a', 'audio', [
        Clip('one', AssetReference('tone', str(source)), R.of(0), R.of(1), R.of(0), volume=R.of(1,2)),
        Clip('two', AssetReference('tone', str(source)), R.of(1), R.of(2), R.of(1), fade_in=R.of(1,4))])]
    server = MCPServer(service, media_dir=str(tmp_path / 'media'))
    yield server, project, tmp_path
    server.close()


def call(server, **values):
    return server._handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
        'name': 'preview_audio', 'arguments': {'projectId': 'inspect', 'expectedRevision': '0', 'time': 1, **values}}})['result']


@pytest.mark.parametrize("mixed", [False, True])
def test_mcp_audio_peaks_match_export_and_preserve_project(setup, mixed):
    server, project, root = setup
    if mixed:
        source = project.sequence.tracks[0].clips[0].asset_ref
        project.sequence.tracks.append(Track("overlay", "audio", [
            Clip("fast", source, R.of(1,4), R.of(7,4), R.of(0), speed=R.of(3,2), volume=R.of(1,3))]))
    before = project.to_dict()
    result = call(server)
    assert not result['isError'], result
    metadata = json.loads(result['content'][0]['text'])
    assert metadata['revision'] == '0'
    assert metadata['startSample'] == 24000 and metadata['endSample'] == 72000
    assert len(metadata['peaks']) == 100
    content = result['content'][1]
    assert content['type'] == 'audio' and content['mimeType'] == 'audio/wav'
    with wave.open(io.BytesIO(base64.b64decode(content['data']))) as wav:
        assert wav.getnframes() == 48000 and wav.getnchannels() == 2
        samples = np.frombuffer(wav.readframes(wav.getnframes()), '<i2').reshape(-1,2) / 32767
    for index, (a, b) in enumerate(zip(metadata['peakBinEdges'], metadata['peakBinEdges'][1:])):
        actual = np.max(np.abs(samples[a-24000:b-24000]), axis=0)
        np.testing.assert_allclose(actual, metadata['peaks'][index], atol=1/32767)
    # A true full export is the independent delivery path, including AAC.
    output = root / 'export.mp4'
    server.render.gpu_acceleration = False
    server.render.render(project, str(output), quality='medium')
    pcm = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(output), '-f', 'f32le', '-ac', '2', '-ar', '48000', 'pipe:1'])
    exported = np.frombuffer(pcm, '<f4').reshape(-1,2)[24000:72000]
    assert np.corrcoef(exported.ravel(), samples.ravel())[0,1] > .995
    assert project.to_dict() == before


@pytest.mark.parametrize('values', [{'time': -1}, {'time': 3}, {'time': float('nan')}, {'radius': 3}, {'radius': True}, {'expectedRevision': 'stale'}])
def test_reject_invalid_and_stale(setup, values):
    server, project, _ = setup
    before = project.to_dict()
    assert call(server, **values)['isError']
    assert project.to_dict() == before


@pytest.mark.parametrize('at,first,last', [(0,0,24000), (2,72000,96000)])
def test_edges_are_clamped(setup, at, first, last):
    server, _, _ = setup
    result = call(server, time=at)
    assert not result['isError'], result
    metadata = json.loads(result['content'][0]['text'])
    assert (metadata['startSample'], metadata['endSample']) == (first,last)


def test_muted_timeline_returns_no_fabricated_audio(setup):
    server, project, _ = setup
    project.sequence.tracks[0].muted = True
    from PIL import Image
    image = setup[2] / 'silent.png'
    Image.new('RGB', (160,90)).save(image)
    project.sequence.tracks.append(Track('v', 'video', [Clip('still', AssetReference('still', str(image)), R.of(0), R.of(2), R.of(0))]))
    result = call(server)
    assert not result['isError'], result
    assert len(result['content']) == 1
    assert json.loads(result['content'][0]['text'])['hasAudio'] is False


def test_failure_cleans_temporary_audio(setup, monkeypatch):
    server, _, _ = setup
    from pathlib import Path
    from cutvoke.rendering.render import RenderCancelled
    paths = []
    def fail(project, out_path, *args, **kwargs):
        paths.append(Path(out_path))
        Path(out_path).write_bytes(b'partial')
        raise RenderCancelled('synthetic cancellation')
    monkeypatch.setattr(server.render, 'render_audio_window', fail)
    assert call(server)['isError']
    assert paths and not paths[0].parent.exists()


def test_snapshot_survives_edit_during_render(setup, monkeypatch):
    server, project, _ = setup
    original = server.render.render_audio_window
    def render(snapshot, *args, **kwargs):
        project.revision = '1'
        project.sequence.tracks[0].muted = True
        return original(snapshot, *args, **kwargs)
    monkeypatch.setattr(server.render, 'render_audio_window', render)
    result = call(server)
    assert not result['isError'], result
    value = json.loads(result['content'][0]['text'])
    assert value['revision'] == '0' and value['hasAudio']
    assert project.revision == '1'


def test_gap_is_silence(setup):
    server, project, _ = setup
    clips = project.sequence.tracks[0].clips
    clips[0].timeline_end = R.of(1,2)
    clips[1].timeline_start = R.of(3,2)
    result = call(server, radius=.25)
    assert not result['isError'], result
    value = json.loads(result['content'][0]['text'])
    assert value['hasAudio']
    assert np.max(value['peaks']) == 0


def test_nested_fails_before_visual_preparation(setup, monkeypatch):
    import copy
    server, project, _ = setup
    project.sequence.tracks[0].clips[0].nested = copy.deepcopy(project.sequence)
    def unexpected(*args, **kwargs):
        raise AssertionError('must not prepare visual caches')
    monkeypatch.setattr('cutvoke.rendering.compound_sources.prepare', unexpected)
    result = call(server)
    assert result['isError']
    assert 'nested compounds' in result['content'][0]['text']


def test_embedded_audio_skips_visual_preparation(setup, monkeypatch):
    server, project, root = setup
    source = project.sequence.tracks[0].clips[0].asset_ref.source_path
    movie = root / 'embedded.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=30:d=3',
                    '-i', source, '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(movie)], check=True)
    for clip in project.sequence.tracks[0].clips:
        clip.asset_ref.source_path = str(movie)
        clip.effects = [{'effectId': 'cutvoke.fx.blur', 'params': {'sigma': 8}}]
    project.sequence.tracks[0].kind = 'video'
    def unexpected(*args, **kwargs):
        raise AssertionError('must not prepare visual caches')
    monkeypatch.setattr('cutvoke.rendering.text_layers.prepare', unexpected)
    result = call(server)
    assert not result['isError'], result
    assert max(max(row) for row in json.loads(result['content'][0]['text'])['peaks']) > .1


def test_nondefault_sample_rate_max_radius_and_opposite_polarity(setup):
    server, project, _ = setup
    project.sequence.audio_sample_rate = 44100
    source = project.sequence.tracks[0].clips[0].asset_ref.source_path
    t = np.arange(3 * 48000) / 48000
    mono = .3 * np.sin(2*np.pi*401*t)
    with wave.open(source, 'wb') as wav:
        wav.setnchannels(2); wav.setsampwidth(2); wav.setframerate(48000)
        wav.writeframes((np.stack([mono, -mono], axis=1) * 32767).astype('<i2').tobytes())
    result = call(server, radius=2.5)
    assert not result['isError'], result
    value = json.loads(result['content'][0]['text'])
    assert value['sampleRate'] == 44100 and value['sampleCount'] == 88200
    peaks = np.array(value['peaks'])
    assert peaks.max() > .2
    np.testing.assert_allclose(peaks[:,0], peaks[:,1], atol=.0001)
    assert len(base64.b64decode(result['content'][1]['data'])) == 88200 * 4 + 44


def test_expired_budget_rejects_before_render(setup, monkeypatch):
    server, _, _ = setup
    class Expired:
        def __init__(self, seconds, callback): self.callback = callback
        def start(self): self.callback()
        def cancel(self): pass
    def unexpected(*args, **kwargs):
        raise AssertionError('expired request must not render')
    monkeypatch.setattr('cutvoke.rendering.audio_inspection.threading.Timer', Expired)
    monkeypatch.setattr(server.render, 'render_audio_window', unexpected)
    result = call(server)
    assert result['isError']
    assert 'superseded' in result['content'][0]['text']
