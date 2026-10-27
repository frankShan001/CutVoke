import copy
import subprocess

import numpy as np
import pytest
from PIL import Image

from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.protocol import Command
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditError, EditService
from cutvoke.rendering.render import RenderService
from cutvoke.storage.store import ProjectStore


from tests.support.composition_fixtures import setup, frames


def test_effect_mixing_region_and_opacity_use_same_pixels_in_preview_and_export(tmp_path):
    service, store, renderer, command = setup(tmp_path)
    try:
        command("effect.add", clipId="base", effectId="cutvoke.fx.invert", params={})
        instance = service.get_project("composition").sequence.tracks[0].clips[0].effects[0]["instanceId"]
        command("effect.composite", clipId="base", instanceId=instance, compositing={"opacity": .5, "region": {"x": 0, "y": 0, "width": .5, "height": 1}})
        project = service.get_project("composition")
        output, preview = tmp_path / "mix.mp4", tmp_path / "preview.mp4"
        renderer.render(project, str(output), quality="high")
        renderer.render_preview_window(project, str(preview), 1, .5)
        full, part = frames(output), frames(preview)
        assert np.abs(full[10:15] - part).mean() < 3
        assert np.abs(full[3, 20, 20] - 127).max() < 8
        assert full[3, 20, 75, 0] > 170 and full[3, 20, 75, 2] < 30
        command("history.undo")
        assert "compositing" not in store.load("composition").sequence.tracks[0].clips[0].effects[0]
    finally:
        service.close(); store.close()


def test_adjustment_interval_and_layer_order(tmp_path):
    service, store, renderer, command = setup(tmp_path)
    try:
        command("composition.adjustment", clipId="adj", trackId="adjustments", timelineStart=1, timelineEnd=2)
        command("effect.add", clipId="adj", effectId="cutvoke.fx.invert", params={})
        output = tmp_path / "adjust.mp4"
        renderer.render(service.get_project("composition"), str(output), quality="high")
        rgb = frames(output)
        assert rgb[5, 20, 20, 0] > 170
        assert rgb[15, 20, 20, 0] < 85
        assert rgb[25, 20, 20, 0] > 170
    finally:
        service.close(); store.close()


def test_text_can_drive_a_matte_and_participate_in_adjustment_order(tmp_path):
    service, store, renderer, command = setup(tmp_path)
    try:
        project = service.get_project("composition")
        project.sequence.width, project.sequence.height = 384, 216
        text = Clip("title", AssetReference("", ""), R.of(0), R.of(3), R.of(0), effects=[{
            "instanceId": "letters", "effectId": "cutvoke.text", "params": {"content": "A", "fontSize": 120,
            "fontFamily": "Barlow Condensed", "color": "#ffffff", "strokeWidth": 0, "shadow": 0}}])
        project.sequence.tracks.append(Track("title_track", "text", [text]))
        store.save(project)
        command("clip.composite", clipId="title", compositing={"matteOnly": True})
        command("clip.composite", clipId="base", compositing={"matte": {"clipId": "title"}})
        output = tmp_path / "text-matte.mp4"
        renderer.render(service.get_project("composition"), str(output), quality="high")
        image = frames(output, 384, 216)[10]
        count = np.count_nonzero(image[..., 0] > 100)
        assert 10 < count < 2000
        assert image[0].max() < 8
    finally:
        service.close(); store.close()


def test_generic_effect_mixing_retains_semtransparent_alpha_without_color_halos(tmp_path):
    service, store, renderer, command = setup(tmp_path)
    try:
        Image.new("RGBA", (96, 64), (180, 40, 20, 128)).save(tmp_path / "red.png")
        command("effect.add", clipId="base", effectId="cutvoke.fx.invert", params={})
        instance = service.get_project("composition").sequence.tracks[0].clips[0].effects[0]["instanceId"]
        command("effect.composite", clipId="base", instanceId=instance, compositing={"opacity": .5})
        output = tmp_path / "alpha.png"
        renderer.extract_frame(service.get_project("composition"), .5, str(output), alpha=True)
        pixel = np.asarray(Image.open(output).convert("RGBA"))[20, 20]
        assert 120 < pixel[3] < 135
        assert np.abs(pixel[:3].astype(float)-127).max() < 10
    finally:
        service.close(); store.close()


@pytest.mark.parametrize("channel,invert", [("alpha", False), ("alpha", True), ("luma", False), ("luma", True)])
def test_selectable_matte_preserves_source_alpha_and_target_clock(tmp_path, channel, invert):
    service, store, renderer, command = setup(tmp_path)
    try:
        image = Image.new("RGBA", (96, 64), (255, 255, 255, 0))
        for x in range(48):
            for y in range(64): image.putpixel((x, y), (255, 255, 255, 255))
        source = tmp_path / "matte.png"; image.save(source)
        command("clip.insert", clipId="matte", trackId="v_matte", createTrackKind="video", sourcePath=str(source), timelineStart=R.of(1).to_json(), timelineEnd=R.of(2).to_json())
        command("clip.composite", clipId="matte", compositing={"matteOnly": True})
        command("clip.composite", clipId="base", compositing={"matte": {"clipId": "matte", "channel": channel, "invert": invert}})
        output, preview = tmp_path / "matte.mp4", tmp_path / "part.mp4"
        renderer.render(service.get_project("composition"), str(output), quality="high")
        renderer.render_preview_window(service.get_project("composition"), str(preview), .7, 1.6)
        rgb = frames(output)
        assert np.abs(rgb[7:23] - frames(preview)).mean() < 4
        bright, dark = (75, 20) if invert else (20, 75)
        assert rgb[15, 20, bright, 0] > 160
        assert rgb[15, 20, dark].max() < 8
        assert (rgb[5].mean() > 50) == invert
        assert (rgb[25].mean() > 50) == invert
        saved = store.load("composition")
        assert saved.sequence.tracks[0].clips[0].compositing["matte"]["clipId"] == "matte"
        snapshot = copy.deepcopy(saved.to_dict())
        with pytest.raises(EditError):
            command("clip.composite", clipId="matte", compositing={"matte": {"clipId": "base"}})
        assert store.load("composition").to_dict() == snapshot
    finally:
        service.close(); store.close()
