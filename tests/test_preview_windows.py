"""Interactive preview encodes only requested windows and reuses unchanged ones."""

from __future__ import annotations

import os
import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from array import array
import shutil
import subprocess
import tempfile
import unittest

from cutvoke.core.httpapi import HttpApi
from cutvoke.core.model import AssetReference, Caption, Clip, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderCancelled, RenderError, RenderService
from cutvoke.core.service import EditService


def r(value: int) -> Rational:
    return Rational.of(value)


class RecordingRenderer:
    def __init__(self) -> None:
        self.projects: list[dict] = []
        self.preview_modes: list[list[dict] | None] = []
        self.preview_ranges: list[tuple[float, float]] = []
        self._render_service = RenderService()

    def _clip_needs_canvas(self, clip: Clip) -> bool:
        return self._render_service._clip_needs_canvas(clip)

    def render(self, project, out_path: str, *, quality: str, overwrite: bool) -> dict:
        self.projects.append(project.to_dict())
        with open(out_path, "wb") as output:
            output.write(b"preview-window")
        return {"duration": float(project.sequence.tracks[0].clips[0].duration.to_fraction())}

    def render_preview_window(self, project, out_path: str, start: float,
                              duration: float,
                              render_modes: list[dict] | None = None) -> dict:
        self.projects.append(project.to_dict())
        self.preview_modes.append(render_modes)
        self.preview_ranges.append((start, duration))
        with open(out_path, "wb") as output:
            output.write(b"preview-window")
        return {"duration": duration, "windowStart": start}


class PreviewWindowTests(unittest.TestCase):
    def test_late_obsolete_window_does_not_cancel_the_current_render(self) -> None:
        class BlockingRenderer(RecordingRenderer):
            def render(self, project, out_path, **kwargs):
                started.set()
                self_test.assertTrue(release.wait(3))
                return super().render(project, out_path, **kwargs)

        self_test = self
        started, release = threading.Event(), threading.Event()
        with tempfile.TemporaryDirectory() as media_dir:
            service = EditService()
            project = service.create_project("late-preview", width=160, height=90)
            project.sequence.tracks = [Track("v1", "video", [Clip(
                "clip", AssetReference("source", "fake.mp4"), r(0), r(4), r(0))])]
            old = copy.deepcopy(project)
            project.sequence.background_color = "#ffffff"
            renderer = BlockingRenderer()
            api = HttpApi(service, renderer, media_dir=media_dir)
            try:
                with ThreadPoolExecutor(1) as workers:
                    current = workers.submit(api._preview_window_file, copy.deepcopy(project), 0)
                    self.assertTrue(started.wait(2))
                    with self.assertRaises(RenderCancelled):
                        api._preview_window_file(old, 0)
                    release.set()
                    self.assertTrue(os.path.isfile(current.result(timeout=2)[0]))
                self.assertEqual(len(renderer.projects), 1)
            finally:
                release.set()
                api.close()

    def test_queued_previews_discard_snapshots_superseded_by_an_edit(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            service = EditService()
            project = service.create_project("queued-preview", width=160, height=90)
            project.sequence.tracks = [Track("v1", "video", [Clip(
                "clip", AssetReference("source", "fake.mp4"), r(0), r(4), r(0))])]
            old = copy.deepcopy(project)
            renderer = RecordingRenderer()
            api = HttpApi(service, renderer, media_dir=media_dir)
            try:
                with ThreadPoolExecutor(2) as workers:
                    with api._preview_scheduler.slot(threading.Event()):
                        window = workers.submit(api._preview_window_file, old, 0)
                        frame = workers.submit(api._preview_frame_file, old, 0, None)
                        deadline = time.monotonic() + 2
                        while len(api._preview_scheduler.queue) < 2 and time.monotonic() < deadline:
                            time.sleep(.005)
                        self.assertEqual(len(api._preview_scheduler.queue), 2)
                        project.sequence.background_color = "#ffffff"
                    for request in (window, frame):
                        with self.assertRaises(RenderCancelled):
                            request.result(timeout=2)
                self.assertEqual(renderer.projects, [])
            finally:
                api.close()

    def test_missing_active_frame_source_reports_clip_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            missing = os.path.join(media_dir, "missing.png")
            service = EditService()
            project = service.create_project("missing-frame-source", width=160, height=90)
            clip = Clip("relinked-image", AssetReference("image", missing), r(0), r(4), r(0))
            project.sequence.tracks = [Track("v1", "video", [clip])]

            with self.assertRaises(RenderError) as error:
                RenderService().extract_frame(
                    project, 0.5, os.path.join(media_dir, "preview.png"))

            self.assertEqual(
                str(error.exception),
                f"source file missing for clip relinked-image: {missing}")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_dynamic_motion_preview_matches_export_and_moves(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            image = os.path.join(media_dir, "pattern.png")
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30",
                "-frames:v", "1", image,
            ], check=True, capture_output=True)

            def frame(path: str, at: float) -> bytes:
                return subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-ss", str(at), "-i", path, "-frames:v", "1",
                    "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                ], check=True, capture_output=True).stdout

            for name, params in (
                ("swing", {"degrees": 9, "hz": 1.4}),
                ("shake", {"pixels": 14, "hz": 5.2}),
            ):
                with self.subTest(effect=name):
                    service = EditService()
                    project = service.create_project(name, width=160, height=90)
                    clip = Clip(name, AssetReference("pattern", image), r(0), r(2), r(0))
                    clip.effects = [{"effectId": f"cutvoke.fx.{name}", "params": params}]
                    project.sequence.tracks = [Track("v1", "video", [clip])]
                    renderer = RenderService()
                    exported = os.path.join(media_dir, f"{name}-export.mp4")
                    preview = os.path.join(media_dir, f"{name}-preview.mp4")
                    renderer.render(project, exported, quality="low", overwrite=True)
                    renderer.render_preview_window(project, preview, 0.6, 0.9)

                    exported_frame = frame(exported, 1.016667)
                    preview_frame = frame(preview, 0.416667)
                    earlier_frame = frame(exported, 0.75)
                    self.assertEqual(len(exported_frame), 160 * 90 * 3)
                    difference = sum(abs(a - b) for a, b in zip(exported_frame, preview_frame)) / len(exported_frame)
                    motion = sum(abs(a - b) for a, b in zip(exported_frame, earlier_frame)) / len(exported_frame)
                    self.assertLess(difference, 5.0)
                    self.assertGreater(motion, 5.0)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_glitch_preview_window_matches_export(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            image = os.path.join(media_dir, "pattern.png")
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30",
                "-frames:v", "1", image,
            ], check=True, capture_output=True)
            service = EditService()
            project = service.create_project("glitch-window", width=160, height=90)
            clip = Clip("glitch", AssetReference("pattern", image), r(0), r(2), r(0))
            clip.effects = [{"effectId": "cutvoke.fx.glitch", "params": {"amount": 0.65}}]
            project.sequence.tracks = [Track("v1", "video", [clip])]
            renderer = RenderService()
            exported = os.path.join(media_dir, "export.mp4")
            preview = os.path.join(media_dir, "preview.mp4")
            renderer.render(project, exported, quality="low", overwrite=True)
            renderer.render_preview_window(project, preview, 0.6, 0.9)

            def frame(path: str, at: float) -> bytes:
                return subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-ss", str(at), "-i", path, "-frames:v", "1",
                    "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                ], check=True, capture_output=True).stdout

            full_frame, preview_frame = frame(exported, 1.0), frame(preview, 0.4)
            self.assertEqual(len(full_frame), 160 * 90 * 3)
            difference = sum(abs(a - b) for a, b in zip(full_frame, preview_frame)) / len(full_frame)
            self.assertLess(difference, 5.0)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_image_to_video_transition_matches_preview_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            image = os.path.join(media_dir, "red.png")
            video = os.path.join(media_dir, "blue.mp4")
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=red:s=160x90:r=1:d=1",
                "-frames:v", "1", image,
            ], check=True, capture_output=True)
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=15:d=4",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", video,
            ], check=True, capture_output=True)

            service = EditService()
            project = service.create_project("image-video-transition", width=160,
                                             height=90, fps=Rational.of(15))
            first = Clip("image", AssetReference("image", image), r(0), r(8), r(0))
            second = Clip("video", AssetReference("video", video), r(8), r(12), r(0))
            second.effects = [{"effectId": "cutvoke.transition.crossfade",
                               "params": {"duration": 0.5}}]
            project.sequence.tracks = [Track("v1", "video", [first, second])]
            renderer = RenderService()
            api = HttpApi(service, renderer, media_dir=media_dir)
            try:
                exported = os.path.join(media_dir, "export.mp4")
                renderer.render(project, exported, quality="low", overwrite=True)
                preview, _, _ = api._preview_window_file(project, 1)

                def frame(path: str, at: float) -> bytes:
                    return subprocess.run([
                        "ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-ss", str(at), "-i", path, "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                    ], check=True, capture_output=True).stdout

                during = frame(exported, 8.2)
                preview_during = frame(preview, 0.2)
                self.assertEqual(len(during), 160 * 90 * 3)
                difference = sum(abs(a - b) for a, b in zip(during, preview_during)) / len(during)
                self.assertLess(difference, 8.0)
                self.assertGreater(sum(during[0::3]) / (160 * 90), 40)
                self.assertGreater(sum(during[2::3]) / (160 * 90), 40)
            finally:
                api.close()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_zoom_transition_keeps_both_shots_visible_at_midpoint(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            red = os.path.join(media_dir, "red.png")
            blue = os.path.join(media_dir, "blue.png")
            for color, path in (("red", red), ("blue", blue)):
                subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=15:d=1",
                    "-frames:v", "1", path,
                ], check=True, capture_output=True)
            service = EditService()
            project = service.create_project("zoom-transition", width=160,
                                             height=90, fps=Rational.of(15))
            first = Clip("red", AssetReference("red", red), r(0), r(2), r(0))
            second = Clip("blue", AssetReference("blue", blue), r(2), r(4), r(0))
            second.effects = [{"effectId": "cutvoke.transition.zoom",
                               "params": {"duration": 0.5}}]
            project.sequence.tracks = [Track("v1", "video", [first, second])]
            renderer = RenderService()
            exported = os.path.join(media_dir, "export.mp4")
            preview = os.path.join(media_dir, "preview.mp4")
            renderer.render(project, exported, quality="low", overwrite=True)
            renderer.render_preview_window(project, preview, 1.8, 0.8)

            def frame(path: str, at: float) -> bytes:
                return subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-ss", str(at), "-i", path, "-frames:v", "1",
                    "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                ], check=True, capture_output=True).stdout

            midpoint = frame(exported, 2.25)
            preview_midpoint = frame(preview, 0.45)
            self.assertEqual(len(midpoint), 160 * 90 * 3)
            self.assertGreater(sum(midpoint[0::3]) / (160 * 90), 40)
            self.assertGreater(sum(midpoint[2::3]) / (160 * 90), 40)
            mean_difference = sum(abs(a - b) for a, b in zip(midpoint, preview_midpoint)) / len(midpoint)
            self.assertLess(mean_difference, 8.0)

    def test_distant_clip_and_caption_edits_reuse_an_unchanged_window(self) -> None:
        service = EditService()
        project = service.create_project("window-cache")
        first = Clip("one", AssetReference("a", "first.mp4"), r(0), r(16), r(0))
        later = Clip("two", AssetReference("b", "later.mp4"), r(16), r(24), r(0))
        project.sequence.tracks = [Track("v1", "video", [first, later])]
        renderer = RecordingRenderer()
        with tempfile.TemporaryDirectory() as media_dir:
            api = HttpApi(service, renderer, media_dir=media_dir)
            try:
                path, digest, duration = api._preview_window_file(project, 1)
                self.assertTrue(os.path.isfile(path))
                self.assertEqual(duration, 8)
                self.assertEqual(len(renderer.projects), 1)
                sliced = renderer.projects[0]["sequences"][0]["tracks"][0]["clips"][0]
                self.assertEqual(sliced["sourceStart"], {"num": "8", "den": "1"})
                self.assertEqual(sliced["timelineStart"], {"num": "0", "den": "1"})

                project.sequence.captions.append(Caption("c", "changed", r(1), r(2)))
                later.asset_ref.source_path = "changed-later.mp4"
                again_path, again_digest, _ = api._preview_window_file(project, 1)
                self.assertEqual((again_path, again_digest), (path, digest))
                self.assertEqual(len(renderer.projects), 1)

                later_path, _, _ = api._preview_window_file(project, 2)
                self.assertNotEqual(later_path, path)
                self.assertEqual(len(renderer.projects), 2)
            finally:
                api.close()

    def test_multitrack_cache_reuses_windows_after_distant_edits(self) -> None:
        service = EditService()
        project = service.create_project("multitrack-window-cache")
        primary_now = Clip("primary-now", AssetReference("p0", "primary-now.mp4"),
                           r(0), r(8), r(0))
        primary_later = Clip("primary-later", AssetReference("p1", "primary-later.mp4"),
                             r(16), r(24), r(0))
        overlay_now = Clip("overlay-now", AssetReference("o0", "overlay-now.mp4"),
                           r(0), r(8), r(0))
        overlay_later = Clip("overlay-later", AssetReference("o1", "overlay-later.mp4"),
                             r(16), r(24), r(0))
        audio_now = Clip("audio-now", AssetReference("a0", "audio-now.wav"),
                         r(0), r(8), r(0))
        audio_later = Clip("audio-later", AssetReference("a1", "audio-later.wav"),
                           r(16), r(24), r(0))
        project.sequence.tracks = [
            Track("v1", "video", [primary_now, primary_later]),
            Track("v2", "video", [overlay_now, overlay_later]),
            Track("a1", "audio", [audio_now, audio_later]),
        ]
        renderer = RecordingRenderer()
        with tempfile.TemporaryDirectory() as media_dir:
            api = HttpApi(service, renderer, media_dir=media_dir)
            try:
                first_path, first_digest, _ = api._preview_window_file(project, 0)
                self.assertEqual(len(renderer.projects), 1)

                primary_later.asset_ref.source_path = "replaced-primary-later.mp4"
                overlay_later.effects = [{"effectId": "cutvoke.fx.glitch",
                                          "params": {"amount": 0.9}}]
                audio_later.asset_ref.source_path = "replaced-audio-later.wav"
                same_path, same_digest, _ = api._preview_window_file(project, 0)
                self.assertEqual((same_path, same_digest), (first_path, first_digest))
                self.assertEqual(len(renderer.projects), 1)

                # Even a distant image can change the renderer's track-wide
                # plain/canvas path, so that mode change must invalidate this window.
                overlay_later.asset_ref.source_path = "distant-image.png"
                mode_changed_path, mode_changed_digest, _ = api._preview_window_file(project, 0)
                self.assertNotEqual((mode_changed_path, mode_changed_digest),
                                    (first_path, first_digest))
                self.assertEqual(len(renderer.projects), 2)

                overlay_now.effects = [{"effectId": "cutvoke.fx.glitch",
                                        "params": {"amount": 0.3}}]
                changed_path, changed_digest, _ = api._preview_window_file(project, 0)
                self.assertNotEqual((changed_path, changed_digest),
                                    (mode_changed_path, mode_changed_digest))
                self.assertEqual(len(renderer.projects), 3)
            finally:
                api.close()

    def test_900_clip_late_window_only_compiles_intersecting_clips(self) -> None:
        service = EditService()
        project = service.create_project("large-late-window")
        tracks = []
        for track_index in range(3):
            clips = [Clip(
                f"t{track_index}-c{clip_index}",
                AssetReference("large-source", "large-source.mp4"),
                r(clip_index * 2), r((clip_index + 1) * 2), r(0),
            ) for clip_index in range(300)]
            tracks.append(Track(f"v{track_index + 1}", "video", clips))
        project.sequence.tracks = tracks
        renderer = RecordingRenderer()
        with tempfile.TemporaryDirectory() as media_dir:
            api = HttpApi(service, renderer, media_dir=media_dir)
            try:
                path, _, duration = api._preview_window_file(project, 74)
                self.assertTrue(os.path.isfile(path))
                self.assertEqual(duration, 8)
                self.assertEqual(renderer.preview_ranges, [(0.0, 8.0)])
                rendered_tracks = renderer.projects[0]["sequences"][0]["tracks"]
                self.assertEqual([len(track["clips"]) for track in rendered_tracks], [4, 4, 4])
                self.assertEqual(
                    [track["clips"][0]["timelineStart"] for track in rendered_tracks],
                    [{"num": "0", "den": "1"}] * 3,
                )
                self.assertEqual(
                    [mode["overlay"] for mode in renderer.preview_modes[0]],
                    [False, True, True],
                )
                self.assertEqual(project.sequence.tracks[0].clips[0].timeline_start, r(0))
            finally:
                api.close()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_dense_late_multitrack_preview_slices_graph_and_matches_export(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            source = os.path.join(media_dir, "source.mp4")
            full = os.path.join(media_dir, "full.mp4")
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=10:duration=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", source,
            ], check=True, capture_output=True)

            service = EditService()
            project = service.create_project(
                "dense-late-window", width=64, height=64, fps=Rational.of(10))
            tracks = []
            clip_count = 120
            for track_index in range(3):
                clips = [Clip(
                    f"t{track_index}-c{clip_index}",
                    AssetReference("dense-source", source),
                    Rational.of(clip_index, 5),
                    Rational.of(clip_index + 1, 5),
                    r(0),
                ) for clip_index in range(clip_count)]
                tracks.append(Track(f"v{track_index + 1}", "video", clips))
            project.sequence.tracks = tracks
            renderer = RenderService()
            api = HttpApi(service, renderer, media_dir=media_dir)
            try:
                full_graph, *_ = renderer._compile(project.sequence, (0, 0))
                self.assertGreater(len(full_graph.encode("utf-16-le")) // 2, 12_000)
                renderer.render(project, full, quality="low", overwrite=True)

                preview, _, duration = api._preview_window_file(project, 2)
                self.assertAlmostEqual(duration, 8.0, places=2)
                self.assertAlmostEqual(renderer.probe_media(preview)["duration"], 8.0,
                                       delta=0.1)

                def sample(path: str, at: float) -> bytes:
                    result = subprocess.run([
                        "ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-ss", str(at), "-i", path, "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                    ], check=True, capture_output=True)
                    return result.stdout

                exported, windowed = sample(full, 17.0), sample(preview, 1.0)
                self.assertEqual(len(windowed), 64 * 64 * 3)
                mean_difference = sum(abs(a - b) for a, b in zip(exported, windowed)) / len(windowed)
                self.assertLess(mean_difference, 8.0)
            finally:
                api.close()

    def test_cache_includes_only_transition_neighbor_needed_by_window(self) -> None:
        service = EditService()
        project = service.create_project("transition-neighbor-window-cache")
        first = Clip("first", AssetReference("a", "first.mp4"), r(0), r(8), r(0))
        second = Clip("second", AssetReference("b", "second.mp4"), r(8), r(16), r(0))
        third = Clip("third", AssetReference("c", "third.mp4"), r(16), r(24), r(0))
        second.effects = [{"effectId": "cutvoke.transition.crossfade",
                           "params": {"duration": 0.5}}]
        project.sequence.tracks = [Track("v1", "video", [first, second, third])]
        renderer = RecordingRenderer()
        with tempfile.TemporaryDirectory() as media_dir:
            api = HttpApi(service, renderer, media_dir=media_dir)
            try:
                transition_path, transition_digest, _ = api._preview_window_file(project, 1)
                first.asset_ref.source_path = "changed-transition-handle.mp4"
                changed_path, changed_digest, _ = api._preview_window_file(project, 1)
                self.assertNotEqual((changed_path, changed_digest),
                                    (transition_path, transition_digest))
                self.assertEqual(len(renderer.projects), 2)

                later_path, later_digest, _ = api._preview_window_file(project, 2)
                first.asset_ref.source_path = "changed-again-outside-window.mp4"
                same_later_path, same_later_digest, _ = api._preview_window_file(project, 2)
                self.assertEqual((same_later_path, same_later_digest),
                                 (later_path, later_digest))
                self.assertEqual(len(renderer.projects), 3)
            finally:
                api.close()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_effect_window_matches_the_same_interval_of_export(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            source = os.path.join(media_dir, "source.mp4")
            full = os.path.join(media_dir, "full.mp4")
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=15",
                "-t", "12", "-c:v", "libx264", "-pix_fmt", "yuv420p", source,
            ], check=True, capture_output=True)
            service = EditService()
            project = service.create_project("window-real", width=64, height=64,
                                             fps=Rational.of(15))
            clip = Clip("moving", AssetReference("asset", source), r(0), r(12), r(0))
            clip.effects = [{"effectId": "cutvoke.color", "params": {
                "brightness": 0.1, "contrast": 1.0, "saturation": 1.0,
            }}]
            project.sequence.tracks = [Track("v1", "video", [clip])]
            renderer = RenderService()
            api = HttpApi(service, renderer, media_dir=media_dir)
            try:
                renderer.render(project, full, quality="low", overwrite=True)
                window, _, duration = api._preview_window_file(project, 1)
                self.assertAlmostEqual(duration, 4.0, places=2)
                self.assertAlmostEqual(renderer.probe_media(window)["duration"], 4.0, delta=0.1)

                # A stale disk cache from an older renderer may contain only a
                # short prefix. Reject it and rebuild the whole timeline window.
                truncated = os.path.join(media_dir, "truncated-cache.mp4")
                subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:r=15:d=1",
                    "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    truncated,
                ], check=True, capture_output=True)
                shutil.copyfile(truncated, window)
                # Persistent cache notices replaced media by size/mtime, even
                # while the process remains open; no manual index flush needed.
                window, _, _ = api._preview_window_file(project, 1)
                self.assertAlmostEqual(renderer.probe_media(window)["duration"], 4.0, delta=0.1)

                def sample(path: str, second: float) -> bytes:
                    result = subprocess.run([
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(second),
                        "-i", path, "-frames:v", "1", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "pipe:1",
                    ], check=True, capture_output=True)
                    return result.stdout

                exported, preview = sample(full, 9), sample(window, 1)
                self.assertEqual(len(exported), 64 * 64 * 3)
                mean_difference = sum(abs(a - b) for a, b in zip(exported, preview)) / len(exported)
                nearby = []
                for offset in (-1 / 15, 1 / 15):
                    alternate = sample(full, 9 + offset)
                    nearby.append(sum(abs(a - b) for a, b in zip(alternate, preview)) / len(preview))
                self.assertLess(mean_difference, 10.0)
                self.assertTrue(all(other > mean_difference + 3 for other in nearby))
            finally:
                api.close()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_audio_gaps_follow_absolute_timeline_in_preview_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            picture = os.path.join(media_dir, "picture.mp4")
            tone = os.path.join(media_dir, "tone.wav")
            exported = os.path.join(media_dir, "audio-timeline.mp4")
            for command in (
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "color=c=red:s=64x64:r=15:d=10",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", picture],
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                 "-c:a", "pcm_s16le", tone],
            ):
                subprocess.run(command, check=True, capture_output=True)
            service = EditService()
            project = service.create_project("audio-gap", width=64, height=64,
                                             fps=Rational.of(15))
            project.sequence.tracks = [
                Track("v1", "video", [Clip(
                    "picture", AssetReference("picture", picture), r(0), r(10), r(0),
                )]),
                Track("a1", "audio", [
                    Clip("tone-first", AssetReference("tone", tone), r(1), r(3), r(0)),
                    Clip("tone-second", AssetReference("tone", tone), r(6), r(8), r(0)),
                    Clip("tone-future", AssetReference("tone", tone), r(12), r(14), r(0)),
                ]),
            ]
            renderer = RenderService()
            api = HttpApi(service, renderer, media_dir=media_dir)
            try:
                renderer.render(project, exported, quality="low", overwrite=True)
                window, digest, _ = api._preview_window_file(project, 0)

                decoded: dict[str, array] = {}

                def level(path: str, second: float) -> float:
                    if path not in decoded:
                        raw = subprocess.run([
                            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
                            "-map", "0:a:0", "-ac", "1", "-ar", "8000",
                            "-f", "s16le", "pipe:1",
                        ], check=True, capture_output=True).stdout
                        decoded[path] = array("h")
                        decoded[path].frombytes(raw)
                    start = round(second * 8000)
                    samples = decoded[path][start:start + 2000]
                    return (sum(value * value for value in samples) / len(samples)) ** 0.5 if samples else 0.0

                for path in (exported, window):
                    with self.subTest(path=path):
                        self.assertGreater(level(path, 1.5), 100)
                        self.assertLess(level(path, 4.5), 20)
                        self.assertGreater(level(path, 6.5), 100)
                self.assertGreater(level(exported, 12.5), 100)
                black_tail = subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "12",
                    "-i", exported, "-frames:v", "1", "-f", "rawvideo",
                    "-pix_fmt", "rgb24", "pipe:1",
                ], check=True, capture_output=True).stdout
                self.assertEqual(len(black_tail), 64 * 64 * 3)
                self.assertLess(max(black_tail), 12)
                alternate_tone = os.path.join(media_dir, "alternate-tone.wav")
                shutil.copyfile(tone, alternate_tone)
                project.sequence.tracks[1].clips[2].asset_ref.source_path = alternate_tone
                same_window, same_digest, _ = api._preview_window_file(project, 0)
                self.assertEqual((same_window, same_digest), (window, digest))
                later_window, _, _ = api._preview_window_file(project, 1)
                self.assertNotEqual(later_window, window)
                self.assertGreater(level(later_window, 4.5), 100)
            finally:
                api.close()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_transition_at_window_boundary_and_caption_export(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            sources = []
            for color, duration in (("red", 8), ("blue", 4)):
                path = os.path.join(media_dir, f"{color}.mp4")
                subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=15:d={duration}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", path,
                ], check=True, capture_output=True)
                sources.append(path)
            service = EditService()
            project = service.create_project("transition-window", width=160, height=90,
                                             fps=Rational.of(15))
            first = Clip("red", AssetReference("red", sources[0]), r(0), r(8), r(0))
            second = Clip("blue", AssetReference("blue", sources[1]), r(8), r(12), r(0))
            second.effects = [{"effectId": "cutvoke.transition.crossfade",
                               "params": {"duration": 0.5}}]
            project.sequence.tracks = [Track("v1", "video", [first, second])]
            project.sequence.captions = [Caption("title", "HELLO", r(1), r(3))]
            renderer = RenderService()
            api = HttpApi(service, renderer, media_dir=media_dir)
            try:
                full = os.path.join(media_dir, "export.mp4")
                renderer.render(project, full, quality="low", overwrite=True)
                start_window, _, _ = api._preview_window_file(project, 0)
                cut_window, _, cut_duration = api._preview_window_file(project, 1)
                self.assertAlmostEqual(cut_duration, 4.0, places=2)
                self.assertAlmostEqual(
                    renderer.probe_media(cut_window)["duration"], 4.0, delta=0.1)

                def frame(path: str, at: float) -> bytes:
                    return subprocess.run([
                        "ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-ss", str(at), "-i", path, "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                    ], check=True, capture_output=True).stdout

                during_cut = frame(full, 8.2)
                preview_cut = frame(cut_window, 0.2)
                self.assertEqual(len(during_cut), 160 * 90 * 3)
                cut_difference = sum(abs(a - b) for a, b in zip(during_cut, preview_cut)) / len(during_cut)
                self.assertLess(cut_difference, 8.0)
                with_caption = frame(full, 2.0)
                without_caption = frame(start_window, 2.0)
                changed_caption_pixels = sum(
                    1 for index in range(0, len(with_caption), 3)
                    if max(abs(with_caption[index + channel] - without_caption[index + channel])
                           for channel in range(3)) > 16
                )
                self.assertGreater(changed_caption_pixels, 20)
            finally:
                api.close()
