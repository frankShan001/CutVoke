"""Export-scoped SubRip must use verified picture time, never live project captions."""

import copy
from fractions import Fraction
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

from cutvoke.domain.captions import parse_srt
from cutvoke.domain.model import (
    CURRENT_PROJECT_SCHEMA, AssetReference, Caption, Clip, Project, Sequence, Track,
)
from cutvoke.domain.rational import Rational
from cutvoke.editing.service import EditService
from cutvoke.interfaces.httpapi import HttpApi, build_handler
from cutvoke.interfaces.security import SessionToken
from cutvoke.rendering.render import RenderService
from cutvoke.storage.store import ProjectStore


RATE = Fraction(30000, 1001)
WIDTH, HEIGHT = 160, 96


def _rational(value):
    fraction = Fraction(value)
    return Rational.of(fraction.numerator, fraction.denominator)


def _frame_time(frame):
    return _rational(Fraction(frame) / RATE)


def _ffmpeg(*args, data=None):
    return subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", *map(str, args)],
        input=data, capture_output=True, check=True, timeout=45,
    ).stdout


def _frame_ids(path):
    data = _ffmpeg("-i", path, "-map", "0:v:0", "-fps_mode", "passthrough",
                   "-threads", 1, "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1")
    size = WIDTH * HEIGHT
    assert data and len(data) % size == 0
    return [sum((data[offset + 40 * WIDTH + bit * 16 + 8] > 128) << bit
                for bit in range(9)) for offset in range(0, len(data), size)]


def _decode(path, kind, fmt, *options):
    return _ffmpeg("-i", path, "-map", f"0:{kind}:0", "-threads", 1, *options,
                   "-f", fmt, "pipe:1")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires FFmpeg")
class ExportCaptionSidecarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "frames.mp4"
        data = []
        for frame in range(374):
            row = b"".join(bytes([224 if frame & (1 << bit) else 32]) * 16
                           for bit in range(9)) + bytes([128]) * 16
            data.append(row * HEIGHT)
        _ffmpeg("-f", "rawvideo", "-pixel_format", "gray", "-video_size", f"{WIDTH}x{HEIGHT}",
                "-framerate", str(RATE), "-i", "pipe:0", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", "-threads", 1, "-g", 30, "-bf", 2, self.source,
                data=b"".join(data))
        self.assertEqual(_frame_ids(self.source), list(range(374)))
        self.audio = self.root / "clock.wav"
        _ffmpeg("-f", "lavfi", "-i", "aevalsrc=0.22*sin(2*PI*(260*t+111*t*t)):s=48000:d=13",
                "-c:a", "pcm_s16le", self.audio)
        self.database = self.root / "projects.sqlite"
        self.store = ProjectStore(str(self.database))
        self.service = EditService(store=self.store)
        self.api = HttpApi(self.service, render=RenderService(gpu_effects=False),
                           media_dir=str(self.root / "media"))
        self.addCleanup(self.service.close)
        self.addCleanup(self.api.close)

    def project(self, project_id, dense=False):
        asset = AssetReference("numbered", str(self.source))
        slices = ([(i, i + 12) for i in range(0, 360, 12)] if dense else [(0, 360)])
        picture = Track("picture", "video", clips=[
            Clip(f"picture-{i}", asset, _frame_time(i), _frame_time(j), _frame_time(i))
            for i, j in slices
        ])
        sound = Track("sound", "audio", clips=[
            Clip("sound-0", AssetReference("tone", str(self.audio)), _rational(0),
                 _frame_time(360), _rational(0))
        ])
        # Valid title graphics enter ASS but are not authored subtitle cues.
        title = Track("titles", "text", clips=[
            Clip("visible-title", asset, _rational(0), _rational(.2), _rational(.2),
                 effects=[{"effectId": "cutvoke.text", "instanceId": "title-effect",
                           "params": {"content": "Visible graphic only", "fontSize": 9, "x": .2, "y": .15}}])
        ])
        seq = Sequence("s", WIDTH, HEIGHT, _rational(RATE), tracks=[picture, sound, title])
        seq.captions = [
            Caption("before", "before", _rational(0), _frame_time(4), fontSize=9, y=.85),
            Caption("straddle", "中文 / café ☀", _rational("0.128"), _rational("0.23346666667"),
                    fontSize=9, y=.85),
            Caption("later", "検証", _rational("7.5"), _rational("7.6"), fontSize=9, y=.85),
            Caption("last", "尾", _rational("9.01"), _rational("9.4"), fontSize=9, y=.85),
            Caption("beyond-request", "past exact request", _rational("9.171"), _rational("9.173"),
                    fontSize=9, y=.85),
        ]
        return Project(schema_version=CURRENT_PROJECT_SCHEMA, project_id=project_id,
                       revision="0", name=project_id, sequence=seq)

    def submit(self, project, name, frames=None, opted=True, *, extra=None, command=False):
        target = self.root / (name + ".mp4")
        options = {"outPath": str(target), "quality": "low", "overwrite": True}
        if opted:
            options["captionSidecar"] = True
        if frames:
            options["range"] = dict(zip(("start", "end"),
                                        (float(Fraction(frame) / RATE) for frame in frames)))
        options.update(extra or {})
        route = f"/api/v1/projects/{project.project_id}"
        if command:
            status, response = self.api.handle("POST", route + "/commands", {
                "type": "export.enqueue", "commandId": "selected-caption-export",
                "expectedRevision": self.service.get_project(project.project_id).revision,
                "actor": {"kind": "human", "id": "test"}, "payload": options,
            })
            self.assertEqual(status, 200, response)
            job_id = response["changedEntities"][0]["jobId"]
        else:
            status, response = self.api.handle("POST", route + "/exports", options)
            self.assertEqual(status, 202, response)
            job_id = response["jobId"]
        return job_id, target, route + f"/exports/{job_id}/captions.srt"

    def wait_job(self, job_id, expected="succeeded"):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            job = self.store.get_export_job(job_id)
            if job["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                self.assertEqual(job["status"], expected, job.get("error"))
                return job
            time.sleep(.03)
        self.fail("fixture export did not finish")

    def read_cues(self, endpoint, api=None):
        status, response = (api or self.api).handle("GET", endpoint, {})
        self.assertEqual(status, 200, response)
        return {c.text: (c.start.to_fraction(), c.end.to_fraction())
                for c in parse_srt(response["__srt_attachment__"])}

    def test_direct_and_dense_off_grid_picture_and_separate_audio(self):
        direct = self.project("direct")
        dense = self.project("dense", dense=True)
        self.service.import_project(direct, project_id="direct")
        self.service.import_project(dense, project_id="dense")
        for project, name, window, frames in (
            (direct, "short", ("3.25", "90.75"), range(4, 91)),
            (dense, "dense", ("3.25", "274.75"), range(4, 275)),
        ):
            with self.subTest(name=name):
                jid, path, endpoint = self.submit(project, name, window)
                job = self.wait_job(jid)
                self.assertEqual(_frame_ids(path), list(frames))
                self.assertEqual(job["result"]["audioVerification"]["status"], "passed")
                # Independently decode the known continuous chirp from the
                # source, rather than treating the renderer's report as truth.
                import numpy as np
                def acoustic_start(file, seconds=None):
                    seek = [] if seconds is None else ["-ss", f"{float(seconds):.9f}"]
                    data = _ffmpeg(*seek, "-i", file, "-map", "0:a:0", "-t", "0.5",
                                   "-ac", 1, "-ar", 48000, "-f", "f32le", "pipe:1")
                    return np.frombuffer(data, dtype="<f4")
                observed = acoustic_start(path)
                audio_ref = acoustic_start(self.audio, Fraction("3.25") / RATE)
                picture_ref = acoustic_start(self.audio, 4 / RATE)
                dot = lambda a, b: float(np.dot(a, b) / np.linalg.norm(a) / np.linalg.norm(b))
                self.assertGreater(dot(observed, audio_ref), .98)
                self.assertLess(abs(dot(observed, picture_ref)), .5)
                self.assertEqual(job["result"]["captionSidecarStatus"], "available")
                encoded = self.api.handle("GET", f"/api/v1/projects/{project.project_id}/exports", {})[1]
                self.assertNotIn("café", repr(encoded))
                self.assertFalse(list(self.root.glob("*.srt")))
                self.assertNotIn("Visible graphic only", self.read_cues(endpoint))
                cues = self.read_cues(endpoint)
                self.assertNotIn("before", cues)
                self.assertNotIn("past exact request", cues)
                self.assertEqual(cues["中文 / café ☀"], (0, Fraction(".100")))
                if name == "dense":
                    self.assertEqual(job["result"]["renderStrategy"], "lossless-windows")
                    self.assertEqual(job["result"]["audioStrategy"], "continuous-pcm")
                    self.assertGreater(job["result"]["windowCount"], 1)
                    self.assertEqual(cues["尾"][1], Fraction("9.034"))
                    self.assertIn("検証", cues)
                if name == "short":
                    did, default, legacy_endpoint = self.submit(project, "default", window, opted=False)
                    self.assertNotIn("captionSidecarStatus", self.wait_job(did)["result"])
                    self.assertEqual(self.api.handle("GET", legacy_endpoint, {})[0], 404)
                    self.assertEqual(_decode(path, "a", "s16le"), _decode(default, "a", "s16le"))
                    self.assertEqual(_decode(path, "v", "rawvideo"), _decode(default, "v", "rawvideo"))
        status, standalone = self.api.handle("GET", "/api/v1/projects/direct/captions.srt", {})
        self.assertEqual(status, 200)
        self.assertIn("00:00:07,500", standalone["__text__"])
        self.assertIn("past exact request", standalone["__text__"])
        jid, path, endpoint = self.submit(direct, "whole-default", opted=False)
        self.assertNotIn("captionSidecarStatus", self.wait_job(jid)["result"])
        self.assertEqual(_frame_ids(path), list(range(360)))
        self.assertEqual(self.api.handle("GET", endpoint, {})[0], 404)

    def test_direct_with_late_origin_cut_and_retime(self):
        project = self.project("late")
        sequence = project.sequence
        asset = sequence.tracks[0].clips[0].asset_ref
        sequence.tracks = [Track("picture", "video", clips=[
            Clip("left", asset, _frame_time(100), _frame_time(145), _frame_time(0),
                 speed=_rational(2)),
            Clip("right", asset, _frame_time(145), _frame_time(202), _frame_time(240)),
        ])]
        sequence.captions = [
            Caption("after-cut", "clip cut", _frame_time(145), _frame_time(151), fontSize=9, y=.85),
        ]
        self.service.import_project(project, project_id="late")
        jid, path, endpoint = self.submit(project, "late-range", ("101.25", "195.75"))
        job = self.wait_job(jid)
        ids = _frame_ids(path)
        self.assertEqual(len(ids), 94)
        absolute = range(102, 196)
        for frame, actual in zip(absolute, ids):
            source = 2*(frame-100) if frame<145 else 240+(frame-145)
            upper = source+1 if frame<145 else source
            self.assertGreaterEqual(actual, source, (frame,actual,source))
            self.assertLessEqual(actual, upper, (frame,actual,upper))
        self.assertEqual(job["result"]["captionSidecar"]["startFrame"], 102)
        start, end = self.read_cues(endpoint)["clip cut"]
        self.assertEqual((start, end), (Fraction("1.435"), Fraction("1.635")))

        # An independent render omits the cue, not the picture. Pixel differences
        # near the bottom can only be authored text, not media/retiming.
        no_cue = copy.deepcopy(project)
        no_cue.project_id = "no-cue"
        no_cue.sequence.captions = []
        self.service.import_project(no_cue, project_id="no-cue")
        cid, cp, _ = self.submit(no_cue, "no-cue", ("101.25", "195.75"), opted=False)
        self.wait_job(cid)
        self.assertEqual(_frame_ids(cp),ids)
        a=_decode(path,"v","rawvideo","-pix_fmt","gray")
        b=_decode(cp,"v","rawvideo","-pix_fmt","gray")
        n=WIDTH*HEIGHT
        diffs=[sum(abs(x-y)>35 for x,y in zip(a[i+60*WIDTH:i+n],b[i+60*WIDTH:i+n]))
               for i in range(0,len(a),n)]
        text=[i for i,d in enumerate(diffs) if d>20]
        self.assertTrue(text)
        self.assertEqual(text, [index for index in range(len(ids))
                                if start <= index / RATE < end])

    def test_aggregate_video_metadata_does_not_prove_packet_grid(self):
        regular = self.root / "regular.mp4"
        shifted = self.root / "shifted.mp4"
        _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=96x64:r=30", "-frames:v", 30,
                "-c:v", "libx264", "-threads", 1, regular)
        _ffmpeg("-i", regular, "-map", "0:v:0", "-c", "copy", "-bsf:v",
                r"setts=pts=PTS+if(eq(N\,15)\,1\,0)", shifted)
        def aggregate(path):
            return json.loads(subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-of", "json",
                 "-show_entries", "stream=nb_frames,r_frame_rate,avg_frame_rate,time_base,start_pts,duration_ts",
                 str(path)], capture_output=True, check=True, timeout=10).stdout)["streams"][0]
        actual = aggregate(regular)
        self.assertEqual(actual, aggregate(shifted))
        timebase = Fraction(actual["time_base"])
        self.assertTrue(self.api.render._verify_picture_packet_grid(regular, Fraction(30), 30, timebase, None))
        self.assertFalse(self.api.render._verify_picture_packet_grid(shifted, Fraction(30), 30, timebase, None))

    def test_two_ranged_versions_get_matching_download_names(self):
        from cutvoke.storage.export_queue import ExportQueue

        project = self.project("two-versions")
        self.service.import_project(project, project_id=project.project_id)
        queue = ExportQueue(self.store, self.service,
                            render_factory=lambda: self.api.render, autostart=False)
        self.service._export_queue = queue
        first, _, first_url = self.submit(project, "take", ("3.25", "90.75"),
                                          extra={"versioned": True})
        second, _, second_url = self.submit(project, "take", ("217", "254"),
                                            extra={"versioned": True})

        queue.start()
        for jid, endpoint, stem, cue, frames in (
            (first, first_url, "take", "中文 / café ☀", range(4, 91)),
            (second, second_url, "take_v2", "検証", range(217, 254)),
        ):
            with self.subTest(stem=stem):
                job = self.wait_job(jid)
                expected_path = self.root / f"{stem}.mp4"
                self.assertEqual(Path(job["outPath"]), expected_path)
                self.assertEqual(_frame_ids(expected_path), list(frames))
                code, response = self.api.handle("GET", endpoint, {})
                self.assertEqual(code, 200, response)
                self.assertEqual(response["__srt_disposition__"],
                                 f"""attachment; filename="{stem}.srt"; filename*=UTF-8''{stem}.srt""")
                self.assertEqual(set(self.read_cues(endpoint)), {cue})

    def test_selected_sequence_frozen_job_after_edit_and_restart(self):
        project = self.project("main")
        requested = copy.deepcopy(project.sequence)
        requested.id = "selected"
        project.sequences.append(requested)
        project.sequence.captions[1].text = "other live active sequence"
        other = self.project("other")
        self.service.import_project(project, project_id="main")
        self.service.import_project(other, project_id="other")
        queue = self.api._export_queue()
        released = threading.Event()
        blocked = threading.Event()
        original_render = self.api.render.render

        def pause(*args, **kwargs):
            blocked.set()
            if not released.wait(10):
                raise TimeoutError("fixture gate not released")
            return original_render(*args, **kwargs)

        self.api.render.render = pause
        self.addCleanup(released.set)
        jid, path, endpoint = self.submit(project, "剪辑 café_v2", ("3.25", "90.75"), command=True,
                                          extra={"sequenceId": "selected"})
        self.assertTrue(blocked.wait(10))
        self.assertEqual(self.api.handle("GET", endpoint, {})[0], 404)
        active = self.service.get_project("main")
        self.assertEqual(active.sequence.id, "s")
        status, response = self.api.handle("POST", "/api/v1/projects/main/commands", {
            "type": "sequence.switch", "commandId": "select-after-enqueue",
            "expectedRevision": active.revision, "actor": {"kind": "human", "id": "test"},
            "payload": {"sequenceId": "selected"},
        })
        self.assertEqual(status, 200, response)
        active = self.service.get_project("main")
        sid = active.sequence.captions[1].id
        status, response = self.api.handle("POST", "/api/v1/projects/main/commands", {
            "type": "caption.update", "commandId": "edit-after-enqueue",
            "expectedRevision": active.revision, "actor": {"kind": "human", "id": "test"},
            "payload": {"captionId": sid, "text": "edited after enqueue"},
        })
        self.assertEqual(status, 200, response)
        released.set()
        self.wait_job(jid)
        self.assertEqual(_frame_ids(path), list(range(4, 91)))
        frozen = self.read_cues(endpoint)
        self.assertEqual(set(frozen), {"中文 / café ☀"})
        self.assertEqual(self.api.handle("GET", f"/api/v1/projects/other/exports/{jid}/captions.srt", {})[0],
                         404)
        second_service = EditService(store=ProjectStore(str(self.database)))
        second_api = HttpApi(second_service, media_dir=str(self.root / "media"))
        try:
            self.assertEqual(self.read_cues(endpoint, api=second_api), frozen)
            result = second_api.handle("GET", "/api/v1/projects/main/captions.srt", {})[1]
            self.assertIn("edited after enqueue", result["__text__"])
        finally:
            second_api.close()
            second_service.close()
        self.assertIs(self.api._export_queue(), queue)
        token = SessionToken.create(str(self.root / "test-token.json"))
        self.api.session = token
        with ThreadingHTTPServer(("127.0.0.1", 0), build_handler(self.api)) as server:
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            try:
                connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                try:
                    connection.request("GET", endpoint)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 401)
                    response.read()
                    connection.request("GET", endpoint, headers={"X-CutVoke-Token": token._token})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.getheader("Content-Type"), "text/plain; charset=utf-8")
                    self.assertEqual(response.getheader("Cache-Control"), "no-store")
                    self.assertEqual(
                        response.getheader("Content-Disposition"),
                        """attachment; filename="__ caf__v2.srt"; filename*=UTF-8''%E5%89%AA%E8%BE%91%20caf%C3%A9_v2.srt""")
                    self.assertIn("中文 / café ☀", response.read().decode("utf-8"))
                finally:
                    connection.close()
            finally:
                server.shutdown()
                worker.join(5)

    def test_cancellation_failure_empty_and_unverified_never_serve_text(self):
        project = self.project("jobs")
        self.service.import_project(project, project_id="jobs")
        route = "/api/v1/projects/jobs/exports"
        self.assertEqual(self.api.handle("POST", route, {
            "outPath": str(self.root / "unsupported.mov"), "captionSidecar": True,
        })[0], 400)
        self.assertEqual(self.api.handle("POST", route, {
            "outPath": str(self.root / "invalid.mp4"), "captionSidecar": "yes",
        })[0], 400)

        self.assertEqual(self.api.handle("POST", route, {
            "outPath": str(self.root / "whole.mp4"), "captionSidecar": True,
        })[0], 400)

        queue = self.api._export_queue()
        queue.shutdown()
        jid, path, endpoint = self.submit(project, "canceled", (4, 90))
        self.assertEqual(self.api.handle("GET", endpoint, {})[0], 404)
        queue.cancel(jid)
        self.wait_job(jid, "cancelled")
        self.assertFalse(path.exists())
        self.assertEqual(self.api.handle("GET", endpoint, {})[0], 404)
        queue.start()

        jid, path, endpoint = self.submit(project, "empty", (95, 100))
        self.assertEqual(self.wait_job(jid)["result"]["captionSidecarStatus"], "empty")
        self.assertEqual(self.api.handle("GET", endpoint, {})[0], 404)

        self.api.render._verify_picture_packet_grid = lambda *args: False
        jid, path, endpoint = self.submit(project, "unverified", (4, 90))
        self.assertEqual(self.wait_job(jid)["result"]["captionSidecarStatus"], "unavailable")
        self.assertTrue(path.exists())
        self.assertEqual(self.api.handle("GET", endpoint, {})[0], 404)

        self.source.unlink()
        jid, path, endpoint = self.submit(project, "failed", (4, 90))
        self.wait_job(jid, "failed")
        self.assertFalse(path.exists())
        self.assertEqual(self.api.handle("GET", endpoint, {})[0], 404)
