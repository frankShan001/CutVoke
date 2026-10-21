"""CutVoke 本地访问治理（任务书 T31）：绑定策略、来源检查、会话令牌、路径边界、资源上限。

纯标准库实现，运行时零第三方依赖（与 core 其它模块一致）。
所有拒绝路径都返回结构化、可诊断的中文原因，便于用户自查与排障。

本模块只新增能力，不修改 httpapi / main / service / store / render / mcp_server，
由后续接线把这些能力接入 HTTP 服务与 CLI。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# 回环白名单与公共常量
# ---------------------------------------------------------------------------

# 允许作为本地来源（Origin / Host）的主机名集合（端口不限）。
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

# 会话令牌目录 / 文件名（用户目录 ~/.cutvoke/session.json）。
SESSION_DIR_NAME = "cutvoke"
SESSION_FILE_NAME = "session.json"

# 令牌字节长度（32 字节 = 256 bit）。
TOKEN_BYTES = 32

# 请求令牌头 / 查询参数名。
TOKEN_HEADER = "X-CutVoke-Token"


# ---------------------------------------------------------------------------
# 错误类型
# ---------------------------------------------------------------------------

class GovernanceError(Exception):
    """治理层统一异常基类；携带可诊断的中文原因与错误码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - 仅调试用
        return f"{self.code}: {self.message}"


class BindingError(GovernanceError):
    """非回环绑定被拒绝。"""


class PathEscapeError(GovernanceError):
    """静态文件路径越界。"""


class LimitExceededError(GovernanceError):
    """资源上限被突破。"""


# ---------------------------------------------------------------------------
# 本地默认绑定策略
# ---------------------------------------------------------------------------

def is_loopback_host(host: Optional[str]) -> bool:
    """判断主机名是否属于回环白名单（去除 IPv6 方括号后比较）。"""
    if not host:
        return False
    h = host.strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    return h in LOOPBACK_HOSTS


def resolve_binding(host: str, *, allow_non_loopback: bool = False) -> str:
    """确认绑定地址是否允许（默认仅回环）。

    默认只接受回环地址（127.0.0.1 / ::1 / localhost）。监听所有接口
    （0.0.0.0 / :: / * / 空）或非回环地址，必须显式传入
    allow_non_loopback=True，否则抛 BindingError，错误信息给出可诊断的解决办法。

    返回规范化后的 host（通过检查）。
    """
    h = (host or "").strip()
    # 监听所有接口：等同于非回环，需显式允许。
    if h in ("", "0.0.0.0", "::", "*"):
        if allow_non_loopback:
            return host
        raise BindingError(
            "BIND_NOT_LOOPBACK",
            "拒绝绑定到非回环地址 %r：CutVoke 默认只允许本地访问（127.0.0.1 / ::1 / localhost）。"
            "若确需对外暴露，请显式传入 allow_non_loopback=True，"
            "并确保前置反向代理已开启认证（如会话令牌），否则存在被远程调用的风险。" % host,
        )
    if is_loopback_host(h):
        return host
    if allow_non_loopback:
        return host
    raise BindingError(
        "BIND_NOT_LOOPBACK",
        "拒绝绑定到非回环地址 %r：CutVoke 默认只允许本地访问（127.0.0.1 / ::1 / localhost）。"
        "若确需对外暴露，请显式传入 allow_non_loopback=True，"
        "并确保前置反向代理已开启认证（如会话令牌）。" % host,
    )


# ---------------------------------------------------------------------------
# 来源检查（Origin / Host）—— 防非授权网页与 DNS rebinding
# ---------------------------------------------------------------------------

ORIGIN_OK = "ORIGIN_ALLOWED"
ORIGIN_FORBIDDEN = "ORIGIN_FORBIDDEN"
HOST_NOT_LOOPBACK = "HOST_NOT_LOOPBACK"

ORIGIN_MESSAGES = {
    ORIGIN_OK: "来源合法（同源简单请求、CLI/脚本调用或回环本地来源）",
    ORIGIN_FORBIDDEN: "来源被拒绝：Origin 不是回环本地来源，非授权网页无法直接访问 CutVoke API（已阻止跨站请求）。",
    HOST_NOT_LOOPBACK: "来源被拒绝：Host 头不是回环地址，疑似 DNS 重绑定攻击，已拒绝。",
}


def _header_get(headers: Any, name: str) -> Optional[str]:
    """大小写不敏感取头；兼容 dict 与 http.server 的 email.message.Message。"""
    if headers is None:
        return None
    if isinstance(headers, Mapping):
        low = name.lower()
        for k, v in headers.items():
            if k.lower() == low:
                return v
        return None
    # email.message.Message 等兼容对象
    val = headers.get(name)
    return val if isinstance(val, str) else None


def _host_from_value(value: Optional[str]) -> Optional[str]:
    """从 Origin/Host 头里取出纯主机名（剥离端口，IPv6 去方括号）。"""
    if not value:
        return None
    v = value.strip()
    if v.startswith("["):  # IPv6 形式 [::1]:8787
        end = v.find("]")
        if end != -1:
            return v[1:end]
        return v
    return v.split(":", 1)[0]  # 普通 host:port


def check_origin(headers: Any) -> Tuple[bool, str]:
    """检查请求来源（Origin / Host）。

    规则（精确）：
      - Origin 缺失 → 允许（同源简单请求、CLI/脚本调用）
      - Origin 的 host 属于回环白名单（localhost / 127.0.0.1 / [::1]，端口不限）→ 允许
      - 其他 Origin → 拒绝，返回错误码 ORIGIN_FORBIDDEN
      - Host 头若存在，也必须是回环，否则拒绝（防 DNS rebinding）

    返回 (ok, code)；code 可用 ORIGIN_MESSAGES[code] 取得中文说明，
    用于构造 403 响应或日志。
    """
    origin = _header_get(headers, "Origin")
    if origin:
        parsed = urlparse(origin)
        host = _host_from_value(parsed.netloc) if parsed.netloc else None
        if not host or not is_loopback_host(host):
            return False, ORIGIN_FORBIDDEN

    # Host 头：存在则必须是回环（防 DNS rebinding）。
    host_hdr = _header_get(headers, "Host")
    if host_hdr:
        host = _host_from_value(host_hdr)
        if host and not is_loopback_host(host):
            return False, HOST_NOT_LOOPBACK

    return True, ORIGIN_OK


# ---------------------------------------------------------------------------
# 会话令牌访问控制
# ---------------------------------------------------------------------------

TOKEN_OK = "TOKEN_ALLOWED"
TOKEN_MISSING = "TOKEN_MISSING"
TOKEN_INVALID = "TOKEN_INVALID"


class SessionToken:
    """会话令牌：32 字节随机令牌，持久化到用户目录，常量时间校验。

    默认不启用（匿名本地访问）。启用后所有 /api/ 请求必须携带令牌，
    否则 401。令牌**绝不写入日志**（见 redact）。
    """

    def __init__(self, token_hash: str, *, token: Optional[str] = None,
                 path: Optional[str] = None) -> None:
        self._hash = token_hash
        # 原始令牌仅在 create()/rotate() 后短时间内驻留内存，用于一次性回显。
        self._token = token
        self.path = Path(path) if path else self._default_path()

    # ---- 路径 ----
    @staticmethod
    def _user_dir() -> Path:
        base = os.environ.get("CUTVOKE_HOME")
        if base:
            return Path(base)
        return Path(os.path.expanduser(f"~/.{SESSION_DIR_NAME}"))

    @classmethod
    def _default_path(cls) -> Path:
        return cls._user_dir() / SESSION_FILE_NAME

    # ---- 创建 / 加载 ----
    @classmethod
    def create(cls, path: Optional[str] = None) -> "SessionToken":
        """生成新令牌并持久化，返回持有原始令牌的实例（供 CLI 一次性回显）。"""
        token = secrets.token_hex(TOKEN_BYTES)
        inst = cls(cls._hash_token(token), token=token, path=path)
        inst.persist()
        return inst

    @classmethod
    def load(cls, path: Optional[str] = None) -> Optional["SessionToken"]:
        """从文件加载；未创建（文件不存在）返回 None。加载后内存不持有原始令牌。"""
        p = Path(path) if path else cls._default_path()
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise GovernanceError(
                "SESSION_LOAD_FAILED", f"会话令牌文件读取失败：{e}") from e
        if "tokenHash" not in data:
            raise GovernanceError(
                "SESSION_LOAD_FAILED",
                "会话令牌文件缺少 tokenHash 字段，可能已损坏。")
        return cls(data["tokenHash"], token=None, path=str(p))

    @classmethod
    def is_enabled(cls, path: Optional[str] = None) -> bool:
        """是否已启用令牌（session.json 是否存在）。"""
        p = Path(path) if path else cls._default_path()
        return p.is_file()

    # ---- 校验 ----
    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def verify(self, token: Optional[str]) -> bool:
        """常量时间校验令牌（hmac.compare_digest 比较哈希，避免时序侧信道）。"""
        if not token or not self._hash:
            return False
        return hmac.compare_digest(self._hash_token(token), self._hash)

    def rotate(self) -> "SessionToken":
        """轮换令牌：生成新令牌、持久化、使旧令牌立即失效。"""
        token = secrets.token_hex(TOKEN_BYTES)
        self._hash = self._hash_token(token)
        self._token = token
        self.persist()
        return self

    def reveal(self) -> Optional[str]:
        """返回明文令牌（仅在 create()/rotate() 之后内存中仍有副本时可用）。

        从文件 load() 出来的实例**永远**拿不到明文（只存哈希），
        这是刻意的设计：磁盘上不落明文令牌。
        """
        return self._token

    # ---- 持久化（权限尽量收紧）----
    def persist(self) -> None:
        """原子写入会话文件，目录 0700、文件 0600（Windows 上 chmod 尽力而为）。"""
        d = self.path.parent
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass  # Windows 无 POSIX 权限语义，忽略
        payload = {
            "tokenHash": self._hash,
            "createdAt": time.time(),
            "note": "CutVoke 会话令牌：仅本地 CLI/HTTP 使用，切勿外泄或写入日志。",
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)  # 原子替换，避免半写文件
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    # ---- 脱敏 ----
    @staticmethod
    def redact(value: Optional[str]) -> str:
        """令牌脱敏：日志中一律以占位符代替，绝不出现真实令牌。"""
        if value is None:
            return "<none>"
        return "<token:redacted>"


def extract_request_token(headers: Any,
                          query: Optional[Mapping[str, list]] = None) -> Optional[str]:
    """从 X-CutVoke-Token 头或 ?token= 查询参数取出令牌（头优先）。"""
    h = _header_get(headers, TOKEN_HEADER)
    if h:
        return h
    if query:
        vals = query.get("token")
        if vals:
            return vals[0]
    return None


def check_request_token(headers: Any,
                        query: Optional[Mapping[str, list]] = None,
                        session: Optional[SessionToken] = None) -> Tuple[bool, str]:
    """校验一次 /api/ 请求是否携带有效令牌。

    - 未启用令牌（session 为 None）→ 允许匿名本地访问。
    - 启用后缺令牌 → (False, TOKEN_MISSING)；令牌错误 → (False, TOKEN_INVALID)。
    """
    if session is None:
        return True, TOKEN_OK
    tok = extract_request_token(headers, query)
    if not tok:
        return False, TOKEN_MISSING
    if session.verify(tok):
        return True, TOKEN_OK
    return False, TOKEN_INVALID


# ---------------------------------------------------------------------------
# 路径边界（静态文件服务必用入口）
# ---------------------------------------------------------------------------

def safe_path(base_dir: Any, requested: Any) -> Path:
    """把用户请求的相对路径约束在 base_dir 之内，解析后返回绝对路径。

    拒绝：
      - 绝对路径（盘符 / UNC 逃逸）
      - 通过 ../ 穿越到 base_dir 之外
      - Windows 下盘符或 UNC 前缀与基目录不一致的路径

    越界抛 PathEscapeError，错误信息给出可诊断的中文原因。
    """
    base = Path(base_dir).resolve()
    req = Path(requested)
    if req.is_absolute():
        raise PathEscapeError(
            "PATH_ABSOLUTE",
            f"拒绝绝对路径 {requested!r}：静态文件必须相对于基目录 {base} 请求。",
        )
    resolved = (base / req).resolve()
    # Windows 盘符 / UNC 一致性检查，防盘符与 UNC 逃逸。
    if os.name == "nt":
        if resolved.drive != base.drive or resolved.root != base.root:
            raise PathEscapeError(
                "PATH_DRIVE_MISMATCH",
                f"路径越界 {requested!r}：解析为 {resolved}，其盘符/根与基目录 "
                f"{base} 不一致（疑似盘符或 UNC 逃逸）。",
            )
    if resolved != base and base not in resolved.parents:
        raise PathEscapeError(
            "PATH_ESCAPE",
            f"路径越界 {requested!r}：解析为 {resolved}，超出基目录 {base}。",
        )
    return resolved


# ---------------------------------------------------------------------------
# 资源上限（线程安全；超限即抛异常，不静默排队到死）
# ---------------------------------------------------------------------------

@dataclass
class Limits:
    """资源上限配置。"""
    max_body_bytes: int = 16 * 1024 * 1024          # 单请求体最大字节
    max_concurrent_exports: int = 2                  # 最大并发导出任务数
    request_timeout_seconds: float = 30.0            # 单请求超时（秒）


class ResourceLimiter:
    """线程安全的资源上限闸门。超限立即抛 LimitExceededError。"""

    def __init__(self, limits: Optional[Limits] = None) -> None:
        self.limits = limits or Limits()
        self._sem = threading.Semaphore(self.limits.max_concurrent_exports)

    def check_body(self, n: int) -> None:
        """检查请求体字节数是否超限。"""
        if n > self.limits.max_body_bytes:
            raise LimitExceededError(
                "BODY_TOO_LARGE",
                f"请求体过大：{n} 字节超过上限 {self.limits.max_body_bytes} 字节，已拒绝。",
            )

    def export_slot(self) -> "_ExportSlot":
        """上下文管理器：获取一个导出槽位；无可用槽位立即抛异常（不阻塞排队）。"""
        return _ExportSlot(self._sem, self.limits.max_concurrent_exports)


class _ExportSlot:
    def __init__(self, sem: threading.Semaphore, cap: int) -> None:
        self._sem = sem
        self._cap = cap
        self._held = False

    def __enter__(self) -> "_ExportSlot":
        if not self._sem.acquire(blocking=False):
            raise LimitExceededError(
                "EXPORT_SLOT_EXHAUSTED",
                f"并发导出任务已达上限 {self._cap}：拒绝新导出，请稍后重试或降低并发。",
            )
        self._held = True
        return self

    def __exit__(self, *exc) -> bool:
        if self._held:
            self._sem.release()
            self._held = False
        return False
