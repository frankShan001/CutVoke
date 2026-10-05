"""Real source footage beyond a cut should drive outgoing transition motion."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from cutvoke.core.model import AssetReference, Clip, Project, Rational, Sequence, Track
from cutvoke.core.keyframes import Keyframe
from cutvoke.core.render import RenderService
from cutvoke.core.speed_curve import SpeedCurve


class TransitionSourceHandleTests(unittest.TestCase):
    @staticmethod
    def _write_sources(root: Path) -> tuple[Path, Path]:
        outgoing = root / "outgoing.mp4"
        incoming = root / "incoming.mp4"
        subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=red:s=64x48:r=10:d=1",
                "-f", "lavfi", "-i", "color=c=blue:s=64x48:r=10:d=1",
                "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
                "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "ultrafast",
                str(outgoing),
            ], check=True, capture_output=True)
        subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=green:s=64x48:r=10:d=1",
                "-an", "-c:v", "libx264", "-preset", "ultrafast", str(incoming),
            ], check=True, capture_output=True)
        return outgoing, incoming

    @staticmethod
    def _transition(clip_id: str, path: Path, start: Rational, end: Rational,
                    source_start: Rational = Rational.of(0),
                    effects: list[dict] | None = None,
                    speed_curve: SpeedCurve | None = None) -> Clip:
        return Clip(
            id=clip_id, asset_ref=AssetReference(clip_id, str(path)),
            timeline_start=start, timeline_end=end, source_start=source_start,
            effects=effects or [], speed_curve=speed_curve,
        )

    @staticmethod
    def _project(clips: list[Clip], additional_tracks: list[Track] | None = None) -> Project:
        tracks = [Track(id="video", kind="video", clips=clips)]
        tracks.extend(additional_tracks or [])
        return Project(
            schema_version="1.0", project_id="transition-handles", revision="1",
            sequence=Sequence(id="sequence", width=64, height=48,
                              fps=Rational.of(10), tracks=tracks),
        )

    @staticmethod
    def _render_and_sample(project: Project, path: Path,
                           times: list[float]) -> tuple[dict, list[tuple[int, int, int]]]:
        report = RenderService().render(project, str(path), quality="low")
        raw = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-vf", "fps=10", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ], check=True, capture_output=True).stdout
        frame_bytes = 64 * 48 * 3
        samples = []
        for time in times:
            index = round(time * 10)
            offset = index * frame_bytes + (24 * 64 + 32) * 3
            samples.append(tuple(raw[offset:offset + 3]))
        return report, samples

    @staticmethod
    def _sample_pixel(path: Path, time: float, x: int, y: int) -> tuple[int, int, int]:
        raw = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-vf", "fps=10", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ], check=True, capture_output=True).stdout
        frame_bytes = 64 * 48 * 3
        offset = round(time * 10) * frame_bytes + (y * 64 + x) * 3
        return tuple(raw[offset:offset + 3])

    @staticmethod
    def _assert_blue_green_midpoint(test: unittest.TestCase,
                                    pixel: tuple[int, int, int]) -> None:
        red, green, blue = pixel
        test.assertGreater(blue, red + 35, pixel)
        test.assertGreater(green, red + 35, pixel)

    def test_transition_uses_available_outgoing_source_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outgoing, incoming = self._write_sources(root)
            first = self._transition("outgoing", outgoing, Rational.of(0), Rational.of(1))
            second = self._transition(
                "incoming", incoming, Rational.of(1), Rational.of(2),
                effects=[{
                    "effectId": "cutvoke.transition.crossfade", "version": "1.0.0",
                    "params": {"duration": 0.4},
                }],
            )
            project = self._project([first, second])
            report, samples = self._render_and_sample(
                project, root / "transition.mp4", [0.9, 1.2, 1.4])
            self.assertAlmostEqual(report["duration"], 2.0, delta=0.12)
            self.assertGreater(samples[0][0], samples[0][1] + 100)
            # Held-frame fallback would stay red/green at the midpoint.
            self._assert_blue_green_midpoint(self, samples[1])
            self.assertGreater(samples[2][1], samples[2][0] + 100, samples[2])
            self.assertLess(samples[2][2], 35)

    def test_transition_handle_keeps_static_color_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outgoing, incoming = self._write_sources(root)
            first = self._transition(
                "outgoing", outgoing, Rational.of(0), Rational.of(1),
                effects=[{
                    "effectId": "cutvoke.transform", "version": "1.0.0",
                    "params": {"position": {"x": 8, "y": 0}},
                }, {
                    "effectId": "cutvoke.color", "version": "1.0.0",
                    "params": {"brightness": 0.05},
                }],
            )
            second = self._transition(
                "incoming", incoming, Rational.of(1), Rational.of(2),
                effects=[{
                    "effectId": "cutvoke.transition.crossfade", "version": "1.0.0",
                    "params": {"duration": 0.4},
                }],
            )
            output = root / "static-effect.mp4"
            report, samples = self._render_and_sample(
                self._project([first, second]), output, [1.2, 1.5])
            self.assertAlmostEqual(report["duration"], 2.0, delta=0.12)
            self._assert_blue_green_midpoint(self, samples[0])
            self.assertGreater(samples[1][1], samples[1][0] + 100, samples[1])
            left_edge = self._sample_pixel(output, 1.2, 0, 24)
            self.assertLess(left_edge[2], 35, left_edge)

    def test_transition_handle_holds_outgoing_keyframe_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outgoing, incoming = self._write_sources(root)
            first = self._transition(
                "outgoing", outgoing, Rational.of(0), Rational.of(1))
            first.keyframes = {"opacity": [
                Keyframe("opacity-start", Rational.of(0), 1.0),
                Keyframe("opacity-end", Rational.of(1), 0.4),
            ]}
            second = self._transition(
                "incoming", incoming, Rational.of(1), Rational.of(2),
                effects=[{
                    "effectId": "cutvoke.transition.crossfade", "version": "1.0.0",
                    "params": {"duration": 0.4},
                }],
            )
            _report, samples = self._render_and_sample(
                self._project([first, second]), root / "keyframe-handle.mp4", [1.2])
            red, green, blue = samples[0]
            self.assertGreater(blue, red + 15, samples[0])
            self.assertGreater(green, red + 25, samples[0])
            self.assertLess(blue, 90, samples[0])

    def test_transition_handle_continues_terminal_speed_curve(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outgoing, incoming = self._write_sources(root)
            curve = SpeedCurve.from_points(Rational.of(1), [
                {"at": Rational.of(0), "speed": Rational.of(3, 2)},
                {"at": Rational.of(1), "speed": Rational.of(3, 2)},
            ])
            seam = curve.timeline_duration
            first = self._transition(
                "outgoing", outgoing, Rational.of(0), seam, speed_curve=curve)
            second = self._transition(
                "incoming", incoming, seam, seam + Rational.of(1),
                effects=[{
                    "effectId": "cutvoke.transition.crossfade", "version": "1.0.0",
                    "params": {"duration": 0.3},
                }],
            )
            report, samples = self._render_and_sample(
                self._project([first, second]), root / "curve-handle.mp4",
                [0.8, 1.2])
            self.assertAlmostEqual(report["duration"],
                                   float((seam + Rational.of(1)).to_fraction()),
                                   delta=0.12)
            self._assert_blue_green_midpoint(self, samples[0])
            self.assertGreater(samples[1][1], samples[1][0] + 100, samples[1])

    def test_transition_handle_keeps_motion_interpolation_for_slow_motion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outgoing, incoming = self._write_sources(root)
            first = self._transition(
                "outgoing", outgoing, Rational.of(0), Rational.of(1),
                source_start=Rational.of(1, 2))
            first.speed = Rational.of(1, 2)
            first.frame_interpolation = "motion"
            second = self._transition(
                "incoming", incoming, Rational.of(1), Rational.of(2),
                effects=[{
                    "effectId": "cutvoke.transition.crossfade", "version": "1.0.0",
                    "params": {"duration": 0.4},
                }],
            )
            project = self._project([first, second])
            renderer = RenderService()
            graph, _inputs, _duration, _has_audio, _warnings, _caption_cwd = (
                renderer._compile(project.sequence, (0, 0)))
            # The selected slow-motion clip and its source-handle continuation
            # both need the same interpolation filter in the compiled graph.
            self.assertEqual(graph.count("minterpolate=fps=10/1"), 2, graph)

            report, samples = self._render_and_sample(
                project, root / "motion-interpolated-handle.mp4", [0.9, 1.2, 1.5])
            self.assertAlmostEqual(report["duration"], 2.0, delta=0.12)
            self.assertGreater(samples[0][0], samples[0][1] + 100)
            self._assert_blue_green_midpoint(self, samples[1])
            self.assertGreater(samples[2][1], samples[2][0] + 100, samples[2])

    def test_transition_handle_continues_static_video_fx(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outgoing, incoming = self._write_sources(root)
            first = self._transition(
                "outgoing", outgoing, Rational.of(0), Rational.of(1),
                effects=[{
                    "effectId": "cutvoke.fx.grayscale", "version": "1.0.0",
                    "params": {"strength": 1.0},
                }],
            )
            second = self._transition(
                "incoming", incoming, Rational.of(1), Rational.of(2),
                effects=[{
                    "effectId": "cutvoke.transition.crossfade", "version": "1.0.0",
                    "params": {"duration": 0.4},
                }],
            )
            project = self._project([first, second])
            graph, _inputs, _duration, _has_audio, _warnings, _caption_cwd = (
                RenderService()._compile(project.sequence, (0, 0)))
            self.assertEqual(graph.count("hue=s=0.000000"), 2, graph)

            report, samples = self._render_and_sample(
                project, root / "static-fx-handle.mp4", [1.0, 1.2, 1.4])
            self.assertAlmostEqual(report["duration"], 2.0, delta=0.12)
            red, green, blue = samples[0]
            self.assertLess(max(samples[0]) - min(samples[0]), 12, samples[0])
            self.assertLess((red + green + blue) / 3, 50, samples[0])
            self.assertGreater(samples[1][1], samples[1][0] + 35, samples[1])
            self.assertGreater(samples[2][1], samples[2][0] + 100, samples[2])

    def test_time_dependent_video_fx_keeps_transition_hold_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outgoing, incoming = self._write_sources(root)
            first = self._transition(
                "outgoing", outgoing, Rational.of(0), Rational.of(1),
                effects=[{"effectId": "cutvoke.fx.flicker", "version": "1.0.0",
                          "params": {"hz": 3.0}}],
            )
            second = self._transition(
                "incoming", incoming, Rational.of(1), Rational.of(2),
                effects=[{
                    "effectId": "cutvoke.transition.crossfade", "version": "1.0.0",
                    "params": {"duration": 0.4},
                }],
            )
            project = self._project([first, second])
            graph, _inputs, _duration, _has_audio, _warnings, _caption_cwd = (
                RenderService()._compile(project.sequence, (0, 0)))
            self.assertEqual(
                graph.count("eq=brightness=0.3*sin(2*PI*t*3.000):eval=frame"), 1, graph)

    def test_temporal_range_static_fx_keeps_transition_hold_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outgoing, incoming = self._write_sources(root)
            first = self._transition(
                "outgoing", outgoing, Rational.of(0), Rational.of(1),
                effects=[{
                    "effectId": "cutvoke.fx.grayscale", "version": "1.0.0",
                    "params": {"strength": 1.0},
                    "range": {
                        "start": {"num": "0", "den": "1"},
                        "end": {"num": "1", "den": "1"},
                    },
                }],
            )
            second = self._transition(
                "incoming", incoming, Rational.of(1), Rational.of(2),
                effects=[{
                    "effectId": "cutvoke.transition.crossfade", "version": "1.0.0",
                    "params": {"duration": 0.4},
                }],
            )
            graph, _inputs, _duration, _has_audio, _warnings, _caption_cwd = (
                RenderService()._compile(self._project([first, second]).sequence, (0, 0)))
            self.assertEqual(graph.count("hue=s=0.000000"), 1, graph)

    def test_slow_motion_transition_falls_back_when_source_context_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outgoing, incoming = self._write_sources(root)
            first = self._transition(
                "outgoing", outgoing, Rational.of(0), Rational.of(1),
                source_start=Rational.of(3, 2))
            first.speed = Rational.of(1, 2)
            first.frame_interpolation = "motion"
            second = self._transition(
                "incoming", incoming, Rational.of(1), Rational.of(2),
                effects=[{
                    "effectId": "cutvoke.transition.crossfade", "version": "1.0.0",
                    "params": {"duration": 0.4},
                }],
            )
            project = self._project([first, second])
            graph, _inputs, _duration, _has_audio, _warnings, _caption_cwd = (
                RenderService()._compile(project.sequence, (0, 0)))
            # The outgoing file ends at the selected range, so it cannot
            # provide the context required by minterpolate for a real handle.
            self.assertEqual(graph.count("minterpolate=fps=10/1"), 1, graph)

            report, samples = self._render_and_sample(
                project, root / "motion-handle-fallback.mp4", [0.9, 1.2, 1.5])
            self.assertAlmostEqual(report["duration"], 2.0, delta=0.12)
            self.assertLess(samples[0][0], 35)
            self.assertGreater(samples[0][2], samples[0][1] + 100)
            self._assert_blue_green_midpoint(self, samples[1])
            self.assertGreater(samples[2][1], samples[2][0] + 100, samples[2])

    def test_transition_handle_works_on_transparent_overlay_track(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outgoing, incoming = self._write_sources(root)
            base = root / "base.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=yellow:s=64x48:r=10:d=2",
                "-an", "-c:v", "libx264", "-preset", "ultrafast", str(base),
            ], check=True, capture_output=True)
            base_clip = self._transition(
                "base", base, Rational.of(0), Rational.of(2))
            overlay_clips = [
                self._transition("outgoing", outgoing, Rational.of(0), Rational.of(1)),
                self._transition(
                    "incoming", incoming, Rational.of(1), Rational.of(2),
                    effects=[{
                        "effectId": "cutvoke.transition.crossfade", "version": "1.0.0",
                        "params": {"duration": 0.4},
                    }],
                ),
            ]
            overlay = Track(id="overlay", kind="video", role="sticker",
                            clips=overlay_clips)
            report, samples = self._render_and_sample(
                self._project([base_clip], [overlay]),
                root / "transparent-overlay.mp4", [1.2, 1.5])
            self.assertAlmostEqual(report["duration"], 2.0, delta=0.12)
            self._assert_blue_green_midpoint(self, samples[0])
            self.assertGreater(samples[1][1], samples[1][0] + 100, samples[1])


if __name__ == "__main__":
    unittest.main()
