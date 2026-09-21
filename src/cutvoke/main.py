"""CutVoke CLI（任务书 T12 / 第 9.3 章 CLI 规范）。

命令形态（M1 冻结）：
  cutvoke serve [--host 127.0.0.1 --port 8787]    启动本地服务 + Web UI
  cutvoke doctor --json                             环境诊断
  cutvoke project create --name demo [--width --height --fps] --json
  cutvoke project inspect --project <id> --json
  cutvoke edit apply --project <id> --file commands.json --json
  cutvoke export --project <id> --out out.mp4 [--versioned] --json
  cutvoke job inspect --job <id> --json          查导出任务状态
  cutvoke job list [--project <id>] --json       列导出任务（最新在前）
  cutvoke template list [--category <c>] --json  列内置工程模板（J12）
  cutvoke template apply --project <id> --template <tid> [--sources s.json] --json

JSON 模式 stdout 仅输出机器可读结果；日志进 stderr。
统一退出码（第 9.3）：0 成功 / 2 参数或 schema / 3 版本冲突 / 4 资源不可用 /
5 执行失败 / 6 取消 / 7 超时 / 8 服务错误。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from typing import Optional

from .core.service import EditService, EditError
from .core.store import ProjectStore
from .core.protocol import Command, Actor
from .core.rational import Rational
from .core.render import RenderService, RenderError, RenderCancelled

# 统一数据位置：CLI 各子命令默认共享同一个工程库，使
# `project create` → `edit apply` → `export` 能像任务书要求的那样串联执行（F01/F38）。
DEFAULT_DATA_DIR = os.path.join(os.path.expanduser("~"), ".cutvoke", "data")


def _json_out(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def _json_err(code: str, msg: str, exit_code: int) -> int:
    _json_out({"ok": False, "error": {"code": code, "message": msg}})
    return exit_code


def _resolve_db(args) -> str:
    """解析 SQLite 数据库路径。

    优先级：`--data` > 环境变量 `CUTVOKE_DATA` > `~/.cutvoke/data/projects.sqlite`。
    `--data` 传目录时自动补 `projects.sqlite` 文件名。
    """
    raw = (getattr(args, "data", None) or os.environ.get("CUTVOKE_DATA")
           or os.path.join(DEFAULT_DATA_DIR, "projects.sqlite"))
    raw = os.path.expanduser(raw)
    if raw.endswith((".sqlite", ".db")):
        os.makedirs(os.path.dirname(os.path.abspath(raw)), exist_ok=True)
        return raw
    os.makedirs(raw, exist_ok=True)
    return os.path.join(raw, "projects.sqlite")


def _service_for(args) -> tuple[EditService, ProjectStore]:
    """按统一数据位置创建带持久化的服务，并恢复已保存工程。"""
    store = ProjectStore(_resolve_db(args))
    svc = EditService(store=store)
    for pid in store.list_all():
        try:
            svc.load_project(pid)
        except Exception:  # noqa: BLE001 — 单个工程损坏不阻断其它工程
            pass
    return svc, store


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------

def cmd_serve(args) -> int:
    """启动本地服务（T31：默认仅回环绑定 + 可选会话令牌 + 结构化日志）。"""
    import os
    from .core.httpapi import HttpApi, serve
    from .core.security import SessionToken
    from .core.diagnostics import JsonlLogger
    db_path = _resolve_db(args)
    store = ProjectStore(db_path)
    service = EditService(store=store)
    # 启动时恢复所有已保存工程（持久化：重启后工程不丢）
    loaded = 0
    for pid in store.list_all():
        try:
            service.load_project(pid)
            loaded += 1
        except Exception:
            pass
    # 会话令牌：显式 --token 或本机已存在令牌文件时启用（安全默认）
    session = None
    if getattr(args, "token", False) or SessionToken.is_enabled():
        try:
            session = SessionToken.load()
        except Exception as e:  # noqa: BLE001
            print(f"会话令牌加载失败：{e}", file=sys.stderr)
        if getattr(args, "token", False) and session is None:
            print("提示：尚未创建会话令牌，先运行 `cutvoke token create` 再启动。",
                  file=sys.stderr)
    # 结构化日志（T31）：默认写入 ~/.cutvoke/logs/，可用 --log 覆盖
    logger = JsonlLogger(getattr(args, "log", None))
    logger.info("serve.boot", detail={"data": db_path, "restored": loaded})
    # 素材落盘目录（WP-03/A07）：与数据库同级，保证自定义 --db 时数据与素材同处一地
    media_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), "media")
    api = HttpApi(service, RenderService(), logger=logger, session=session,
                  media_dir=media_dir)
    # J07：首次启动把内置声音资产登记进素材账本（幂等，与用户素材共存同表）
    from .core.builtin_assets import (ensure_builtin_audio, ensure_builtin_stickers,
                                       ensure_builtin_backgrounds)
    try:
        n_builtin = len(ensure_builtin_audio(store))
        logger.info("serve.builtin_audio", detail={"registered": n_builtin})
    except Exception as e:  # noqa: BLE001 — 内置资产登记失败不阻断服务启动
        print(f"内置声音资产登记失败（忽略）：{e}", file=sys.stderr)
    try:
        n_stickers = len(ensure_builtin_stickers(store))
        logger.info("serve.builtin_stickers", detail={"registered": n_stickers})
    except Exception as e:  # noqa: BLE001
        print(f"内置贴纸登记失败（忽略）：{e}", file=sys.stderr)
    # J07 内容扩展：内置背景图登记（幂等，与用户素材共存同表）
    try:
        n_backgrounds = len(ensure_builtin_backgrounds(store))
        logger.info("serve.builtin_backgrounds", detail={"registered": n_backgrounds})
    except Exception as e:  # noqa: BLE001
        print(f"内置背景图登记失败（忽略）：{e}", file=sys.stderr)
    # Web UI 静态目录解析，兼容两种安装形态：
    #   1) wheel 安装：走包内 src/cutvoke/web/（pyproject package-data 打进）
    #   2) 仓库开发：走项目根 web/dist（Vite 产物）> web/（旧单文件）
    web_dir = None
    _in_pkg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    if os.path.isdir(_in_pkg):
        web_dir = _in_pkg
    else:
        pkg_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for cand in ("web/dist", "web"):
            d = os.path.join(pkg_dir, cand)
            if os.path.isdir(d):
                web_dir = d
                break
    if web_dir is None:
        print("警告：未找到 web/ 目录，serve 将不带 Web UI（仅 API）", file=sys.stderr)
    print(f"CutVoke serve: data at {db_path} (restored {loaded} projects)", file=sys.stderr)
    if getattr(args, 'json', False):
        _json_out({"ok": True, "host": args.host, "port": args.port, "data": db_path,
                   "restoredProjects": loaded, "tokenRequired": session is not None,
                   "log": str(logger.path)})
    serve(api, host=args.host, port=args.port, web_dir=web_dir,
          allow_non_loopback=bool(getattr(args, "allow_non_loopback", False)))
    return 0


def cmd_doctor(args) -> int:
    """环境诊断。"""
    import platform
    import shutil
    info = {
        "ok": True,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
    }
    if getattr(args, 'json', False):
        _json_out(info)
    else:
        for k, v in info.items():
            print(f"{k}: {v}")
    return 0


def cmd_effects_list(args) -> int:
    """列出当前可用效果（AC18：CLI 与 UI / HTTP / MCP 发现同一份能力清单）。"""
    from .core.effects import default_registry
    reg = default_registry()
    lang = getattr(args, "lang", None) or "zh-CN"
    cat = getattr(args, "category", None)
    specs = reg.by_category(cat) if cat else reg.all()
    items = [s.to_dict(lang) for s in specs]
    if getattr(args, "json", False):
        _json_out({"ok": True, "count": len(items), "effects": items,
                   "loadErrors": reg.load_errors})
        return 0
    for s in items:
        print(f"{s['effectId']}  [{s['category']}]  {s['name']}  v{s['version']}")
        if s["description"]:
            print(f"    {s['description']}")
        props = (s["parameters"].get("properties") or {})
        for pname, pschema in props.items():
            default = s["defaults"].get(pname, "-")
            anim = " 可动画" if pname in s["animatable"] else ""
            print(f"    - {pname}: {pschema.get('type', '?')}  默认 {default}{anim}")
    if reg.load_errors:
        print(f"有 {len(reg.load_errors)} 个效果清单加载失败（--json 查看详情）", file=sys.stderr)
    return 0


def cmd_effects_show(args) -> int:
    """查看单个效果的完整规格（参数 schema、默认值、实现声明）。"""
    from .core.effects import default_registry, EffectNotFound
    reg = default_registry()
    try:
        spec = reg.get(args.effectId)
    except EffectNotFound as e:
        return _json_err("EFFECT_UNAVAILABLE", str(e), 4)
    d = spec.to_dict(getattr(args, "lang", None) or "zh-CN")
    _json_out({"ok": True, "effect": d})
    return 0


def cmd_capabilities(args) -> int:
    """能力发现（D09/AC18）：命令目录 + 效果 + 质量预设，对齐 HTTP /capabilities。"""
    from .core.effects import default_registry
    svc, _store = _service_for(args)
    reg = default_registry()
    lang = getattr(args, "lang", None) or "zh-CN"
    result = {
        "ok": True,
        "commands": svc.command_catalog(lang),
        "effects": reg.list_capabilities(lang) if hasattr(reg, "list_capabilities")
                    else [s.to_dict(lang) for s in reg.all()],
        "qualityPresets": ["high", "medium", "low"],
    }
    _json_out(result)
    return 0


def cmd_probe(args) -> int:
    """素材探测（D09/F04）：ffprobe 单个媒体文件的真实信息。"""
    from .core.render import RenderService, RenderError
    rs = RenderService()
    try:
        info = rs.probe_media(args.path)
    except RenderError as e:
        return _json_err("PROBE_FAILED", str(e), 5)
    _json_out({"ok": True, **info})
    return 0


def cmd_token(args) -> int:
    """会话令牌管理（T31 本地访问治理）。"""
    from .core.security import SessionToken
    sub = getattr(args, "subcmd", "show")
    if sub == "create":
        inst = SessionToken.create()
        token = inst.reveal()
        _json_out({"ok": True, "action": "create", "path": str(inst.path),
                   "token": token,
                   "note": "明文令牌仅本次回显，磁盘只保存哈希；请妥善保存。"})
        return 0
    if sub == "rotate":
        inst = SessionToken.load()
        if inst is None:
            return _json_err("TOKEN_NOT_CONFIGURED",
                             "尚未启用会话令牌，先运行 `cutvoke token create`。", 2)
        inst.rotate()
        _json_out({"ok": True, "action": "rotate", "path": str(inst.path),
                   "token": inst.reveal(),
                   "note": "旧令牌已立即失效；明文仅本次回显。"})
        return 0
    if sub == "remove":
        from pathlib import Path
        p = SessionToken._default_path()
        existed = Path(p).is_file()
        if existed:
            Path(p).unlink()
        _json_out({"ok": True, "action": "remove", "path": str(p),
                   "removed": existed,
                   "note": "已关闭会话令牌要求，恢复为仅回环匿名访问。"})
        return 0
    # show
    inst = SessionToken.load()
    _json_out({"ok": True, "action": "show",
               "enabled": inst is not None,
               "path": str(SessionToken._default_path()),
               "note": "磁盘只保存令牌哈希，明文不可回读；如需新令牌请 rotate。"})
    return 0


def cmd_diagnose(args) -> int:
    """工程/素材/任务故障诊断（T31，只读，不改动任何文件）。"""
    from .core.diagnostics import diagnose_project, recovery_report
    report = diagnose_project(args.projectDir)
    errors = report.get("errors", []) if isinstance(report, dict) else []
    if getattr(args, "json", False):
        _json_out({"ok": bool(report.get("ok", False)), "report": report,
                   "recovery": recovery_report(errors)})
        return 0
    print(f"工程目录: {args.projectDir}")
    print(f"结论: {'正常' if report.get('ok') else '存在问题'}")
    for key in ("errors", "warnings"):
        for item in report.get(key, []) or []:
            code = item.get("code") if isinstance(item, dict) else item
            msg = item.get("message", "") if isinstance(item, dict) else ""
            print(f"  [{key[:-1].upper()}] {code} {msg}")
    rec = recovery_report(errors)
    for step in rec.get("steps", []) or []:
        print(f"  恢复建议: {step}")
    return 0 if report.get("ok") else 5


def cmd_pack(args) -> int:
    """把工程及其素材打包为单个 .cvkpkg（T33 迁移）。"""
    import os as _os
    from .core.store import ProjectStore
    from .core.projectpack import pack_project, ProjectPackError
    from .core.effects import default_registry
    store = ProjectStore(args.data)
    try:
        project = store.load(args.project)
    except Exception as e:  # noqa: BLE001
        return _json_err("PROJECT_NOT_FOUND", f"读取工程失败: {e}", 4)
    if project is None:
        return _json_err("PROJECT_NOT_FOUND", f"工程不存在: {args.project}", 4)
    out = args.out or _os.path.abspath(f"{args.project}.cvkpkg")
    try:
        written = pack_project(project, out, media_root=args.mediaRoot)
    except ProjectPackError as e:
        return _json_err("PACK_FAILED", str(e), 5)
    _json_out({"ok": True, "package": written, "projectId": project.project_id})
    return 0


def cmd_unpack(args) -> int:
    """把 .cvkpkg 解包到新目录并重定位素材路径（T33 迁移 / AC27）。"""
    from .core.store import ProjectStore
    from .core.projectpack import (unpack_project, inspect_package,
                                   ProjectPackError)
    from .core.effects import default_registry
    known = set(default_registry().ids())
    try:
        meta = inspect_package(args.package, core_version=_core_version(),
                               known_effect_ids=known)
    except ProjectPackError as e:
        return _json_err("PACK_INVALID", str(e), 2)
    compat = meta["compatibility"]
    if not compat.get("acceptable", True):
        return _json_err("PACK_INCOMPATIBLE", "; ".join(compat.get("errors", [])), 2)
    try:
        project, mapping, warnings = unpack_project(
            args.package, args.dest, overwrite=bool(args.overwrite))
    except ProjectPackError as e:
        return _json_err("UNPACK_FAILED", str(e), 5)
    # 落盘到目标目录的 SQLite，便于随后直接 serve / export
    db = args.data or f"{args.dest}/projects.sqlite"
    store = ProjectStore(db)
    store.save(project)
    _json_out({"ok": True, "projectId": project.project_id, "dest": args.dest,
               "store": db, "mediaFiles": len(mapping),
               "warnings": list(warnings) + list(compat.get("warnings", [])),
               "unknownEffects": compat.get("unknown_effects", [])})
    return 0


def _core_version() -> str:
    try:
        from . import __version__ as v  # type: ignore
        return str(v)
    except Exception:  # noqa: BLE001
        return "0.1.0"


def cmd_project_create(args) -> int:
    svc, store = _service_for(args)
    proj = svc.create_project(args.project or args.name, name_hint=args.name,
                              width=args.width, height=args.height,
                              fps=Rational.of(args.fps, 1))
    _json_out({"ok": True, "projectId": proj.project_id, "revision": proj.revision,
               "name": proj.name, "data": _resolve_db(args)})
    return 0


def cmd_project_inspect(args) -> int:
    svc, _store = _service_for(args)
    try:
        proj = svc.get_project(args.project)
    except EditError as e:
        return _json_err(e.code, e.message, 3)
    _json_out({"ok": True, "project": proj.to_dict()})
    return 0


def cmd_project_rename(args) -> int:
    svc, _store = _service_for(args)
    ok = svc.rename_project(args.project, args.name)
    if not ok:
        return _json_err("NOT_FOUND", f"unknown project: {args.project}", 3)
    _json_out({"ok": True, "id": args.project, "name": args.name})
    return 0


def cmd_edit_apply(args) -> int:
    """从文件读取命令（批量或单条）并应用到工程。"""
    svc, store = _service_for(args)
    with open(args.file, encoding="utf-8") as f:
        spec = json.load(f)
    commands = spec if isinstance(spec, list) else [spec]
    results = []
    for c in commands:
        try:
            # expectedRevision 缺省时取当前 revision（顺序批量应用的常见用法）
            expected = c.get("expectedRevision") or svc.get_project(args.project).revision
            cmd = Command(
                type=c["type"], payload=c.get("payload", {}),
                project_id=args.project,
                expected_revision=expected,
                edit_lease_id=c.get("editLeaseId", ""),
            )
            r = svc.execute(cmd)
            results.append(r.to_dict())
        except EditError as e:
            return _json_err(e.code, e.message, 3 if e.code == "REVISION_CONFLICT" else 5)
    _json_out({"ok": True, "results": results})
    return 0


def cmd_template_list(args) -> int:
    """列出内置工程模板（J12）。CLI 与 Web/HTTP/MCP 看到同一份清单。"""
    svc, store = _service_for(args)
    try:
        r = svc.execute(Command(type="template.list",
                                payload={"category": getattr(args, "category", None)},
                                project_id=getattr(args, "project", None) or "-",
                                actor=Actor("cli", "user")))
    except EditError as e:
        return _json_err(e.code, e.message, 5)
    finally:
        store.close()
    ent = r.changed_entities[0]
    if getattr(args, "json", False):
        _json_out({"ok": True, **ent})
        return 0
    print(f"共 {ent['count']} 个模板"
          + (f"（跳过 {len(ent['skipped'])} 个坏模板）" if ent["skipped"] else ""))
    for t in ent["templates"]:
        bits = [f"{t['aspect']}", t["category"], f"{t['slotCount']} 个素材位",
                f"约 {t['estimatedSeconds']:.1f}s"]
        if t.get("hasBgm"):
            bits.append("含背景音乐")
        if t.get("captionCount"):
            bits.append(f"{t['captionCount']} 条字幕")
        if t.get("transition"):
            bits.append("含转场")
        print(f"{t['id']}")
        print(f"    {t['name']}  [{' / '.join(bits)}]")
        if t["description"]:
            print(f"    {t['description']}")
        for s in t["slots"]:
            print(f"    - 素材位 {s['key']}（{s['kind']}，{s['duration']}s）：{s['label']}")
    if ent["skipped"]:
        print("以下模板加载失败（未计入）：", file=sys.stderr)
        for s in ent["skipped"]:
            print(f"  {s}", file=sys.stderr)
    return 0


def cmd_template_apply(args) -> int:
    """把内置模板套用到工程（J12）：套完即可直接导出成片。"""
    svc, store = _service_for(args)
    payload: dict = {"templateId": args.template}
    if getattr(args, "sources", None):
        with open(args.sources, encoding="utf-8") as f:
            payload["sources"] = json.load(f)
    if getattr(args, "clear_existing", False):
        payload["clearExisting"] = True
    try:
        r = svc.execute(Command(
            type="template.apply", payload=payload, project_id=args.project,
            expected_revision=svc.get_project(args.project).revision,
            actor=Actor("cli", "user")))
    except EditError as e:
        return _json_err(e.code, e.message, 3 if e.code == "REVISION_CONFLICT" else 5)
    finally:
        store.close()
    if getattr(args, "json", False):
        _json_out({"ok": True, **r.to_dict()})
        return 0
    summary = next((e for e in r.changed_entities
                    if e.get("type") == "template_applied"), None)
    if summary:
        print(f"已套用模板 {summary['templateId']}（{summary['name']}）")
        print(f"  画幅 {summary['aspect']}  画布 {summary['canvas'][0]}x{summary['canvas'][1]}"
              f"  时长约 {summary['durationSec']:.1f}s")
        print(f"  占位素材 {summary['placeholderCount']} 个"
              + ("（先导出即可看到效果，替换素材后可精修）"
                 if summary["placeholderCount"] else ""))
    return 0


def cmd_export(args) -> int:
    svc, store = _service_for(args)
    try:
        proj = svc.get_project(args.project)
    except EditError as e:
        return _json_err(e.code, e.message, 3)
    render = RenderService()
    versioned = bool(getattr(args, "versioned", False))
    out = getattr(args, "out", None)
    if not out:
        return _json_err("INVALID_ARGUMENT", "export requires --out", 2)
    if versioned:
        # V02 多版本：永不覆盖已有成片，name.mp4 → name_v2.mp4 → …
        from .core.export_queue import versioned_path
        out = versioned_path(out)

    # V02：CLI 导出同样落一条任务账本，使 `cutvoke job inspect/list` 在
    # 任何接口导出之后都能查到任务与结果（与 HTTP 导出的可查性一致）。
    job_id: Optional[str] = None
    if store is not None:
        job_id = f"cli_{uuid.uuid4().hex[:12]}"
        try:
            store.create_export_job(
                job_id=job_id, project_id=args.project, revision=proj.revision,
                request_hash=f"cli:{job_id}", out_path=os.path.abspath(out),
                quality=args.quality, status="running")
        except Exception:  # noqa: BLE001 — 账本写入失败不应阻断导出本身
            job_id = None
    try:
        result = render.render(proj, out, quality=args.quality, overwrite=True,
                               allow_unknown_effects=bool(getattr(args, "allow_unknown_effects", False)))
        if job_id is not None:
            store.finish_export_job(job_id, status="succeeded",
                                    result=dict(result, jobId=job_id,
                                                versioned=versioned))
    except RenderCancelled:
        if job_id is not None:
            store.finish_export_job(job_id, status="cancelled",
                                    error={"code": "CANCELLED", "message": "render cancelled"})
        return _json_err("CANCELLED", "render cancelled", 6)
    except RenderError as e:
        if job_id is not None:
            store.finish_export_job(job_id, status="failed",
                                    error={"code": "RENDER_FAILED", "message": str(e)})
        return _json_err("RENDER_FAILED", str(e), 5)
    finally:
        # CLI 是一次性进程，但同进程内可能被复用（测试/内嵌调用），
        # 显式放掉 sqlite 文件句柄，避免在 Windows 上锁住数据文件。
        if store is not None:
            store.close()
    _json_out({"ok": True, **result, "versioned": versioned, "jobId": job_id})
    return 0


def cmd_job_inspect(args) -> int:
    """V02 导出任务查询：读账本（进程重启后历史仍在）。

    队列本身活在 `cutvoke serve` 进程里，CLI 是独立进程，所以这里只读不取消；
    取消请走 HTTP `POST /api/v1/jobs/{id}/cancel`（或 Web 导出面板）。
    """
    _svc, store = _service_for(args)
    try:
        row = store.get_export_job(args.job)
    finally:
        store.close()
    if row is None:
        return _json_err("NOT_FOUND", f"unknown job: {args.job}", 4)
    _json_out({"ok": True, **row})
    return 0


def cmd_job_list(args) -> int:
    """V02 导出任务列表：按工程过滤，最新在前。"""
    _svc, store = _service_for(args)
    try:
        jobs = store.list_export_jobs(getattr(args, "project", None),
                                      limit=int(getattr(args, "limit", 50) or 50))
    finally:
        store.close()
    _json_out({"ok": True, "jobs": jobs})
    return 0


def cmd_mcp(args) -> int:
    """启动 MCP server（stdio），供 Claude Code / Codex / Cursor 等 Agent 接入。

    MCP 与 CLI / HTTP 共享同一个持久化工程库，所以 Agent 建的工程
    在 `cutvoke serve` 的 Web UI 里能直接看到（同一时间线，RQ03）。
    """
    from .core.mcp_server import MCPServer
    svc, store = _service_for(args)
    server = MCPServer(service=svc)
    # J07：MCP 启动同样登记内置声音/贴纸资产（幂等，与用户素材共存同表）
    from .core.builtin_assets import (ensure_builtin_audio, ensure_builtin_stickers,
                                       ensure_builtin_backgrounds)
    try:
        ensure_builtin_audio(store)
        ensure_builtin_stickers(store)
        ensure_builtin_backgrounds(store)
    except Exception as e:  # noqa: BLE001
        print(f"内置资产登记失败（忽略）：{e}", file=sys.stderr)
    print(f"CutVoke MCP: data at {_resolve_db(args)} "
          f"(restored {len(store.list_all())} projects)", file=sys.stderr)
    server.run_stdio()
    return 0


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cutvoke", description="CutVoke: agent-first, local-first video editing.")
    p.add_argument("--json", action="store_true", help="machine-readable JSON output")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_mcp = sub.add_parser("mcp", help="start MCP server (stdio) for AI agents")
    p_mcp.add_argument("--data", help="SQLite store path（与 serve 共用同一工程库）")
    p_mcp.set_defaults(func=cmd_mcp)

    p_serve = sub.add_parser("serve", help="start local service + web UI")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8787)
    p_serve.add_argument("--data", help="SQLite data directory")
    p_serve.add_argument("--allow-non-loopback", action="store_true",
                         help="允许绑定非回环地址（默认只监听本机，T31）")
    p_serve.add_argument("--token", action="store_true",
                         help="启用会话令牌要求（API 请求需带 X-CutVoke-Token）")
    p_serve.add_argument("--log", help="结构化日志文件路径（默认 ~/.cutvoke/logs/）")
    p_serve.set_defaults(func=cmd_serve)

    p_doctor = sub.add_parser("doctor", help="diagnose environment")
    # 不再单独定义 --json：顶层开关已覆盖，且重复定义会覆盖顶层的解析结果。
    p_doctor.set_defaults(func=cmd_doctor)

    # 效果发现（AC18：CLI 与 UI / HTTP / MCP 共用同一份能力清单）
    p_eff = sub.add_parser("effects", help="list and inspect available effects")
    p_eff_sub = p_eff.add_subparsers(dest="subcmd", required=True)
    p_eff_l = p_eff_sub.add_parser("list", help="list effects")
    p_eff_l.add_argument("--category", help="filter: transition / transform / color")
    p_eff_l.add_argument("--lang", default="zh-CN")
    p_eff_l.set_defaults(func=cmd_effects_list)
    p_eff_s = p_eff_sub.add_parser("show", help="show one effect spec")
    p_eff_s.add_argument("effectId")
    p_eff_s.add_argument("--lang", default="zh-CN")
    p_eff_s.set_defaults(func=cmd_effects_show)

    # 能力发现（D09）：命令目录 + 效果 + 质量预设（对齐 HTTP /capabilities）
    p_cap = sub.add_parser("capabilities", help="list command catalog + effects + presets")
    p_cap.add_argument("--lang", default="zh-CN")
    p_cap.set_defaults(func=cmd_capabilities)

    # 素材探测（D09 / F04）：ffprobe 单个媒体文件的真实信息
    p_probe = sub.add_parser("probe", help="probe a media file (duration/streams)")
    p_probe.add_argument("path", help="media file path to probe")
    p_probe.set_defaults(func=cmd_probe)

    # 本地访问治理（T31）
    p_tok = sub.add_parser("token", help="session token for API access control")
    p_tok_sub = p_tok.add_subparsers(dest="subcmd", required=True)
    for name, helptext in (("show", "show token status"),
                           ("create", "create and enable session token"),
                           ("rotate", "rotate the token (old one dies)"),
                           ("remove", "disable token requirement")):
        sp = p_tok_sub.add_parser(name, help=helptext)
        sp.set_defaults(func=cmd_token)

    # 故障诊断（T31，只读）
    p_diag = sub.add_parser("diagnose", help="diagnose a project directory (read-only)")
    p_diag.add_argument("--project-dir", dest="projectDir", required=True)
    p_diag.set_defaults(func=cmd_diagnose)

    # 工程迁移（T33）
    p_pack = sub.add_parser("pack", help="pack project + media into one .cvkpkg")
    p_pack.add_argument("--project", required=True)
    p_pack.add_argument("--data", required=True, help="SQLite store path")
    p_pack.add_argument("--out")
    p_pack.add_argument("--media-root", dest="mediaRoot")
    p_pack.set_defaults(func=cmd_pack)

    p_unpack = sub.add_parser("unpack", help="unpack .cvkpkg into a new directory")
    p_unpack.add_argument("--package", required=True)
    p_unpack.add_argument("--dest", required=True)
    p_unpack.add_argument("--data", help="target SQLite store path")
    p_unpack.add_argument("--overwrite", action="store_true")
    p_unpack.set_defaults(func=cmd_unpack)

    p_pc = sub.add_parser("project", help="project operations")
    p_pc_sub = p_pc.add_subparsers(dest="subcmd", required=True)
    p_create = p_pc_sub.add_parser("create", help="create project")
    p_create.add_argument("--project")
    p_create.add_argument("--name", default="demo")
    p_create.add_argument("--width", type=int, default=1920)
    p_create.add_argument("--height", type=int, default=1080)
    p_create.add_argument("--fps", type=int, default=30)
    p_create.add_argument("--data", help="SQLite store path（缺省 ~/.cutvoke/data/projects.sqlite）")
    p_create.set_defaults(func=cmd_project_create)
    p_inspect = p_pc_sub.add_parser("inspect", help="inspect project")
    p_inspect.add_argument("--project", required=True)
    p_inspect.add_argument("--data", help="SQLite store path")
    p_inspect.set_defaults(func=cmd_project_inspect)
    p_rename = p_pc_sub.add_parser("rename", help="rename project")
    p_rename.add_argument("--project", required=True)
    p_rename.add_argument("--name", required=True)
    p_rename.add_argument("--data", help="SQLite store path")
    p_rename.set_defaults(func=cmd_project_rename)

    p_edit = sub.add_parser("edit", help="edit operations")
    p_edit_sub = p_edit.add_subparsers(dest="subcmd", required=True)
    p_apply = p_edit_sub.add_parser("apply", help="apply commands from file")
    p_apply.add_argument("--project", required=True)
    p_apply.add_argument("--file", required=True)
    p_apply.add_argument("--data", help="SQLite store path")
    p_apply.set_defaults(func=cmd_edit_apply)

    p_export = sub.add_parser("export", help="export project")
    p_export.add_argument("--project", required=True)
    p_export.add_argument("--out", required=True)
    p_export.add_argument("--quality", default="high")
    p_export.add_argument("--data", help="SQLite store path")
    p_export.add_argument("--allow-unknown-effects", action="store_true",
                          dest="allow_unknown_effects",
                          help="工程含未知效果时降级导出并记录警告（默认严格拒绝）")
    p_export.add_argument("--versioned", action="store_true",
                          help="多版本导出：不覆盖已有成片，自动落 name_v2/name_v3（V02）")
    p_export.set_defaults(func=cmd_export)

    p_job = sub.add_parser("job", help="job operations")
    p_job_sub = p_job.add_subparsers(dest="subcmd", required=True)
    p_job_i = p_job_sub.add_parser("inspect", help="inspect one export job")
    p_job_i.add_argument("--job", required=True)
    p_job_i.add_argument("--data", help="SQLite store path")
    p_job_i.set_defaults(func=cmd_job_inspect)
    p_job_l = p_job_sub.add_parser("list", help="list export jobs (newest first)")
    p_job_l.add_argument("--project", help="filter by project id")
    p_job_l.add_argument("--limit", type=int, default=50)
    p_job_l.add_argument("--data", help="SQLite store path")
    p_job_l.set_defaults(func=cmd_job_list)

    p_tpl = sub.add_parser("template", help="built-in project templates (J12)")
    p_tpl_sub = p_tpl.add_subparsers(dest="subcmd", required=True)
    p_tpl_l = p_tpl_sub.add_parser("list", help="list built-in templates")
    p_tpl_l.add_argument("--category", help="filter by category")
    p_tpl_l.add_argument("--data", help="SQLite store path")
    p_tpl_l.set_defaults(func=cmd_template_list)
    p_tpl_a = p_tpl_sub.add_parser("apply", help="apply a template to a project")
    p_tpl_a.add_argument("--project", required=True)
    p_tpl_a.add_argument("--template", required=True, help="template id")
    p_tpl_a.add_argument("--sources",
                         help="JSON file mapping slotKey -> assetId or absolute path")
    p_tpl_a.add_argument("--clear-existing", action="store_true",
                         dest="clear_existing",
                         help="clear existing tracks/captions before applying")
    p_tpl_a.add_argument("--data", help="SQLite store path")
    p_tpl_a.set_defaults(func=cmd_template_apply)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # `--json` 是顶层开关，但文档与用户习惯都写成 `cutvoke export ... --json`。
    # argparse 的子命令解析器不认识顶层开关，直接透传会以 exit 2 报错
    # （发布候选验证实测发现）。这里把任意位置的 --json 提到最前，使
    # `cutvoke --json X` 与 `cutvoke X --json` 完全等价。
    if "--json" in raw:
        raw = ["--json"] + [a for a in raw if a != "--json"]
    parser = build_parser()
    args = parser.parse_args(raw)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
