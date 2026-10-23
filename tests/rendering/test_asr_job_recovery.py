"""Exercise crash recovery, concurrent ownership, source safety and library moves."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch
import wave

import pytest

from cutvoke.domain.protocol import Actor, Command
from cutvoke.editing.service import EditError, EditService
from cutvoke.interfaces.httpapi import HttpApi
from cutvoke.rendering.asr_jobs import AsrJobManager
from cutvoke.rendering.speech_recognition import find_source_clip, source_signature
from cutvoke.storage.asr_journal import AsrJournal, RETAIN_COMPLETED
from cutvoke.storage.storage_migration import migrate_library
from cutvoke.storage.store import ProjectStore


def edit(service, kind, payload):
    project = service.get_project("speech")
    return service.execute(Command(type=kind, payload=payload, command_id=os.urandom(12).hex(),
        project_id="speech", expected_revision=project.revision, actor=Actor("human", "recovery-test")))


def library(root):
    root.mkdir(parents=True, exist_ok=True)
    source = root / "media" / "voice.wav"
    source.parent.mkdir()
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 16000)
    store = ProjectStore(str(root / "projects.sqlite"))
    service = EditService(store)
    service.create_project("speech")
    edit(service, "track.add", {"trackId": "audio", "kind": "audio"})
    edit(service, "clip.insert", {"trackId": "audio", "clipId": "voice", "sourcePath": str(source),
        "timelineStart": {"num": "0", "den": "1"}, "timelineEnd": {"num": "1", "den": "1"}})
    return store, service, source


def proposal(project, clip_id, **kwargs):
    kwargs.get("progress", lambda *_: None)("识别中", 50)
    return {"clipId": clip_id, "sourceSignature": source_signature(find_source_clip(project, clip_id)),
        "model": kwargs["model_size"], "language": kwargs["language"], "duration": 1.0,
        "segmentCount": 1, "segments": [{"index": 1, "text": "Recovered words",
            "start": {"num": "0", "den": "1"}, "end": {"num": "1", "den": "1"}, "words": []}]}


@contextmanager
def recognition(implementation=proposal):
    with patch("cutvoke.rendering.asr_jobs.availability", return_value={"installed": True}), \
            patch("cutvoke.rendering.asr_jobs.transcribe_clip", implementation):
        yield


def wait_for(check, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.01)
    pytest.fail("Timed out waiting for ASR state")


def completed(manager, job_id):
    return wait_for(lambda: (job if (job := manager.get(job_id))["status"] == "completed" else None))


def test_killed_process_restores_running_and_queued_jobs_with_original_options(tmp_path):
    store, service, _ = library(tmp_path / "library")
    original = service.get_project("speech").to_dict()
    ready = tmp_path / "ready.json"
    script = """
import json, sys, time
from pathlib import Path
from cutvoke.storage.store import ProjectStore
from cutvoke.rendering import asr_jobs
asr_jobs.availability = lambda: {"installed": True}
def hold(project, clip_id, **kwargs):
    kwargs["progress"]("holding", 20)
    time.sleep(60)
asr_jobs.transcribe_clip = hold
store = ProjectStore(sys.argv[1])
manager = asr_jobs.AsrJobManager(sys.argv[1], project_loader=store.load)
project = store.load("speech")
first = manager.submit(project, "voice", model="tiny", language="en")
second = manager.submit(project, "voice", model="small", language="zh")
Path(sys.argv[2]).write_text(json.dumps([first["jobId"],second["jobId"]]))
time.sleep(60)
"""
    process = subprocess.Popen([sys.executable, "-c", script, store.path, str(ready)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        wait_for(lambda: ready.exists() or process.poll() is not None)
        assert ready.exists(), process.communicate(timeout=5)[1].decode()
        first, second = json.loads(ready.read_text())
        process.kill()
        process.communicate(timeout=5)
        manager = AsrJobManager(store.path, project_loader=store.load)
        try:
            assert {job["status"] for job in manager.jobs("speech")} == {"interrupted"}
            assert manager.get(first)["phase"] == "等待恢复"
            assert service.get_project("speech").to_dict() == original
            with recognition():
                assert manager.resume(first)["model"] == "tiny"
                assert manager.resume(second)["requestedLanguage"] == "zh"
                assert completed(manager, first)["result"]["language"] == "en"
                assert completed(manager, second)["result"]["model"] == "small"
            # Recognition and recovery only propose; they never change the project.
            assert service.get_project("speech").to_dict() == original
        finally:
            manager.close()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        service.close()
        store.close()


def test_live_server_is_not_recovered_and_cross_server_cancel_survives_late_result(tmp_path):
    store, service, _ = library(tmp_path / "library")
    entered, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        entered.set()
        release.wait(5)
        return proposal(*args, **kwargs)
    first = AsrJobManager(store.path, project_loader=store.load)
    second = None
    try:
        with recognition(slow):
            job = first.submit(service.get_project("speech"), "voice", model="tiny", language="en")
            assert entered.wait(5)
            second = AsrJobManager(store.path, project_loader=store.load)
            assert second.jobs("speech")[0]["status"] == "running"
            assert second.cancel(job["jobId"])["status"] == "cancelled"
            release.set()
            wait_for(lambda: not first._threads[job["jobId"]].is_alive())
            assert first.get(job["jobId"])["status"] == "cancelled"
            assert first.get(job["jobId"])["result"] is None
        second.close()
        second = AsrJobManager(store.path, project_loader=store.load)
        assert second.get(job["jobId"])["status"] == "cancelled"
        assert second.resume(job["jobId"])["status"] == "cancelled"
    finally:
        release.set()
        first.close()
        if second:
            second.close()
        service.close()
        store.close()


def test_restart_retains_result_and_reviewed_draft_but_rejects_changed_source(tmp_path):
    store, service, source = library(tmp_path / "library")
    manager = AsrJobManager(store.path, project_loader=store.load)
    try:
        with recognition():
            job = manager.submit(service.get_project("speech"), "voice", model="tiny", language="en")
            done = completed(manager, job["jobId"])
        draft = [{"index": 1, "text": "校对后的文字", "start": "0", "end": "1", "words": []}]
        manager.save_drafts(job["jobId"], draft)
        manager.close()
        manager = AsrJobManager(store.path, project_loader=store.load)
        assert manager.jobs("speech")[0]["drafts"] == draft
        assert manager.get(job["jobId"])["result"] == done["result"]
        source.write_bytes(source.read_bytes() + b"changed")
        before = service.get_project("speech").to_dict()
        with pytest.raises(EditError, match="来源片段已变化"):
            edit(service, "caption.bulkAdd", {"sourceClipId": "voice",
                "sourceSignature": done["result"]["sourceSignature"], "segments": done["result"]["segments"]})
        assert service.get_project("speech").to_dict() == before
        manager.dismiss(job["jobId"])
        manager.close()
        manager = AsrJobManager(store.path, project_loader=store.load)
        assert manager.jobs("speech") == []
    finally:
        manager.close()
        service.close()
        store.close()


def test_resume_refuses_changed_source_before_invoking_model(tmp_path):
    store, service, source = library(tmp_path / "library")
    entered, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        entered.set()
        release.wait(5)
        return proposal(*args, **kwargs)
    manager = AsrJobManager(store.path, project_loader=store.load)
    try:
        with recognition(slow):
            job = manager.submit(service.get_project("speech"), "voice", model="small", language="ja")
            assert entered.wait(5)
            manager.close()
            release.set()
            wait_for(lambda: not manager._threads[job["jobId"]].is_alive())
        source.write_bytes(source.read_bytes() + b"changed")
        manager = AsrJobManager(store.path, project_loader=store.load)
        with recognition() as _unused, patch("cutvoke.rendering.asr_jobs.transcribe_clip") as model:
            resumed = manager.resume(job["jobId"])
            assert resumed["status"] == "failed"
            assert "已变化" in resumed["error"]
            assert resumed["result"] is None
            model.assert_not_called()
        assert service.get_project("speech").sequence.captions == []
    finally:
        release.set()
        manager.close()
        service.close()
        store.close()


def test_library_move_rebinds_recovered_proposals_and_keeps_original_library(tmp_path):
    store, service, _ = library(tmp_path / "original")
    manager = AsrJobManager(store.path, project_loader=store.load)
    with recognition():
        job = manager.submit(service.get_project("speech"), "voice", model="tiny", language="en")
        done = completed(manager, job["jobId"])
    draft = [{"index": 1, "text": "Migrated review", "start": "0", "end": "1", "words": []}]
    manager.save_drafts(job["jobId"], draft)
    original = service.get_project("speech").to_dict()
    entered, release = threading.Event(), threading.Event()
    def pause(*args, **kwargs):
        entered.set()
        release.wait(5)
        return proposal(*args, **kwargs)
    with recognition(pause):
        pending = manager.submit(service.get_project("speech"), "voice", model="small", language="ja")
        assert entered.wait(5)
        manager.close()
        release.set()
        wait_for(lambda: not manager._threads[pending["jobId"]].is_alive())
    service.close()
    store.close()
    moved = migrate_library(str(tmp_path / "original" / "projects.sqlite"), str(tmp_path / "moved"))
    with ProjectStore(moved) as moved_store:
        moved_service = EditService(moved_store)
        manager = AsrJobManager(moved, project_loader=moved_store.load)
        try:
            recovered = manager.get(job["jobId"])
            signature = source_signature(find_source_clip(moved_service.get_project("speech"), "voice"))
            assert recovered["sourceSignature"] == signature
            assert recovered["result"]["sourceSignature"] == signature
            assert recovered["result"]["sourceSignature"] != done["result"]["sourceSignature"]
            assert recovered["drafts"] == draft
            assert manager.get(pending["jobId"])["status"] == "interrupted"
            with recognition():
                resumed = manager.resume(pending["jobId"])
                assert resumed["requestedLanguage"] == "ja"
                assert completed(manager, pending["jobId"])["result"]["sourceSignature"] == signature
            edit(moved_service, "caption.bulkAdd", {"sourceClipId": "voice", "sourceSignature": signature,
                "segments": recovered["result"]["segments"]})
            assert len(moved_service.get_project("speech").sequence.captions) == 1
        finally:
            manager.close()
            moved_service.close()
    with ProjectStore(str(tmp_path / "original" / "projects.sqlite")) as original_store:
        assert original_store.load("speech").to_dict() == original


def test_journal_history_is_bounded_without_expiring_active_jobs(tmp_path):
    journal = AsrJournal(str(tmp_path / "journal.sqlite"))
    try:
        for index in range(RETAIN_COMPLETED + 5):
            journal.insert({"jobId": str(index), "projectId": "speech", "status": "completed",
                "updatedAt": time.time() + index}, {})
        journal.insert({"jobId": "active", "projectId": "speech", "status": "running", "updatedAt": 1}, {})
        journal.insert({"jobId": "expired", "projectId": "speech", "status": "completed", "updatedAt": 1}, {})
        journal.prune()
        assert len(journal.jobs()) == RETAIN_COMPLETED + 1
        assert journal.get("active")["status"] == "running"
        assert journal.get("expired") is None
    finally:
        journal.close()


def test_http_discovers_saved_jobs_and_routes_resume_drafts_and_dismiss(tmp_path):
    store, service, _ = library(tmp_path / "library")
    api = HttpApi(service, media_dir=str(tmp_path / "library" / "media"))
    try:
        with recognition():
            status, job = api.handle("POST", "/api/v1/projects/speech/asr-jobs",
                                    {"clipId": "voice", "model": "tiny", "language": "en"})
            assert status == 202
            done = completed(api._asr_jobs, job["jobId"])
        path = "/api/v1/asr-jobs/" + job["jobId"]
        draft = [{"index": 1, "text": "HTTP review", "start": "0", "end": "1"}]
        assert api.handle("POST", path + "/drafts", {"drafts": draft})[0] == 200
        newest = [{**draft[0], "text": "Newer review"}]
        newest_version = time.time_ns() // 1000 + 10
        assert api.handle("POST", path + "/drafts", {
            "drafts": newest, "updatedAt": newest_version})[1]["drafts"] == newest
        # Simulate a save from the old page arriving after a newer page's save.
        delayed = api.handle("POST", path + "/drafts", {
            "drafts": draft, "updatedAt": newest_version - 1})[1]
        assert delayed["drafts"] == newest and delayed["draftsUpdatedAt"] == newest_version
        status, listed = api.handle("GET", "/api/v1/projects/speech/asr-jobs", {})
        assert status == 200 and listed["jobs"][0]["drafts"] == newest
        assert api.handle("POST", path + "/resume", {})[1]["status"] == "completed"
        assert api.handle("POST", path + "/dismiss", {})[1]["result"] is None
        assert api.handle("GET", "/api/v1/projects/speech/asr-jobs", {})[1]["jobs"] == []
        assert api.handle("POST", "/api/v1/asr-jobs/missing/resume", {})[0] == 404
        assert service.get_project("speech").sequence.captions == []
    finally:
        api.close()
        store.close()
