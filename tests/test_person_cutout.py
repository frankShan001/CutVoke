"""Local cutout jobs apply to one video clip as a single undoable edit."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cutvoke.core.httpapi import HttpApi
from cutvoke.core.person_cutout import (
    PersonCutoutCancelled,
    PersonCutoutJobManager,
    _select_person_component,
    _select_prompted_person,
    _prompt_reacquisition_allowed,
    _track_prompt_point,
    _track_person_component,
)
from cutvoke.core.sam2_worker import _propagate_in_both_directions, _write_progress
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.service import EditService


def _edit(service: EditService, command_type: str, payload: dict, index: int):
    project = service.get_project("person-cutout")
    return service.execute(Command(
        type=command_type,
        payload=payload,
        command_id=f"person-cutout-{index}",
        project_id="person-cutout",
        expected_revision=project.revision,
        actor=Actor("human", "person-cutout-test"),
    ))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class PersonCutoutApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.mp4"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=1:r=25",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(self.source),
        ], check=True, capture_output=True)
        self.service = EditService()
        self.service.create_project("person-cutout", width=320, height=180)
        _edit(self.service, "track.add", {"trackId": "video", "kind": "video"}, 1)
        _edit(self.service, "clip.insert", {
            "trackId": "video", "clipId": "clip-a", "sourcePath": str(self.source),
            "timelineStart": {"num": "0", "den": "1"},
            "timelineEnd": {"num": "1", "den": "1"},
        }, 2)
        self.media_dir = self.root / "media"
        self.api = HttpApi(self.service, media_dir=str(self.media_dir))

    def tearDown(self):
        self.api.close()
        self.temporary.cleanup()

    def _make_cutout(self) -> Path:
        target_dir = self.media_dir / ".person-cutouts"
        target_dir.mkdir(parents=True)
        output = target_dir / "generated.mov"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=320x180:d=1:r=25",
            "-c:v", "qtrle", "-pix_fmt", "argb", str(output),
        ], check=True, capture_output=True)
        return output

    def test_sam2_progress_update_retries_a_transient_windows_file_lock(self):
        import json
        import os

        path = self.root / "progress.json"
        replace = os.replace
        attempts = []

        def replace_after_transient_lock(source, target):
            attempts.append((source, target))
            if len(attempts) == 1:
                raise PermissionError("transient Windows file lock")
            replace(source, target)

        with patch("cutvoke.core.sam2_worker.os.replace",
                   side_effect=replace_after_transient_lock), \
             patch("cutvoke.core.sam2_worker.time.sleep"):
            _write_progress(path, 3, 12, "cuda")

        self.assertEqual(len(attempts), 2)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                         {"completed": 3, "total": 12, "device": "cuda"})

    def test_sam2_propagates_from_a_mid_clip_prompt_to_every_frame(self):
        class Predictor:
            def __init__(self):
                self.calls = []

            def propagate_in_video(self, state, *, start_frame_idx,
                                   max_frame_num_to_track, reverse):
                self.calls.append((start_frame_idx, max_frame_num_to_track, reverse))
                step = -1 if reverse else 1
                end = start_frame_idx + step * max_frame_num_to_track
                stop = end + step
                for frame in range(start_frame_idx, stop, step):
                    yield frame, [1], frame

        predictor = Predictor()
        results = list(_propagate_in_both_directions(
            predictor, object(), selection_frame=2, frame_count=5))

        self.assertEqual(sorted(frame for frame, _ids, _mask in results), list(range(5)))
        self.assertEqual(predictor.calls, [(2, 2, False), (2, 2, True)])

    def test_sam2_rejects_a_direction_that_does_not_cover_its_frame_range(self):
        class Predictor:
            def propagate_in_video(self, state, *, start_frame_idx,
                                   max_frame_num_to_track, reverse):
                yield start_frame_idx, [1], 0

        with self.assertRaisesRegex(RuntimeError, "未生成第 0 帧"):
            list(_propagate_in_both_directions(
                Predictor(), object(), selection_frame=2, frame_count=5))

    def test_apply_route_swaps_one_source_and_undo_restores_original(self):
        from cutvoke.core.speech_recognition import source_signature

        output = self._make_cutout()
        project = self.service.get_project("person-cutout")
        clip = project.sequence.tracks[0].clips[0]
        job_id = "person_test_apply"
        self.api._person_cutout_jobs._jobs[job_id] = {
            "jobId": job_id,
            "projectId": project.project_id,
            "clipId": clip.id,
            "sourceSignature": source_signature(clip),
            "status": "completed",
            "phase": "透明片段已生成",
            "progress": 100,
            "error": "",
            "result": {"outputPath": str(output), "alpha": True},
        }

        status, result = self.api.handle(
            "POST", f"/api/v1/projects/{project.project_id}/person-cutout-jobs/{job_id}/apply", {})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["job"]["status"], "applied")
        applied_clip = self.service.get_project(project.project_id).sequence.tracks[0].clips[0]
        self.assertEqual(applied_clip.asset_ref.source_path, str(output.resolve()))

        _edit(self.service, "history.undo", {}, 3)
        restored_clip = self.service.get_project(project.project_id).sequence.tracks[0].clips[0]
        self.assertEqual(restored_clip.asset_ref.source_path, str(self.source.resolve()))

    def test_apply_rejects_a_stale_source_signature(self):
        from cutvoke.core.speech_recognition import source_signature

        output = self._make_cutout()
        project = self.service.get_project("person-cutout")
        clip = project.sequence.tracks[0].clips[0]
        job_id = "person_test_stale"
        self.api._person_cutout_jobs._jobs[job_id] = {
            "jobId": job_id, "projectId": project.project_id, "clipId": clip.id,
            "sourceSignature": "stale", "status": "completed", "phase": "完成",
            "progress": 100, "error": "", "result": {"outputPath": str(output)},
        }
        status, result = self.api.handle(
            "POST", f"/api/v1/projects/{project.project_id}/person-cutout-jobs/{job_id}/apply", {})
        self.assertEqual(status, 400)
        self.assertIn("发生变化", result["error"]["message"])
        self.assertEqual(self.api._person_cutout_jobs.get(job_id)["status"], "completed")
        self.assertEqual(self.api._person_cutout_jobs.get(job_id)["error"],
                         "原片段在抠像期间发生变化；请重新生成抠像结果")
        self.assertEqual(self.service.get_project(project.project_id)
                         .sequence.tracks[0].clips[0].asset_ref.source_path,
                         str(self.source.resolve()))

    def test_running_job_can_be_cancelled_and_discards_partial_output(self):
        entered = threading.Event()
        destination = self.media_dir / ".person-cutouts"

        def wait_until_cancelled(source, output, **kwargs):
            entered.set()
            while not kwargs["cancelled"]():
                time.sleep(0.005)
            raise PersonCutoutCancelled("人物抠像已取消")

        with patch("cutvoke.core.person_cutout.availability",
                   return_value={"ready": True}), \
             patch("cutvoke.core.person_cutout.generate_person_cutout", wait_until_cancelled):
            manager = PersonCutoutJobManager()
            try:
                project = self.service.get_project("person-cutout")
                job = manager.submit(project, "clip-a", self.media_dir)
                self.assertTrue(entered.wait(timeout=2))
                self.assertEqual(manager.cancel(job["jobId"])["status"], "cancelled")
                deadline = time.time() + 2
                while time.time() < deadline:
                    final = manager.get(job["jobId"])
                    if final["status"] == "cancelled":
                        break
                    time.sleep(0.01)
                self.assertEqual(manager.get(job["jobId"])["status"], "cancelled")
                self.assertEqual(list(destination.glob("*.mov")), [])
            finally:
                manager.close()

    def test_preview_route_returns_a_frame_from_the_selected_clip_source_in(self):
        status, result = self.api.handle(
            "GET", f"/api/v1/projects/{self.service.get_project('person-cutout').project_id}/person-cutout-preview",
            {"clipId": "clip-a", "atSeconds": 0.4},
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["__png__"].startswith(b"\x89PNG\r\n\x1a\n"))

    def test_preview_route_rejects_a_time_outside_the_selected_clip_source_range(self):
        status, result = self.api.handle(
            "GET", "/api/v1/projects/person-cutout/person-cutout-preview",
            {"clipId": "clip-a", "atSeconds": 1.0},
        )
        self.assertEqual(status, 422)
        self.assertIn("源素材范围", result["error"]["message"])

    def test_single_person_selection_tracks_the_clicked_component(self):
        import cv2
        import numpy as np

        first = np.zeros((120, 200), dtype=np.float32)
        first[20:100, 20:45] = 0.9
        first[8:112, 130:185] = 0.9
        chosen = _select_person_component(first, cv2, np, (0.16, 0.5))
        self.assertIsNotNone(chosen)
        selected_confidence, selected_mask, center, count = chosen
        self.assertEqual(count, 2)
        self.assertTrue(selected_mask[60, 30])
        self.assertFalse(selected_mask[60, 150])
        self.assertEqual(float(selected_confidence[60, 150]), 0.0)

        second = np.zeros((120, 200), dtype=np.float32)
        second[20:100, 34:59] = 0.9
        second[8:112, 128:183] = 0.9
        tracked = _track_person_component(second, cv2, np, selected_mask, center)
        self.assertIsNotNone(tracked)
        tracked_confidence, tracked_mask, _, count = tracked
        self.assertEqual(count, 2)
        self.assertTrue(tracked_mask[60, 45])
        self.assertFalse(tracked_mask[60, 150])
        self.assertEqual(float(tracked_confidence[60, 150]), 0.0)

    def test_selected_person_route_rejects_invalid_click_coordinates(self):
        with patch("cutvoke.core.person_cutout.availability",
                   return_value={"ready": True}):
            status, result = self.api.handle(
                "POST",
                f"/api/v1/projects/person-cutout/person-cutout-jobs",
                {"clipId": "clip-a", "selectionMode": "selected",
                 "selectionPoint": {"x": 1.1, "y": 0.5},
                 "selectionAtSeconds": 0},
            )
        self.assertEqual(status, 400)
        self.assertIn("提示点", result["error"]["message"])
        self.assertEqual(self.api._person_cutout_jobs._jobs, {})

    def test_magic_touch_route_requires_prepared_interactive_model(self):
        with patch("cutvoke.core.person_cutout.availability",
                   return_value={"ready": True, "interactiveModelReady": False}):
            status, result = self.api.handle(
                "POST",
                f"/api/v1/projects/person-cutout/person-cutout-jobs",
                {"clipId": "clip-a", "selectionMode": "selected",
                 "selectionPoint": {"x": 0.5, "y": 0.5},
                 "selectionAtSeconds": 0, "trackingMode": "magic_touch"},
            )
        self.assertEqual(status, 400)
        self.assertIn("--interactive", result["error"]["message"])
        self.assertEqual(self.api._person_cutout_jobs._jobs, {})

    def test_selected_job_records_magic_touch_tracking_mode(self):
        with patch("cutvoke.core.person_cutout.availability",
                   return_value={"ready": True, "interactiveModelReady": True}), \
             patch.object(PersonCutoutJobManager, "_run", autospec=True):
            status, job = self.api.handle(
                "POST",
                f"/api/v1/projects/person-cutout/person-cutout-jobs",
                {"clipId": "clip-a", "selectionMode": "selected",
                 "selectionPoint": {"x": 0.5, "y": 0.5},
                 "selectionAtSeconds": 0, "trackingMode": "magic_touch"},
            )
        self.assertEqual(status, 202, job)
        self.assertEqual(job["selectionMode"], "selected_person")
        self.assertEqual(job["trackingMode"], "magic_touch")

    def test_sam2_route_preserves_positive_and_exclusion_prompt_points(self):
        points = [
            {"x": 0.42, "y": 0.55, "label": 1},
            {"x": 0.62, "y": 0.54, "label": 0},
        ]
        with patch("cutvoke.core.person_cutout.availability",
                   return_value={"ready": True, "sam2Ready": True}), \
             patch.object(PersonCutoutJobManager, "_run", autospec=True):
            status, job = self.api.handle(
                "POST", "/api/v1/projects/person-cutout/person-cutout-jobs",
                {"clipId": "clip-a", "selectionMode": "selected",
                 "trackingMode": "sam2", "selectionPoint": points[0],
                 "selectionPoints": points, "selectionAtSeconds": 0},
            )
        self.assertEqual(status, 202, job)
        self.assertEqual(job["trackingMode"], "sam2")
        self.assertEqual(job["selectionPoint"], [0.42, 0.55])
        self.assertEqual(job["selectionPoints"], [[0.42, 0.55, 1], [0.62, 0.54, 0]])

    def test_sam2_route_records_ordered_correction_prompt_frames(self):
        prompts = [
            {"atSeconds": 0.1, "points": [{"x": 0.42, "y": 0.55, "label": 1}]},
            {"atSeconds": 0.8, "points": [{"x": 0.31, "y": 0.52, "label": 1},
                                           {"x": 0.62, "y": 0.54, "label": 0}]},
        ]
        with patch("cutvoke.core.person_cutout.availability",
                   return_value={"ready": True, "sam2Ready": True}), \
             patch.object(PersonCutoutJobManager, "_run", autospec=True):
            status, job = self.api.handle(
                "POST", "/api/v1/projects/person-cutout/person-cutout-jobs",
                {"clipId": "clip-a", "selectionMode": "selected", "trackingMode": "sam2",
                 "selectionPrompts": prompts},
            )
        self.assertEqual(status, 202, job)
        self.assertEqual(job["selectionAtSeconds"], 0.1)
        self.assertEqual(job["selectionPrompts"], [
            {"atSeconds": 0.1, "points": [[0.42, 0.55, 1]]},
            {"atSeconds": 0.8, "points": [[0.31, 0.52, 1], [0.62, 0.54, 0]]},
        ])

    def test_sam2_route_rejects_correction_prompt_outside_clip_source_range(self):
        with patch("cutvoke.core.person_cutout.availability",
                   return_value={"ready": True, "sam2Ready": True}), \
             patch.object(PersonCutoutJobManager, "_run", autospec=True):
            status, result = self.api.handle(
                "POST", "/api/v1/projects/person-cutout/person-cutout-jobs",
                {"clipId": "clip-a", "selectionMode": "selected", "trackingMode": "sam2",
                 "selectionPrompts": [{"atSeconds": 1.0,
                                       "points": [{"x": 0.42, "y": 0.55, "label": 1}]}]},
            )
        self.assertEqual(status, 400)
        self.assertIn("片段的源素材范围", result["error"]["message"])
        self.assertEqual(self.api._person_cutout_jobs._jobs, {})

    def test_sam2_route_requires_local_runtime_and_checkpoint(self):
        with patch("cutvoke.core.person_cutout.availability",
                   return_value={"ready": True, "sam2Ready": False}), \
             patch.object(PersonCutoutJobManager, "_run", autospec=True):
            status, result = self.api.handle(
                "POST", "/api/v1/projects/person-cutout/person-cutout-jobs",
                {"clipId": "clip-a", "selectionMode": "selected",
                 "trackingMode": "sam2", "selectionPoint": {"x": 0.5, "y": 0.5},
                 "selectionAtSeconds": 0},
            )
        self.assertEqual(status, 400)
        self.assertIn("--sam2", result["error"]["message"])

    def test_magic_touch_rejects_all_people_mode(self):
        with patch("cutvoke.core.person_cutout.availability",
                   return_value={"ready": True, "interactiveModelReady": True}):
            manager = PersonCutoutJobManager()
            try:
                with self.assertRaisesRegex(RuntimeError, "仅适用于点选人物"):
                    manager.submit(
                        self.service.get_project("person-cutout"), "clip-a",
                        self.media_dir, tracking_mode="magic_touch")
                self.assertEqual(manager._jobs, {})
            finally:
                manager.close()

    def test_magic_touch_prompt_point_tracks_local_optical_flow(self):
        import cv2
        import numpy as np

        rng = np.random.default_rng(4)
        previous = np.zeros((120, 200), dtype=np.uint8)
        previous[20:100, 20:90] = rng.integers(32, 230, (80, 70), dtype=np.uint8)
        matrix = np.float32([[1, 0, 8], [0, 1, 4]])
        current = cv2.warpAffine(previous, matrix, (200, 120))
        person_mask = np.zeros((120, 200), dtype=bool)
        person_mask[20:100, 20:90] = True

        tracked = _track_prompt_point(
            previous, current, person_mask, (0.27, 0.5), cv2, np)

        self.assertIsNotNone(tracked)
        self.assertAlmostEqual(tracked[0], 0.27 + 8 / 199, delta=0.015)
        self.assertAlmostEqual(tracked[1], 0.5 + 4 / 119, delta=0.015)

    def test_magic_touch_retries_short_occlusion_then_expires_safely(self):
        import cv2
        import numpy as np

        rng = np.random.default_rng(4)
        previous = np.zeros((120, 200), dtype=np.uint8)
        previous[20:100, 20:90] = rng.integers(32, 230, (80, 70), dtype=np.uint8)
        mask = np.zeros((120, 200), dtype=bool)
        mask[20:100, 20:90] = True
        occluded = np.zeros_like(previous)
        reappeared = cv2.warpAffine(
            previous, np.float32([[1, 0, 8], [0, 1, 4]]), (200, 120))

        self.assertIsNone(_track_prompt_point(
            previous, occluded, mask, (0.27, 0.5), cv2, np))
        self.assertTrue(_prompt_reacquisition_allowed(4, 2, 25))
        self.assertFalse(_prompt_reacquisition_allowed(8, 2, 25))
        tracked = _track_prompt_point(
            previous, reappeared, mask, (0.27, 0.5), cv2, np)

        self.assertIsNotNone(tracked)
        self.assertAlmostEqual(tracked[0], 0.27 + 8 / 199, delta=0.015)
        self.assertAlmostEqual(tracked[1], 0.5 + 4 / 119, delta=0.015)

    def test_magic_touch_prompt_keeps_only_clicked_person_pixels(self):
        import cv2
        import numpy as np

        prompted = np.zeros((120, 200), dtype=np.float32)
        prompted[20:100, 20:45] = 0.9
        prompted[8:112, 130:185] = 0.9
        person = prompted.copy()
        selected = _select_prompted_person(prompted, person, (0.16, 0.5), cv2, np)

        self.assertIsNotNone(selected)
        confidence, mask, _center, component_count = selected
        self.assertEqual(component_count, 2)
        self.assertTrue(mask[60, 30])
        self.assertFalse(mask[60, 150])
        self.assertEqual(float(confidence[60, 150]), 0.0)


if __name__ == "__main__":
    unittest.main()
