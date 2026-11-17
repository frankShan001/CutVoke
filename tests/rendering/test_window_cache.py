"""Recovery must skip work, not verification, audio mixing or source identity."""
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image
from cutvoke.domain.model import AssetReference, Clip, Track, Caption
from cutvoke.domain.keyframes import Keyframe
from cutvoke.domain.invariants import validate_project
from cutvoke.domain.rational import Rational
from cutvoke.editing.service import EditService
from cutvoke.rendering.render import RenderService, RenderError, RenderCancelled
from cutvoke.rendering.window_cache import WindowCache, recovery_session, simple_snapshot


def r(value):
    return Rational.from_float(value)


class CacheStorage(unittest.TestCase):
    def test_bounded_concurrent_atomic_blobs_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = WindowCache(root / 'cache')
            cache.MAX_BYTES = 64
            cache.MAX_ENTRIES = 3
            source = root / 'window'; source.write_bytes(b'x' * 20)
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda n: cache.publish(str(n), source), range(12)))
            with sqlite3.connect(cache.database) as db:
                count, size = db.execute('SELECT count(*),sum(length(data)) FROM windows').fetchone()
                self.assertLessEqual(count, 3); self.assertLessEqual(size, 64)
                key = db.execute('SELECT key FROM windows LIMIT 1').fetchone()[0]
            target = root / 'private'
            self.assertTrue(cache.restore(key, target))
            cache.publish('other', source)
            self.assertEqual(target.read_bytes(), b'x' * 20)
            with sqlite3.connect(cache.database) as db:
                db.execute('UPDATE windows SET data=?', (b'broken',))
            self.assertFalse(cache.restore('other', target))
            source.write_bytes(b'x' * 65)
            self.assertFalse(cache.publish('oversize', source))

    def test_malformed_sqlite_payload_types_are_misses(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = WindowCache(tmp)
            source = Path(tmp) / 'source'; source.write_bytes(b'valid media')
            self.assertTrue(cache.publish('key', source))
            for data, digest in [('bad text payload', 'abc'), (12, 'abc'), (b'bytes', 12)]:
                with self.subTest(data=data, digest=digest):
                    with sqlite3.connect(cache.database) as db:
                        db.execute('UPDATE windows SET data=?,digest=?', (data, digest))
                    self.assertFalse(cache.restore('key', Path(tmp) / 'private'))

    def test_malformed_eviction_metadata_cannot_stall_publication(self):
        # Isolate the regression: the old implementation loops forever rather
        # than raising, so a bounded child process must catch that failure.
        for ignore_delete in (False, True):
            with self.subTest(ignore_delete=ignore_delete), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cache = WindowCache(root / 'cache')
                source = root / 'source'
                source.write_bytes(b'encoded')
                self.assertTrue(cache.publish('initial', source))
                with sqlite3.connect(cache.database) as db:
                    db.execute('DELETE FROM windows')
                    db.executemany('INSERT INTO windows VALUES (NULL,?,?,?)',
                                   [(b'x', 'invalid', 0)] * cache.MAX_ENTRIES)
                    if ignore_delete:
                        db.execute('CREATE TRIGGER prevent_eviction BEFORE DELETE ON windows '
                                   'BEGIN SELECT RAISE(IGNORE); END')
                code = ('from cutvoke.rendering.window_cache import WindowCache; import sys; '
                        'print(WindowCache(sys.argv[1]).publish("new", sys.argv[2]))')
                completed = subprocess.run([sys.executable, '-c', code,
                                            str(cache.database.parent), str(source)],
                                           capture_output=True, text=True, check=True, timeout=5)
                self.assertEqual(completed.stdout.strip(), str(not ignore_delete))
                with sqlite3.connect(cache.database) as db:
                    self.assertLessEqual(db.execute('SELECT count(*) FROM windows').fetchone()[0],
                                         cache.MAX_ENTRIES)
                if not ignore_delete:
                    self.assertTrue(cache.restore('new', root / 'restored'))
                    self.assertEqual((root / 'restored').read_bytes(), b'encoded')

    def test_unavailable_or_invalid_database_is_disposable(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = WindowCache(tmp)
            cache.database.write_bytes(b'not sqlite')
            source = Path(tmp) / 'source'; source.write_bytes(b'encoded')
            self.assertFalse(cache.publish('a', source))
            self.assertFalse(cache.restore('a', Path(tmp) / 'dest'))

    def test_symlink_database_or_directory_never_changes_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            other = root / 'user-data'; other.mkdir()
            original = other / 'windows.sqlite3'; original.write_bytes(b'user data')
            source = root / 'encoded'; source.write_bytes(b'video')
            linked_dir = root / 'linked'
            try:
                linked_dir.symlink_to(other, target_is_directory=True)
            except OSError:
                self.skipTest('symlinks not available for this account')
            self.assertFalse(WindowCache(linked_dir).publish('x', source))
            cache = WindowCache(root / 'normal'); cache.database.parent.mkdir()
            cache.database.symlink_to(original)
            self.assertFalse(cache.publish('x', source))
            self.assertFalse(cache.restore('x', root / 'target'))
            self.assertEqual(original.read_bytes(), b'user data')


class RealRecovery(unittest.TestCase):
    def project(self, root):
        image = root / 'source.png'
        Image.new('RGB', (64, 48), 'red').save(image)
        audio = root / 'tone.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'sine=frequency=440:sample_rate=48000:duration=19', '-y', str(audio)], check=True)
        project = EditService().create_project('recovery', width=64, height=48)
        project.sequence.fps = Rational.of(12)
        clips = [Clip(str(n), AssetReference('image', str(image)), r(n*.75), r((n+1)*.75), r(0))
                 for n in range(26)]
        tone = Clip('tone', AssetReference('audio', str(audio)), r(0), r(19.5), r(0))
        tone.fade_in = r(1); tone.fade_out = r(1)
        project.sequence.tracks = [Track('v', 'video', clips), Track('a', 'audio', [tone])]
        return project

    def test_failure_resume_matches_cold_frames_audio_and_still_verifies(self):
        class FailSecond(RenderService):
            def _run_ffmpeg(self, *args, **kwargs):
                if kwargs.get('lossless') and Path(args[3]).name == 'window-000000096.mkv':
                    raise RenderError('injected interruption')
                return super()._run_ffmpeg(*args, **kwargs)
        with tempfile.TemporaryDirectory() as tmp, patch('os.cpu_count', return_value=1):
            root = Path(tmp); project = self.project(root)
            target = root / 'resumed.mp4'
            with self.assertRaisesRegex(RenderError, 'injected'):
                FailSecond(gpu_effects=False).render(project, str(target))
            self.assertFalse(target.exists())
            self.assertFalse(list(root.glob('cutvoke-export-windows-*')))
            renderer = RenderService(gpu_effects=False)
            with patch.object(renderer, '_render_continuous_audio', wraps=renderer._render_continuous_audio) as audio, \
                    patch.object(renderer, '_verify_window_seams', wraps=renderer._verify_window_seams) as seams:
                resumed = renderer.render(project, str(target))
                self.assertEqual(audio.call_count, 1); self.assertEqual(seams.call_count, 1)
            # Executor may finish already-queued windows after one worker fails.
            self.assertEqual(resumed['reusedWindowCount'], 2)
            cold_dir = root / 'cold'; cold_dir.mkdir()
            cold = cold_dir / 'cold.mp4'
            result = RenderService(gpu_effects=False).render(project, str(cold))
            self.assertEqual(result['reusedWindowCount'], 0)
            for options in (['-an', '-f', 'rawvideo', '-pix_fmt', 'rgb24'],
                            ['-vn', '-f', 'f32le', '-acodec', 'pcm_f32le']):
                def decode(path):
                    return subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), *options, 'pipe:1'],
                                          check=True, capture_output=True).stdout
                self.assertEqual(decode(target), decode(cold))
            self.assertIsNotNone(resumed['audioVerification'])
            # A new renderer instance simulates process restart; all windows hit.
            warm = RenderService(gpu_effects=False).render(project, str(root / 'warm.mp4'))
            self.assertEqual(warm['reusedWindowCount'], 3)
            Image.new('RGB', (64, 48), 'blue').save(root / 'source.png')
            changed = renderer.render(project, str(root / 'changed.mp4'))
            self.assertEqual(changed['reusedWindowCount'], 0)

    def test_snapshot_edit_invalidates_then_warm_matches_independent_cold(self):
        """A late keyframe edit invalidates all windows, not just its own window."""
        with tempfile.TemporaryDirectory() as tmp, patch('os.cpu_count', return_value=1):
            root = Path(tmp)
            project = self.project(root)
            changed = copy.deepcopy(project)
            late_clip = changed.sequence.tracks[0].clips[-1]
            self.assertGreaterEqual(late_clip.timeline_start.to_fraction(), 16)
            late_clip.keyframes['opacity'] = [Keyframe('late-opacity', r(0), .5)]
            for document in (project, changed):
                validation = validate_project(document)
                self.assertTrue(validation.structurally_valid, validation.to_dict())
                self.assertTrue(validation.renderable, validation.to_dict())
                self.assertIsNotNone(simple_snapshot(document))

            def export(document, path, expected_reused):
                renderer = RenderService(gpu_effects=False)
                with patch.object(renderer, '_render_in_windows', wraps=renderer._render_in_windows) as windows, \
                        patch.object(renderer, '_render_continuous_audio', wraps=renderer._render_continuous_audio) as audio, \
                        patch.object(renderer, '_verify_window_seams', wraps=renderer._verify_window_seams) as seams:
                    result = renderer.render(document, str(path))
                self.assertEqual(windows.call_count, 1)
                self.assertEqual(audio.call_count, 1)
                self.assertEqual(seams.call_count, 1)
                self.assertEqual(result['renderStrategy'], 'lossless-windows')
                self.assertEqual(result['windowCount'], 3)
                self.assertEqual(result['reusedWindowCount'], expected_reused)
                self.assertIsNotNone(result['audioVerification'])

            baseline, edited, warm = (root / name for name in ('baseline.mp4', 'edited.mp4', 'warm.mp4'))
            cold_dir = root / 'independent-cold'
            cold_dir.mkdir()
            cold = cold_dir / 'cold.mp4'
            export(project, baseline, 0)
            # The changed node lies outside the first two eight-second windows.
            # Full-snapshot recovery must not reuse those windows either.
            export(changed, edited, 0)
            export(changed, warm, 3)
            export(changed, cold, 0)
            for kind, options in (
                    ('video', ['-an', '-f', 'rawvideo', '-pix_fmt', 'rgb24']),
                    ('audio', ['-vn', '-f', 'f32le', '-acodec', 'pcm_f32le'])):
                with self.subTest(kind=kind):
                    def decode(path):
                        return subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), *options, 'pipe:1'],
                                              check=True, capture_output=True).stdout
                    reference = decode(cold)
                    self.assertTrue(reference)
                    self.assertEqual(decode(edited), reference)
                    self.assertEqual(decode(warm), reference)
                    if kind == 'video':
                        self.assertNotEqual(decode(baseline), reference)
                    else:
                        self.assertEqual(decode(baseline), reference)
            self.assertFalse(list(root.glob('cutvoke-export-windows-*')))
            self.assertFalse(list(cold_dir.glob('cutvoke-export-windows-*')))

    def test_identity_fallback_cancellation_and_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = self.project(root)
            snapshot = simple_snapshot(project)
            first = recovery_session(root, snapshot, 'ffmpeg', ['options'])
            self.assertIsNotNone(first)
            from cutvoke.rendering.window_cache import digest_file
            def changing_implementation(path, cancel=None):
                return None if Path(path).name == 'render.py' else digest_file(path, cancel)
            with patch('cutvoke.rendering.window_cache.digest_file', side_effect=changing_implementation):
                self.assertIsNone(recovery_session(root, snapshot, 'ffmpeg', ['options']))
            def changed_dependency(path, cancel=None):
                result = digest_file(path, cancel)
                return ('changed-domain', result[1]) if Path(path).name == 'keyframes.py' else result
            with patch('cutvoke.rendering.window_cache.digest_file', side_effect=changed_dependency):
                dependency = recovery_session(root, snapshot, 'ffmpeg', ['options'])
            self.assertNotEqual(first.key(0, 8), dependency.key(0, 8))
            changed = copy.deepcopy(project); changed.sequence.width += 2
            second = recovery_session(root, simple_snapshot(changed), 'ffmpeg', ['options'])
            self.assertNotEqual(first.key(0, 8), second.key(0, 8))
            options = recovery_session(root, snapshot, 'ffmpeg', ['different-alpha'])
            self.assertNotEqual(first.key(0, 8), options.key(0, 8))
            cancelled = threading.Event(); cancelled.set()
            self.assertIsNone(recovery_session(root, snapshot, 'ffmpeg', [], cancelled))
            with patch('cutvoke.rendering.window_cache.ABI', 'future-abi'):
                future = recovery_session(root, snapshot, 'ffmpeg', ['options'])
            self.assertNotEqual(first.key(0, 8), future.key(0, 8))
            with patch('cutvoke.rendering.window_cache.subprocess.run') as run:
                run.return_value.stdout = b'changed FFmpeg build'
                future = recovery_session(root, snapshot, 'ffmpeg', ['options'])
            self.assertNotEqual(first.key(0, 8), future.key(0, 8))
            effect = copy.deepcopy(project)
            effect.sequence.tracks[0].clips[0].effects = [{'effectId': 'external.effect'}]
            self.assertIsNone(simple_snapshot(effect))
            project.sequence.captions = [Caption('c', 'no cached fonts', r(0), r(1))]
            self.assertIsNone(simple_snapshot(project))
            (root / 'source.png').write_bytes(b'changed')
            self.assertFalse(first.unchanged())

    def test_cancel_retains_only_completed_windows_then_resumes(self):
        cancel = threading.Event()
        class CancelSecond(RenderService):
            def _run_ffmpeg(self, *args, **kwargs):
                if kwargs.get('lossless') and Path(args[3]).name == 'window-000000096.mkv':
                    cancel.set()
                    raise RenderCancelled('injected cancellation')
                return super()._run_ffmpeg(*args, **kwargs)
        with tempfile.TemporaryDirectory() as tmp, patch('os.cpu_count', return_value=1):
            root = Path(tmp); project = self.project(root)
            target = root / 'cancelled.mp4'
            with self.assertRaises(RenderCancelled):
                CancelSecond(gpu_effects=False).render(project, str(target), cancel_event=cancel)
            self.assertFalse(target.exists())
            self.assertFalse(list(root.glob('cutvoke-export-windows-*')))
            result = RenderService(gpu_effects=False).render(project, str(target))
            self.assertEqual(result['reusedWindowCount'], 1)
            cancel.clear()
            class CancelHit(RenderService):
                def _verify(self, path, *args, **kwargs):
                    probe = super()._verify(path, *args, **kwargs)
                    if Path(path).name.startswith('window-'):
                        cancel.set()
                    return probe
            hit_renderer = CancelHit(gpu_effects=False)
            with patch.object(hit_renderer, '_run_ffmpeg', wraps=hit_renderer._run_ffmpeg) as encode:
                with self.assertRaises(RenderCancelled):
                    hit_renderer.render(project, str(root / 'cancel-hit.mp4'), cancel_event=cancel)
                self.assertFalse(any(call.kwargs.get('lossless') for call in encode.call_args_list))
            self.assertFalse((root / 'cancel-hit.mp4').exists())

    def test_fractional_range_and_cache_corruption_regenerates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = self.project(root)
            project.sequence.fps = Rational.of(30000, 1001)
            video = root / 'source-video.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=64x48:rate=30000/1001:duration=2',
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-y', str(video)], check=True)
            for clip in project.sequence.tracks[0].clips:
                clip.asset_ref.source_path = str(video)
                clip.source_start = r(.2)
            renderer = RenderService(gpu_effects=False)
            options = dict(output_start=.5, output_duration=17)
            cold = renderer.render(project, str(root / 'cold.mp4'), **options)
            warm = renderer.render(project, str(root / 'warm.mp4'), **options)
            self.assertEqual(cold['reusedWindowCount'], 0)
            self.assertEqual(warm['reusedWindowCount'], warm['windowCount'])
            def rgb(path):
                return subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-an',
                                       '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'],
                                      check=True, capture_output=True).stdout
            self.assertEqual(rgb(root / 'cold.mp4'), rgb(root / 'warm.mp4'))
            cache = WindowCache(root / '.cutvoke-render-cache')
            with sqlite3.connect(cache.database) as db:
                db.execute('UPDATE windows SET data=?', (b'truncated',))
            repaired = renderer.render(project, str(root / 'repaired.mp4'), **options)
            self.assertEqual(repaired['reusedWindowCount'], 0)
            self.assertEqual(repaired['videoVerification'], warm['videoVerification'])

    def test_concurrent_exports_own_their_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = self.project(root)
            def render(n):
                return RenderService(gpu_effects=False).render(project, str(root / f'{n}.mp4'))
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(render, range(2)))
            self.assertTrue(all(result['audioVerification'] for result in results))
            self.assertTrue((root / '0.mp4').exists() and (root / '1.mp4').exists())
            self.assertFalse(list(root.glob('cutvoke-export-windows-*')))
