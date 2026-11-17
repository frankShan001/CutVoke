"""Real CPU exporter acceptance for compiled keys, not a copied render pipeline."""
import copy
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from cutvoke.domain.model import Clip, Track, AssetReference
from cutvoke.domain.keyframes import Keyframe
from cutvoke.domain.rational import Rational
from cutvoke.rendering.render import RenderService
from cutvoke.rendering.window_cache import WindowCache


class SelectiveRecoveryTests(unittest.TestCase):
    def exercise(self, *, motion=False, cpus=1):
        from tests.rendering.test_window_cache import RealRecovery
        with tempfile.TemporaryDirectory() as tmp, patch('os.cpu_count', return_value=cpus):
            root=Path(tmp)
            project=RealRecovery().project(root)
            if motion:
                source=root/'motion.mkv'
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',
                    'color=c=red:size=64x48:rate=12:duration=20','-c:v','ffv1',str(source)], check=True, capture_output=True)
                for clip in project.sequence.tracks[0].clips:
                    clip.asset_ref.source_path=str(source)
                    clip.source_start=clip.timeline_start
            if not motion:
                overlay=root/'overlay.png'
                Image.new('RGBA',(64,48),(0,0,255,80)).save(overlay)
                project.sequence.tracks.insert(1,Track('overlay','video',[
                    Clip('overlay-clip',AssetReference('overlay-source',str(overlay)),
                         Rational.of(0),Rational.of(39,2),Rational.of(0))]))
            edited=copy.deepcopy(project)
            edited.sequence.tracks[0].clips[-1].keyframes['opacity']=[Keyframe('late-opacity',Rational.of(0),.5)]
            runs=[]
            def export(document, target, expected):
                renderer=RenderService(gpu_effects=False)
                seen=set(); guard=threading.Lock(); pending=threading.local()
                original_restore,original_publish=WindowCache.restore,WindowCache.publish
                def restore(cache,key,path):
                    pending.key=key
                    with guard: seen.add(threading.get_ident())
                    return original_restore(cache,key,path)
                def publish(cache,key,path):
                    self.assertEqual(key,pending.key)
                    return original_publish(cache,key,path)
                with patch.object(WindowCache,'restore',restore), patch.object(WindowCache,'publish',publish), \
                      patch.object(renderer,'_render_continuous_audio',wraps=renderer._render_continuous_audio) as audio, \
                     patch.object(renderer,'_verify_window_seams',wraps=renderer._verify_window_seams) as seams:
                    result=renderer.render(document,str(target))
                self.assertEqual(len(seen),2 if cpus==16 else 1)
                self.assertEqual(result['windowCount'],3)
                self.assertEqual(result['reusedWindowCount'],expected)
                self.assertEqual(audio.call_count,1)
                self.assertEqual(seams.call_count,1)
                runs.append(result)
            baseline,changed,warm=(root/name for name in ('base.mp4','changed.mp4','warm.mp4'))
            export(project,baseline,0)
            # PNG already forces canvas: only last window changes. Plain FFV1
            # with late keyframes is deliberately unsupported and uses full-snapshot recovery.
            export(edited,changed,0 if motion else 2)
            export(edited,warm,3)
            cold_dir=root/'cold';cold_dir.mkdir();cold=cold_dir/'cold.mp4'
            export(edited,cold,0)
            for options in (['-an','-pix_fmt','rgba','-f','rawvideo'],['-vn','-f','f32le','-acodec','pcm_f32le']):
                def decode(path):
                    return subprocess.run(['ffmpeg','-v','error','-i',str(path),*options,'pipe:1'],check=True,capture_output=True).stdout
                reference=decode(cold)
                self.assertTrue(reference)
                self.assertEqual(decode(changed),reference)
                self.assertEqual(decode(warm),reference)
                if '-an' in options:
                    self.assertNotEqual(decode(baseline),reference)
                else:
                    self.assertEqual(decode(baseline),reference)
            # Damage one CURRENT entry while preserving its old digest. Older
            # snapshot entries may coexist; only published edited keys qualify.
            from cutvoke.rendering import window_cache
            keys=[]
            original=window_cache.WindowCache.restore
            def capture(cache,key,target):
                keys.append(key)
                return original(cache,key,target)
            with patch.object(window_cache.WindowCache,'restore',capture):
                export(edited,root/'capture.mp4',3)
            cache=WindowCache(root/'.cutvoke-render-cache')
            with sqlite3.connect(cache.database) as db:
                db.execute('UPDATE windows SET data=? WHERE key=?',(b'truncated',keys[0]))
            export(edited,root/'repaired.mp4',2)
            for options in (['-an','-pix_fmt','rgba','-f','rawvideo'],['-vn','-f','f32le','-acodec','pcm_f32le']):
                def decode(path):
                    return subprocess.run(['ffmpeg','-v','error','-i',str(path),*options,'pipe:1'],check=True,capture_output=True).stdout
                self.assertEqual(decode(root/'repaired.mp4'),decode(cold))

    def test_png_selective_recovery_single_and_two_workers(self):
        for cpus in (1,16):
            with self.subTest(cpus=cpus):
                self.exercise(cpus=cpus)

    def test_ffv1_late_keyframes_fall_back_and_invalidate_all_windows(self):
        self.exercise(motion=True)

    def test_recursive_halves_keep_distinct_keys_and_warm_outputs(self):
        from tests.rendering.test_window_cache import RealRecovery
        from cutvoke.rendering.render import RenderError
        with tempfile.TemporaryDirectory() as tmp, patch('os.cpu_count',return_value=1):
            root=Path(tmp);project=RealRecovery().project(root)
            def export(target,expected):
                renderer=RenderService(gpu_effects=False)
                encode=renderer._run_ffmpeg
                restored=[];published=[]
                original_restore,original_publish=WindowCache.restore,WindowCache.publish
                def restore(cache,key,path):
                    restored.append((Path(path).name,key))
                    return original_restore(cache,key,path)
                def publish(cache,key,path):
                    published.append(key)
                    return original_publish(cache,key,path)
                def stall(*args,**kwargs):
                    if (kwargs.get('lossless') and Path(args[3]).name=='window-000000000.mkv'
                            and kwargs.get('output_duration')==8):
                        raise RenderError('injected stopped making progress')
                    return encode(*args,**kwargs)
                with patch.object(renderer,'_run_ffmpeg',side_effect=stall), \
                     patch.object(WindowCache,'restore',restore),patch.object(WindowCache,'publish',publish):
                    result=renderer.render(project,str(target))
                self.assertEqual(result['windowCount'],4)
                self.assertEqual(result['reusedWindowCount'],expected)
                zero_keys=[key for name,key in restored if name=='window-000000000.mkv']
                self.assertEqual(len(zero_keys),2)
                self.assertNotEqual(*zero_keys)
                self.assertNotIn(zero_keys[0],published)
            cold,warm=root/'cold.mp4',root/'warm.mp4'
            export(cold,0);export(warm,4)
            independent=root/'independent';independent.mkdir();reference=independent/'cold.mp4'
            export(reference,0)
            for options in (['-an','-pix_fmt','rgba','-f','rawvideo'],['-vn','-f','f32le','-acodec','pcm_f32le']):
                def decode(path):
                    return subprocess.run(['ffmpeg','-v','error','-i',str(path),*options,'pipe:1'],check=True,capture_output=True).stdout
                self.assertEqual(decode(warm),decode(reference))
                self.assertEqual(decode(cold),decode(reference))

    def test_source_changes_after_snapshot_never_restore_or_publish(self):
        from tests.rendering.test_window_cache import RealRecovery
        from cutvoke.rendering import window_cache
        with tempfile.TemporaryDirectory() as tmp, patch('os.cpu_count',return_value=1):
            root=Path(tmp);project=RealRecovery().project(root)
            def export(name,expected):
                result=RenderService(gpu_effects=False).render(project,str(root/name))
                self.assertEqual(result['reusedWindowCount'],expected)
            export('seed.mp4',0)
            original_factory=window_cache.recovery_session
            original_publish=WindowCache.publish
            publications=[]
            def mutate(*args,**kwargs):
                result=original_factory(*args,**kwargs)
                Image.new('RGB',(64,48),'blue').save(root/'source.png')
                return result
            def publish(cache,key,path):
                publications.append(key)
                return original_publish(cache,key,path)
            with patch.object(window_cache,'recovery_session',side_effect=mutate), \
                    patch.object(WindowCache,'publish',publish):
                export('raced.mp4',0)
            self.assertEqual(publications,[])
            export('valid.mp4',0)
            export('warm.mp4',3)
            for options in (['-an','-pix_fmt','rgba','-f','rawvideo'],['-vn','-f','f32le','-acodec','pcm_f32le']):
                def decode(name):
                    return subprocess.run(['ffmpeg','-v','error','-i',str(root/name),*options,'pipe:1'],check=True,capture_output=True).stdout
                self.assertEqual(decode('raced.mp4'),decode('valid.mp4'))
                self.assertEqual(decode('warm.mp4'),decode('valid.mp4'))

if __name__=='__main__':unittest.main()


