from pathlib import Path
import subprocess
import wave

import numpy as np
import pytest
from PIL import Image

from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational
from cutvoke.editing.service import EditService
from cutvoke.rendering.render import RenderService
from cutvoke.rendering.audio_integrity import verify_audio, AudioIntegrityError


def chirp(path, seconds=36):
    rate = 48000
    t = np.arange(round(rate * seconds)) / rate
    left = .2 * np.sin(2*np.pi*(137*t+18*t*t)) + .12*np.sin(2*np.pi*(941*t+31*t*t))
    right = .18*np.sin(2*np.pi*(211*t+23*t*t)) + .13*np.sin(2*np.pi*(831*t+27*t*t))
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(2); stream.setsampwidth(2); stream.setframerate(rate)
        stream.writeframes((np.stack([left, right], axis=1) * 32767).astype('<i2').tobytes())


@pytest.mark.parametrize('start_frame,end_frame', [(0, 1549), (577, 1501)])
def test_48fps_dense_export_keeps_the_master_on_the_sample_clock(tmp_path, start_frame, end_frame):
    source = tmp_path/'chirp.wav'; chirp(source)
    image = tmp_path/'picture.png'; Image.new('RGB', (160,90), '#2570c0').save(image)
    service = EditService(); project = service.create_project('dense', width=160, height=90, fps=Rational.of(48))
    at = lambda frame: Rational.of(int(frame),48)
    frames = np.linspace(0,1549,29,dtype=int)
    clips = [Clip(f'v{i}',AssetReference('image',str(image)),at(a),at(b),Rational.of(0))
             for i,(a,b) in enumerate(zip(frames,frames[1:]))]
    project.sequence.tracks = [Track('v','video',clips), Track('music','audio',[
        Clip('master',AssetReference('master',str(source)),at(0),at(1549),Rational.of(0))])]
    renderer = RenderService(); renderer.gpu_acceleration = False
    out = tmp_path/'out.mp4'; duration = (end_frame-start_frame)/48
    result = renderer.render(project,str(out),quality='medium',
        **({'output_start':start_frame/48,'output_duration':duration} if start_frame else {}))
    assert result['audioStrategy'] == 'continuous-pcm'
    assert result['pix_fmt'] == 'yuv420p'
    assert result['color_space'] == result['color_primaries'] == result['color_transfer'] == 'bt709'
    assert result['color_range'] == 'tv'
    assert abs(result['audio_start_time']-result['video_start_time']) < .05
    assert result['audioVerification']['status'] == 'passed'
    assert min(item['zeroOffsetCorrelation'] for item in result['audioVerification']['windows']) > .995
    assert result['windowCount'] >= 3
    assert result['timings']['compilationWorkerSeconds'] >= 0
    assert result['timings']['compositionWorkerSeconds'] > 0
    expected = tmp_path/'expected.wav'
    subprocess.run(['ffmpeg','-v','error','-i',str(source),'-af',
        f'atrim=start_sample={start_frame*1000}:end_sample={end_frame*1000},asetpts=N/SR/TB',str(expected)],check=True)
    report = verify_audio(expected,out)
    assert report['status'] == 'passed' and abs(report['lengthDifferenceSeconds']) < .03


@pytest.mark.parametrize('filter', ['adelay=2:all=1', 'volume=0'])
def test_delivery_check_rejects_millisecond_shift_or_missing_signal(tmp_path, filter):
    source = tmp_path/'master.wav'; chirp(source,8)
    broken = tmp_path/'broken.m4a'
    subprocess.run(['ffmpeg','-v','error','-i',str(source),'-af',filter,'-c:a','aac','-b:a','192k',str(broken)],check=True)
    with pytest.raises(AudioIntegrityError, match='verification failed') as error:
        verify_audio(source,broken)
    assert error.value.report['status'] == 'failed'
