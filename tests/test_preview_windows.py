"""Interactive preview encodes only requested windows and reuses unchanged ones."""

from __future__ import annotations

import os
from array import array
import shutil
import subprocess
import tempfile
import unittest

from cutvoke.core.httpapi import HttpApi
from cutvoke.core.model import AssetReference, Caption, Clip, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService


def r(value: int) -> Rational:
    return Rational.of(value)


class RecordingRenderer:
    def __init__(self) -> None:
        self.projects: list[dict] = []

    def render(self, project, out_path: str, *, quality: str, overwrite: bool) -> dict:
        self.projects.append(project.to_dict())
        with open(out_path, "wb") as output:
            output.write(b"preview-window")
        return {"duration": float(project.sequence.tracks[0].clips[0].duration.to_fraction())}


class PreviewWindowTests(unittest.TestCase):
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
                cut_window, _, _ = api._preview_window_file(project, 1)

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
                caption_difference = sum(abs(a - b) for a, b in zip(with_caption, without_caption)) / len(with_caption)
                self.assertGreater(caption_difference, 1.0)
            finally:
                api.close()
