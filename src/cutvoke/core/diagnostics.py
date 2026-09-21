"""CutVoke 诊断与结构化日志（任务书 T31）：可定位、可脱敏、可恢复。

纯标准库实现，运行时零第三方依赖。
- 日志：字段 ts/level/event/projectId/revision/taskId/durationMs/detail，每行一个 JSON。
- 脱敏：屏蔽密钥类字段与长随机串；素材只记路径摘要与大小，绝不读媒体正文。
- 诊断：磁盘预算、工程完整性（只读）、故障恢复分类。

本模块只新增能力，不修改任何既有文件。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 脱敏
# ---------------------------------------------------------------------------

# 命中这些 key 的值一律脱敏（不区分大小写）。
_SECRET_KEY_RE = re.compile(
    r"(token|secret|key|password|authorization|auth|sessionid|api[_-]?key)",
    re.IGNORECASE,
)
# 看起来像长随机凭证的字符串（>=16 位大小写字母/数字/+/=/_/-）。
_RANDOM_STR_RE = re.compile(r"^[A-Za-z0-9+/=_-]{16,}$")
# 句子/自由文本里内嵌的凭证子串（>=24 位十六进制或 base64 风格连续串）。
# 必要性：日志的 detail 字段常是拼出来的自由文本（例如
# "Authorization: <token>"），只按 key 名脱敏会把令牌整段漏出去。
# 32 字节 secrets.token_hex() 长度正好 64，这里从 24 起就能拦住。
_EMBEDDED_SECRET_RE = re.compile(r"[A-Za-z0-9+/=_-]{24,}")


def redact(obj: Any, _depth: int = 0) -> Any:
    """递归脱敏：屏蔽密钥类字段值，以及看起来像长随机串的值。

    - dict：key 命中密钥模式 → 值置 "<redacted>"；否则递归处理 value。
    - list/tuple：逐元素递归。
    - str：整串命中长随机串模式 → "<redacted:token-like>"；
      否则把串内嵌的长随机子串也替换掉（防"自由文本里带令牌"）。
    不读取、不记录任何媒体正文，仅处理结构化元数据。
    """
    if _depth > 64:
        return "<redacted:too-deep>"
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                out[k] = "<redacted>"
            else:
                out[k] = redact(v, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(x, _depth + 1) for x in obj]
    if isinstance(obj, str):
        if _RANDOM_STR_RE.match(obj):
            return "<redacted:token-like>"
        return _EMBEDDED_SECRET_RE.sub("<redacted:token-like>", obj)
    return obj


def summarize_media_ref(path: Any) -> Dict[str, Any]:
    """只记录素材引用的摘要（文件名 + 大小），绝不读取媒体正文。"""
    p = Path(path)
    try:
        exists = p.exists()
        size = p.stat().st_size if exists else 0
    except OSError:
        exists = False
        size = 0
    return {
        "name": p.name,
        "exists": exists,
        "sizeBytes": size,
        "path": str(p),
    }


# ---------------------------------------------------------------------------
# 磁盘预算
# ---------------------------------------------------------------------------

class DiskBudgetError(Exception):
    """磁盘空间不足：携带可用/所需字节，便于可诊断报错。"""

    def __init__(self, message: str, *, free_bytes: int,
                 required_bytes: int, path: str) -> None:
        super().__init__(message)
        self.free_bytes = free_bytes
        self.required_bytes = required_bytes
        self.path = path


def check_disk_budget(path: Any, required_bytes: int) -> Dict[str, Any]:
    """检查路径所在文件系统剩余空间是否足以容纳 required_bytes。

    不足时抛 DiskBudgetError（含可用/所需空间与路径）；充足时返回结构化信息。
    只读操作，不修改任何文件。
    """
    target = Path(path) if path else Path.cwd()
    try:
        usage = shutil.disk_usage(target)
    except OSError:
        # 路径不存在：退到父目录再探测一次。
        parent = target.parent if target.parent else Path.cwd()
        try:
            usage = shutil.disk_usage(parent)
        except OSError as e2:
            raise DiskBudgetError(
                f"无法获取磁盘信息（{e2}）；请确认路径 {target} 所在文件系统可访问。",
                free_bytes=0, required_bytes=required_bytes, path=str(target),
            ) from e2
    free = usage.free
    if free < required_bytes:
        raise DiskBudgetError(
            f"磁盘空间不足：可用 {free} 字节，所需 {required_bytes} 字节（路径 {target}）。"
            "请清理磁盘或更改导出/缓存目录后再试，工程数据不受影响。",
            free_bytes=free, required_bytes=required_bytes, path=str(target),
        )
    return {"ok": True, "freeBytes": free,
            "requiredBytes": required_bytes, "path": str(target)}


# ---------------------------------------------------------------------------
# 结构化 JSONL 日志
# ---------------------------------------------------------------------------

class JsonlLogger:
    """结构化 JSONL 日志：每行一个 JSON 对象，线程安全。

    默认路径 ~/.cutvoke/logs/cutvoke-YYYY-MM-DD.jsonl。
    任何写入的 detail 都会经 redact() 脱敏，确保不记录敏感令牌或媒体正文。
    """

    _LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

    def __init__(self, log_path: Optional[Any] = None) -> None:
        if log_path is None:
            d = Path(os.path.expanduser("~/.cutvoke/logs"))
            d.mkdir(parents=True, exist_ok=True)
            log_path = d / f"cutvoke-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        self.path = Path(log_path)
        self._lock = threading.Lock()

    def log(self, level: str, event: str, *,
            project_id: Optional[str] = None,
            revision: Optional[str] = None,
            task_id: Optional[str] = None,
            duration_ms: Optional[float] = None,
            detail: Any = None) -> None:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level if level in self._LEVELS else "INFO",
            "event": event,
            "projectId": project_id,
            "revision": revision,
            "taskId": task_id,
            "durationMs": duration_ms,
            "detail": redact(detail),
        }
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def info(self, event: str, **kw) -> None:
        self.log("INFO", event, **kw)

    def warning(self, event: str, **kw) -> None:
        self.log("WARNING", event, **kw)

    def error(self, event: str, **kw) -> None:
        self.log("ERROR", event, **kw)


# ---------------------------------------------------------------------------
# 工程完整性诊断（只读）
# ---------------------------------------------------------------------------

# 内建效果 ID 的回退清单：仅在效果注册表不可用时使用（正常路径走注册表，
# 这样用户自行安装的第三方效果不会被误判为"缺失"）。
KNOWN_EFFECT_IDS = frozenset({
    "cutvoke.transform",
    "cutvoke.color",
    "cutvoke.transition.crossfade",
    "cutvoke.transition.fade",
})


def _resolve_known_effects(override: Optional[Any] = None) -> frozenset:
    """解析"已知效果"集合：显式覆盖 > 效果注册表 > 内建回退清单。"""
    if override is not None:
        return frozenset(override)
    try:
        from .effects import default_registry
        ids = default_registry().ids()
        if ids:
            return frozenset(ids)
    except Exception:  # noqa: BLE001 — 诊断不应因注册表异常而整体失败
        pass
    return KNOWN_EFFECT_IDS


def _find_db(project_dir: Any) -> Optional[Path]:
    """在 project_dir 中定位工程 SQLite（不修改任何文件）。"""
    d = Path(project_dir)
    if d.is_file():
        return d
    if not d.is_dir():
        return None
    for name in ("projects.sqlite", "project.sqlite", "cutvoke.sqlite"):
        cand = d / name
        if cand.is_file():
            return cand
    for cand in sorted(d.iterdir()):  # 兜底：首个 .sqlite/.db
        if cand.is_file() and cand.suffix in (".sqlite", ".db", ".sqlite3"):
            return cand
    return None


def _check_asset(src: str) -> Dict[str, Any]:
    """检查单个素材是否可达 / 是否断流（0 字节）。只 stat，不读内容。"""
    p = Path(src)
    try:
        exists = p.exists()
        size = p.stat().st_size if exists else 0
    except OSError:
        exists, size = False, 0
    status = "ok"
    if not exists:
        status = "missing"
    elif size == 0:
        status = "broken"
    return {
        "name": p.name,
        "path": str(p),
        "exists": exists,
        "sizeBytes": size,
        "status": status,
    }


def diagnose_project(project_dir: Any, *,
                     known_effect_ids: Optional[Any] = None) -> Dict[str, Any]:
    """工程完整性只读诊断。

    检查：工程文件 / SQLite 是否可读、素材是否可达、素材是否断流
    （文件缺失或大小为 0）、引用的效果是否缺失。

    已知效果集合默认取自**效果注册表**（内置 + 用户效果目录），
    因此用户自行安装的第三方效果不会被误报为缺失；也可用 known_effect_ids 显式覆盖。

    不修改任何东西（只读）；返回 {ok, projectDir, projects, warnings, errors, checkedAt}。
    """
    known_effects = _resolve_known_effects(known_effect_ids)
    report: Dict[str, Any] = {
        "ok": True,
        "projectDir": str(project_dir),
        "projects": [],
        "warnings": [],
        "errors": [],
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "knownEffectCount": len(known_effects),
    }

    db = _find_db(project_dir)
    if db is None:
        report["ok"] = False
        report["errors"].append({
            "code": "PROJECT_NOT_FOUND",
            "message": f"未找到工程数据库（期望 projects.sqlite 等）于 {project_dir}。",
        })
        return report

    # 只读打开（mode=ro），绝不写 -wal/-shm 或修改数据库。
    uri = f"file:///{db.resolve().as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        report["ok"] = False
        report["errors"].append({
            "code": "DB_UNREADABLE",
            "message": f"工程数据库无法读取：{e}",
        })
        return report

    try:
        try:
            cur = conn.execute(
                "SELECT project_id, revision, sequence_json FROM projects")
            rows = cur.fetchall()
        except sqlite3.Error as e:
            report["ok"] = False
            report["errors"].append({
                "code": "DB_SCHEMA_INVALID",
                "message": f"工程表读取失败（schema 可能不兼容）：{e}",
            })
            return report

        if not rows:
            report["warnings"].append({
                "code": "NO_PROJECT",
                "message": "工程数据库为空，未含任何工程。",
            })

        for project_id, revision, sequence_json in rows:
            pinfo: Dict[str, Any] = {
                "projectId": project_id, "revision": revision,
                "assets": [], "effects": [],
            }
            try:
                seq = json.loads(sequence_json)
            except json.JSONDecodeError as e:
                report["ok"] = False
                report["errors"].append({
                    "code": "SEQUENCE_CORRUPT",
                    "message": f"工程 {project_id} 的序列数据损坏：{e}",
                })
                continue

            tracks = seq.get("tracks", []) if isinstance(seq, dict) else []
            for track in tracks:
                if not isinstance(track, dict):
                    continue
                clips = track.get("clips", [])
                for clip in clips:
                    if not isinstance(clip, dict):
                        continue
                    # 素材可达性 / 断流
                    asset = clip.get("assetRef", {}) or {}
                    src = asset.get("sourcePath", "") if isinstance(asset, dict) else ""
                    if src:
                        pinfo["assets"].append(_check_asset(src))
                    # 效果缺失
                    effects = clip.get("effects", []) or []
                    for eff in effects:
                        if isinstance(eff, dict):
                            eid = eff.get("effectId", "")
                            if eid and eid not in known_effects:
                                pinfo["effects"].append({
                                    "effectId": eid,
                                    "status": "unknown",
                                    "message": (f"引用的效果 {eid} 未注册（内置与用户效果目录"
                                                "均未找到），可能缺失或未安装。"),
                                })
            report["projects"].append(pinfo)

            # 汇总素材 / 效果问题为 warnings
            for a in pinfo["assets"]:
                if a.get("status") in ("missing", "broken"):
                    report["warnings"].append({
                        "code": "ASSET_" + a["status"].upper(),
                        "message": (f"工程 {project_id} 素材{a['status']}："
                                    f"{a['name']}（{a['path']}）"),
                        "ref": a,
                    })
            for e in pinfo["effects"]:
                if e.get("status") == "unknown":
                    report["warnings"].append({
                        "code": "EFFECT_UNKNOWN",
                        "message": e["message"],
                        "ref": e,
                    })
    finally:
        conn.close()

    report["ok"] = report["ok"] and not report["errors"]
    return report


# ---------------------------------------------------------------------------
# 故障恢复分类
# ---------------------------------------------------------------------------

# 可自动恢复的故障（通常无需人工干预，或在恢复条件满足后自动重试）。
_AUTO_RECOVERABLE = {
    "DISK_FULL", "DISK_BUDGET", "REVISION_CONFLICT", "REVISION_UNAVAILABLE",
    "TIMEOUT", "RESOURCE_LIMIT", "CANCELLED", "EXPORT_SLOT_EXHAUSTED",
    "BODY_TOO_LARGE",
}
# 需人工处理的故障。
_MANUAL_REQUIRED = {
    "ASSET_MISSING", "ASSET_BROKEN", "ASSET_CHANGED", "EFFECT_UNAVAILABLE",
    "EFFECT_UNKNOWN", "PROJECT_NOT_FOUND", "DB_UNREADABLE",
    "DB_SCHEMA_INVALID", "SEQUENCE_CORRUPT", "SESSION_LOAD_FAILED",
}

_RECOVERY_STEPS = {
    "DISK_FULL": "磁盘已满：清理导出/缓存目录或扩容后重试，工程数据不受影响。",
    "DISK_BUDGET": "磁盘空间不足：释放空间或更改导出目录后再试。",
    "REVISION_CONFLICT": "版本冲突：以服务端最新 revision 重新拉取工程并合并后再提交（可自动 resync）。",
    "REVISION_UNAVAILABLE": "版本不可用：拉取最新工程快照后重试。",
    "TIMEOUT": "超时：减少单批命令量或提升超时阈值后重试。",
    "RESOURCE_LIMIT": "资源上限：降低并发或分批处理。",
    "EXPORT_SLOT_EXHAUSTED": "并发导出已满：稍后重试或降低并发导出数。",
    "BODY_TOO_LARGE": "请求体过大：拆分请求或提高 max_body_bytes 后重试。",
    "CANCELLED": "任务已取消：按需重新触发。",
    "ASSET_MISSING": "素材缺失：用「重新链接素材」指向正确的媒体文件，或恢复原始素材。",
    "ASSET_BROKEN": "素材断流（0 字节）：重新抓取/导出该素材到原路径。",
    "ASSET_CHANGED": "素材内容变更：确认指纹一致后重新关联，或重新导入。",
    "EFFECT_UNAVAILABLE": "效果不可用：安装或启用对应效果插件（见本地开发/effects）。",
    "EFFECT_UNKNOWN": "效果缺失：安装对应效果，或改用内建效果（transform/color/crossfade/fade）。",
    "PROJECT_NOT_FOUND": "工程不存在：确认工程目录与 SQLite 路径正确。",
    "DB_UNREADABLE": "数据库不可读：检查文件权限或磁盘健康，必要时从备份恢复 -wal/-shm。",
    "DB_SCHEMA_INVALID": "数据库 schema 不兼容：升级后执行迁移或用备份恢复。",
    "SEQUENCE_CORRUPT": "序列数据损坏：用最近一次完整提交/检查点恢复工程。",
    "SESSION_LOAD_FAILED": "会话令牌损坏：删除 session.json 后重新 create()。",
}


def recovery_report(errors: List[Any]) -> Dict[str, Any]:
    """把故障分类为可自动恢复 / 需人工处理，并给出恢复步骤文本。

    errors 元素可为错误码字符串，或含 'code' 的 dict（如 diagnose_project 输出）。
    返回 {autoRecoverable, manualRequired, summary}。
    """
    auto: List[Dict[str, str]] = []
    manual: List[Dict[str, str]] = []
    for e in errors:
        code = e.get("code") if isinstance(e, dict) else str(e)
        base = code.split("_")[0] if "_" in code else code
        if code in _AUTO_RECOVERABLE:
            cat = "auto"
        elif code in _MANUAL_REQUIRED:
            cat = "manual"
        else:
            # 前缀匹配归类（如 ASSET_MISSING_WARN 归为 manual）。
            cat = "manual" if any(
                code.startswith(c.split("_")[0]) for c in _MANUAL_REQUIRED
            ) else "auto"
        step = (_RECOVERY_STEPS.get(code)
                or _RECOVERY_STEPS.get(base, "未知故障：请查看日志 detail 并联系支持。"))
        entry = {"code": code, "category": cat, "step": step}
        (auto if cat == "auto" else manual).append(entry)
    return {
        "autoRecoverable": auto,
        "manualRequired": manual,
        "summary": (f"共 {len(auto) + len(manual)} 项故障："
                    f"{len(auto)} 项可自动恢复，{len(manual)} 项需人工处理。"),
    }
