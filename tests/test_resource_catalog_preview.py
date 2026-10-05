"""Resource browsing and trial rendering must share the real effect registry."""

from __future__ import annotations

import tempfile
import unittest
import shutil
import subprocess
from pathlib import Path

from cutvoke.core.effects import default_registry
from cutvoke.core.httpapi import HttpApi
from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService


class RecordingFrameRenderer:
    def __init__(self, target_clip_id: str = "clip-one") -> None:
        self.effect_ids: list[list[str]] = []
        self.effect_params: list[list[dict]] = []
        self.times: list[float] = []
        self.target_clip_id = target_clip_id

    def extract_frame(self, project, t, out_png, **kwargs):
        clip = next(c for track in project.sequence.tracks for c in track.clips
                    if c.id == self.target_clip_id)
        self.effect_ids.append([effect["effectId"] for effect in clip.effects])
        self.effect_params.append([effect.get("params", {}) for effect in clip.effects])
        self.times.append(t)
        Path(out_png).write_bytes(b"rendered-png")
        return out_png


class ResourceCatalogPreviewTests(unittest.TestCase):
    def test_builtin_subcategories_are_queryable(self) -> None:
        registry = default_registry()
        slides = registry.search(category="transition", subcategory="运镜")
        self.assertTrue(any(item.id == "cutvoke.transition.slide" for item in slides))
        self.assertTrue(all(item.to_dict()["subcategory"] == "运镜" for item in slides))
        self.assertEqual(registry.get("cutvoke.anim.fadeOut").to_dict()["subcategory"], "出场")
        self.assertEqual(registry.get("cutvoke.fx.compressor").to_dict()["browseCategory"], "audio")
        self.assertEqual(registry.get("cutvoke.fx.blur").to_dict()["browseCategory"], "fx")
        self.assertEqual(registry.get("cutvoke.fx.sepia").to_dict()["browseCategory"], "filter")
        self.assertEqual(registry.get("cutvoke.fx.curves").to_dict()["browseCategory"], "color")
        self.assertTrue(any(item.id == "cutvoke.fx.sepia" for item in registry.search(category="filter")))

    def test_trial_frame_does_not_commit_effect_or_revision(self) -> None:
        service = EditService()
        project = service.create_project("resource-preview")
        project.sequence.tracks = [Track("v1", "video", [Clip(
            "clip-one", AssetReference("asset", "source.mp4"),
            Rational.of(0), Rational.of(4), Rational.of(0),
        )])]
        renderer = RecordingFrameRenderer()
        with tempfile.TemporaryDirectory() as media_dir:
            api = HttpApi(service, renderer, media_dir=media_dir)
            before = project.to_dict()
            status, body = api.handle("GET", f"/api/v1/projects/{project.project_id}/resource-preview", {
                "clipId": "clip-one", "effectId": "cutvoke.fx.grayscale",
            })
            self.assertEqual(status, 200)
            self.assertEqual(body["__png__"], b"rendered-png")
            self.assertEqual(renderer.effect_ids, [["cutvoke.fx.grayscale"]])
            self.assertEqual(renderer.times, [2.0])
            self.assertEqual(project.to_dict(), before)
            self.assertEqual(service.get_project(project.project_id).to_dict(), before)
            status, body = api.handle("GET", f"/api/v1/projects/{project.project_id}/resource-preview", {
                "clipId": "clip-one", "effectId": "cutvoke.transition.crossfade",
            })
            self.assertEqual(status, 422)
            self.assertEqual(body["error"]["code"], "INVALID_TRANSITION_TARGET")
            status, body = api.handle("GET", f"/api/v1/projects/{project.project_id}/resource-preview", {
                "clipId": "clip-one", "effectId": "cutvoke.anim.fadeIn",
            })
            self.assertEqual(status, 200)
            self.assertEqual(renderer.times[-1], 0.25)
            status, listing = api.handle("GET", f"/api/v1/projects/{project.project_id}/resources", {
                "category": "fx", "subcategory": "动感",
            })
            self.assertEqual(status, 200)
            self.assertGreater(listing["count"], 0)
            self.assertTrue(all(effect["subcategory"] == "动感" for effect in listing["effects"]))
            api.close()

    def test_transition_trial_replaces_existing_transition_on_clone(self) -> None:
        service = EditService()
        project = service.create_project("transition-preview")
        first = Clip("clip-one", AssetReference("asset", "source.mp4"),
                     Rational.of(0), Rational.of(2), Rational.of(0))
        second = Clip("clip-two", AssetReference("asset", "source.mp4"),
                      Rational.of(2), Rational.of(4), Rational.of(0),
                      effects=[{"effectId": "cutvoke.transition.crossfade",
                                "params": {"duration": 1.0}}])
        project.sequence.tracks = [Track("v1", "video", [first, second])]
        renderer = RecordingFrameRenderer("clip-two")
        with tempfile.TemporaryDirectory() as media_dir:
            api = HttpApi(service, renderer, media_dir=media_dir)
            before = project.to_dict()
            status, body = api.handle("GET", f"/api/v1/projects/{project.project_id}/resource-preview", {
                "clipId": "clip-two", "effectId": "cutvoke.transition.slide",
                "duration": "1.6",
            })
            self.assertEqual(status, 200, body)
            self.assertEqual(renderer.effect_ids, [["cutvoke.transition.slide"]])
            self.assertEqual(renderer.effect_params[0][0]["duration"], 1.6)
            self.assertAlmostEqual(renderer.times[0], 2.8)
            self.assertEqual(project.to_dict(), before)
            status, body = api.handle("GET", f"/api/v1/projects/{project.project_id}/resource-preview", {
                "clipId": "clip-two", "effectId": "cutvoke.transition.slide",
                "duration": "NaN",
            })
            self.assertEqual(status, 400)
            api.close()

    def test_animation_trial_replaces_only_its_slot_on_clone(self) -> None:
        service = EditService()
        project = service.create_project("animation-preview")
        project.sequence.tracks = [Track("v1", "video", [Clip(
            "clip-one", AssetReference("asset", "source.mp4"),
            Rational.of(0), Rational.of(4), Rational.of(0),
            effects=[
                {"effectId": "cutvoke.anim.fadeIn", "params": {"duration": 1}},
                {"effectId": "cutvoke.anim.fadeOut", "params": {"duration": 1}},
                {"effectId": "cutvoke.anim.float", "params": {"amplitude": 0.03, "period": 3}},
            ],
        )])]
        renderer = RecordingFrameRenderer()
        with tempfile.TemporaryDirectory() as media_dir:
            api = HttpApi(service, renderer, media_dir=media_dir)
            before = project.to_dict()
            status, body = api.handle("GET", f"/api/v1/projects/{project.project_id}/resource-preview", {
                "clipId": "clip-one", "effectId": "cutvoke.anim.slideOutRight",
            })
            self.assertEqual(status, 200, body)
            self.assertEqual(renderer.effect_ids, [[
                "cutvoke.anim.fadeIn", "cutvoke.anim.slideOutRight", "cutvoke.anim.float",
            ]])
            self.assertEqual(renderer.times, [3.75])
            self.assertEqual(project.to_dict(), before)
            api.close()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_trial_frame_renders_effect_with_export_engine(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            source = str(Path(media_dir) / "red.mp4")
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=red:s=64x64:r=15:d=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", source,
            ], check=True, capture_output=True)
            service = EditService()
            project = service.create_project("resource-preview-real", width=64, height=64,
                                             fps=Rational.of(15))
            project.sequence.tracks = [Track("v1", "video", [Clip(
                "clip-one", AssetReference("asset", source),
                Rational.of(0), Rational.of(2), Rational.of(0),
            )])]
            api = HttpApi(service, RenderService(), media_dir=media_dir)
            status, body = api.handle("GET", f"/api/v1/projects/{project.project_id}/resource-preview", {
                "clipId": "clip-one", "effectId": "cutvoke.fx.grayscale",
            })
            self.assertEqual(status, 200, body)
            png = Path(media_dir) / "preview.png"
            png.write_bytes(body["__png__"])
            pixel = subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(png),
                "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
            ], check=True, capture_output=True).stdout[:3]
            self.assertEqual(len(pixel), 3)
            self.assertLess(max(pixel) - min(pixel), 3)
            self.assertEqual(project.sequence.tracks[0].clips[0].effects, [])
            api.close()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_new_transition_presets_render_real_frames(self) -> None:
        ids = [
            "cutvoke.transition.fadegrays", "cutvoke.transition.fadefast",
            "cutvoke.transition.fadeslow", "cutvoke.transition.distance",
            "cutvoke.transition.smoothright", "cutvoke.transition.smoothup",
            "cutvoke.transition.squeezev", "cutvoke.transition.wiperight",
        ]
        self.assertEqual(len({default_registry().get(eid).xfade_transition for eid in ids}), 8)
        with tempfile.TemporaryDirectory() as media_dir:
            paths = {}
            for color in ("red", "blue"):
                path = str(Path(media_dir) / f"{color}.mp4")
                subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c={color}:s=64x64:r=15:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", path,
                ], check=True, capture_output=True)
                paths[color] = path
            service = EditService()
            project = service.create_project("transition-preset-render", width=64,
                                             height=64, fps=Rational.of(15))
            project.sequence.tracks = [Track("v1", "video", [
                Clip("clip-one", AssetReference("red", paths["red"]),
                     Rational.of(0), Rational.of(2), Rational.of(0)),
                Clip("clip-two", AssetReference("blue", paths["blue"]),
                     Rational.of(2), Rational.of(4), Rational.of(0)),
            ])]
            api = HttpApi(service, RenderService(), media_dir=media_dir)
            for effect_id in ids:
                with self.subTest(effect_id=effect_id):
                    status, body = api.handle(
                        "GET", f"/api/v1/projects/{project.project_id}/resource-preview",
                        {"clipId": "clip-two", "effectId": effect_id})
                    self.assertEqual(status, 200, body)
                    self.assertGreater(len(body["__png__"]), 100)
            self.assertEqual(project.sequence.tracks[0].clips[1].effects, [])
            api.close()


if __name__ == "__main__":
    unittest.main()
