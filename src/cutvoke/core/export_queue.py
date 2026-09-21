"""V02 导出队列：排队 / 取消 / 多版本（真实可用的后台导出队列）。

为什么需要它：1.5 之前的导出是**同步阻塞**的（HTTP 请求里直接把整片渲完），
用户在浏览器里只能干等，多个导出请求互相抢 CPU，也没法取消已经点错的导出。

本模块提供最小但真实的一层：
  - **排队**：单一后台工作线程串行消费。ffmpeg 本身吃满 CPU，并发渲染只会
    互相拖慢（本机实测），队列语义比并发更贴近"导出"这件事。
  - **取消**：cancel 置 threading.Event，RenderService 的看门狗会 terminate
    ffmpeg 进程，任务落到 cancelled，**不留半成品成片**。
  - **多版本**：versioned=true 时永不覆盖已有成片，依次产出
    `name.mp4` → `name_v2.mp4` → `name_v3.mp4`（"导出多版本"）。
  - **可查**：任务状态同时写 SQLite 账本（复用 store.export_jobs），
    进程重启后仍能查到历史任务与结果。

设计边界（不装作更强）：
  - 队列是**进程内**的：服务重启后，之前 queued/running 的任务不会自动续跑，
    会被标记为 interrupted，避免"卡在 running 永远不结束"的假状态。
  - 不做优先级/并行度配置，先保证"串行 + 可取消 + 状态可查"三件事是真的。
"""

from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from typing import Any, Callable, Optional

from .model import Project
from .render import RenderService, RenderCancelled, RenderError

# 任务状态机：queued -> running -> succeeded | failed | cancelled
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_INTERRUPTED = "interrupted"   # 服务重启导致的中断（不是用户取消）

TERMINAL_STATUSES = frozenset({STATUS_SUCCEEDED, STATUS_FAILED,
                               STATUS_CANCELLED, STATUS_INTERRUPTED})

# 队列任务在 export_jobs.request_hash 上的固定前缀。
# 用途一：让"同一工程同样参数"可以重复排队，不撞幂等导出的自然键唯一索引；
# 用途二（更重要）：区分"队列任务"与"同步导出任务"——两者都会把状态写成
# running，重启恢复时只能把队列自己的任务标 interrupted，否则会误伤同步渲染。
_QUEUE_HASH_PREFIX = "queue:"


def versioned_path(out_path: str) -> str:
    """多版本落点：已被占用则依次尝试 _v2 / _v3 …

    `name.mp4`（空闲）→ `name.mp4`；`name.mp4` 已存在 → `name_v2.mp4`。
    多版本语义是"保留历史成片"，所以永不覆盖。
    """
    if not os.path.exists(out_path):
        return out_path
    base, ext = os.path.splitext(out_path)
    n = 2
    while True:
        cand = f"{base}_v{n}{ext}"
        if not os.path.exists(cand):
            return cand
        n += 1


class ExportQueue:
    """串行导出队列（后台线程 + SQLite 账本）。"""

    def __init__(self,
                 store: Any = None,
                 service: Any = None,
                 render_factory: Callable[[], RenderService] = RenderService,
                 autostart: bool = True) -> None:
        self._store = store
        self._service = service
        self._render_factory = render_factory
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._jobs: dict[str, dict] = {}          # job_id -> 内存态
        # job_id -> 入队时的不可变工程快照；导出不能随着用户后续编辑漂移。
        self._snapshots: dict[str, dict] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.RLock()
        self._worker: Optional[threading.Thread] = None
        self._stopping = False
        if autostart:
            self.start()
        # 启动时把上一次进程遗留的 queued/running 标成 interrupted（不假装还在跑）
        self._mark_interrupted_from_last_run()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stopping = False
            self._worker = threading.Thread(target=self._run_loop, name="export-queue",
                                            daemon=True)
            self._worker.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._stopping = True
        self._q.put(None)
        w = self._worker
        if w is not None:
            w.join(timeout=timeout)

    def _mark_interrupted_from_last_run(self) -> None:
        if self._store is None:
            return
        try:
            rows = self._store.list_export_jobs(None, limit=1000)
        except Exception:
            return
        for row in rows:
            if row.get("status") not in (STATUS_QUEUED, STATUS_RUNNING):
                continue
            # 只处理**本队列**产生的任务：同步导出路径（POST .../export）也会把
            # 账本写成 running，若不加区分，队列一启动就会把此刻正在同步渲染的
            # 任务误标成 interrupted。队列任务的 request_hash 固定为 "queue:<id>"。
            if not str(row.get("requestHash") or "").startswith(_QUEUE_HASH_PREFIX):
                continue
            try:
                self._store.finish_export_job(
                    row["jobId"], status=STATUS_INTERRUPTED,
                    error={"code": "INTERRUPTED",
                           "message": "server restarted before this job "
                                      "finished"})
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 提交 / 取消 / 查询
    # ------------------------------------------------------------------
    def submit(self, project: Project, payload: dict) -> dict:
        """入队一次导出。payload：{outPath, quality?, videoBitrateKbps?,
        audioBitrateKbps?, transparent?, versioned?}。"""
        out_path = payload.get("outPath")
        if not out_path:
            raise ValueError("export queue requires 'outPath'")
        out_path = os.path.abspath(out_path)
        if payload.get("versioned"):
            out_path = versioned_path(out_path)
        job_id = f"export_{uuid.uuid4().hex[:12]}"
        job = {
            "jobId": job_id,
            "projectId": project.project_id,
            "revision": project.revision,
            "outPath": out_path,
            "quality": str(payload.get("quality", "high")),
            "status": STATUS_QUEUED,
            # 入队顺序（队列视图里按它排队；账本按 updated_at 排序）
            "queuedAt": time.time(),
            "versioned": bool(payload.get("versioned")),
            "payload": dict(payload, outPath=out_path),
        }
        snapshot = project.to_dict()
        with self._lock:
            self._jobs[job_id] = job
            self._snapshots[job_id] = snapshot
            self._cancel_events[job_id] = threading.Event()
        if self._store is not None:
            # 队列任务用独立 request_hash（含 jobId）：允许"同一工程同样参数"
            # 排多次队，不撞 export_jobs 的自然键唯一索引。
            self._store.create_export_job(
                job_id=job_id, project_id=project.project_id,
                revision=project.revision,
                request_hash=f"{_QUEUE_HASH_PREFIX}{job_id}",
                out_path=out_path, quality=job["quality"],
                status=STATUS_QUEUED,
                project_snapshot=snapshot)
        self._q.put(job_id)
        return dict(job, payload=None)

    def cancel(self, job_id: str) -> dict:
        """取消任务：queued 直接出队，running 交给 RenderService 看门狗终止。"""
        with self._lock:
            job = self._jobs.get(job_id)
            ev = self._cancel_events.get(job_id)
        if job is None:
            # 内存里没有（例如重启前提交的）→ 以账本为准，不假装能取消
            if self._store is not None:
                row = self._store.get_export_job(job_id)
                if row is not None:
                    if row["status"] in TERMINAL_STATUSES:
                        return dict(row, cancelled=False,
                                    message=f"job already {row['status']}")
                    raise KeyError(
                        f"job {job_id} is not in this process's queue "
                        f"(status={row['status']}); cannot cancel")
            raise KeyError(f"unknown job: {job_id}")
        if job["status"] in TERMINAL_STATUSES:
            return dict(job, payload=None, cancelled=False,
                        message=f"job already {job['status']}")
        if ev is not None:
            ev.set()
        if self._store is not None:
            expected_status = job["status"]
            changed = self._store.finish_export_job(
                job_id, status=STATUS_CANCELLED,
                error={"code": "CANCELLED", "message": "cancelled by user"},
                expected_status=expected_status)
            if not changed:
                row = self._store.get_export_job(job_id)
                if row is not None:
                    with self._lock:
                        job["status"] = row["status"]
                    return dict(job, payload=None, cancelled=False,
                                message=f"job already {row['status']}")
                return dict(job, payload=None, cancelled=False,
                            message="job state changed before cancellation")
        with self._lock:
            job["status"] = STATUS_CANCELLED
        return dict(job, payload=None, cancelled=True)

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None:
            return dict(job, payload=None)
        if self._store is not None:
            row = self._store.get_export_job(job_id)
            return row
        return None

    def list(self, project_id: Optional[str] = None,
             status: Optional[str] = None) -> list[dict]:
        """队列视图：内存态优先，账本兜底（重启后仍可查历史）。"""
        rows: dict[str, dict] = {}
        if self._store is not None:
            for r in self._store.list_export_jobs(project_id, limit=200):
                rows[r["jobId"]] = r
        with self._lock:
            for jid, j in self._jobs.items():
                if project_id is not None and j["projectId"] != project_id:
                    continue
                rows[jid] = dict(j, payload=None)
        out = [r for r in rows.values()
               if status is None or r.get("status") == status]
        out.sort(key=lambda r: r.get("queuedAt") or 0, reverse=False)
        return out

    # ------------------------------------------------------------------
    # 工作线程
    # ------------------------------------------------------------------
    def _run_loop(self) -> None:
        while True:
            job_id = self._q.get()
            if job_id is None or self._stopping:
                return
            with self._lock:
                job = self._jobs.get(job_id)
                ev = self._cancel_events.get(job_id)
            if job is None:
                continue
            if job["status"] == STATUS_CANCELLED:
                continue  # 出队前已被取消：不渲染，不覆盖状态
            self._execute(job, ev)

    def _execute(self, job: dict, cancel_event: Optional[threading.Event]) -> None:
        with self._lock:
            if job["status"] != STATUS_QUEUED or \
                    (cancel_event is not None and cancel_event.is_set()):
                return
            job["status"] = STATUS_RUNNING
        if self._store is not None:
            changed = self._store.finish_export_job(
                job["jobId"], status=STATUS_RUNNING,
                expected_status=STATUS_QUEUED)
            if not changed:
                row = self._store.get_export_job(job["jobId"])
                with self._lock:
                    if row is not None:
                        job["status"] = row["status"]
                return
            # 取消可能恰好发生在内存态从 queued 切到 running、但数据库 CAS
            # 尚未完成的窗口；CAS 成功后再次确认，避免取消请求仍启动渲染。
            if cancel_event is not None and cancel_event.is_set():
                self._finish(
                    job, STATUS_CANCELLED,
                    error={"code": "CANCELLED", "message": "cancelled by user"},
                    expected_status=STATUS_RUNNING)
                return
        payload = job["payload"]
        svc = self._render_factory()
        try:
            project = self._load_project(
                job["projectId"], job["revision"],
                self._snapshots.get(job["jobId"]))
            result = svc.render(
                project, job["outPath"], quality=job["quality"],
                overwrite=bool(payload.get("overwrite", False)),
                cancel_event=cancel_event,
                video_bitrate_kbps=(int(payload["videoBitrateKbps"])
                                    if payload.get("videoBitrateKbps") else None),
                audio_bitrate_kbps=(int(payload["audioBitrateKbps"])
                                    if payload.get("audioBitrateKbps") else None),
                alpha=bool(payload.get("transparent", False)))
        except RenderCancelled:
            self._finish(job, STATUS_CANCELLED,
                         error={"code": "CANCELLED", "message": "render cancelled"},
                         expected_status=STATUS_RUNNING)
            return
        except Exception as e:  # noqa: BLE001 - 失败必须落账，不能吞
            if cancel_event is not None and cancel_event.is_set():
                self._finish(job, STATUS_CANCELLED,
                             error={"code": "CANCELLED",
                                    "message": "render cancelled"},
                             expected_status=STATUS_RUNNING)
                return
            self._finish(job, STATUS_FAILED,
                         error={"code": "RENDER_FAILED", "message": str(e)},
                         expected_status=STATUS_RUNNING)
            return
        job["result"] = result
        job["finishedAt"] = time.time()
        finished = self._finish(job, STATUS_SUCCEEDED, result=result,
                                expected_status=STATUS_RUNNING)
        # 只有取消 CAS 真正赢得了任务状态，才清理输出。仅仅看到 event 已置位
        # 不足以证明取消成功：它可能发生在 succeeded 已经落库之后。
        if not finished and job.get("status") == STATUS_CANCELLED:
            output_path = result.get("output_path") if isinstance(result, dict) else None
            if output_path and os.path.isfile(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass

    def _finish(self, job: dict, status: str, result: Optional[dict] = None,
                error: Optional[dict] = None,
                expected_status: Optional[str] = None) -> bool:
        if self._store is not None:
            changed = self._store.finish_export_job(
                job["jobId"], status=status, result=result, error=error,
                expected_status=expected_status)
            if not changed:
                row = self._store.get_export_job(job["jobId"])
                if row is not None:
                    with self._lock:
                        job["status"] = row["status"]
                return False
        with self._lock:
            if expected_status is not None and job["status"] != expected_status:
                return False
            job["status"] = status
            job["finishedAt"] = time.time()
        return True

    def _load_project(self, project_id: str, revision: str,
                      snapshot: Optional[dict] = None) -> Project:
        """读取入队时快照；旧任务无快照时才回退到当前工程。"""
        if snapshot is not None:
            project = Project.from_dict(snapshot)
            if project.revision != revision:
                raise RenderError(
                    f"export snapshot revision {project.revision} != job revision {revision}")
            return project
        if self._service is not None:
            return self._service.get_project(project_id)
        if self._store is None:
            raise RenderError("export queue has no store/service to load project")
        proj = self._store.load(project_id)
        if proj is None:
            raise RenderError(f"unknown project: {project_id}")
        return proj
