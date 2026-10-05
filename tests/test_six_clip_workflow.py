"""One real six-video timeline exercises the shared preview/export composition path."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from array import array
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from cutvoke.core.builtin_stickers import load_builtin_stickers
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore


def _rat(seconds: int) -> dict[str, str]:
    return {"num": str(seconds), "den": "1"}


def _run(*args: str) -> None:
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr[-900:])


def _apply(service: EditService, kind: str, payload: dict, serial: int) -> None:
    current = service.get_project("six-clip-workflow")
    service.execute(Command(
        type=kind, payload=payload, command_id=f"six-clip-{serial}",
        project_id=current.project_id, expected_revision=current.revision,
        actor=Actor("agent", "six-clip-workflow-test"),
    ))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class SixClipWorkflowTests(unittest.TestCase):
    def test_six_videos_bgm_transition_animation_caption_and_stickers(self) -> None:
        stickers = load_builtin_stickers()
        static = next(item for item in stickers if item["stickerId"].endswith("heart"))
        dynamic = next(item for item in stickers if item["stickerId"].endswith("star_pulse"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            colors = ("red", "green", "blue", "yellow", "magenta", "cyan")
            clips = []
            for index, color in enumerate(colors):
                path = root / f"scene-{index + 1}.mp4"
                _run("ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                     f"color=c={color}:s=160x90:r=15:d=1", "-an", "-c:v", "libx264",
                     "-pix_fmt", "yuv420p", str(path))
                clips.append(path)
            music = root / "music.wav"
            _run("ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                 "sine=frequency=440:duration=6:sample_rate=48000", str(music))

            database = root / "projects.sqlite"
            with ProjectStore(str(database)) as store:
                service = EditService(store)
                service.create_project("six-clip-workflow", width=160, height=90,
                                       fps=Rational.of(15))
                for index, path in enumerate(clips):
                    _apply(service, "clip.insert", {
                        "trackId": "video", "clipId": f"scene-{index + 1}",
                        **({"createTrackKind": "video"} if index == 0 else {}),
                        "sourcePath": str(path), "timelineStart": _rat(index),
                        "timelineEnd": _rat(index + 1),
                    }, index + 1)
                _apply(service, "clip.insert", {
                    "trackId": "bgm", "createTrackKind": "audio", "clipId": "music",
                    "sourcePath": str(music), "timelineStart": _rat(0),
                    "timelineEnd": _rat(6),
                }, 7)
                _apply(service, "effect.setTransition", {
                    "clipId": "scene-2", "effectId": "cutvoke.transition.crossfade",
                    "params": {"duration": 0.3},
                }, 8)
                _apply(service, "effect.setAnimation", {
                    "clipId": "scene-3", "effectId": "cutvoke.anim.fadeIn",
                }, 9)
                _apply(service, "effect.add", {
                    "clipId": "scene-4", "effectId": "cutvoke.color",
                    "params": {"brightness": 0.12},
                }, 10)
                _apply(service, "caption.add", {
                    "captionId": "title", "text": "六段成片", "start": _rat(0),
                    "end": _rat(2), "fontSize": 18,
                }, 11)
                _apply(service, "clip.insert", {
                    "trackId": "static-stickers", "createTrackKind": "video",
                    "createTrackRole": "sticker", "role": "sticker",
                    "clipId": "static-heart", "assetId": static["assetId"],
                    "sourcePath": static["path"], "timelineStart": _rat(1),
                    "timelineEnd": _rat(3),
                }, 12)
                _apply(service, "clip.insert", {
                    "trackId": "dynamic-stickers", "createTrackKind": "video",
                    "createTrackRole": "sticker", "role": "sticker",
                    "clipId": "dynamic-star", "assetId": dynamic["assetId"],
                    "sourcePath": dynamic["path"], "timelineStart": _rat(3),
                    "timelineEnd": _rat(5),
                    "stickerAnimation": {"effectId": dynamic["effectId"],
                                         "params": dynamic["params"]},
                }, 13)
                self.assertEqual(len(service.get_project("six-clip-workflow").sequence.tracks), 4)

            with ProjectStore(str(database)) as reopened:
                service = EditService(reopened)
                project = service.get_project("six-clip-workflow")
                self.assertEqual(len(project.sequence.tracks[0].clips), 6)
                self.assertEqual(project.sequence.tracks[-1].role, "sticker")
                self.assertEqual(project.sequence.tracks[-1].clips[0].effects[1]["effectId"],
                                 dynamic["effectId"])
                _apply(service, "history.undo", {}, 14)
                self.assertEqual(len(service.get_project("six-clip-workflow").sequence.tracks), 3)
                _apply(service, "history.redo", {}, 15)
                project = service.get_project("six-clip-workflow")
                self.assertEqual(len(project.sequence.tracks), 4)

                renderer = RenderService()
                exported = root / "export.mp4"
                preview = root / "preview.mp4"
                report = renderer.render(project, str(exported))
                renderer.render_preview_window(project, str(preview), 0, 6)
                self.assertAlmostEqual(report["duration"], 6, delta=0.12)
                self.assertTrue(report["has_audio"])
                def audio_rms(path: Path) -> float:
                    decoded = subprocess.run(
                        ("ffmpeg", "-loglevel", "error", "-i", str(path), "-vn",
                         "-ac", "1", "-ar", "48000", "-f", "f32le", "-"),
                        capture_output=True, timeout=60, check=True,
                    ).stdout
                    samples = array("f")
                    samples.frombytes(decoded)
                    middle = samples[48000:96000]
                    return (sum(value * value for value in middle) / len(middle)) ** 0.5

                export_rms = audio_rms(exported)
                preview_rms = audio_rms(preview)
                self.assertGreater(export_rms, 0.02)
                self.assertAlmostEqual(export_rms, preview_rms, delta=0.015)
                for time in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5):
                    frame_a = root / f"export-{time}.png"
                    frame_b = root / f"preview-{time}.png"
                    frame_index = int(time * 15)
                    selector = f"select=eq(n\\,{frame_index})"
                    _run("ffmpeg", "-y", "-loglevel", "error", "-i", str(exported),
                         "-vf", selector, "-frames:v", "1", str(frame_a))
                    _run("ffmpeg", "-y", "-loglevel", "error", "-i", str(preview),
                         "-vf", selector, "-frames:v", "1", str(frame_b))
                    with Image.open(frame_a) as a, Image.open(frame_b) as b:
                        difference = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
                        mean_difference = max(ImageStat.Stat(difference).mean)
                        if mean_difference >= 18:
                            evidence = Path.cwd() / "output" / "acceptance" / "six-clip-workflow-mismatch"
                            evidence.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(exported, evidence / "export.mp4")
                            shutil.copy2(preview, evidence / "preview.mp4")
                            shutil.copy2(frame_a, evidence / f"export-{time}.png")
                            shutil.copy2(frame_b, evidence / f"preview-{time}.png")
                            difference.save(evidence / f"difference-{time}.png")
                        self.assertLess(mean_difference, 18, f"t={time}")
