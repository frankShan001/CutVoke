"""Bounded in-process recognition jobs for the Web subtitle workbench."""

from __future__ import annotations

import copy
import threading
import time
import uuid

from .model import Project
from .speech_recognition import (LANGUAGE_CHOICES, MODEL_CHOICES,
                                 SpeechRecognitionError, availability,
                                 find_source_clip, source_signature,
                                 transcribe_clip)


class AsrJobManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._slot = threading.Semaphore(1)
        self._closed = threading.Event()
        self._jobs: dict[str, dict] = {}
        self._threads: dict[str, threading.Thread] = {}

    def submit(self, project: Project, clip_id: str, *, model: str = "base",
               language: str = "auto") -> dict:
        if model not in MODEL_CHOICES or language not in LANGUAGE_CHOICES:
            raise SpeechRecognitionError("识别模型或语言选项无效")
        if not availability()["installed"]:
            raise SpeechRecognitionError("自动识别待配置：请安装 cutvoke[asr]")
        clip = find_source_clip(project, clip_id)
        signature = source_signature(clip)
        snapshot = copy.deepcopy(project)
        with self._lock:
            if self._closed.is_set():
                raise SpeechRecognitionError("识别服务已关闭")
            pending = sum(j["status"] in ("queued", "running")
                          for j in self._jobs.values())
            self._threads = {key: thread for key, thread in self._threads.items()
                             if thread.is_alive()}
            if pending >= 2 or len(self._threads) >= 2:
                raise SpeechRecognitionError("已有两个识别任务在等待或运行，请稍后重试")
            job_id = "asr_" + uuid.uuid4().hex
            self._jobs[job_id] = {
                "jobId": job_id, "projectId": project.project_id,
                "clipId": clip_id, "model": model, "requestedLanguage": language,
                "sourceSignature": signature, "status": "queued",
                "phase": "排队中", "progress": 0, "error": "",
                "result": None, "createdAt": time.time(),
            }
            # Retain current jobs and the newest completed jobs for refresh/retry.
            old = [key for key, value in self._jobs.items()
                   if value["status"] not in ("queued", "running")]
            for key in old[:-24]:
                self._jobs.pop(key, None)
            thread = threading.Thread(target=self._run, args=(job_id, snapshot),
                                      daemon=True, name=f"cutvoke-{job_id}")
            self._threads[job_id] = thread
            thread.start()
        return self.get(job_id) or {}

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job is not None else None

    def cancel(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job["status"] in ("queued", "running"):
                job.update(status="cancelled", phase="已取消", error="")
            return copy.deepcopy(job)

    def _update(self, job_id: str, **changes: object) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(changes)

    def _run(self, job_id: str, project: Project) -> None:
        with self._slot:
            if (self.get(job_id) or {}).get("status") == "cancelled":
                return
            if self._closed.is_set():
                self._update(job_id, status="cancelled", phase="服务已关闭")
                return
            job = self.get(job_id)
            if job is None:
                return
            with self._lock:
                if self._jobs[job_id]["status"] == "cancelled":
                    return
                self._jobs[job_id].update(status="running", phase="准备识别")
            try:
                def report(phase: str, percent: int) -> None:
                    if (self.get(job_id) or {}).get("status") == "cancelled":
                        raise SpeechRecognitionError("识别已取消")
                    self._update(job_id, phase=phase, progress=percent)

                result = transcribe_clip(
                    project, job["clipId"], model_size=job["model"],
                    language=job["requestedLanguage"],
                    progress=report,
                )
                with self._lock:
                    current = self._jobs[job_id]
                    if current["status"] == "cancelled":
                        return
                    if self._closed.is_set():
                        current.update(status="cancelled", phase="服务已关闭")
                    else:
                        current.update(status="completed", phase="识别完成",
                                       progress=100, result=result)
            except Exception as exc:
                with self._lock:
                    if self._jobs[job_id]["status"] != "cancelled":
                        self._jobs[job_id].update(status="failed", phase="识别失败",
                                                  error=str(exc))

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            for job in self._jobs.values():
                if job["status"] in ("queued", "running"):
                    job.update(status="cancelled", phase="服务已关闭")
