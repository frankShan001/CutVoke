"""Controlled subprocess outcomes, not real-media or ASR acceptance."""
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import patch

import pytest

from cutvoke.domain.rational import Rational
from cutvoke.editing import auto_caption
from cutvoke.editing.service import EditError, EditService


@pytest.mark.parametrize('stderr', ['', 'silence_start: 0\nsilence_end: 1\n'])
def test_failed_detection_never_proposes_captions(tmp_path, stderr):
    audio = tmp_path / 'input.wav'
    audio.touch()
    with patch.object(auto_caption, '_probe_duration', return_value=3), patch(
        'subprocess.run', return_value=CompletedProcess([], 1, '', stderr)
    ):
        with pytest.raises(RuntimeError, match='silencedetect failed.*exit code 1'):
            auto_caption.segment_audio(str(audio))
        with pytest.raises(EditError, match='silencedetect failed'):
            EditService._h_caption_auto_segment(None, None, {'audioPath': str(audio)})


def test_successful_detection_without_silence_keeps_full_segment(tmp_path):
    audio = tmp_path / 'input.wav'
    audio.touch()
    with patch.object(auto_caption, '_probe_duration', return_value=3), patch(
        'subprocess.run', return_value=CompletedProcess([], 0, '', '')
    ):
        result = auto_caption.segment_audio(str(audio))
    assert result['segmentCount'] == 1
    assert result['captions'][0]['start'] == Rational(0, 1)
    assert result['captions'][0]['end'] == Rational(3, 1)


def captions():
    return {'captions': [
        {'index': i, 'start': Rational(i - 1, 1), 'end': Rational(i, 1)}
        for i in (1, 2, 3)
    ]}


@pytest.mark.parametrize('failed_at', [1, 2])
def test_failed_segment_export_stops_without_success(tmp_path, failed_at):
    results = [CompletedProcess([], 0, '', '')] * (failed_at - 1)
    results.append(CompletedProcess([], 2, '', 'private input path'))
    with patch.object(auto_caption, 'segment_audio', return_value=captions()), patch(
        'subprocess.run', side_effect=results
    ) as run:
        with pytest.raises(EditError, match=f'segment {failed_at}.*exit code 2') as error:
            EditService._h_audio_split_sentences(None, None, {
                'audioPath': 'synthetic.wav', 'outDir': str(tmp_path)})
    assert run.call_count == failed_at
    assert 'private input path' not in str(error.value)


def test_successful_segment_exports_keep_response(tmp_path):
    with patch.object(auto_caption, 'segment_audio', return_value=captions()), patch(
        'subprocess.run', return_value=CompletedProcess([], 0, '', '')
    ) as run:
        result = EditService._h_audio_split_sentences(None, None, {
            'audioPath': 'synthetic.wav', 'outDir': str(tmp_path)})
    assert run.call_count == 3
    assert result[0]['change'] == 'split'
    assert result[0]['count'] == 3


def test_detection_timeout_still_fails():
    with patch('subprocess.run', side_effect=TimeoutExpired('ffmpeg', 180)):
        with pytest.raises(RuntimeError, match='timeout'):
            auto_caption._run_silencedetect('synthetic.wav', .35, -30, 'ffmpeg')
