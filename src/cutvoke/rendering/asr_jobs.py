"""Bounded, restartable recognition proposals for the Web subtitle workbench."""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid

from cutvoke.domain.model import Project
from cutvoke.storage.asr_journal import ACTIVE, AsrJournal
from cutvoke.rendering.speech_recognition import (LANGUAGE_CHOICES, MODEL_CHOICES,
                                 SpeechRecognitionError, availability,
                                 find_source_clip, source_signature,
                                 transcribe_clip)


class AsrJobManager:
    def __init__(self, database: str | None = None, *, project_loader=None) -> None:
        self._lock = threading.RLock()
        self._slot = threading.Semaphore(1)
        self._closed = threading.Event()
        self._journal = AsrJournal(database)
        self._project_loader = project_loader
        self._threads: dict[str, threading.Thread] = {}
        self._leases = {}
        self._recover()
        self._journal.prune()

    def _recover(self):
        for job in self._journal.jobs():
            if job["status"] not in ACTIVE:
                continue
            if job["jobId"] in self._threads and self._threads[job["jobId"]].is_alive():
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
            self._start(job_id, snapshot, lease)
            self._journal.prune()
        return self.get(job_id) or {}

    def _check_open(self):
        if self._closed.is_set():
            raise SpeechRecognitionError("识别服务已关闭")
        self._threads = {key: thread for key, thread in self._threads.items() if thread.is_alive()}
        if len(self._threads) >= 2:
            raise SpeechRecognitionError("已有两个识别任务在等待或运行，请稍后重试")

    def _start(self, job_id, project, lease):
        thread = threading.Thread(target=self._run, args=(job_id, project),
                                  daemon=True, name=f"cutvoke-{job_id}")
        self._leases[job_id] = lease
        self._threads[job_id] = thread
        thread.start()

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
                self._start(job_id, snapshot, lease)
            except BaseException as exc:
                lease.close()
                if isinstance(exc, ValueError):
                    raise SpeechRecognitionError(str(exc)) from exc
                raise
            return job

    def cancel(self, job_id: str) -> dict | None:
        return self._journal.update(job_id, {"status": "cancelled", "phase": "已取消", "error": ""},
                                    statuses=(*ACTIVE, "interrupted"))

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
        try:
            with self._slot:
                with self._lock:
                    if self._closed.is_set():
                        return
                    job = self.get(job_id)
                    if not job or job["status"] not in ACTIVE:
                        return
                    self._journal.update(job_id, {"status": "running", "phase": "准备识别"}, statuses=ACTIVE)
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
                lease = self._leases.pop(job_id, None)
                if lease:
                    lease.close()

    def close(self) -> None:
        with self._lock:
            if self._closed.is_set():
                return
            self._closed.set()
            for job_id, lease in self._leases.items():
                self._journal.update(job_id, {"status": "interrupted", "phase": "等待恢复", "error": ""},
                                     statuses=ACTIVE)
                lease.close()
            self._leases.clear()
            self._journal.close()
