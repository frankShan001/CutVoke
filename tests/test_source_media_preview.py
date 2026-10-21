"""Project-source previews never grant access to unreferenced local files."""
from pathlib import Path

from cutvoke.core.httpapi import HttpApi
from cutvoke.core.model import AssetReference, Clip, Track, Sequence
from cutvoke.core.rational import Rational as R
from cutvoke.core.service import EditService


def test_project_source_preview_is_scoped_and_handles_missing_and_unsupported(tmp_path):
    service = EditService()
    project = service.create_project("preview-project", width=320, height=180, fps=R.of(30))
    image = tmp_path / "工程 图片.jpg"
    image.write_bytes(b"jpeg fixture")
    other = tmp_path / "private.png"
    other.write_bytes(b"private")
    unsupported = tmp_path / "script.html"
    unsupported.write_text("private document")
    def clip(name, path):
        return Clip(id=name, asset_ref=AssetReference(asset_id=name, source_path=str(path)),
                    timeline_start=R.of(0), timeline_end=R.of(2), source_start=R.of(0))
    project.sequence.tracks = [Track(id="v", kind="video", clips=[clip("image", image), clip("unsupported", unsupported)])]
    api = HttpApi(service, media_dir=str(tmp_path / "media"))
    try:
        route = "/api/v1/projects/preview-project/source-media"
        status, media = api.handle("GET", route, {"path": str(image)})
        assert status == 200
        assert media["__media_type__"] == "image/jpeg"
        assert Path(media["__media_path__"]).read_bytes() == image.read_bytes()
        assert api.handle("GET", route, {"path": str(other)})[0] == 404
        assert api.handle("GET", route, {"path": [str(image)]})[0] == 404
        assert api.handle("GET", route, {"path": str(unsupported)})[0] == 415
        image.unlink()
        assert api.handle("GET", route, {"path": str(image)})[0] == 404
    finally:
        api.close()


def test_source_in_secondary_sequence_can_be_previewed(tmp_path):
    source = tmp_path / "nested.wav"
    source.write_bytes(b"audio fixture")
    service = EditService()
    project = service.create_project("nested-project", width=320, height=180, fps=R.of(30))
    project.sequences = [Sequence(id="nested", width=320, height=180, fps=R.of(30), tracks=[
        Track(id="a", kind="audio", clips=[Clip(id="c", asset_ref=AssetReference(asset_id="nested-source", source_path=str(source)),
            timeline_start=R.of(0), timeline_end=R.of(2), source_start=R.of(0))])])]
    api = HttpApi(service, media_dir=str(tmp_path / "media"))
    try:
        status, media = api.handle("GET", "/api/v1/projects/nested-project/source-media", {"path": str(source)})
        assert status == 200
        assert media["__media_type__"] == "audio/wav"
    finally:
        api.close()
