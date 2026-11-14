import io
import json
from contextlib import contextmanager
import pytest
from preview_diagnostics import install_preview_diagnostics


class Scheduler:
    def __init__(self):
        self.error = None
        self.released = False
        self.suppress = False

    @contextmanager
    def slot(self, *args, **kwargs):
        if self.error:
            raise self.error
        try:
            yield 'token'
        except RuntimeError:
            if not self.suppress:
                raise
        finally:
            self.released = True


class Render:
    def _compile(self, *args, **kwargs):
        return 'compiled'

    def extract_frame(self, *args, **kwargs):
        return self._compile(*args, **kwargs)


class Api:
    def __init__(self):
        self.render = Render()
        self._frame_scheduler = Scheduler()

    def _require_current_preview(self, *args, **kwargs):
        pass

    def _preview_frame_file(self, *args, **kwargs):
        self._require_current_preview(*args, **kwargs)
        with self._frame_scheduler.slot(*args, **kwargs) as token:
            assert token == 'token'
            return self.render.extract_frame(*args, **kwargs)


def records(out):
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_success_private_nested_and_restored():
    api, out = Api(), io.StringIO()
    before = [dict(vars(o)) for o in (api, api.render, api._frame_scheduler)]
    with install_preview_diagnostics(api, out, enabled=True):
        assert api._preview_frame_file('/private/project', secret='secret') == 'compiled'
    before[2]["released"] = True
    assert before == [dict(vars(o)) for o in (api, api.render, api._frame_scheduler)]
    assert '_preview_frame_file' not in vars(api)
    assert 'slot' not in vars(api._frame_scheduler)
    assert '_compile' not in vars(api.render)
    data = records(out)
    assert {r['stage'] for r in data} == {'preview', 'current', 'slot', 'extract', 'compile'}
    assert not any(x in out.getvalue() for x in ('private', 'secret', 'compiled', 'token'))
    assert all(set(r) == {'stage','event','elapsed_s','t_s','thread_id','id','parent_id'} for r in data)
    assert all(r['elapsed_s'] >= 0 for r in data)
    starts = {r['stage']: r for r in data if r['event'] == 'start'}
    assert starts['compile']['parent_id'] == starts['extract']['id']
    assert starts['extract']['parent_id'] == starts['slot']['id']
    assert [r['event'] for r in data if r['stage']=='slot'] == ['start','waiting','acquired','released','end']


@pytest.mark.parametrize('error', [RuntimeError('private failure'), KeyboardInterrupt('cancel')])
def test_exception_identity_and_flush(error):
    api, out = Api(), io.StringIO()
    def fail(*args, **kwargs):
        assert records(out)[-1]['event'] == 'start'
        raise error
    api.render._compile = fail
    with install_preview_diagnostics(api, out, enabled=True):
        with pytest.raises(type(error)) as caught:
            api._preview_frame_file('private')
        assert caught.value is error
    assert api.render._compile is fail
    assert api._frame_scheduler.released
    assert 'private' not in out.getvalue()
    assert records(out)[-1]['event'] == 'error'


def test_slot_acquisition_failure():
    api, out = Api(), io.StringIO()
    error = RuntimeError('private')
    api._frame_scheduler.error = error
    with install_preview_diagnostics(api, out, enabled=True):
        with pytest.raises(RuntimeError) as caught:
            with api._frame_scheduler.slot():
                pytest.fail('must not acquire')
        assert caught.value is error
    assert [r['event'] for r in records(out)] == ['start','waiting','error']


def test_slot_suppression_semantics():
    api, out = Api(), io.StringIO()
    api._frame_scheduler.suppress = True
    with install_preview_diagnostics(api, out, enabled=True):
        with api._frame_scheduler.slot():
            raise RuntimeError('secret')
    assert records(out)[-1]['event'] == 'end'


def test_disabled_limit_and_io_failure():
    api, out = Api(), io.StringIO()
    with install_preview_diagnostics(api, out):
        assert '_preview_frame_file' not in vars(api)
        api._preview_frame_file()
    assert out.getvalue() == ''
    with install_preview_diagnostics(api, out, enabled=True, max_records=2):
        api._preview_frame_file()
    assert len(records(out)) == 3
    assert records(out)[-1]['event'] == 'truncated'
    class Broken:
        def write(self, text):
            raise OSError('private')
    with install_preview_diagnostics(api, Broken(), enabled=True):
        assert api._preview_frame_file() == 'compiled'


def test_start_is_flushed_before_stage_body():
    class Flushed(io.StringIO):
        def flush(self):
            self.flushed = self.getvalue()
    api, out = Api(), Flushed()
    def check(*args, **kwargs):
        assert json.loads(out.flushed.splitlines()[-1])['event'] == 'start'
        return 123
    api.render._compile = check
    with install_preview_diagnostics(api, out, enabled=True):
        assert api.render._compile() == 123


class CatalogApi(Api):
    def __init__(self):
        super().__init__()
        self.child = Api()
        self.child.render = self.render

    def project_api(self, project_id):
        return self.child


def test_child_api_shared_renderer_and_restore():
    api, out = CatalogApi(), io.StringIO()
    objects = (api, api.child, api.render, api._frame_scheduler, api.child._frame_scheduler)
    before = [dict(vars(obj)) for obj in objects]
    with install_preview_diagnostics(api, out, enabled=True):
        renderer_wrapper = api.render.extract_frame
        child = api.project_api('private-id')
        assert child is api.child
        assert api.project_api('another-private-id') is child
        assert api.render.extract_frame is renderer_wrapper
        assert child._preview_frame_file('secret') == 'compiled'
    before[-1]['released'] = True
    assert before == [dict(vars(obj)) for obj in objects]
    starts = [r for r in records(out) if r['event'] == 'start']
    assert [r['stage'] for r in starts] == ['preview','current','slot','extract','compile']
    assert all(r['t_s'] >= 0 for r in records(out))
    assert [r['t_s'] for r in records(out)] == sorted(r['t_s'] for r in records(out))
    assert 'private' not in out.getvalue() and 'secret' not in out.getvalue()


def test_concurrent_child_install_deduplicates():
    from concurrent.futures import ThreadPoolExecutor
    import threading
    api, out = CatalogApi(), io.StringIO()
    barrier = threading.Barrier(8)
    def request(index):
        barrier.wait(timeout=5)
        child = api.project_api('private-' + str(index))
        return child._preview_frame_file('secret')
    with install_preview_diagnostics(api, out, enabled=True):
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert list(pool.map(request, range(8))) == ['compiled'] * 8
    starts = [r for r in records(out) if r['event'] == 'start']
    for stage in ('preview','current','slot','extract','compile'):
        assert sum(r['stage'] == stage for r in starts) == 8
    assert len({r['id'] for r in starts}) == 40
    by_id = {r['id']: r for r in starts}
    for row in starts:
        if row['parent_id'] is not None:
            assert by_id[row['parent_id']]['thread_id'] == row['thread_id']
    for obj, name in ((api,'project_api'), (api.child,'_preview_frame_file'),
                      (api.child._frame_scheduler,'slot'), (api.render,'extract_frame')):
        assert name not in vars(obj)
    assert 'private' not in out.getvalue() and 'secret' not in out.getvalue()


def compile_fixture(monkeypatch):
    import cutvoke.rendering.render as rendering
    import cutvoke.rendering.caption_render as caption
    import fontTools.varLib.instancer as instancer
    from functools import lru_cache
    calls = []
    @lru_cache(maxsize=16)
    def digest(*args):
        calls.append('digest')
        return 1
    monkeypatch.setattr(caption, '_font_source_digest', digest)
    monkeypatch.setattr(instancer, 'instantiateVariableFont', lambda *a, **k: 2)
    def font(*args, **kwargs):
        from fontTools.varLib.instancer import instantiateVariableFont
        return caption._font_source_digest('secret-path') + instantiateVariableFont('secret-font')
    monkeypatch.setattr(caption, '_caption_font_instance', font)
    monkeypatch.setattr(caption, 'build_ass', lambda *a, **k: 4)
    def prepare(*args, **kwargs):
        return caption._caption_font_instance() + caption.build_ass()
    # Simulate render.py's already-imported function alias.
    monkeypatch.setattr(caption, 'prepare_caption_render', prepare)
    monkeypatch.setattr(rendering, 'prepare_caption_render', prepare)
    api = Api()
    api.render.probe_media = lambda *a: 5
    api.render._build_video_track = lambda *a: 6
    def compile(*args, **kwargs):
        return api.render.probe_media('secret') + api.render._build_video_track() + rendering.prepare_caption_render()
    api.render._compile = compile
    return api, rendering, caption, instancer, digest, calls


def test_compile_substages_alias_cache_scope_restore(monkeypatch):
    api, rendering, caption, instancer, digest, calls = compile_fixture(monkeypatch)
    targets = [(rendering,'prepare_caption_render'), (caption,'_caption_font_instance'),
               (caption,'_font_source_digest'), (caption,'build_ass'),
               (instancer,'instantiateVariableFont'), (api.render,'probe_media'),
               (api.render,'_build_video_track')]
    before = [getattr(obj,name) for obj,name in targets]
    out = io.StringIO()
    with install_preview_diagnostics(api, out, enabled=True, compile_stages=True):
        assert rendering.prepare_caption_render() == 7
        assert out.getvalue() == ''  # Outside compile, even on instrumented thread.
        assert api.render._compile() == 18
        assert api.render._compile() == 18
    assert all(getattr(obj,name) is old for (obj,name),old in zip(targets,before))
    assert calls == ['digest']  # The original cache was retained, not cleared.
    assert digest.cache_info().hits == 2
    expected = {'compile','compile_probe','compile_video','caption_prepare','caption_font',
                'font_digest','font_instantiate','caption_ass'}
    assert {r['stage'] for r in records(out)} == expected
    assert 'secret' not in out.getvalue()


def test_compile_modules_concurrent_exclusive_exception(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    api, rendering, caption, instancer, digest, calls = compile_fixture(monkeypatch)
    out = io.StringIO()
    barrier = threading.Barrier(4)
    def request(_):
        barrier.wait(timeout=5)
        return api.render._compile()
    original = rendering.prepare_caption_render
    with install_preview_diagnostics(api, out, enabled=True, compile_stages=True):
        with pytest.raises(RuntimeError, match='already installed'):
            with install_preview_diagnostics(Api(), io.StringIO(), enabled=True, compile_stages=True):
                pass
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(request, range(4))) == [18]*4
    starts = [r for r in records(out) if r['event']=='start']
    parents = {r['id']:r for r in starts}
    for row in starts:
        if row['parent_id'] is not None:
            assert parents[row['parent_id']]['thread_id'] == row['thread_id']
    assert sum(r['stage']=='caption_prepare' for r in starts) == 4
    assert rendering.prepare_caption_render is original
    error = RuntimeError('private error')
    def fail(*a, **k):
        raise error
    monkeypatch.setattr(rendering, 'prepare_caption_render', fail)
    with install_preview_diagnostics(api, out, enabled=True, compile_stages=True):
        with pytest.raises(RuntimeError) as caught:
            api.render._compile()
        assert caught.value is error
    assert rendering.prepare_caption_render is fail
    assert 'private error' not in out.getvalue()


def test_partial_install_failure_restores_and_unlocks(monkeypatch):
    import preview_diagnostics as diagnostics
    api, rendering, caption, instancer, digest, calls = compile_fixture(monkeypatch)
    original = rendering.prepare_caption_render
    real_import = diagnostics.importlib.import_module
    def broken_import(name):
        if name == 'cutvoke.rendering.caption_render':
            raise ImportError('private import failure')
        return real_import(name)
    with monkeypatch.context() as patch:
        patch.setattr(diagnostics.importlib, 'import_module', broken_import)
        with pytest.raises(ImportError):
            with install_preview_diagnostics(api, io.StringIO(), enabled=True, compile_stages=True):
                pass
    assert rendering.prepare_caption_render is original
    with install_preview_diagnostics(api, io.StringIO(), enabled=True, compile_stages=True):
        assert api.render._compile() == 18
