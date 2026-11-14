"""Contract checks for the runnable audio-first tutorial, using original media."""
import importlib.util
from pathlib import Path
import wave

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('audio_first_tutorial', ROOT / 'examples/audio-first/demo.py')
tutorial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tutorial)


def test_tones_are_original_stereo_and_unfaded(tmp_path):
    first, second = tmp_path / 'one.wav', tmp_path / 'two.wav'
    tutorial.tone(first, 1, [220,330]); tutorial.tone(second, 1, [220,330])
    assert first.read_bytes() == second.read_bytes()
    with wave.open(str(first)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) == (2,2,48000,48000)


def test_existing_output_is_preserved(tmp_path):
    marker = tmp_path / 'keep.txt'; marker.write_text('keep')
    with pytest.raises(FileExistsError): tutorial.run(tmp_path)
    assert marker.read_text() == 'keep'
    assert list(tmp_path.iterdir()) == [marker]


def test_roundtrip_normalization_preserves_all_editable_fields():
    clip = {'assetRef':{'sourcePath':'old.wav','assetId':'music'},'volume':.5}
    before = {'sequence':{'tracks':[{'clips':[clip]}]},'captions':[{'text':'keep'}]}
    normalized = tutorial.semantic_project(before)
    assert normalized['sequence']['tracks'][0]['clips'][0]['assetRef']['sourcePath'] == '<relocated>'
    assert normalized['sequence']['tracks'][0]['clips'][0]['volume'] == .5
    assert normalized['captions'] == before['captions']
    assert clip['assetRef']['sourcePath'] == 'old.wav'


def test_real_audio_first_export_and_package_roundtrip(tmp_path):
    result = tutorial.run(tmp_path / 'new-run')
    assert result['fullDecode'] and result['editablePackageRoundTrip'] and result['roundTripAudioMatches']
    assert result['audioRoles'] == ['music','sound_effect']
    assert result['audio']['startRms'] < result['audio']['middleRms'] * .3
    assert result['audio']['endRms'] < result['audio']['middleRms'] * .3
