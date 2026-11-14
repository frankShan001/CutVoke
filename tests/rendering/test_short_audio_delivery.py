"""Keep short sound effects sample-aligned and pad silent delivery tails."""
import shutil
import subprocess
import wave

import numpy as np
import pytest
from PIL import Image

from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditService
from cutvoke.rendering.render import RenderService


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg required')
@pytest.mark.parametrize('start_ms,duration_ms', [(0, 10), (451, 10), (451, 100), (451, 549), (451, 749)])
def test_short_effect_keeps_silent_tail_and_original_sample_clock(tmp_path, start_ms, duration_ms):
    rate = 48000
    source = tmp_path / 'effect.wav'
    signal = (.35 * np.sin(2 * np.pi * 1000 * np.arange(duration_ms * 48) / rate))
    with wave.open(str(source), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes((signal * 32767).astype('<i2').tobytes())
    image = tmp_path / 'still.png'
    Image.new('RGB', (160, 90), 'navy').save(image)
    service = EditService()
    try:
        project = service.create_project('effect', width=160, height=90, fps=R.of(30))
        project.sequence.tracks = [
            Track('v', 'video', [Clip('v', AssetReference('i', str(image)), R.of(0), R.of(1), R.of(0))]),
            Track('a', 'audio', [Clip('a', AssetReference('a', str(source)), R.of(start_ms, 1000),
                                    R.of(start_ms + duration_ms, 1000), R.of(0))])]
        output = tmp_path / 'delivery.mp4'
        result = RenderService(gpu_effects=False).render(project, str(output), quality='low')
        assert result['audioVerification']['status'] == 'passed'
        raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(output),
                                       '-vn', '-ac', '1', '-ar', str(rate), '-f', 'f32le', '-'])
        samples = np.frombuffer(raw, dtype='<f4')
        assert abs(len(samples) / rate - max(1, (start_ms + duration_ms) / 1000)) < .03
        audio_only = tmp_path / 'delivery.m4a'
        RenderService(gpu_effects=False).render_audio_only(project, str(audio_only))
        decoded = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(audio_only),
                                          '-ac', '1', '-ar', str(rate), '-f', 'f32le', '-'])
        assert abs(len(decoded) / 4 / rate - max(1, (start_ms + duration_ms) / 1000)) < .03
        start, end = start_ms * 48, (start_ms + duration_ms) * 48
        # Independent expected source waveform, not a second compiler invocation.
        assert np.corrcoef(samples[start:end], signal)[0, 1] > .98
        assert np.sqrt(np.mean(samples[start:end] ** 2)) > .15
        # AAC may ring briefly outside the event; the remaining tail is silence.
        if end + 2400 < rate:
            assert np.max(np.abs(samples[end + 2400:rate])) < .001
        if start > 2400:
            assert np.max(np.abs(samples[:start - 2400])) < .001
    finally:
        service.close()
