"""Admission API only; production renderer remains on full-snapshot recovery."""
from dataclasses import FrozenInstanceError, replace
import unittest
from concurrent.futures import ThreadPoolExecutor
from cutvoke.rendering.compiled_window_identity import CompiledInput, CompiledWindowIdentity
from cutvoke.rendering.window_cache import RecoverySession


def plan():
    return CompiledWindowIdentity('graph', (CompiledInput('/source', 'a'*64, ('-ss', '2')),),
        0., (2., 4.), (30000, 1001), (64, 48), 'format=rgb24', 'bt709', False, 2,
        ('lossless', 'output_start=2', 'duration=2'), ('CPU', 'outer_range=.5,17'))


class CompiledIdentityTests(unittest.TestCase):
    def session(self, enabled=True):
        return RecoverySession(None, ['original'], [],
            compiled_namespace='verified-tools-and-implementation' if enabled else None,
            source_hashes=(('/source', 'a'*64),))

    def test_deep_immutable_and_reject_mutable_inputs(self):
        original = plan()
        with self.assertRaises(FrozenInstanceError):
            original.graph = 'changed'
        for changes in ({'inputs': list(original.inputs)}, {'window': [2., 4.]},
                        {'context': ['CPU']}, {'output_arguments': ['lossless']}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(original, **changes)
        with self.assertRaises(ValueError):
            CompiledInput('/source', 'a'*64, ['-ss', '2'])

    def test_namespace_and_every_dependency_invalidate(self):
        original = plan(); session = self.session()
        key = session.key(2., 2., compiled=original)
        self.assertNotEqual(key, session.key(2., 2.))
        variants = [dict(graph='other'), dict(inputs=(CompiledInput('/source','a'*64,('-c:v','libvpx-vp9')),)),
            dict(origin=1.), dict(frame_rate=(24,1)), dict(size=(128,96)),
            dict(working_filter='format=rgba'), dict(working_color='bt2020'), dict(alpha=True),
            dict(filter_threads=4), dict(output_arguments=('different-trim',)), dict(context=('different-range',))]
        for changes in variants:
            with self.subTest(changes=changes):
                self.assertNotEqual(key, session.key(2.,2.,compiled=replace(original,**changes)))
        self.assertNotEqual(original.key('one-tool-build'), original.key('another-tool-build'))

    def test_unknown_inputs_window_or_disabled_gate_use_exact_fallback(self):
        original = plan(); session = self.session(); fallback = session.key(2.,2.)
        self.assertEqual(self.session(False).key(2.,2.,compiled=original), fallback)
        for namespace in ('', [], ['not-a-namespace'], 1, True):
            invalid = RecoverySession(None, ['original'], [], compiled_namespace=namespace,
                                      source_hashes=(('/source', 'a'*64),))
            self.assertEqual(invalid.key(2.,2.,compiled=original), fallback)
        for candidate in (object(), replace(original,inputs=(CompiledInput('/unknown','a'*64,()),)),
                          replace(original,inputs=(CompiledInput('/source','b'*64,()),)),
                          replace(original,window=(3.,5.))):
            self.assertEqual(session.key(2.,2.,compiled=candidate), fallback)
        # No remembered key survives a previous supported call.
        self.assertNotEqual(session.key(2.,2.,compiled=original), fallback)
        self.assertEqual(session.key(2.,2.,compiled=None), fallback)

    def test_parallel_and_recursive_windows_have_no_shared_pending_key(self):
        session = self.session(); parent = plan()
        children = [replace(parent,window=(2.,3.)),replace(parent,window=(3.,4.))]
        def compute(p):
            return session.key(p.window[0],p.window[1]-p.window[0],compiled=p)
        with ThreadPoolExecutor(max_workers=2) as pool:
            actual=list(pool.map(compute, [parent,*children]*20))
        self.assertEqual(len(set(actual)),3)
        self.assertEqual(actual, [compute(parent),*(compute(p) for p in children)]*20)



class AdmissionTests(unittest.TestCase):
    def test_context_unknown_input_and_decoder_fail_closed(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from cutvoke.domain.rational import Rational
        from cutvoke.rendering.compiled_window_identity import admit_window
        from cutvoke.rendering.render import RenderCancelled, RenderError
        seq=SimpleNamespace(tracks=[],fps=Rational.of(24),width=64,height=48)
        ctx=SimpleNamespace(alpha=False,input_seeks={},working_color='bt709')
        renderer=SimpleNamespace(_render_ctx=ctx,ffmpeg='ffmpeg',
            _working_video_filter=lambda:'format=rgb24',_sequence_duration=lambda seq:6.,
            _video_input_arguments=lambda path:[])
        session=RecoverySession(None,[],[],compiled_namespace='tools',
            source_hashes=(('/source.png','a'*64),('/source.mkv','a'*64),('/source.webm','a'*64)))
        arguments=dict(cursor=2.,duration=2.,origin=0.,overlay_position=(0,0),
                       alpha=False,quality='high',chunk_audio=False,caption_cwd=None)
        def admit(inputs=('/source.png',),**changes):
            return admit_window(renderer,session,seq,'graph',list(inputs),**(arguments|changes))
        with patch('cutvoke.rendering.render._setpts_clears_frame_rate',return_value=False):
            self.assertIsNotNone(admit())
            for changes in ({'alpha':True},{'chunk_audio':True},{'caption_cwd':'prepared'}):
                self.assertIsNone(admit(**changes))
            self.assertIsNone(admit(('/unknown',)))
            self.assertIsNone(admit(()))
            for name,value in [('gpu',object()),('bounded_origin',0.),('clip_clocks',{'c':1}),('preview_scale',.5)]:
                setattr(ctx,name,value)
                self.assertIsNone(admit())
                delattr(ctx,name)
            from cutvoke.domain.model import Track, Clip, AssetReference
            from cutvoke.domain.keyframes import Keyframe
            clip=Clip('c',AssetReference('a','/source.mkv'),Rational.of(0),Rational.of(6),Rational.of(0))
            clip.keyframes['opacity']=[Keyframe('k',Rational.of(0),.5)]
            seq.tracks=[Track('v','video',[clip])]
            self.assertIsNone(admit(('/source.mkv',)))
            seq.tracks=[]
            keys = set()
            for decoder in ('libvpx', 'libvpx-vp9'):
                with patch.object(renderer, '_video_input_arguments', return_value=['-c:v', decoder]):
                    self.assertIsNone(admit(('/source.mkv',)))
                    plan = admit(('/source.webm',))
                    self.assertEqual(plan.inputs[0].arguments, ('-threads', '2', '-c:v', decoder))
                    keys.add(plan.key('tools'))
            self.assertEqual(len(keys), 2)
            for override in (['-c:v', 'unknown'], ['-c:v', 'libvpx', '-unknown']):
                with patch.object(renderer, '_video_input_arguments', return_value=override):
                    self.assertIsNone(admit(('/source.webm',)))
            for error in (RuntimeError('probe unavailable'),RenderError('probe unavailable')):
                with patch.object(renderer,'_video_input_arguments',side_effect=error):
                    self.assertIsNone(admit(('/source.mkv',)))
            with patch.object(renderer,'_video_input_arguments',side_effect=RenderCancelled('cancel')):
                with self.assertRaises(RenderCancelled):admit(('/source.mkv',))

    def test_tool_failure_preserves_original_snapshot_and_symlink_fence(self):
        import os
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from cutvoke.rendering.window_cache import compiled_tool_identity, recovery_session
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch('cutvoke.rendering.window_cache.compiled_tool_identity',return_value=None):
                fallback=recovery_session(root,({},[]),'ffmpeg',[],ffprobe='missing')
            self.assertIsNotNone(fallback)
            self.assertFalse(fallback.compiled_enabled)
            legacy=recovery_session(root,({},[]),'ffmpeg',[])
            self.assertEqual(fallback.key(0,2),legacy.key(0,2))
            a,b,link=root/'a',root/'b',root/'tool'
            a.write_bytes(b'first');b.write_bytes(b'second')
            try:link.symlink_to(a)
            except OSError:self.skipTest('symlinks unavailable')
            with patch('cutvoke.rendering.window_cache.shutil.which',return_value=str(link)), \
                    patch('cutvoke.rendering.window_cache.subprocess.run',return_value=SimpleNamespace(stdout=b'version')):
                tools,sources=compiled_tool_identity('ffmpeg','ffprobe')
            self.assertEqual(sources[0][0],str(link))
            session=RecoverySession(None,[],[],compiled_namespace='tools',tool_sources=sources)
            self.assertTrue(session.unchanged())
            link.unlink();link.symlink_to(b)
            self.assertFalse(session.unchanged())
            command_session=RecoverySession(None,[],[],tool_commands=(('ffmpeg',str(a)),))
            with patch('cutvoke.rendering.window_cache.shutil.which',return_value=str(b)):
                self.assertFalse(command_session.unchanged())
            with patch('cutvoke.rendering.window_cache.shutil.which',return_value=None):
                self.assertIsNone(compiled_tool_identity('missing','missing'))

    def test_tool_capture_errors_and_mid_capture_changes_disable_compiled_identity(self):
        import subprocess
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from cutvoke.rendering.window_cache import compiled_tool_identity
        with tempfile.TemporaryDirectory() as temp:
            tool=Path(temp)/'fake-tool';tool.write_bytes(b'original')
            with patch('cutvoke.rendering.window_cache.shutil.which',return_value=str(tool)):
                for error in (OSError('unavailable'),subprocess.TimeoutExpired('tool',10),
                              subprocess.CalledProcessError(1,'tool')):
                    with self.subTest(error=type(error).__name__), \
                         patch('cutvoke.rendering.window_cache.subprocess.run',side_effect=error):
                        self.assertIsNone(compiled_tool_identity('ffmpeg','ffprobe'))
                def mutate(*args,**kwargs):
                    tool.write_bytes(b'different bytes after hashing')
                    return SimpleNamespace(stdout=b'version')
                with patch('cutvoke.rendering.window_cache.subprocess.run',side_effect=mutate):
                    self.assertIsNone(compiled_tool_identity('ffmpeg','ffprobe'))

if __name__ == '__main__': unittest.main()
