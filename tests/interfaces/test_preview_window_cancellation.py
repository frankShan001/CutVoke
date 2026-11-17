"""Expected window cancellation is distinct from failed rendering on the wire."""
import pytest
from cutvoke.editing.service import EditService
from cutvoke.interfaces.httpapi import HttpApi
from cutvoke.rendering.render import RenderService, RenderCancelled, RenderError


@pytest.mark.parametrize("error,status,code", [
    (RenderCancelled("superseded by edit"), 409, "PREVIEW_CANCELLED"),
    (RenderCancelled("speculative request cancelled"), 409, "PREVIEW_CANCELLED"),
    (RenderError("decoder failed"), 422, "PREVIEW_FAILED"),
    # Message text alone must never suppress a real failure.
    (RenderError("preview superseded by a newer edit"), 422, "PREVIEW_FAILED"),
])
def test_window_cancellation_has_structured_response(tmp_path, error, status, code):
    service = EditService()
    service.create_project("cancelled-window")
    api = HttpApi(service, RenderService(), media_dir=str(tmp_path))
    def render(*args, **kwargs):
        raise error
    api._preview_window_file = render
    try:
        actual, body = api.handle("GET", "/api/v1/projects/cancelled-window/preview-window", {})
        assert actual == status
        assert body == {"error": {"code": code, "message": str(error)}}
    finally:
        api.close()


def test_real_background_export_yield_is_cancellation_not_unbound_local(tmp_path):
    from tests.interfaces.test_preview_lookahead import setup
    from cutvoke.rendering.preview_prepare import PreviewPreempted
    api, project, renderer = setup(tmp_path)
    class BusyExport:
        def reload_status(self):
            return {"busy": True}
    previous = api.service._export_queue
    api.service._export_queue = BusyExport()
    try:
        with pytest.raises(PreviewPreempted, match="yields to export"):
            api._preview_window_file(project, 0, priority=10)
        assert renderer.calls == 0
    finally:
        api.service._export_queue = previous
        api.close()


def test_real_stale_snapshot_is_cancelled_before_encoding(tmp_path):
    import copy
    from tests.interfaces.test_preview_lookahead import setup
    api, project, renderer = setup(tmp_path)
    stale = copy.deepcopy(project)
    project.sequence.background_color = "#123456"
    try:
        with pytest.raises(RenderCancelled, match="superseded"):
            api._preview_window_file(stale, 0)
        assert renderer.calls == 0
    finally:
        api.close()
