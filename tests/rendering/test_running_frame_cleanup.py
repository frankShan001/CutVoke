"""Deterministic running-frame cancellation contracts, not OS process-tree tests."""
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditService
from cutvoke.rendering.render import RenderCancelled, RenderService


class RunningFrameCleanupTests(unittest.TestCase):
    def exercise(self, already_exited=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"synthetic input; compiler and child are mocked")
            captions = root / "captions"
            captions.mkdir()
            script = root / "filter.txt"
            script.write_text("synthetic filter", encoding="utf-8")
            service = EditService()
            self.addCleanup(service.close)
            project = service.create_project("cleanup", width=96, height=64)
            project.sequence.tracks = [Track("v", "video", [Clip(
                "clip", AssetReference("asset", str(source)),
                R.of(0), R.of(1), R.of(0))])]
            # Explicit non-default path avoids probing a system executable.
            renderer = RenderService(str(root / "mock-ffmpeg"), gpu_effects=False)
            renderer._render_ctx.compile_origin = 0
            cancelled = threading.Event()
            process = Mock()
            process.poll.return_value = 0 if already_exited else None

            def communicate(timeout):
                if not cancelled.is_set():
                    cancelled.set()
                    raise subprocess.TimeoutExpired("synthetic child", timeout)
                return b"", b""

            process.communicate.side_effect = communicate
            with patch.object(renderer, "_compile", return_value=(
                "[v]null[outv]", [], 1, False, [], str(captions))), \
                    patch.object(renderer, "_gpu_arguments", return_value=[]), \
                    patch("cutvoke.rendering.render._filter_graph_arguments",
                          return_value=([], str(script))), \
                    patch("cutvoke.rendering.render.subprocess.Popen",
                          return_value=process) as launch:
                # Exercise the subprocess lifecycle, independently of preparation.
                with self.assertRaises(RenderCancelled):
                    RenderService.extract_frame.__wrapped__(
                        renderer, project, .2, str(root / "output.png"),
                        cancel_event=cancelled)
                launch.assert_called_once()
            self.assertFalse(captions.exists())
            self.assertFalse(script.exists())
            if already_exited:
                process.kill.assert_not_called()
                self.assertEqual(process.communicate.call_count, 1)
            else:
                process.kill.assert_called_once_with()
                self.assertEqual(process.communicate.call_count, 2)
                self.assertEqual(process.communicate.call_args.kwargs["timeout"], 10)

    def test_running_child_is_killed_and_drained_and_temporary_assets_removed(self):
        self.exercise()

    def test_exited_child_is_not_killed_and_temporary_assets_are_still_removed(self):
        self.exercise(already_exited=True)
