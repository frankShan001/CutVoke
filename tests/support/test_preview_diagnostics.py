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
