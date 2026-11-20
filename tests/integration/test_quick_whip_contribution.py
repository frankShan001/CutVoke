"""Quick Whip's opt-in manifest, editing history and real preview/export contract."""
from pathlib import Path
import subprocess

import numpy as np
import pytest
from PIL import Image

from cutvoke.domain.model import Project
from cutvoke.domain.protocol import Actor, Command
from cutvoke.domain.rational import Rational
from cutvoke.editing.service import EditService, EditError
from cutvoke.interfaces.httpapi import HttpApi
from cutvoke.rendering.effects import EffectRegistry, EffectParamInvalid
from cutvoke.rendering.render import RenderService
from cutvoke.storage.store import ProjectStore
from tools.verify.validate_effect_contributions import validate_folder

ROOT = Path(__file__).resolve().parents[2]
EFFECT_ID = "cutvoke.transition.community.quick-whip"
FOLDER = ROOT / "effects" / "transitions" / EFFECT_ID
LOCALES = ("en", "zh-CN", "zh-TW", "ja", "ko", "es", "fr", "de", "pt-BR", "ru", "ar")


@pytest.fixture
def registry():
    result = EffectRegistry()
    result.discover([FOLDER.parent], strict=True)
    return result


def test_quick_whip_manifest_and_locales(registry):
    assert validate_folder(FOLDER) == []
    effect = registry.get(EFFECT_ID)
    assert effect.xfade_transition == "smoothleft"
    assert registry.validate_params(EFFECT_ID, {}) == {"duration": 0.25}
    for duration in (0.1, 0.25, 0.6):
        assert registry.validate_params(EFFECT_ID, {"duration": duration}) == {"duration": duration}
    for params in ({"duration": 0.099}, {"duration": 0.601}, {"duration": "0.25"},
                   {"duration": True}, {"direction": "right"}):
        with pytest.raises(EffectParamInvalid):
            registry.validate_params(EFFECT_ID, params)
    assert set(effect.name) == set(effect.description) == set(LOCALES)
    for locale in LOCALES:
        data = effect.to_dict(locale)
        assert data["name"] == effect.name[locale]
        assert data["description"] == effect.description[locale]
        assert data["name"] and data["description"]
    assert effect.to_dict("unsupported")["name"] == "Quick Whip"
    assert effect.to_dict("unsupported")["description"] == effect.description["en"]


def apply(service, kind, payload):
    current = service.get_project("quick-whip")
    service.execute(Command(type=kind, payload=payload,
        command_id=f"quick-whip-{current.revision}-{kind}",
        project_id=current.project_id, expected_revision=current.revision,
        actor=Actor("test", "quick-whip")))
    return service.get_project(current.project_id)


def seed(service, root, media_kind="image"):
    service.create_project("quick-whip", width=160, height=90, fps=Rational.of(60))
    for index, color in enumerate(((240, 30, 20), (20, 80, 240))):
        source = root / f"scene-{index}.png"
        Image.new("RGB", (160, 90), color).save(source)
        if media_kind == "video":
            video = source.with_suffix(".mp4")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(source),
                "-t", "2", "-r", "60", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
                check=True, capture_output=True, timeout=30)
            source = video
        apply(service, "clip.insert", {"trackId": "v", "clipId": f"scene-{index}",
            **({"createTrackKind": "video"} if index == 0 else {}), "sourcePath": str(source),
            "timelineStart": {"num": index, "den": 1},
            "timelineEnd": {"num": index + 1, "den": 1}})


def test_quick_whip_service_validation_history_and_persistence(tmp_path, registry):
    database = str(tmp_path / "projects.sqlite")
    with ProjectStore(database) as store:
        service = EditService(store=store, registry=registry)
        seed(service, tmp_path)
        project = apply(service, "effect.setTransition", {"clipId": "scene-1", "effectId": EFFECT_ID})
        effects = project.sequence.tracks[0].clips[1].effects
        assert effects[0]["effectId"] == EFFECT_ID
        assert effects[0]["params"] == {"duration": 0.25}
        assert Project.from_dict(project.to_dict()).sequence.tracks[0].clips[1].effects == effects
        original = project.to_dict()
        with pytest.raises(EditError):
            apply(service, "effect.setTransition", {"clipId": "scene-1", "effectId": EFFECT_ID,
                "params": {"duration": 0.601}})
        assert service.get_project("quick-whip").to_dict() == original
        assert apply(service, "history.undo", {}).sequence.tracks[0].clips[1].effects == []
        assert apply(service, "history.redo", {}).sequence.tracks[0].clips[1].effects == effects
        service.close()
    with ProjectStore(database) as store:
        service = EditService(store=store, registry=registry)
        assert service.get_project("quick-whip").sequence.tracks[0].clips[1].effects == effects
        service.close()


def test_quick_whip_is_in_editor_resource_catalog(tmp_path, registry):
    service = EditService(registry=registry, effect_modules_dir=tmp_path / "modules")
    seed(service, tmp_path)
    api = HttpApi(service, RenderService(registry=registry), media_dir=str(tmp_path / "media"))
    try:
        for locale in LOCALES:
            status, result = api.handle("GET", "/api/v1/projects/quick-whip/resources",
                {"category": "transition", "lang": locale, "q": "quick-whip"})
            assert status == 200
            assert result["count"] == 1
            effect = result["effects"][0]
            assert effect["effectId"] == EFFECT_ID
            assert effect["name"] == registry.get(EFFECT_ID).name[locale]
            assert effect["category"] == "transition"
            assert effect["parameters"]["properties"]["duration"]["default"] == 0.25
    finally:
        api.close()
        service.close()


def decode(path):
    result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path),
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True, timeout=30)
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(-1, 90, 160, 3)


@pytest.mark.parametrize("media_kind", ["image", "video"])
@pytest.mark.parametrize("duration", [0.1, 0.25, 0.6])
def test_quick_whip_export_and_preview(tmp_path, registry, media_kind, duration):
    service = EditService(registry=registry, effect_modules_dir=tmp_path / "modules")
    seed(service, tmp_path, media_kind)
    project = apply(service, "effect.setTransition", {"clipId": "scene-1", "effectId": EFFECT_ID,
        "params": {"duration": duration}})
    renderer = RenderService(registry=registry, gpu_effects=False)
    renderer.gpu_acceleration = False
    output = tmp_path / "export.mp4"
    result = renderer.render(project, str(output), quality="medium")
    frames = decode(output)
    assert len(frames) == 120
    assert abs(result["duration"] - 2) < 1 / 60
    assert np.max(np.abs(frames[30, 45, 80].astype(int) - (240, 30, 20))) < 12
    assert np.max(np.abs(frames[110, 45, 80].astype(int) - (20, 80, 240))) < 12
    middle = frames[60 + round(duration * 60 / 2)]
    # Incoming blue enters on the right and sweeps left, not a uniform dissolve.
    assert middle[45, 140, 2] > middle[45, 20, 2] + 100
    assert middle[45, 20, 0] > middle[45, 140, 0] + 100
    transition = frames[60:60 + round(duration * 60) + 1].astype(float)
    blue_means = transition[:, :, :, 2].mean(axis=(1, 2))
    assert np.all(np.diff(blue_means) >= -2)
    assert blue_means[-1] > blue_means[0] + 190
    preview = tmp_path / "preview.mp4"
    renderer.render_preview_window(project, str(preview), 0.75, 1.0)
    window = decode(preview)
    assert len(window) == 60
    assert np.max(np.abs(window.astype(int) - frames[45:105].astype(int))) < 15
    service.close()
