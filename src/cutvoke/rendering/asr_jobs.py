"""Bounded, restartable recognition proposals for the Web subtitle workbench."""
from __future__ import annotations

import copy
import json
import logging
import sqlite3
import threading
import time
import uuid

from cutvoke.domain.model import Project
from cutvoke.storage.asr_journal import ACTIVE, AsrJournal
from cutvoke.rendering.speech_recognition import (LANGUAGE_CHOICES, MODEL_CHOICES,
                                 SpeechRecognitionError, availability,
                                 find_source_clip, source_signature,
                                 transcribe_clip)


_LOG = logging.getLogger(__name__)


class AsrJobManager:
    def __init__(self, database: str | None = None, *, project_loader=None) -> None:
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._journal = AsrJournal(database)
        self._project_loader = project_loader
        self._threads: dict[str, threading.Thread] = {}
        # Hold one execution owner and one pending snapshot, not a waiting thread.
        self._active_job: str | None = None
        self._pending: tuple[str, Project] | None = None
        self._leases = {}
        self._recover()
        self._journal.prune()

    def _recover(self):
        for job in self._journal.jobs():
            if job["status"] not in ACTIVE:
                continue
            if job["jobId"] == self._active_job or (self._pending and job["jobId"] == self._pending[0]):
                continue
            lease = self._journal.lock_job(job["jobId"])
            if lease is not None:
                try:
                    self._journal.update(job["jobId"], {
                        "status": "interrupted", "phase": "等待恢复", "error": "",
                    }, statuses=ACTIVE)
                finally:
                    lease.close()

    def submit(self, project: Project, clip_id: str, *, model: str = "base",
               language: str = "auto") -> dict:
        if model not in MODEL_CHOICES or language not in LANGUAGE_CHOICES:
            raise SpeechRecognitionError("识别模型或语言选项无效")
        if not availability()["installed"]:
            raise SpeechRecognitionError("自动识别待配置：请安装 cutvoke[asr]")
        signature = source_signature(find_source_clip(project, clip_id))
        snapshot = copy.deepcopy(project)
        with self._lock:
            self._check_open()
            job_id = "asr_" + uuid.uuid4().hex
            lease = self._journal.lock_job(job_id)
            if lease is None:
                raise SpeechRecognitionError("识别任务正在运行")
            now = time.time()
            job = {
                "jobId": job_id, "projectId": project.project_id,
                "clipId": clip_id, "model": model, "requestedLanguage": language,
                "sourceSignature": signature, "status": "queued",
                "phase": "排队中", "progress": 0, "error": "",
                "result": None, "drafts": None, "createdAt": now, "updatedAt": now,
            }
            try:
                self._journal.insert(job, snapshot.to_dict())
            except BaseException as exc:
                lease.close()
                if isinstance(exc, ValueError):
                    raise SpeechRecognitionError(str(exc)) from exc
                raise
            self._enqueue(job_id, snapshot, lease)
            self._journal.prune()
            return self.get(job_id) or {}

    def _check_open(self):
        if self._closed.is_set():
            raise SpeechRecognitionError("识别服务已关闭")
        self._threads = {key: thread for key, thread in self._threads.items() if thread.is_alive()}
        self._drop_inactive_pending()
        if self._pending is not None:
            raise SpeechRecognitionError("已有两个识别任务在等待或运行，请稍后重试")

    def _release_lease(self, job_id):
        lease = self._leases.pop(job_id, None)
        if lease:
            try:
                lease.close()
            except OSError:
                _LOG.warning("Could not close an ASR job lease")

    def _mark_interrupted(self, job_id):
        try:
            self._journal.update(job_id, {"status": "interrupted", "phase": "等待恢复", "error": ""},
                                 statuses=ACTIVE)
        except sqlite3.OperationalError:
            # Release the lease even if this write fails. Normal journal
            # recovery can retry the transition when storage is available.
            _LOG.warning("Could not record ASR interruption; journal recovery is pending")

    def _drop_inactive_pending(self):
        # Another manager can cancel our queued row through the shared journal.
        if self._pending is not None:
            job_id, _ = self._pending
            if (self.get(job_id) or {}).get("status") != "queued":
                self._pending = None
                self._release_lease(job_id)

    def _enqueue(self, job_id, project, lease):
        self._leases[job_id] = lease
        if self._active_job is None:
            self._start(job_id, project)
        else:
            self._pending = job_id, project

    def _start(self, job_id, project):
        self._active_job = job_id
        try:
            thread = threading.Thread(target=self._run, args=(job_id, project),
                                      daemon=True, name=f"cutvoke-{job_id}")
            self._threads[job_id] = thread
            thread.start()
        except BaseException as exc:
            self._active_job = None
            self._threads.pop(job_id, None)
            try:
                _LOG.warning("Could not start an ASR worker")
                self._mark_interrupted(job_id)
            finally:
                self._release_lease(job_id)
            if not isinstance(exc, RuntimeError):
                raise

    def get(self, job_id: str) -> dict | None:
        return self._journal.get(job_id)

    def jobs(self, project_id: str) -> list[dict]:
        with self._lock:
            self._recover()
            self._journal.prune()
            return [job for job in self._journal.jobs(project_id) if not job.get("dismissed")]

    def _validate_source(self, job, snapshot):
        try:
            project = self._project_loader(job["projectId"]) if self._project_loader else snapshot
        except Exception as exc:
            raise SpeechRecognitionError("识别工程已不可用，请重新打开工程后再识别") from exc
        if project is None or source_signature(find_source_clip(project, job["clipId"])) != job["sourceSignature"]:
            raise SpeechRecognitionError("识别来源片段已变化，请重新识别后再应用字幕")
        if source_signature(find_source_clip(snapshot, job["clipId"])) != job["sourceSignature"]:
            raise SpeechRecognitionError("识别来源片段已变化，请重新识别后再应用字幕")

    def resume(self, job_id: str) -> dict | None:
        with self._lock:
            self._check_open()
            self._recover()
            job = self.get(job_id)
            if job is None or job["status"] != "interrupted" or job.get("dismissed"):
                return job
            snapshot = Project.from_dict(self._journal.snapshot(job_id))
            try:
                self._validate_source(job, snapshot)
            except (SpeechRecognitionError, ValueError, OSError) as exc:
                return self._journal.update(job_id, {
                    "status": "failed", "phase": "识别来源已变化", "error": str(exc), "result": None,
                }, statuses=("interrupted",))
            if not availability()["installed"]:
                raise SpeechRecognitionError("自动识别待配置：请安装 cutvoke[asr]")
            lease = self._journal.lock_job(job_id)
            if lease is None:
                raise SpeechRecognitionError("识别任务正在运行")
            try:
                job = self._journal.update(job_id, {"status": "queued", "phase": "排队中",
                    "progress": 0, "error": "", "result": None},
                    statuses=("interrupted",), check_capacity=True)
            except BaseException as exc:
                lease.close()
                if isinstance(exc, ValueError):
                    raise SpeechRecognitionError(str(exc)) from exc
                raise
            if job is None or job["status"] != "queued":
                lease.close()
                return job
            self._enqueue(job_id, snapshot, lease)
            return self.get(job_id)

    def cancel(self, job_id: str) -> dict | None:
        job = self._journal.update(job_id, {"status": "cancelled", "phase": "已取消", "error": ""},
                                   statuses=(*ACTIVE, "interrupted"))
        with self._lock:
            if self._pending and self._pending[0] == job_id:
                self._drop_inactive_pending()
        return job

    def dismiss(self, job_id: str) -> dict | None:
        self.cancel(job_id)
        return self._journal.update(job_id, {"dismissed": True, "result": None, "drafts": None})

    def save_drafts(self, job_id: str, drafts, *, updated_at=None) -> dict | None:
        job = self.get(job_id)
        if job is None:
            return None
        if job["status"] != "completed" or job.get("dismissed"):
            raise SpeechRecognitionError("识别结果尚不可编辑")
        if updated_at is None:
            updated_at = time.time_ns() // 1000
        if type(updated_at) is not int or not 0 <= updated_at <= 2**53 - 1:
            raise SpeechRecognitionError("识别草稿版本无效")
        originals = {segment["index"] for segment in job["result"]["segments"]}
        if (not isinstance(drafts, list) or len(drafts) > 1000 or
                len(json.dumps(drafts, ensure_ascii=False).encode("utf-8")) > 1024 * 1024):
            raise SpeechRecognitionError("识别草稿过大或无效")
        seen = set()
        for draft in drafts:
            if (not isinstance(draft, dict) or draft.get("index") not in originals or
                    draft["index"] in seen or any(not isinstance(draft.get(key), str)
                    for key in ("text", "start", "end"))):
                raise SpeechRecognitionError("识别草稿无效")
            seen.add(draft["index"])
        return self._journal.update(job_id, {"drafts": drafts, "draftsUpdatedAt": updated_at},
                                    statuses=("completed",), version_field="draftsUpdatedAt")

    def _run(self, job_id: str, project: Project) -> None:
        claim_failed = False
        try:
            with self._lock:
                if self._closed.is_set() or self._active_job != job_id:
                    return
                try:
                    job = self._journal.update(job_id, {"status": "running", "phase": "准备识别"},
                                               statuses=("queued",))
                except sqlite3.OperationalError:
                    claim_failed = True
                    _LOG.warning("Could not claim an ASR job; recognition did not start")
                    self._mark_interrupted(job_id)
                    return
                if not job or job["status"] != "running":
                    return
            try:
                self._validate_source(job, project)
                def report(phase: str, percent: int) -> None:
                    with self._lock:
                        if self._closed.is_set() or (self.get(job_id) or {}).get("status") not in ACTIVE:
                            raise SpeechRecognitionError("识别已取消")
                        self._journal.update(job_id, {"phase": phase, "progress": percent}, statuses=ACTIVE)

                result = transcribe_clip(project, job["clipId"], model_size=job["model"],
                                         language=job["requestedLanguage"], progress=report)
                with self._lock:
                    if self._closed.is_set():
                        return
                    self._validate_source(job, project)
                    self._journal.update(job_id, {"status": "completed", "phase": "识别完成",
                        "progress": 100, "result": result}, statuses=ACTIVE)
            except Exception as exc:
                with self._lock:
                    if not self._closed.is_set():
                        self._journal.update(job_id, {"status": "failed", "phase": "识别失败",
                            "error": str(exc)}, statuses=ACTIVE)
            with self._lock:
                if not self._closed.is_set():
                    self._journal.prune()
        finally:
            with self._lock:
                if self._active_job == job_id:
                    self._active_job = None
                    if claim_failed:
                        self._threads.pop(job_id, None)
                    try:
                        self._release_lease(job_id)
                    finally:
                        if not self._closed.is_set() and self._pending is not None:
                            # The next worker's conditional claim decides whether
                            # this row was cancelled; don't read SQLite here.
                            pending, self._pending = self._pending, None
                            self._start(*pending)

    def close(self) -> None:
        with self._lock:
            if self._closed.is_set():
                return
            self._closed.set()
            self._pending = None
            try:
                for job_id in self._leases:
                    self._mark_interrupted(job_id)
            finally:
                try:
                    for job_id in tuple(self._leases):
                        self._release_lease(job_id)
                finally:
                    self._journal.close()
