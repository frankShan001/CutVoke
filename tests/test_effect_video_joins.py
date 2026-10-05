"""Regression coverage for video effects followed by a plain clip."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from cutvoke.core.model import AssetReference, Clip, Project, Sequence, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import QUALITY_PRESETS, RenderService


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class EffectVideoJoinTests(unittest.TestCase):
    def test_stalled_preview_recovers_from_finite_intermediate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "testsrc2=size=160x90:rate=30:duration=2", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=2", "-c:v", "libx264", "-c:a", "aac",
                            str(source)], check=True, capture_output=True, timeout=30)
            p = Project("1", "recovery", "0", Sequence("seq", 160, 90, Rational.of(30), tracks=[
                Track("visual", "video", clips=[Clip("clip", AssetReference("source", str(source)),
                    Rational.of(0), Rational.of(2), Rational.of(0),
                    effects=[{"effectId": "cutvoke.fx.shake", "params": {"pixels": 8, "hz": 5.2}}])])]))
            output = root / "preview.mp4"
            reference = root / "reference.mp4"
            with patch.dict(QUALITY_PRESETS, {"low": {"crf": "0", "preset": "veryfast"}}):
                RenderService().render_preview_window(p, str(reference), .4, .8)
            original = subprocess.Popen
            injected = []
            def spawn(args, **kwargs):
                if not injected and "-t" in args and Path(args[0]).stem == "ffmpeg":
                    stalled = Mock(returncode=-15)
                    stalled.communicate.side_effect = [subprocess.TimeoutExpired(args, 2), (b"", b"stalled")]
                    injected.append(stalled)
                    return stalled
                return original(args, **kwargs)
            with patch("subprocess.Popen", spawn), patch("cutvoke.core.render._process_cpu_seconds", return_value=0), \
                    patch("cutvoke.core.render._PREVIEW_IDLE_SECONDS", 0), \
                    patch.dict(QUALITY_PRESETS, {"low": {"crf": "0", "preset": "veryfast"}}):
                report = RenderService().render_preview_window(p, str(output), .4, .8)
            self.assertEqual(report["retryCount"], 1)
            self.assertEqual(report["recoveryMode"], "finite-intermediate")
            self.assertTrue(report["has_audio"])
            self.assertAlmostEqual(report["duration"], .8, places=2)
            injected[0].terminate.assert_called_once()
            self.assertFalse(list(root.glob("*.recovery.mkv")))
            info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=nb_frames", "-of", "json", str(output)],
                check=True, capture_output=True, text=True, timeout=15).stdout)["streams"][0]
            self.assertEqual(int(info["nb_frames"]), 24)
            def pixels(path):
                return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path),
                    "-an", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                    check=True, capture_output=True, timeout=15).stdout
            self.assertEqual(pixels(reference), pixels(output))
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "NUL"],
                           check=True, capture_output=True, timeout=15)

    def test_short_window_with_eight_sticker_layers_finishes_and_decodes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "testsrc2=size=320x180:rate=30:duration=2", "-c:v", "libx264", str(source)],
                           check=True, capture_output=True, timeout=30)
            tracks = [Track("base", "video", clips=[Clip("video", AssetReference("source", str(source)),
                      Rational.of(0), Rational.of(2), Rational.of(0))])]
            assets = Path(__file__).resolve().parents[1] / "src/cutvoke/assets/stickers"
            names = ("food_taiyaki", "food_soft_serve", "food_egg_tart", "food_tanghulu",
                     "food_peach", "food_lemon_tea", "food_burger_fries", "food_donut")
            for i, name in enumerate(names):
                tracks.append(Track(f"track-{i}", "video", role="sticker", clips=[
                    Clip(f"sticker-{i}", AssetReference(name, str(assets / f"{name}.png")),
                         Rational.of(0), Rational.of(2), Rational.of(0), role="sticker",
                         effects=[{"effectId": "cutvoke.transform", "params": {"scale": .32,
                             "position": {"x": i % 4 * 160 + 20, "y": i // 4 * 180 + 25}}}])]))
            sequence = Sequence("seq", 640, 360, Rational.of(30), tracks=tracks)
            p = Project("1", "short-window-regression", "0", sequence)
            output = root / "preview.mp4"
            original = subprocess.Popen

            def bounded_process(*args, **kwargs):
                process = original(*args, **kwargs)
                communicate = process.communicate
                def bounded_communicate(*args, **kwargs):
                    if kwargs.get("timeout") is None:
                        kwargs["timeout"] = 20
                    try:
                        return communicate(*args, **kwargs)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        communicate()
                        raise
                process.communicate = bounded_communicate
                return process

            with patch("subprocess.Popen", bounded_process):
                report = RenderService().render_preview_window(p, str(output), .4, .8)
            self.assertAlmostEqual(report["duration"], .8, places=2)
            info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=nb_frames", "-of", "json", str(output)],
                check=True, capture_output=True, text=True, timeout=15).stdout)["streams"][0]
            self.assertEqual(int(info["nb_frames"]), 24)

    def test_shake_and_trail_join_plain_video_with_square_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "testsrc2=size=192x108:rate=30:duration=2", "-c:v", "libx264",
                            str(source)], check=True, capture_output=True, timeout=30)
            for effects in ([{"effectId": "cutvoke.fx.shake", "params": {"pixels": 8, "hz": 5.2}}],
                            [{"effectId": "cutvoke.fx.shake", "params": {"pixels": 9, "hz": 2.2}},
                             {"effectId": "cutvoke.fx.trail", "params": {"frames": 10, "decay": 0.78}}]):
                with self.subTest(effects=effects):
                    sequence = Sequence("seq", 160, 90, Rational.of(30), tracks=[Track("visual", "video", clips=[
                        Clip("first", AssetReference("source", str(source)), Rational.of(0), Rational.of(1),
                             Rational.of(0), effects=effects),
                        Clip("second", AssetReference("source", str(source)), Rational.of(1), Rational.of(2),
                             Rational.of(0))])])
                    project = Project("1", "join-regression", "0", sequence)
                    renderer = RenderService()
                    for name in ("export", "preview"):
                        path = root / f"{name}.mp4"
                        if name == "export":
                            renderer.render(project, str(path), quality="low", overwrite=True)
                        else:
                            renderer.render_preview_window(project, str(path), 0.6, 0.8)
                        info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=sample_aspect_ratio,width,height", "-of", "json", str(path)],
                            check=True, capture_output=True, text=True, timeout=15).stdout)["streams"][0]
                        self.assertEqual(info["sample_aspect_ratio"], "1:1")
                        self.assertEqual((info["width"], info["height"]), (160, 90))
