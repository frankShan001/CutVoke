"""CutVoke 工程持久化存储（任务书第 11 章 / T08）。

使用 Python 标准库 sqlite3，零第三方运行时依赖（M0/M1 阶段）。
SQLite 事务为运行中的工程状态权威来源（任务书 11.1）：

- projects 表：工程完整状态以 JSON 快照存 sequence_json 列，
  时间走 Rational 的十进制字符串（{"num": "...", "den": "..."}），
  复用 model.to_dict()/from_dict()，不重写序列化逻辑。
- events 表：变更事件 outbox，用于事件重发（任务书 11.1 的 event outbox）。

原子保存设计见 save() 文档字符串。
"""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Optional

from .model import Project


SCHEMA_VERSION_PLACEHOLDER = "1"  # 仅用于 projects 表初始 schema 元列占位


def _iso_utc(ts: float) -> str:
    """把 unix 秒时间戳转成 ISO 8601 UTC 字符串（毫秒精度、末尾 Z）。"""
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ")[:-3] + "Z"


class RevisionConflict(Exception):
    """持久层检测到跨进程版本冲突（另一写入者推进了数据库）。"""


class ExportJobMismatch(Exception):
    """同一 jobId 被用于参数不同的导出请求（幂等冲突，拒绝重放）。"""


class EditLeaseConflict(Exception):
    """工程当前被另一个 Agent 编辑租约占用。"""


def _synchronized(fn: Callable) -> Callable:
    """把方法串行化到实例级可重入锁上。

    HTTP 服务用 ThreadingHTTPServer，每个请求在不同线程里处理；sqlite3 连接
    默认禁止跨线程使用。这里用 `check_same_thread=False` 放开限制，再用
    实例锁把访问串行化 —— WAL 保证读不阻塞读，锁保证写不交错（T31/AC22）。
    """

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)

    return wrapper


class ProjectStore:
    """基于 SQLite 的工程快照存储。

    构造时传入 SQLite 文件路径（可为含中文/空格的路径）。
    所有写操作在单一事务内提交，WAL 模式提升并发与崩溃安全。
    """

    def __init__(self, db_path: str, *, busy_timeout_ms: int = 5000):
        # db_path 可能是含中文/空格的任意路径，sqlite3 直接接受 str。
        self._db_path = db_path
        # 实例锁：HTTP 多线程请求下串行化同一个连接的访问（见 _synchronized）
        self._lock = threading.RLock()
        # check_same_thread=False：连接可被 HTTP 工作线程使用；
        # 并发安全由 _lock 保证，而不是靠 sqlite3 的线程检查。
        self._conn = sqlite3.connect(db_path, isolation_level=None,
                                     check_same_thread=False)
        # WAL：写不阻塞读，崩溃后可通过 -wal/-shm 恢复到最后一次提交。
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._ensure_schema()

    @property
    def path(self) -> str:
        """数据库文件路径（诊断与 CLI 输出用）。"""
        return self._db_path

    # ------------------------------------------------------------------
    # 事务原语（原子性的唯一来源）
    # ------------------------------------------------------------------

    @contextmanager
    def _transaction(self, *, immediate: bool = True):
        """显式事务：BEGIN IMMEDIATE → COMMIT / ROLLBACK。

        为什么不能用 `with self._conn:`（重要，勿回退）：
        本连接以 `isolation_level=None` 建立，即 sqlite3 的**自动提交**模式。
        该模式下 `with conn` 不会发出 BEGIN，退出时的 commit()/rollback()
        都是空操作——语句逐条即时落盘，中途抛错会留下**部分写入**。
        实测：`with conn:` 内先 INSERT 再抛异常，第一行仍然留存。

        WP-01 承诺的「工程快照 + 幂等 + outbox 事件 + 历史同事务原子提交」
        依赖本方法，而不是 `with self._conn:`。

        BEGIN IMMEDIATE 立即取写锁（而非等到首次写），使跨进程写入串行化，
        避免「校验通过后才发现别人已提交」的窗口被放大。

        已在事务中时（嵌套调用）复用外层事务，不重复 BEGIN（SQLite 不支持
        嵌套事务）。
        """
        if self._conn.in_transaction:
            yield
            return
        self._conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield
        except BaseException:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:  # 部分错误已触发 SQLite 自身回滚
                pass
            raise
        else:
            self._conn.execute("COMMIT")

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                project_id    TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                revision      TEXT NOT NULL,
                sequence_json TEXT NOT NULL,
                updated_at    REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id     TEXT PRIMARY KEY,
                project_id   TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                seq          INTEGER
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id, seq)"
        )
        # WP-01：持久化幂等表（跨进程/重启复用，A10）
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency (
                command_id   TEXT PRIMARY KEY,
                project_id   TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                result_json  TEXT NOT NULL,
                committed_at REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_idem_project ON idempotency(project_id)"
        )
        # Agent 编辑租约：跨 Web/MCP/CLI 进程共享，带过期时间避免异常退出永久锁死。
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS edit_leases (
                project_id TEXT PRIMARY KEY,
                lease_id   TEXT NOT NULL,
                owner      TEXT NOT NULL,
                expires_at REAL NOT NULL,
                acquired_at REAL NOT NULL
            )
            """
        )
        # WP-01：持久化撤销历史栈（project_id, depth 序号 -> 提交前快照）
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS history (
                project_id   TEXT NOT NULL,
                depth        INTEGER NOT NULL,
                snapshot_json TEXT NOT NULL,
                revision     TEXT NOT NULL,
                PRIMARY KEY (project_id, depth)
            )
            """
        )
        # WP-03：导出任务账本（导出任务同样要有幂等保证，指导第 9 章）
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS export_jobs (
                job_id       TEXT PRIMARY KEY,
                project_id   TEXT NOT NULL,
                revision     TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                out_path     TEXT NOT NULL,
                quality      TEXT NOT NULL,
                status       TEXT NOT NULL,
                result_json  TEXT,
                error_json   TEXT,
                project_json TEXT,
                created_at   REAL NOT NULL,
                updated_at   REAL NOT NULL
            )
            """
        )
        # 旧库迁移：导出任务需要保留入队时的工程快照，不能在执行时读取新版本。
        export_columns = {
            row[1] for row in self._conn.execute("PRAGMA table_info(export_jobs)")
        }
        if "project_json" not in export_columns:
            self._conn.execute("ALTER TABLE export_jobs ADD COLUMN project_json TEXT")
        # 自然键唯一：同一工程/版本/输出路径/质量 只保留一个任务，
        # 重复导出命中缓存而不是重复渲染。
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_export_request "
            "ON export_jobs(request_hash)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_export_project "
            "ON export_jobs(project_id, updated_at)"
        )
        # D02：素材元数据账本（资产 = 服务端权威，素材库刷新不丢）。
        # 与 projects 同库同连接；资产文件本身落媒体目录（main.py / httpapi 决定），
        # 本表只持久化可检索的元数据 + 落盘路径 + 探测结果。
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assets (
                asset_id   TEXT PRIMARY KEY,
                name       TEXT NOT NULL DEFAULT '',
                path       TEXT NOT NULL,
                size       INTEGER NOT NULL DEFAULT 0,
                kind       TEXT NOT NULL DEFAULT 'unknown',
                duration   REAL,
                has_video  INTEGER NOT NULL DEFAULT 0,
                has_audio  INTEGER NOT NULL DEFAULT 0,
                width      INTEGER,
                height     INTEGER,
                created_at REAL NOT NULL,
                builtin    INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_assets_created ON assets(created_at)"
        )
        # 幂等迁移：旧库（projects 表无 name 列）补列，新库无动作。
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """幂等迁移：旧库 projects 表缺列时补列，新库无动作。

        1.10 之前：工程只存 sequence_json（轨道/片段/字幕）+ 少量标量列，
        J01 新增的工程级资源状态（favorites / recent_effects / presets）
        存在 project.to_dict() 的 sequence 之外，旧设计会丢字段——
        commit 后 load 回来三个字段全是空。迁移补 project_json 列存完整
        工程 dict（含新增字段），并保证旧库无 project_json 时能回退到
        sequence_json。
        """
        cols = {row[1] for row in
                self._conn.execute("PRAGMA table_info(projects)")}
        if "name" not in cols:
            self._conn.execute(
                "ALTER TABLE projects ADD COLUMN name TEXT NOT NULL DEFAULT ''")
        if "project_json" not in cols:
            self._conn.execute(
                "ALTER TABLE projects ADD COLUMN project_json TEXT NOT NULL "
                "DEFAULT ''")
        # J07：内置声音资产需要 builtin 标记列（旧库 assets 表缺列时补列）
        acols = {row[1] for row in
                 self._conn.execute("PRAGMA table_info(assets)")}
        if "builtin" not in acols:
            self._conn.execute(
                "ALTER TABLE assets ADD COLUMN builtin INTEGER NOT NULL DEFAULT 0")

    # ------------------------------------------------------------------
    # WP-01 权威提交：工程 + 幂等 + 事件 + 历史 一个事务原子写
    # ------------------------------------------------------------------

    @_synchronized
    def db_revision(self, project_id: str) -> Optional[str]:
        """读数据库里持久化的最新 revision（跨进程权威比对用）。"""
        row = self._conn.execute(
            "SELECT revision FROM projects WHERE project_id=?", (project_id,)
        ).fetchone()
        return row[0] if row else None

    @_synchronized
    def get_idempotent(self, project_id: str, command_id: str
                       ) -> Optional[dict]:
        """读持久幂等记录（跨进程/重启可复用）：返回 {request_hash, result_json}。"""
        row = self._conn.execute(
            "SELECT request_hash, result_json FROM idempotency "
            "WHERE project_id=? AND command_id=?",
            (project_id, command_id),
        ).fetchone()
        return {"request_hash": row[0], "result_json": row[1]} if row else None

    @_synchronized
    def commit(self, project: Project, *,
               expected_prev_revision: Optional[str] = None,
               idem_entry: Optional[tuple[str, str, str]] = None,
               event_payload: Optional[dict] = None,
               history_push: Optional[dict] = None,
               history_clear_redo: bool = False,
               history_pop_depth: Optional[int] = None) -> None:
        """把一次命令的成功结果作为一个 SQLite 事务原子提交。

        关键（A01/A02/A10）：
          1. BEGIN IMMEDIATE 拿写锁——同一时刻只有一个写入者能提交。
          2. expected_prev_revision 与 DB 当前 revision 比对：若库已被
             其它进程/连接推进，则抛 RevisionConflict（调用方应转
             REVISION_CONFLICT），杜绝「读旧内存→覆盖新数据」。
          3. 工程快照、幂等记录、outbox 事件、撤销历史 全部在同一个
             事务里写，要么全部成功提交，要么全部回滚——成功后调用方
             才允许更新内存权威与确认成功。

        调用方（EditService.execute）仅在 commit 成功后更新内存。
        """
        seq = project.sequence
        sequence_json = json.dumps(seq.to_dict(), ensure_ascii=False)
        # 完整工程快照（sequence + 顶层 favorites/recentEffects/presets 等）。
        # 历史 bug：旧实现只存 sequence_json，J01 新增的工程级资源状态
        # （favorites/recent_effects/presets）在 sequence 之外，commit 后
        # load 回来即丢失。这里存完整 dict，load 也读 project_json。
        payload = json.dumps(project.to_dict(), ensure_ascii=False)
        json.loads(payload)  # 坏数据在事务外失败，不污染已存好数据

        with self._transaction():
            # 1) 跨进程冲突检测（A01）：库已被别人推进则拒绝
            current_db_rev = self.db_revision(project.project_id)
            if expected_prev_revision is not None and current_db_rev is not None:
                if current_db_rev != expected_prev_revision:
                    raise RevisionConflict(
                        f"DB revision {current_db_rev} != expected "
                        f"{expected_prev_revision} (another writer advanced it)")

            # 2) 工程主数据（完整快照 + 兼容列 sequence_json / name）
            self._conn.execute(
                """
                INSERT INTO projects
                    (project_id, schema_version, revision, sequence_json,
                     project_json, name, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    revision       = excluded.revision,
                    sequence_json  = excluded.sequence_json,
                    project_json   = excluded.project_json,
                    name           = excluded.name,
                    updated_at     = excluded.updated_at
                """,
                (project.project_id, project.schema_version, project.revision,
                 sequence_json, payload, project.name, time.time()),
            )

            # 3) 幂等记录（A10 持久化）
            if idem_entry is not None:
                cid, req_hash, result_json = idem_entry
                self._conn.execute(
                    "INSERT INTO idempotency "
                    "(command_id, project_id, request_hash, result_json, committed_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(command_id) DO UPDATE SET "
                    "request_hash=excluded.request_hash, result_json=excluded.result_json, "
                    "committed_at=excluded.committed_at",
                    (cid, project.project_id, req_hash, result_json, time.time()),
                )

            # 4) outbox 事件（A10 持久事件：重启不丢）
            if event_payload is not None:
                event_id = event_payload.get("eventId", "")
                if event_id:
                    next_seq = self._next_event_seq(project.project_id)
                    self._conn.execute(
                        "INSERT INTO events (event_id, project_id, payload_json, seq) "
                        "VALUES (?, ?, ?, ?) ON CONFLICT(event_id) DO NOTHING",
                        (event_id, project.project_id,
                         json.dumps(event_payload, ensure_ascii=False), next_seq),
                    )

            # 5) 撤销历史栈（持久化，且重做分支失效）
            if history_push is not None:
                depth = history_push["depth"]
                self._conn.execute(
                    "INSERT OR REPLACE INTO history "
                    "(project_id, depth, snapshot_json, revision) VALUES (?, ?, ?, ?)",
                    (project.project_id, depth,
                     json.dumps(history_push["snapshot"], ensure_ascii=False),
                     history_push["revision"]),
                )
            if history_clear_redo:
                self._conn.execute(
                    "DELETE FROM history WHERE project_id=? AND depth < 0",
                    (project.project_id,))
            # 撤销消费与工程快照必须同事务：revision 冲突时历史不能丢。
            if history_pop_depth is not None:
                cur = self._conn.execute(
                    "DELETE FROM history WHERE project_id=? AND depth=?",
                    (project.project_id, history_pop_depth),
                )
                if cur.rowcount != 1:
                    raise RevisionConflict(
                        f"undo history depth {history_pop_depth} disappeared "
                        f"for project {project.project_id}")

    def _next_event_seq(self, project_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM events WHERE project_id=?",
            (project_id,),
        ).fetchone()
        return int(row[0]) + 1

    @_synchronized
    def history_depth(self, project_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(depth), -1) FROM history WHERE project_id=? "
            "AND depth >= 0", (project_id,),
        ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------------
    # Agent 编辑租约（页面可见但写操作只允许租约持有者）
    # ------------------------------------------------------------------

    @staticmethod
    def _lease_to_dict(row) -> dict:
        return {
            "projectId": row[0],
            "leaseId": row[1],
            "owner": row[2],
            "expiresAt": row[3],
            "acquiredAt": row[4],
        }

    @_synchronized
    def get_edit_lease(self, project_id: str) -> Optional[dict]:
        """读取有效租约；过期租约会被清理。"""
        row = self._conn.execute(
            "SELECT project_id, lease_id, owner, expires_at, acquired_at "
            "FROM edit_leases WHERE project_id=?", (project_id,)
        ).fetchone()
        if row is None:
            return None
        if float(row[3]) <= time.time():
            with self._transaction():
                self._conn.execute(
                    "DELETE FROM edit_leases WHERE project_id=? AND lease_id=?",
                    (project_id, row[1]))
            return None
        return self._lease_to_dict(row)

    @_synchronized
    def acquire_edit_lease(self, *, project_id: str, lease_id: str,
                           owner: str, ttl_seconds: float = 30.0) -> dict:
        """获取或续租指定 lease_id；不同租约在有效期内不能抢占。"""
        now = time.time()
        ttl = max(5.0, min(float(ttl_seconds), 300.0))
        with self._transaction():
            row = self._conn.execute(
                "SELECT project_id, lease_id, owner, expires_at, acquired_at "
                "FROM edit_leases WHERE project_id=?", (project_id,)
            ).fetchone()
            if row is not None and float(row[3]) > now and row[1] != lease_id:
                raise EditLeaseConflict(
                    f"project {project_id} is locked by {row[2]}")
            acquired_at = row[4] if row is not None and row[1] == lease_id else now
            expires_at = now + ttl
            self._conn.execute(
                "INSERT INTO edit_leases "
                "(project_id, lease_id, owner, expires_at, acquired_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(project_id) DO UPDATE SET lease_id=excluded.lease_id, "
                "owner=excluded.owner, expires_at=excluded.expires_at, "
                "acquired_at=excluded.acquired_at",
                (project_id, lease_id, owner, expires_at, acquired_at),
            )
        return {
            "projectId": project_id,
            "leaseId": lease_id,
            "owner": owner,
            "expiresAt": expires_at,
            "acquiredAt": acquired_at,
        }

    @_synchronized
    def renew_edit_lease(self, *, project_id: str, lease_id: str,
                         ttl_seconds: float = 30.0) -> Optional[dict]:
        """只允许当前租约持有者续租；不存在/已过期返回 None。"""
        now = time.time()
        ttl = max(5.0, min(float(ttl_seconds), 300.0))
        expires_at = now + ttl
        with self._transaction():
            cur = self._conn.execute(
                "UPDATE edit_leases SET expires_at=? "
                "WHERE project_id=? AND lease_id=? AND expires_at>?",
                (expires_at, project_id, lease_id, now),
            )
            if cur.rowcount != 1:
                return None
            row = self._conn.execute(
                "SELECT project_id, lease_id, owner, expires_at, acquired_at "
                "FROM edit_leases WHERE project_id=?", (project_id,)
            ).fetchone()
        return self._lease_to_dict(row) if row is not None else None

    @_synchronized
    def release_edit_lease(self, *, project_id: str, lease_id: str) -> bool:
        with self._transaction():
            cur = self._conn.execute(
                "DELETE FROM edit_leases WHERE project_id=? AND lease_id=?",
                (project_id, lease_id),
            )
            return cur.rowcount == 1

    @_synchronized
    def peek_history(self, project_id: str, depth: int) -> Optional[str]:
        """读取指定深度的历史快照，不改变历史栈。"""
        row = self._conn.execute(
            "SELECT snapshot_json FROM history WHERE project_id=? AND depth=?",
            (project_id, depth),
        ).fetchone()
        return row[0] if row is not None else None

    @_synchronized
    def pop_history(self, project_id: str, depth: int) -> Optional[str]:
        """兼容旧调用：读取并删除指定深度的历史快照 JSON。

        新的命令提交路径应使用 commit(history_pop_depth=...)，让删除和
        工程快照提交处于同一事务内。
        """
        row = self._conn.execute(
            "SELECT snapshot_json FROM history WHERE project_id=? AND depth=?",
            (project_id, depth),
        ).fetchone()
        if row is None:
            return None
        with self._transaction():
            self._conn.execute(
                "DELETE FROM history WHERE project_id=? AND depth=?",
                (project_id, depth))
        return row[0]

    # ------------------------------------------------------------------
    # 导出任务账本（WP-03：导出任务的幂等保证）
    # ------------------------------------------------------------------

    _EXPORT_COLS = ("job_id, project_id, revision, request_hash, out_path, "
                    "quality, status, result_json, error_json, created_at, "
                    "updated_at")

    @staticmethod
    def _export_row_to_dict(row) -> dict:
        return {
            "jobId": row[0],
            "projectId": row[1],
            "revision": row[2],
            "requestHash": row[3],
            "outPath": row[4],
            "quality": row[5],
            "status": row[6],
            "result": json.loads(row[7]) if row[7] else None,
            "error": json.loads(row[8]) if row[8] else None,
            "createdAt": row[9],
            "updatedAt": row[10],
        }

    @_synchronized
    def claim_export_job(self, *, job_id: str, project_id: str, revision: str,
                         request_hash: str, out_path: str, quality: str) -> dict:
        """认领一次导出任务；返回 {outcome, job}。

        outcome 取值（调用方据此决定是否真的渲染）：
          - "claimed"    新任务，调用方负责渲染并调用 finish_export_job
          - "retry"      同键任务此前失败，本次重试（已重置为 running）
          - "cached"     已有成功结果，**不要重复渲染**，直接复用 job.result
          - "in_flight"  同键任务正在渲染中，调用方应返回「进行中」而非重复渲染

        幂等语义：
          - 显式 jobId 重放：同 jobId + 同 request_hash → 返回既有状态；
            同 jobId + 不同 request_hash → 抛 ExportJobMismatch（拒绝改内容复用 ID）
          - 未传 jobId：按自然键（工程/版本/输出路径/质量）命中缓存

        并发：整体在 BEGIN IMMEDIATE 事务内，两个进程同时导出同一请求时
        只有一个能 claim，另一个拿到 in_flight/cached，不会重复渲染。
        """
        now = time.time()
        with self._transaction():
            row = self._conn.execute(
                f"SELECT {self._EXPORT_COLS} FROM export_jobs WHERE job_id=?",
                (job_id,)).fetchone()
            if row is not None:
                if row[3] != request_hash:
                    raise ExportJobMismatch(
                        f"jobId {job_id} already used with different export "
                        f"request (outPath/quality/revision changed)")
                return self._dispatch_export(row, now)

            # 自然键命中（未传 jobId，或传了新 jobId 但请求等价）
            row = self._conn.execute(
                f"SELECT {self._EXPORT_COLS} FROM export_jobs WHERE request_hash=?",
                (request_hash,)).fetchone()
            if row is not None:
                return self._dispatch_export(row, now)

            self._conn.execute(
                "INSERT INTO export_jobs (job_id, project_id, revision, "
                "request_hash, out_path, quality, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)",
                (job_id, project_id, revision, request_hash, out_path, quality,
                 now, now))
            fresh = self._conn.execute(
                f"SELECT {self._EXPORT_COLS} FROM export_jobs WHERE job_id=?",
                (job_id,)).fetchone()
            return {"outcome": "claimed",
                    "job": self._export_row_to_dict(fresh)}

    def _dispatch_export(self, row, now: float) -> dict:
        """按已有任务行的状态决定本次 outcome（须在事务内调用）。"""
        job = self._export_row_to_dict(row)
        if job["status"] == "succeeded":
            return {"outcome": "cached", "job": job}
        if job["status"] == "running":
            return {"outcome": "in_flight", "job": job}
        # failed -> 允许重试：清空错误、重置为 running，沿用原 jobId
        self._conn.execute(
            "UPDATE export_jobs SET status='running', result_json=NULL, "
            "error_json=NULL, updated_at=? WHERE job_id=?",
            (now, job["jobId"]))
        job["status"] = "running"
        job["result"] = None
        job["error"] = None
        return {"outcome": "retry", "job": job}

    @_synchronized
    def finish_export_job(self, job_id: str, *, status: str,
                          result: Optional[dict] = None,
                          error: Optional[dict] = None,
                          expected_status: Optional[str] = None) -> bool:
        """落定导出结果（succeeded / failed / cancelled）。

        expected_status 用于状态机 CAS，避免取消请求被稍后的成功回写覆盖。
        返回是否真的更新了任务行。
        """
        with self._transaction():
            where = "WHERE job_id=?"
            params: list = [
                status,
                json.dumps(result, ensure_ascii=False) if result else None,
                json.dumps(error, ensure_ascii=False) if error else None,
                time.time(),
                job_id,
            ]
            if expected_status is not None:
                where += " AND status=?"
                params.append(expected_status)
            cur = self._conn.execute(
                "UPDATE export_jobs SET status=?, result_json=?, error_json=?, "
                "updated_at=? " + where,
                params)
            return cur.rowcount == 1

    @_synchronized
    def get_export_job(self, job_id: str) -> Optional[dict]:
        row = self._conn.execute(
            f"SELECT {self._EXPORT_COLS} FROM export_jobs WHERE job_id=?",
            (job_id,)).fetchone()
        return self._export_row_to_dict(row) if row else None

    @_synchronized
    def list_export_jobs(self, project_id: Optional[str] = None,
                         limit: int = 50) -> list[dict]:
        """导出任务列表。project_id=None 时列出全部（V02 队列视图需要）。"""
        if project_id is None:
            cur = self._conn.execute(
                f"SELECT {self._EXPORT_COLS} FROM export_jobs "
                "ORDER BY updated_at DESC LIMIT ?", (limit,))
        else:
            cur = self._conn.execute(
                f"SELECT {self._EXPORT_COLS} FROM export_jobs WHERE project_id=? "
                "ORDER BY updated_at DESC LIMIT ?",
                (project_id, limit))
        return [self._export_row_to_dict(r) for r in cur]

    @_synchronized
    def create_export_job(self, *, job_id: str, project_id: str, revision: str,
                          request_hash: str, out_path: str, quality: str,
                          status: str = "queued",
                          project_snapshot: Optional[dict] = None) -> dict:
        """V02 队列：直接落一条指定状态的任务记录（不参与同步路径的认领/缓存语义）。

        与 claim_export_job 的分工：claim 用于"同一请求只渲染一次"的幂等导出；
        本方法用于**排队多次导出**——request_hash 由调用方带上 jobId 保证唯一。
        """
        now = time.time()
        with self._transaction():
            self._conn.execute(
                "INSERT INTO export_jobs (job_id, project_id, revision, "
                "request_hash, out_path, quality, status, project_json, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, project_id, revision, request_hash, out_path, quality,
                 status,
                 json.dumps(project_snapshot, ensure_ascii=False)
                 if project_snapshot is not None else None,
                 now, now))
            row = self._conn.execute(
                f"SELECT {self._EXPORT_COLS} FROM export_jobs WHERE job_id=?",
                (job_id,)).fetchone()
        return self._export_row_to_dict(row)

    @_synchronized
    def get_export_project_snapshot(self, job_id: str) -> Optional[dict]:
        """读取导出任务入队时冻结的工程快照（不暴露到普通任务列表）。"""
        row = self._conn.execute(
            "SELECT project_json FROM export_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None or not row[0]:
            return None
        try:
            value = json.loads(row[0])
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    # ------------------------------------------------------------------
    # 保存：原子写（事务）
    # ------------------------------------------------------------------

    @_synchronized
    def save(self, project: Project) -> None:
        """原子保存工程快照。

        原子性保证（任务书 11.1/11.3，T08 验收「已确认提交在异常退出后恢复」）：

        1. 先在内存中完成 `project.to_dict()` → JSON 序列化，并做一次
           往返解析（json.loads）做完整性校验。若对象不可序列化或产生损坏
           JSON，异常在「进入事务之前」抛出，已存的好数据完全不受影响。
        2. 真正的数据库写入收敛在单个事务（PRAGMA 已设 WAL + 默认 NORMAL
           同步）内：要么整行 UPDATE/INSERT 提交成功，要么因任何异常回滚，
           绝不出现「工程主数据写了一半」的中间态。
        3. updated_at 与快照同事务提交，时间戳与状态一致。

        因此：进程在 save() 提交点之后崩溃可恢复；提交点之前崩溃，原工程
        保持上一次完整提交，符合已声明持久化保证。
        """
        obj = project.to_dict()  # 复用 model 序列化逻辑，不重写
        sequence_json = json.dumps(obj["sequence"], ensure_ascii=False)
        payload = json.dumps(obj, ensure_ascii=False)
        # 往返校验：确保写入的是可完整解析的 JSON，坏数据在事务外就失败。
        json.loads(sequence_json)
        json.loads(payload)

        with self._transaction():  # 显式事务：异常 ROLLBACK，成功 COMMIT
            self._conn.execute(
                """
                INSERT INTO projects
                    (project_id, schema_version, revision, sequence_json,
                     project_json, name, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    revision       = excluded.revision,
                    sequence_json  = excluded.sequence_json,
                    project_json   = excluded.project_json,
                    name           = excluded.name,
                    updated_at     = excluded.updated_at
                """,
                (
                    project.project_id,
                    project.schema_version,
                    project.revision,
                    sequence_json,
                    payload,
                    project.name,
                    time.time(),
                ),
            )

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    @_synchronized
    def load(self, project_id: str) -> Optional[Project]:
        """读取工程快照；不存在返回 None（任务书要求『无则返回 None 或抛错』）。

        优先读完整快照 project_json（含 favorites/recentEffects/presets 等
        工程级字段）；旧库没有该列/该值为空时回退到 sequence_json 组装，
        保证升级前后数据都能读。
        """
        row = self._conn.execute(
            "SELECT schema_version, revision, sequence_json, project_json, name "
            "FROM projects WHERE project_id=?",
            (project_id,),
        ).fetchone()
        if row is None:
            return None
        schema_version, revision, sequence_json, project_json, name = row
        if project_json:
            try:
                d = json.loads(project_json)
                d.setdefault("projectId", project_id)
                d.setdefault("revision", revision)
                # name 列是显示名的权威源（rename_project 只更新该列，
                # 不重写整份快照）；project_json 里的 name 只是创建时的
                # 冗余快照，加载时以列为准，避免改完名读回旧名。
                if name:
                    d["name"] = name
                return Project.from_dict(d)
            except (ValueError, TypeError):
                pass  # 完整快照损坏：回退 sequence 组装
        d = {
            "schemaVersion": schema_version,
            "projectId": project_id,
            "revision": revision,
            "sequence": json.loads(sequence_json),
            "name": name or "",
        }
        return Project.from_dict(d)  # 复用 model 反序列化逻辑

    @_synchronized
    def exists(self, project_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM projects WHERE project_id=?", (project_id,)
        ).fetchone()
        return row is not None

    @_synchronized
    def list_all(self) -> list[str]:
        """列出所有已保存的工程 ID。"""
        cur = self._conn.execute("SELECT project_id FROM projects ORDER BY project_id")
        return [row[0] for row in cur]

    @_synchronized
    def list_projects(self) -> list[dict]:
        """列出已保存工程的富信息，按 updated_at 倒序返回。

        返回字段：id / name / revision / updatedAt（ISO 8601 UTC 字符串）。
        仅读取工程主数据，不反序列化 sequence（列表场景无需全量）。
        """
        cur = self._conn.execute(
            "SELECT project_id, name, revision, updated_at "
            "FROM projects ORDER BY updated_at DESC"
        )
        out = []
        for project_id, name, revision, updated_at in cur:
            out.append({
                "id": project_id,
                "name": name or "",
                "revision": revision,
                "updatedAt": _iso_utc(updated_at),
            })
        return out

    @_synchronized
    def rename_project(self, project_id: str, name: str) -> bool:
        """仅重命名工程，不动 revision / 事件 / 历史。

        不存在返回 False；存在则更新 name 并刷新 updated_at，返回 True。
        """
        if self._conn.execute(
            "SELECT 1 FROM projects WHERE project_id=?", (project_id,)
        ).fetchone() is None:
            return False
        with self._transaction():
            self._conn.execute(
                "UPDATE projects SET name=?, updated_at=? WHERE project_id=?",
                (name, time.time(), project_id),
            )
        return True

    # ------------------------------------------------------------------
    # D02 素材元数据账本（资产 = 服务端权威）
    # ------------------------------------------------------------------

    @_synchronized
    def add_asset(self, *, asset_id: str, name: str, path: str, size: int,
                  kind: str = "unknown", duration: Optional[float] = None,
                  has_video: bool = False, has_audio: bool = False,
                  width: Optional[int] = None, height: Optional[int] = None,
                  builtin: bool = False) -> dict:
        """幂等登记一个素材（已上传并落盘后调用）。

        同 assetId 重复登记（重放 / 重启）时刷新为最新元数据（ON CONFLICT
        upsert），不产生重复行。返回登记后的富描述（与 list_assets 同形）。

        builtin=True 标记该素材为产品内置资产（J07 声音库），与用户上传素材
        共存同表，前端可据此显示「内置」徽标。
        """
        with self._transaction():
            self._conn.execute(
                """
                INSERT INTO assets
                    (asset_id, name, path, size, kind, duration,
                     has_video, has_audio, width, height, created_at, builtin)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET
                    name      = excluded.name,
                    path      = excluded.path,
                    size      = excluded.size,
                    kind      = excluded.kind,
                    duration  = excluded.duration,
                    has_video = excluded.has_video,
                    has_audio = excluded.has_audio,
                    width     = excluded.width,
                    height    = excluded.height,
                    builtin   = excluded.builtin
                """,
                (asset_id, name, path, size, kind, duration,
                 1 if has_video else 0, 1 if has_audio else 0,
                 width, height, time.time(), 1 if builtin else 0),
            )
        return self._asset_row_to_dict(self._asset_row(asset_id))

    def _asset_row(self, asset_id: str):
        return self._conn.execute(
            "SELECT asset_id, name, path, size, kind, duration, "
            "has_video, has_audio, width, height, created_at, builtin "
            "FROM assets WHERE asset_id=?", (asset_id,),
        ).fetchone()

    @staticmethod
    def _asset_row_to_dict(row) -> dict:
        if row is None:
            return {}
        return {
            "assetId": row[0],
            "name": row[1],
            "path": row[2],
            "size": row[3],
            "kind": row[4],
            "duration": row[5],
            "hasVideo": bool(row[6]),
            "hasAudio": bool(row[7]),
            "width": row[8],
            "height": row[9],
            "createdAt": _iso_utc(row[10]),
            "builtin": bool(row[11]),
        }

    @_synchronized
    def list_assets(self) -> list[dict]:
        """列出素材库，最近导入在前。"""
        cur = self._conn.execute(
            "SELECT asset_id, name, path, size, kind, duration, "
            "has_video, has_audio, width, height, created_at, builtin "
            "FROM assets ORDER BY created_at DESC"
        )
        return [self._asset_row_to_dict(r) for r in cur]

    @_synchronized
    def get_asset(self, asset_id: str) -> Optional[dict]:
        row = self._asset_row(asset_id)
        return self._asset_row_to_dict(row) if row else None

    # ------------------------------------------------------------------
    # 事件 outbox（可选，用于事件重发，任务书 11.1）
    # ------------------------------------------------------------------

    @_synchronized
    def append_event(self, project_id: str, payload: dict, event_id: str,
                     seq: Optional[int] = None) -> None:
        with self._transaction():
            self._conn.execute(
                "INSERT INTO events (event_id, project_id, payload_json, seq) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(event_id) DO NOTHING",
                (event_id, project_id, json.dumps(payload, ensure_ascii=False), seq),
            )

    @_synchronized
    def events_since(self, project_id: str, after_seq: Optional[int] = None):
        if after_seq is None:
            after_seq = -1
        cur = self._conn.execute(
            "SELECT event_id, payload_json, seq FROM events "
            "WHERE project_id=? AND seq > ? ORDER BY seq ASC",
            (project_id, after_seq),
        )
        out = []
        for event_id, payload_json, seq in cur:
            item = json.loads(payload_json)
            item.setdefault("eventId", event_id)
            item.setdefault("seq", seq)
            out.append(item)
        return out

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    @_synchronized
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ProjectStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
