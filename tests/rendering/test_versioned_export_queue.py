"""The versioned export receipt reserves an output within one running queue."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import threading
import time
import unittest

from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.protocol import Command
from cutvoke.domain.rational import Rational
from cutvoke.editing.service import EditService
from cutvoke.interfaces.httpapi import HttpApi
from cutvoke.rendering.render import RenderService
from cutvoke.storage.export_queue import ExportQueue
from cutvoke.storage.store import ProjectStore


class VersionedExportQueueTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = ProjectStore(str(self.root / "projects.sqlite"))
        self.addCleanup(self.store.close)
        self.service = EditService(self.store)
        self.addCleanup(self.service.close)
        self.project = self.service.create_project("queue", width=96, height=64,
                                                   fps=Rational.of(24))
        self.renderer = RenderService(gpu_effects=False)
        self.queue = ExportQueue(self.store, self.service,
                                 render_factory=lambda: self.renderer, autostart=False)
        self.service._export_queue = self.queue

    def wait(self, job_id):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            job = self.queue.get(job_id)
            if job["status"] not in {"queued", "running"}:
                return job
            time.sleep(.02)
        self.fail(f"export did not terminate: {self.queue.get(job_id)}")

    def submitted(self, out, **options):
        return self.queue.submit(self.project, {"outPath": str(out), **options})

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg and ffprobe required")
    def test_http_exports_keep_both_videos_and_refuse_late_existing_target(self):
        source = self.root / "source.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=96x64:rate=24:duration=1", "-an",
                        "-threads", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        str(source)], check=True, timeout=20)
        self.project.sequence.tracks = [Track("v", "video", clips=[Clip(
            "c", AssetReference("a", str(source)), Rational.of(0),
            Rational.of(1), Rational.of(0))])]
        self.store.save(self.project)
        api = HttpApi(self.service, render=self.renderer, media_dir=str(self.root / "media"))
        self.addCleanup(api.close)
        route = f"/api/v1/projects/{self.project.project_id}/exports"

        def request(path, end, *, versioned=True, overwrite=True):
            status, receipt = api.handle("POST", route, {
                "outPath": str(path), "range": {"start": 0, "end": end},
                "versioned": versioned, "overwrite": overwrite, "quality": "low",
            })
            self.assertEqual(status, 202, receipt)
            return receipt

        def frame_count(path):
            return int(subprocess.check_output(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(path)],
                text=True, timeout=15).strip())

        pairs = []
        for overwrite in (False, True):
            suffix = str(int(overwrite))
            out, next_out = (self.root / f"take-{suffix}.mp4",
                             self.root / f"take-{suffix}_v2.mp4")
            first = request(out, .5, overwrite=overwrite)
            second = request(out, 1, overwrite=overwrite)
            pairs.extend([(first, out, 12), (second, next_out, 24)])

        occupied = self.root / "appeared-after-submit.mp4"
        protected = request(occupied, 1)
        occupied.write_bytes(b"file added after the receipt")
        plain = self.root / "overwrite-requested.mp4"
        ordinary = request(plain, .5, versioned=False)
        plain.write_bytes(b"file explicitly allowed to be replaced")
        self.queue.start()
        for receipt, path, count in pairs + [(ordinary, plain, 12)]:
            with self.subTest(path=path.name):
                terminal = self.wait(receipt["jobId"])
                self.assertEqual(terminal["status"], "succeeded", terminal)
                self.assertEqual(Path(receipt["outPath"]), path)
                self.assertEqual(Path(self.store.get_export_job(receipt["jobId"])["outPath"]), path)
                self.assertEqual(frame_count(path), count)
        self.assertEqual(self.wait(protected["jobId"])["status"], "failed")
        self.assertEqual(occupied.read_bytes(), b"file added after the receipt")

    def test_concurrent_queue_reservations_include_plain_jobs_and_drop_cancelled(self):
        out = self.root / "series.mp4"
        existing = self.root / "series_v2.mp4"
        existing.write_bytes(b"existing")
        plain = self.submitted(out)
        gate = threading.Barrier(4)

        def racing_submit(_):
            gate.wait(timeout=5)
            return self.submitted(out, versioned=True)

        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = list(pool.map(racing_submit, range(4)))
        self.assertEqual({Path(job["outPath"]).name for job in jobs},
                         {f"series_v{i}.mp4" for i in range(3, 7)})
        self.assertTrue(self.queue.cancel(plain["jobId"])["cancelled"])
        reused = self.submitted(out, versioned=True)
        self.assertEqual(Path(reused["outPath"]), out)
        self.assertEqual(existing.read_bytes(), b"existing")

    def test_running_output_reserved_and_command_replay_keeps_original_path(self):
        entered, release = threading.Event(), threading.Event()

        class ParkedRender:
            def render(self, project, out_path, **options):
                entered.set()
                if not release.wait(timeout=10):
                    raise AssertionError("test did not release renderer")
                Path(out_path).write_bytes(b"rendered")
                return {"output_path": out_path}

        self.renderer = ParkedRender()
        out = self.root / "replay.mp4"
        command = Command.from_dict({
            "commandId": "queued-export", "projectId": self.project.project_id,
            "expectedRevision": self.project.revision, "type": "export.enqueue",
            "payload": {"outPath": str(out), "versioned": True},
        })
        first = self.service.execute(command)
        first_id = first.changed_entities[0]["jobId"]
        self.queue.start()
        try:
            self.assertTrue(entered.wait(timeout=5))
            self.assertFalse(out.exists())
            self.assertEqual(self.queue.get(first_id)["status"], "running")
            self.assertEqual(self.service.execute(command).to_dict(), first.to_dict())
            second = self.submitted(out, versioned=True)
            self.assertEqual(Path(second["outPath"]), self.root / "replay_v2.mp4")
            self.assertEqual(len(self.store.list_export_jobs(self.project.project_id)), 2)
            self.assertEqual(self.service.get_project(self.project.project_id).revision,
                             self.project.revision)
        finally:
            release.set()
        self.assertEqual(self.wait(first_id)["status"], "succeeded")
        self.assertEqual(self.wait(second["jobId"])["status"], "succeeded")
