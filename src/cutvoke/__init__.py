"""CutVoke: agent-first, local-first video editing.

四个接口共享一个核心引擎：
  - Web UI (React)   : 人工编辑、审阅、接受/拒绝
  - MCP server       : Claude / Codex / Cursor agents
  - CLI              : 脚本、CI、自动化
  - HTTP API         : REST/JSON

核心契约（domain/ 与 editing/）：
  - rational  有理数时间（任务书 7.2，禁止浮点秒累加）
  - model     工程/序列/轨道/片段/素材引用（任务书 7.1）
  - protocol  命令/事件/错误/修订（任务书 8/10/15）
  - service   统一编辑服务（任务书 8，幂等 + 版本校验 + 事件）
"""

__version__ = "0.3.4"

from cutvoke.domain.model import Project, Sequence, Track, Clip, AssetReference, EffectInstance, Caption, Marker
from cutvoke.domain.rational import Rational
from cutvoke.domain.protocol import (
    Command, CommandResult, Event, Actor, ErrorCode, ExitCode,
)
from cutvoke.editing.service import EditService
from cutvoke.domain.invariants import validate_project, ValidationResult
from cutvoke.domain.captions import parse_srt, export_srt
from cutvoke.rendering.effects import EffectRegistry, EffectSpec, default_registry

__all__ = [
    "Project", "Sequence", "Track", "Clip", "AssetReference",
    "EffectInstance", "Caption", "Marker",
    "Rational",
    "Command", "CommandResult", "Event", "Actor", "ErrorCode", "ExitCode",
    "EditService", "validate_project", "ValidationResult",
    "parse_srt", "export_srt",
    "EffectRegistry", "EffectSpec", "default_registry",
    "__version__",
]
