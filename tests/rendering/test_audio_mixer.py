"""Actual audio output, preview clocks, mixer undo, and honest device capture."""
import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from cutvoke.domain.keyframes import Keyframe, evaluate
from cutvoke.domain.model import Project
from cutvoke.domain.protocol import Command
from cutvoke.domain.rational import Rational
from cutvoke.editing.service import EditService, EditError
from cutvoke.rendering.audio_tools import record_audio
from cutvoke.rendering.render import RenderService
from cutvoke.storage.store import ProjectStore


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class AudioMixerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "tone.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=1000:sample_rate=48000:duration=3", "-ac", "2", str(self.source)],
                       check=True, capture_output=True)
        self.store = ProjectStore(str(self.root / "projects.sqlite"))
        self.service = EditService(self.store)
        self.service.create_project("mix", width=160, height=90)
        self.serial = 0
        self.apply("track.add", {"kind": "audio", "trackId": "a"})
        self.apply("clip.insert", {"trackId": "a", "clipId": "voice", "sourcePath": str(self.source),
                                  "timelineStart": {"num": "0", "den": "1"}, "timelineEnd": {"num": "3", "den": "1"}})

    def tearDown(self):
        self.service.close()
        self.store.close()
        self.temp.cleanup()

    def apply(self, kind, payload):
        self.serial += 1
        return self.service.execute(Command(type=kind, payload=payload, command_id=f"mix{self.serial}", project_id="mix",
                                            expected_revision=self.service.get_project("mix").revision))

    def samples(self, path):
        result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "2", "-ar", "48000", "-f", "f32le", "-"],
                                capture_output=True, check=True)
        return np.frombuffer(result.stdout, dtype="<f4").reshape(-1, 2)

    def rms(self, path, start=.3, end=.7):
        samples = self.samples(path)[round(start * 48000):round(end * 48000)]
        return np.sqrt(np.mean(samples ** 2, axis=0))

    def test_track_bus_master_chain_is_audible_persisted_and_undoable(self):
        renderer = RenderService()
        base = self.root / "base.mp4"
        renderer.render(self.service.get_project("mix"), str(base), quality="low")
        self.apply("audio.busAdd", {"busId": "dialog", "name": "Dialog"})
        self.apply("audio.mixer", {"trackId": "a", "mix": {"gainDb": -6, "busId": "dialog"}})
        self.apply("audio.mixer", {"busId": "dialog", "mix": {"gainDb": -3, "effects": [
            {"effectId": "cutvoke.fx.equalizer", "params": {"frequency": 1000, "gainDb": -6, "width": 1}}]}})
        self.apply("audio.mixer", {"master": True, "mix": {"gainDb": -3, "pan": -1}})
        edited = self.service.get_project("mix")
        output, preview = self.root / "mix.mp4", self.root / "preview.mp4"
        renderer.render(edited, str(output), quality="low")
        renderer.render_preview_window(edited, str(preview), 0, 1)
        expected = 10 ** (-18 / 20)
        self.assertAlmostEqual(float(self.rms(output)[0] / self.rms(base)[0]), expected, delta=.015)
        self.assertLess(float(self.rms(output)[1]), 0.00005)
        np.testing.assert_allclose(self.rms(preview), self.rms(output), atol=.001)
        self.assertEqual(Project.from_dict(edited.to_dict()).to_dict(), edited.to_dict())
        reopened = EditService(ProjectStore(str(self.root / "projects.sqlite")))
        self.assertEqual(reopened.load_project("mix").sequence.audio_buses, edited.sequence.audio_buses)
        reopened.close(); reopened.store.close()
        self.apply("history.undo", {})
        self.assertEqual(self.service.get_project("mix").sequence.master_audio, {})
        self.apply("history.redo", {})
        self.assertEqual(self.service.get_project("mix").sequence.master_audio["gainDb"], -3)

    def test_absolute_envelope_does_not_restart_in_later_preview_windows(self):
        self.apply("audio.mixer", {"trackId": "a", "mix": {"envelope": [
            {"time": 0, "gainDb": -18}, {"time": 3, "gainDb": 0}]}})
        output, preview = self.root / "env.mp4", self.root / "env-preview.mp4"
        renderer = RenderService()
        renderer.render(self.service.get_project("mix"), str(output), quality="low")
        renderer.render_preview_window(self.service.get_project("mix"), str(preview), 2, .9)
        self.assertGreater(float(self.rms(output, 2.2, 2.5)[0]), float(self.rms(output, .2, .5)[0]) * 3)
        np.testing.assert_allclose(self.rms(preview, .2, .5), self.rms(output, 2.2, 2.5), rtol=.05, atol=.001)

    def test_bezier_clip_volume_multiplies_static_and_track_gain(self):
        self.apply("clip.audio", {"clipId": "voice", "volume": .5})
        self.apply("audio.mixer", {"trackId": "a", "mix": {"gainDb": -6}})
        self.apply("clip.keyframe", {"clipId": "voice", "action": "batch", "keyframes": [
            {"param": "volume", "time": 0, "value": .1, "interpolation": "cubic-bezier", "bezier": [.7, 0, .9, 1]},
            {"param": "volume", "time": 3, "value": 1}]})
        output = self.root / "bezier.mp4"
        RenderService().render(self.service.get_project("mix"), str(output), quality="low")
        curve = self.service.get_project("mix").sequence.tracks[0].clips[0].keyframes["volume"]
        reference = float(self.rms(self.source, 1.4, 1.6)[0])
        expected = reference * .5 * 10 ** (-6 / 20) * evaluate(curve, Rational.of(3, 2))
        self.assertAlmostEqual(float(self.rms(output, 1.4, 1.6)[0]), expected, delta=.001)
        self.assertGreater(float(self.rms(output, 2.6, 2.8)[0]), float(self.rms(output, .2, .4)[0]) * 3)

    def test_invalid_effect_and_route_leave_revision_unchanged(self):
        revision = self.service.get_project("mix").revision
        for mix in ({"busId": "missing"}, {"gainDb": float("nan")}, {"envelope": [{"time": 1, "gainDb": 0}, {"time": 0, "gainDb": 0}]},
                    {"effects": [{"effectId": "cutvoke.fx.equalizer", "params": {"gainDb": 900}}]}):
            with self.assertRaises(EditError):
                self.apply("audio.mixer", {"trackId": "a", "mix": mix})
            self.assertEqual(self.service.get_project("mix").revision, revision)

    def test_range_delete_preserves_overlapping_fades_and_explicit_reset(self):
        self.apply("clip.audio", {"clipId": "voice", "fadeIn": 3, "fadeOut": 3})
        original = self.root / "original-fades.mp4"
        renderer = RenderService()
        renderer.render(self.service.get_project("mix"), str(original), quality="low")
        self.apply("sequence.deleteRange", {"start": 1, "end": 2})
        cut = self.root / "cut-fades.mp4"
        edited = self.service.get_project("mix")
        renderer.render(edited, str(cut), quality="low")
        np.testing.assert_allclose(self.rms(cut, .25, .65), self.rms(original, .25, .65), atol=.0003)
        np.testing.assert_allclose(self.rms(cut, 1.25, 1.65), self.rms(original, 2.25, 2.65), atol=.0005)
        right = edited.sequence.tracks[0].clips[-1]
        self.assertGreater(float(right.audio_fade_offset.to_fraction()), 0)
        self.apply("clip.audio", {"clipId": right.id, "fadeIn": .25})
        reset = self.service.get_project("mix").sequence.tracks[0].clips[-1]
        self.assertIsNone(reset.audio_fade_duration)
        self.assertEqual(reset.audio_fade_offset, Rational.of(0))

    def test_solo_and_bus_removal_restore_routing_with_undo(self):
        self.apply("audio.busAdd", {"busId": "b"})
        self.apply("audio.mixer", {"trackId": "a", "mix": {"busId": "b", "solo": True}})
        self.apply("audio.busRemove", {"busId": "b"})
        self.assertNotIn("busId", self.service.get_project("mix").sequence.tracks[0].audio_mix)
        self.apply("history.undo", {})
        self.assertEqual(self.service.get_project("mix").sequence.tracks[0].audio_mix["busId"], "b")

    def test_supported_dense_envelope_does_not_silently_evaluate_to_zero(self):
        self.apply("audio.mixer", {"trackId": "a", "mix": {"envelope": [
            {"time": index / 400, "gainDb": -6} for index in range(1000)]}})
        output = self.root / "dense-envelope.mp4"
        RenderService().render(self.service.get_project("mix"), str(output), quality="low")
        self.assertAlmostEqual(float(self.rms(output)[0] / self.rms(self.source)[0]), 10 ** (-6 / 20), delta=.02)

    def test_bus_solo_excludes_direct_track_from_actual_mix(self):
        second = self.root / "other-tone.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=2000:sample_rate=48000:duration=3", "-ac", "2", str(second)], check=True, capture_output=True)
        self.apply("track.add", {"kind": "audio", "trackId": "b"})
        self.apply("clip.insert", {"trackId": "b", "sourcePath": str(second),
            "timelineStart": {"num": "0", "den": "1"}, "timelineEnd": {"num": "3", "den": "1"}})
        self.apply("audio.busAdd", {"busId": "dialog"})
        self.apply("audio.mixer", {"trackId": "a", "mix": {"busId": "dialog"}})
        self.apply("audio.mixer", {"busId": "dialog", "mix": {"solo": True}})
        output = self.root / "solo.mp4"
        RenderService().render(self.service.get_project("mix"), str(output), quality="low")
        samples = self.samples(output)[24000:72000, 0]
        spectrum = np.abs(np.fft.rfft(samples))
        self.assertGreater(float(spectrum[1000]), float(spectrum[2000]) * 100)

    def test_record_uses_real_platform_devices_and_never_substitutes_tone(self):
        for platform, device, backend, input_name in (("linux", None, "pulse", "default"),
                ("linux", "alsa:hw:1,0", "alsa", "hw:1,0"), ("darwin", "2", "avfoundation", ":2")):
            with patch("cutvoke.rendering.audio_tools.os.name", "posix"), patch("sys.platform", platform), \
                 patch("cutvoke.rendering.audio_tools.subprocess.run") as run, patch("cutvoke.rendering.audio_tools._probe", return_value=(3, 48000, 2)):
                run.return_value = subprocess.CompletedProcess([], 0, "", "")
                result = record_audio(str(self.source), 3, device)
                args = run.call_args.args[0]
                self.assertEqual(args[args.index("-f") + 1], backend)
                self.assertEqual(args[args.index("-i") + 1], input_name)
                self.assertNotIn("lavfi", args)
                self.assertEqual(result["backend"], backend)
        with patch("cutvoke.rendering.audio_tools.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "device unavailable")):
            with self.assertRaisesRegex(RuntimeError, "record failed"):
                record_audio(str(self.root / "missing-device.wav"))
