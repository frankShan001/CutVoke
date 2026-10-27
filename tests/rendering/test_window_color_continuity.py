"""Real pixels across window seams: mixed RGB effects, YUV clips and clocks."""
import copy
import shutil
import subprocess
import threading
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from cutvoke.domain.keyframes import Keyframe
from cutvoke.domain.model import AssetReference,Clip,Track
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditService
from cutvoke.rendering.render import RenderService,RenderError,RenderCancelled
from cutvoke.rendering.video_integrity import compare_seam,VideoIntegrityError

pytestmark=pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),reason='FFmpeg required')


class Continuous(RenderService):
    """The ordinary export compiler/encoder, without dispatching to windows."""
    def _render_in_windows(self,seq,out_path,quality,overlay_position,cancel_event,alpha,color_chain,
                           expected_primaries,video_bitrate_kbps,audio_bitrate_kbps,start,duration,
                           preflight_warnings,**kwargs):
        graph,inputs,_expected,audio,_warnings,cwd=self._compile(
            seq,overlay_position,alpha=alpha,render_window=(start,start+duration),frame_aligned_origin=True)
        try:
            self._run_ffmpeg(seq,graph,inputs,out_path,quality,cancel_event,audio,caption_cwd=cwd,
                alpha=alpha,color_chain=color_chain,output_start=start-self._render_ctx.compile_origin,
                output_duration=duration,video_bitrate_kbps=video_bitrate_kbps,audio_bitrate_kbps=audio_bitrate_kbps)
        finally:
            if cwd:shutil.rmtree(cwd,ignore_errors=True)
        return self._verify(out_path,duration,seq.width,seq.height,expect_alpha=alpha,
                            expect_primaries=expected_primaries)


def fixture(root,fps=R.of(48),*,alpha=False):
    width,height=160,96
    yy,xx=np.indices((height,width))
    rgb=np.stack([(xx*2+yy)%256,(yy*3+xx//2)%256,(xx+yy*2)%256],axis=2).astype(np.uint8)
    image=root/'pattern.png'
    if alpha:rgb=np.dstack([rgb,np.where(xx<20,0,150+xx//2).astype(np.uint8)])
    Image.fromarray(rgb).save(image)
    video=root/'source.mp4'
    if not alpha:
        subprocess.run(['ffmpeg','-v','error','-nostdin','-loop','1','-framerate',str(fps.to_fraction()),
            '-i',str(image),'-t','24','-vf','scale=out_color_matrix=bt709:out_range=tv,'
            'setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709',
            '-c:v','libx264','-preset','ultrafast','-crf','12','-pix_fmt','yuv420p',str(video)],check=True)
    source=image if alpha else video
    project=EditService().create_project('color-seams',width=width,height=height,fps=fps)
    clip=lambda name,a,b:Clip(name,AssetReference('source',str(source)),R.from_float(a),R.from_float(b),R.of(0))
    first=clip('rgb-treatment',0,2)
    first.effects=[{'effectId':'cutvoke.fx.opticalField','params':{'layer':'flare','duration':1.5,'intensity':.15,'seed':8}}]
    middle=clip('crossing-8-and-16',2,19)
    middle.effects=[{'effectId':'cutvoke.transition.crossfade','params':{'duration':.3}}]
    middle.keyframes={'brightness':[Keyframe('b0',R.of(0),-.03),Keyframe('b1',R.of(17),.04)],
                      'x':[Keyframe('x0',R.of(0),0),Keyframe('x1',R.of(17),3)]}
    # Actual short visible clips make the public exporter select its dense path.
    clips=[first,middle]+[clip(f'end-{i}',19+i/4,19+(i+1)/4) for i in range(24)]
    project.sequence.tracks=[Track('v','video',clips,muted=True)]
    return project


def frames(path):
    raw=subprocess.run(['ffmpeg','-v','error','-nostdin','-i',str(path),'-an','-pix_fmt','rgba',
                        '-f','rawvideo','pipe:1'],capture_output=True,check=True).stdout
    return np.frombuffer(raw,np.uint8).reshape(-1,96,160,4)


@pytest.mark.parametrize('fps,start',[(R.of(48),0.),(R.of(30000,1001),1.23)])
def test_mixed_rgb_yuv_transitions_and_keyframes_match_continuous_render(tmp_path,fps,start):
    project=fixture(tmp_path,fps);outputs=[];report=None
    for cls,name in [(Continuous,'continuous'),(RenderService,'windows')]:
        renderer=cls(gpu_effects=False);renderer.gpu_acceleration=False
        path=tmp_path/(name+'.mp4')
        report=renderer.render(copy.deepcopy(project),str(path),quality='high',output_start=start,output_duration=17)
        outputs.append(frames(path))
    assert outputs[0].shape==outputs[1].shape
    delta=outputs[1].astype(float)-outputs[0].astype(float)
    assert np.abs(delta).mean()<.08
    assert np.abs(delta.mean(axis=(1,2))).max()<.15
    assert report['videoVerification']['status']=='passed'
    assert len(report['videoVerification']['boundaries'])==2
    assert report['audioStrategy']=='none' and report['has_audio'] is False


@pytest.mark.parametrize('options',[
    {'color_space':'bt601','source_color_space':'bt601'},
    {'color_space':'bt2020'},
    {'tone_map':'reinhard','source_transfer':'pq'},
])
def test_export_color_policy_is_applied_once_after_lossless_window_join(tmp_path,options):
    project=fixture(tmp_path,R.of(12));outputs=[]
    for cls,name in [(Continuous,'continuous'),(RenderService,'windows')]:
        renderer=cls(gpu_effects=False);renderer.gpu_acceleration=False
        path=tmp_path/(name+'.mp4')
        report=renderer.render(copy.deepcopy(project),str(path),quality='high',output_start=0,output_duration=17,**options)
        outputs.append(frames(path))
    assert np.abs(outputs[0].astype(float)-outputs[1].astype(float)).mean()<.08
    assert report['videoVerification']['status']=='passed'


def test_transparent_export_keeps_alpha_and_color_across_seams(tmp_path):
    project=fixture(tmp_path,R.of(12),alpha=True);outputs=[]
    for cls,name in [(Continuous,'continuous'),(RenderService,'windows')]:
        renderer=cls(gpu_effects=False);renderer.gpu_acceleration=False
        path=tmp_path/(name+'.mov')
        report=renderer.render(copy.deepcopy(project),str(path),quality='high',alpha=True,output_start=0,output_duration=17)
        outputs.append(frames(path))
    assert np.abs(outputs[0].astype(float)-outputs[1].astype(float)).mean()<.08
    assert outputs[1][96,...,3].min()<10 and outputs[1][96,...,3].max()>100
    assert report['has_alpha'] and report['videoVerification']['status']=='passed'


def test_corrupted_rendered_window_fails_delivery_without_replacing_existing_output(tmp_path):
    class BrokenWindow(RenderService):
        def _run_ffmpeg(self,seq,graph,inputs,out_path,*args,**kwargs):
            if Path(out_path).name.startswith('window-') and '000000000' not in Path(out_path).name:
                graph=graph.replace('[outv]','[beforeFault]')+';[beforeFault]eq=brightness=.08[outv]'
            return super()._run_ffmpeg(seq,graph,inputs,out_path,*args,**kwargs)
    project=fixture(tmp_path,R.of(12));renderer=BrokenWindow(gpu_effects=False);renderer.gpu_acceleration=False
    path=tmp_path/'existing.mp4';path.write_bytes(b'keep original delivery')
    with pytest.raises(RenderError,match='video seam verification failed'):
        renderer.render(project,str(path),quality='high',overwrite=True,output_start=0,output_duration=17)
    assert path.read_bytes()==b'keep original delivery'
    assert not list(tmp_path.glob('cutvoke-export-windows-*'))


def test_intentional_scene_flash_passes_but_extra_seam_brightness_is_detected():
    expected=np.zeros((2,16,16,4),np.uint8);expected[1,:,:,:3]=255;expected[:,:,:,3]=255
    assert compare_seam(expected,expected,8.)['status']=='passed'
    faulty=expected.copy();faulty[0,:,:,:3]=8
    with pytest.raises(VideoIntegrityError,match='verification failed'):compare_seam(faulty,expected,8.)


def test_real_cut_at_window_boundary_with_embedded_audio_is_not_a_false_alarm(tmp_path):
    project=fixture(tmp_path,R.of(12))
    original=tmp_path/'source.mp4';embedded=tmp_path/'with-audio.mp4'
    subprocess.run(['ffmpeg','-v','error','-nostdin','-i',str(original),'-f','lavfi','-i',
        'sine=frequency=431:sample_rate=44100:duration=24','-c:v','copy','-c:a','aac',str(embedded)],check=True)
    track=project.sequence.tracks[0];track.muted=False
    for clip in track.clips:clip.asset_ref.source_path=str(embedded)
    middle=track.clips[1];middle.timeline_end=R.of(8)
    white=tmp_path/'white.png';Image.new('RGB',(160,96),'white').save(white)
    track.clips.insert(2,Clip('true-cut',AssetReference('white',str(white)),R.of(8),R.of(19),R.of(0)))
    renderer=RenderService(gpu_effects=False);renderer.gpu_acceleration=False
    output=tmp_path/'cut.mp4'
    result=renderer.render(project,str(output),quality='high',output_start=0,output_duration=17)
    pixels=frames(output)
    assert np.abs(pixels[95].astype(float)-pixels[96].astype(float)).mean()>50
    assert result['videoVerification']['status']=='passed'
    assert result['audioVerification']['status']=='passed'


def test_cancel_during_seam_verification_does_not_publish(tmp_path):
    project=fixture(tmp_path,R.of(12));renderer=RenderService(gpu_effects=False);renderer.gpu_acceleration=False
    cancel=threading.Event();path=tmp_path/'cancelled.mp4'
    def progress(value):
        if value['phase']=='verifying':cancel.set()
    with pytest.raises(RenderCancelled):
        renderer.render(project,str(path),quality='high',cancel_event=cancel,output_start=0,output_duration=17,
                        progress_callback=progress)
    assert not path.exists() and not list(tmp_path.glob('cutvoke-export-windows-*'))


@pytest.mark.parametrize('yuv', [False, True])
def test_planar_transition_inputs_keep_all_256_alpha_levels_exact(yuv):
    # Compare real coverage bytes, including 128 where FFmpeg 7's ordinary
    # packed-to-planar conversion rounds to 129. Both transition families must
    # preserve coverage independently of their color-plane representation.
    pixels = np.zeros((2, 256, 4), dtype=np.uint8)
    pixels[:, :, :3] = [63, 127, 191]
    pixels[:, :, 3] = np.arange(256, dtype=np.uint8)
    renderer = RenderService(gpu_effects=False)
    parts = []
    renderer._planar_alpha_for_transition('0:v', 'planar', parts, yuv=yuv)
    parts.append('[planar]alphaextract[out]')
    result = subprocess.run([
        'ffmpeg', '-v', 'error', '-nostdin', '-filter_complex_threads', '1',
        '-f', 'rawvideo', '-pixel_format', 'rgba', '-video_size', '256x2',
        '-i', 'pipe:0', '-filter_complex', ';'.join(parts), '-map', '[out]',
        '-frames:v', '1', '-pixel_format', 'gray', '-f', 'rawvideo', 'pipe:1',
    ], input=pixels.tobytes(), capture_output=True, check=True)
    np.testing.assert_array_equal(np.frombuffer(result.stdout, np.uint8).reshape(2, 256),
                                  pixels[:, :, 3])
