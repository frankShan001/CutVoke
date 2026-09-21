"""CutVoke 协议 schema：命令、事件、错误（符合任务书第 8/9/10/15 章契约）。

统一命令结构（8.1）、提交顺序与幂等（8.2）、原子批量（8.3）、
事件同步（10.1）、统一错误码（15.2）。

M0 阶段用 dataclass + 手写 JSON，M1 起可生成 OpenAPI/JSON Schema。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .rational import Rational


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------

@dataclass
class Actor:
    """操作者（审计用）。认证由连接凭据完成，不信任 actor 作为权限依据。"""
    kind: str  # "agent" | "human" | "cli" | "mcp"
    id: str


@dataclass
class Command:
    """统一编辑命令（8.1）。"""
    type: str
    payload: dict[str, Any]
    command_id: str = field(default_factory=lambda: f"cmd_{uuid.uuid4().hex[:12]}")
    project_id: str = ""
    expected_revision: str = ""
    actor: Optional[Actor] = None
    protocol_version: str = "1"
    # Agent 编辑租约；为空表示普通人工编辑请求。
    edit_lease_id: str = ""

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "protocolVersion": self.protocol_version,
            "commandId": self.command_id,
            "projectId": self.project_id,
            "expectedRevision": self.expected_revision,
            "type": self.type,
            "payload": self.payload,
        }
        if self.actor is not None:
            d["actor"] = {"kind": self.actor.kind, "id": self.actor.id}
        if self.edit_lease_id:
            d["editLeaseId"] = self.edit_lease_id
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Command":
        actor = None
        if d.get("actor"):
            actor = Actor(kind=d["actor"]["kind"], id=d["actor"]["id"])
        return cls(
            type=d["type"],
            payload=d.get("payload", {}),
            command_id=d["commandId"],
            project_id=d.get("projectId", ""),
            expected_revision=d.get("expectedRevision", ""),
            actor=actor,
            protocol_version=d.get("protocolVersion", "1"),
            edit_lease_id=d.get("editLeaseId", ""),
        )


@dataclass
class CommandResult:
    """命令成功结果（8.1）。"""
    command_id: str
    previous_revision: str
    revision: str
    transaction_id: str
    changed_entities: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "commandId": self.command_id,
            "previousRevision": self.previous_revision,
            "revision": self.revision,
            "transactionId": self.transaction_id,
            "changedEntities": self.changed_entities,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CommandResult":
        """从 to_dict() 的 dict 还原（持久幂等复用 / 跨进程传递）。"""
        return cls(
            command_id=d["commandId"],
            previous_revision=d["previousRevision"],
            revision=d["revision"],
            transaction_id=d["transactionId"],
            changed_entities=d.get("changedEntities", []),
            warnings=d.get("warnings", []),
        )


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------

@dataclass
class Event:
    """工程变更事件（10.1）。"""
    event_id: str
    project_id: str
    previous_revision: str
    revision: str
    command_id: str
    transaction_id: str
    actor: Optional[Actor]
    type: str = "project.changed"
    changed_entities: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "eventId": self.event_id,
            "projectId": self.project_id,
            "previousRevision": self.previous_revision,
            "revision": self.revision,
            "commandId": self.command_id,
            "transactionId": self.transaction_id,
            "type": self.type,
            "changedEntities": self.changed_entities,
        }
        if self.actor is not None:
            d["actor"] = {"kind": self.actor.kind, "id": self.actor.id}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        """从持久化 outbox 还原事件（跨进程重连/补取使用）。"""
        actor = None
        if d.get("actor"):
            actor = Actor(kind=d["actor"]["kind"], id=d["actor"]["id"])
        return cls(
            event_id=d["eventId"],
            project_id=d["projectId"],
            previous_revision=d.get("previousRevision", ""),
            revision=d.get("revision", ""),
            command_id=d.get("commandId", ""),
            transaction_id=d.get("transactionId", ""),
            actor=actor,
            type=d.get("type", "project.changed"),
            changed_entities=d.get("changedEntities", []),
        )


# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------

# 统一错误码（任务书 15.2 表格）
class ErrorCode:
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    REVISION_UNAVAILABLE = "REVISION_UNAVAILABLE"
    IDEMPOTENCY_MISMATCH = "IDEMPOTENCY_MISMATCH"
    UNDO_CONFLICT = "UNDO_CONFLICT"
    ASSET_MISSING = "ASSET_MISSING"
    ASSET_CHANGED = "ASSET_CHANGED"
    INSUFFICIENT_HANDLES = "INSUFFICIENT_HANDLES"
    EFFECT_UNAVAILABLE = "EFFECT_UNAVAILABLE"
    FONT_UNAVAILABLE = "FONT_UNAVAILABLE"
    UNSUPPORTED_MEDIA = "UNSUPPORTED_MEDIA"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    DISK_FULL = "DISK_FULL"
    RESYNC_REQUIRED = "RESYNC_REQUIRED"
    WORKER_FAILED = "WORKER_FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    ACCESS_DENIED = "ACCESS_DENIED"
    EDIT_LOCKED = "EDIT_LOCKED"


@dataclass
class Error:
    """统一错误响应（15.2）。"""
    code: str
    message: str
    details: Any = None
    retryable: bool = False
    committed: bool = False
    correlation_id: str = field(default_factory=lambda: f"corr_{uuid.uuid4().hex[:12]}")

    def to_dict(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "retryable": self.retryable,
                "committed": self.committed,
                "correlationId": self.correlation_id,
            }
        }


# ---------------------------------------------------------------------------
# 修订 / 版本
# ---------------------------------------------------------------------------

def bump_revision(revision: str) -> str:
    """修订号单调递增（字符串十进制）。"""
    return str(int(revision) + 1)


# ---------------------------------------------------------------------------
# 退出码（任务书 9.3）
# ---------------------------------------------------------------------------

class ExitCode:
    OK = 0
    INVALID_ARGUMENT = 2
    VERSION_CONFLICT = 3
    RESOURCE_UNAVAILABLE = 4
    EXECUTION_FAILED = 5
    CANCELLED = 6
    TIMEOUT = 7
    SERVICE_ERROR = 8
