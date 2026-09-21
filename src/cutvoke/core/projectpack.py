"""CutVoke 工程打包与迁移工具（任务书 T33 / F02 / AC19 / AC27）。

把工程（project.to_dict() 快照）连同其引用的素材文件打包成一个自包含的
zip 包（建议后缀 .cvkpkg），并支持在新目录 / 新机器上解包且路径依然可用。

设计要点：
- 运行期零第三方依赖，只用标准库（zipfile / hashlib / pathlib / json / datetime）。
- 大文件流式拷贝：用 ZipFile.open(name, 'w') 边读边写边算 sha256，不把整段
  视频读进内存。
- 中文 / 空格路径：全程用 pathlib.Path，zip 内 arcname 统一用正斜杠。
- 不 import 尚未实现的 core/effects.py：未知效果检查通过可选参数
  known_effect_ids 注入，默认 None 表示跳过效果检查。
- 迁移动失败保留原件：本模块所有写操作只落在「目标包 / 目标目录」，绝不回写
  源工程或源素材；源丢失时 pack 直接报错，不静默丢素材。
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .model import Project

# 当前模块支持的包格式版本。包格式比此值新 → 明确拒绝（见 check_compatibility）。
SUPPORTED_PACKAGE_FORMAT = 1

# 生成工具版本（与 pyproject.toml 的 version 保持一致；不引入 import 以避免
# 在未安装环境下解析失败）。
TOOL_NAME = "cutvoke.projectpack"
TOOL_VERSION = "0.1.0"

# 流式读写分块大小（1 MiB）。
_CHUNK = 1 << 20


class ProjectPackError(Exception):
    """打包 / 解包 / 兼容性检查的硬错误（可诊断，不静默通过）。"""


# ---------------------------------------------------------------------------
# 兼容性报告
# ---------------------------------------------------------------------------

@dataclass
class CompatibilityReport:
    """check_compatibility 的返回结构。

    - acceptable: 是否可接受（包格式过新时为 False，且会直接抛 ProjectPackError）。
    - errors: 硬错误清单（当前仅包格式过新会走抛异常路径；此列表保留给未来扩展）。
    - warnings: 软警告（素材缺失、校验和不符、未知效果等），不阻断解包。
    - unknown_effects: 工程里引用但不在已知集合内的 effectId 清单。
    - package_format: 被判定包的包格式版本。
    """

    acceptable: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unknown_effects: list[str] = field(default_factory=list)
    package_format: int = SUPPORTED_PACKAGE_FORMAT

    def to_dict(self) -> dict:
        return {
            "acceptable": self.acceptable,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "unknown_effects": list(self.unknown_effects),
            "package_format": self.package_format,
        }


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _sha256_stream(src: Path) -> tuple[str, int]:
    """流式计算文件 sha256 与字节大小，避免整文件读入内存。"""
    h = hashlib.sha256()
    total = 0
    with open(src, "rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
            total += len(chunk)
    return h.hexdigest(), total


def _collect_asset_refs(project: Project) -> list:
    """收集工程里所有片段引用的素材（AssetReference，去重按 asset_id）。"""
    seen: dict[str, object] = {}
    out = []
    for track in project.sequence.tracks:
        for clip in track.clips:
            ref = clip.asset_ref
            if ref.asset_id in seen:
                continue
            seen[ref.asset_id] = True
            out.append(ref)
    return out


def _resolve_media_source(ref, media_root: Optional[str]) -> Path:
    """根据 AssetReference 定位源素材文件。

    1) 直接用 source_path（可为绝对或相对当前工作目录）。
    2) 若不在且给了 media_root，则尝试 media_root / source_path。
    找不到抛 ProjectPackError（不静默丢素材）。
    """
    if not ref.source_path:
        raise ProjectPackError(
            f"素材 {ref.asset_id} 未记录 sourcePath，无法定位源文件"
        )
    src = Path(ref.source_path)
    if src.exists():
        return src
    if media_root is not None:
        alt = Path(media_root) / ref.source_path
        if alt.exists():
            return alt
    raise ProjectPackError(
        f"素材文件缺失，无法打包：asset_id={ref.asset_id} "
        f"path={ref.source_path}"
        + (f" (media_root={media_root})" if media_root else "")
    )


def _scan_unknown_effects(project: Project, known_effect_ids) -> list[str]:
    """扫描工程所有片段的效果，返回不在 known_effect_ids 里的 effectId。

    known_effect_ids 为 None 时跳过检查（返回空列表）。
    工程对象保留原效果，只做记录，不剥离。
    """
    if known_effect_ids is None:
        return []
    unknown: list[str] = []
    for track in project.sequence.tracks:
        for clip in track.clips:
            for fx in clip.effects:
                eid = fx.get("effectId") if isinstance(fx, dict) else None
                if eid is None:
                    continue
                if eid not in known_effect_ids and eid not in unknown:
                    unknown.append(eid)
    return unknown


# ---------------------------------------------------------------------------
# 打包
# ---------------------------------------------------------------------------

def pack_project(project: Project, dest_pkg_path: str, *,
                 media_root: Optional[str] = None) -> str:
    """把工程 + 其引用的素材打包成自包含 zip 包。

    包内结构：
      - manifest.json : 包格式版本 / 工程 id / 生成时间 / 工具版本 / 素材清单
      - project.json  : 工程快照（project.to_dict()）
      - media/<sha256hex><ext> : 素材副本（按内容 sha256 去重命名）

    参数：
      project        : 待打包工程对象（不会被修改）。
      dest_pkg_path  : 输出包路径（建议 .cvkpkg）。
      media_root     : 可选，源素材相对基准目录（用于补定位 source_path）。

    返回：实际写入的包路径字符串。

    任何被引用的素材缺失都会抛 ProjectPackError，绝不产出缺素材的半包；
    源工程与源素材全程只读，不会被本函数改动。
    """
    dest = Path(dest_pkg_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    refs = _collect_asset_refs(project)

    # 第一阶段：定位 + 流式算 sha256 / 大小（只读源，不动源）。
    # 按 asset_id 去重；按 sha256 去重素材副本（同内容只存一份）。
    asset_entries: list[dict] = []
    # 每个 pkg_path 对应的源文件（用于第二阶段落盘去重，同内容只写一次）。
    pkg_to_src: dict[str, Path] = {}

    for ref in refs:
        src = _resolve_media_source(ref, media_root)
        sha_hex, size = _sha256_stream(src)
        suffix = src.suffix  # 含点的扩展名，如 ".mp4"；无则为 ""
        pkg_path = f"media/{sha_hex}{suffix}"
        entry = {
            "assetId": ref.asset_id,
            "originalPath": ref.source_path,
            "packagePath": pkg_path,
            "size": size,
            "sha256": sha_hex,
        }
        asset_entries.append(entry)
        # 同内容只保留一个源，避免重复写入大文件。
        if pkg_path not in pkg_to_src:
            pkg_to_src[pkg_path] = src

    manifest = {
        "packageFormat": SUPPORTED_PACKAGE_FORMAT,
        "projectId": project.project_id,
        "schemaVersion": project.schema_version,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "generator": {"tool": TOOL_NAME, "coreVersion": TOOL_VERSION},
        "assets": asset_entries,
    }

    # 第二阶段：写入 zip。manifest / project 用 writestr；素材流式写入。
    # 素材按 pkg_path 去重，每个 pkg_path 仅流式写入一次（大文件不进内存）。
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "project.json",
            json.dumps(project.to_dict(), ensure_ascii=False, indent=2),
        )
        for pkg_path, src in pkg_to_src.items():
            _stream_copy_into_zip(zf, src, pkg_path)

    return str(dest)


def _stream_copy_into_zip(zf: zipfile.ZipFile, src: Path, arcname: str) -> None:
    """流式把源文件写入 zip 成员（边读边写，不整文件进内存）。"""
    with open(src, "rb") as fsrc, zf.open(arcname, "w", force_zip64=True) as fdst:
        while True:
            chunk = fsrc.read(_CHUNK)
            if not chunk:
                break
            fdst.write(chunk)


# ---------------------------------------------------------------------------
# 兼容性判定
# ---------------------------------------------------------------------------

def check_compatibility(manifest: dict, *, core_version: Optional[str] = None,
                        project: Optional[Project] = None,
                        known_effect_ids: Optional[set[str]] = None) -> CompatibilityReport:
    """判断包与当前核心的兼容性。

    策略：
      - 包格式版本 > 当前支持 → 直接抛 ProjectPackError（明确拒绝，带可诊断信息）。
      - 素材缺失 / 校验和不符：本函数只看 manifest 声明，实际落地校验在解包阶段；
        若 manifest 把某素材标记为 present=false 则记警告。
      - 未知效果：给定 project + known_effect_ids 时扫描，命中记警告并保留原对象。
      - core_version 与打包工具版本不一致：记提示性警告，不阻断。

    返回 CompatibilityReport。
    """
    fmt = manifest.get("packageFormat", SUPPORTED_PACKAGE_FORMAT)
    if not isinstance(fmt, int):
        raise ProjectPackError(f"manifest.packageFormat 非法：{fmt!r}")
    if fmt > SUPPORTED_PACKAGE_FORMAT:
        raise ProjectPackError(
            f"包格式版本 {fmt} 高于当前支持版本 {SUPPORTED_PACKAGE_FORMAT}，"
            f"无法解包。请升级 CutVoke 到支持该包格式的版本后再试。"
        )

    report = CompatibilityReport(package_format=fmt)

    # manifest 层面声明的素材缺失（正常打包不会出现，兼容异常包）。
    for asset in manifest.get("assets", []):
        if asset.get("present") is False:
            report.warnings.append(
                f"素材在包中标记为缺失：asset_id={asset.get('assetId')} "
                f"path={asset.get('originalPath')}"
            )

    # 未知效果（需工程对象 + 已知集合）。
    unknown = _scan_unknown_effects(project, known_effect_ids) if project else []
    if unknown:
        report.unknown_effects.extend(unknown)
        report.warnings.append(
            "工程引用了未知效果（已保留原对象，需人工处理）："
            + ", ".join(sorted(set(unknown)))
        )

    # 工具版本提示。
    gen = manifest.get("generator", {})
    pkg_core = gen.get("coreVersion")
    if core_version and pkg_core and pkg_core > core_version:
        report.warnings.append(
            f"包由更新版本生成（包 coreVersion={pkg_core}，当前={core_version}），"
            f"建议升级以获得完整兼容。"
        )

    return report


# ---------------------------------------------------------------------------
# 只读检查
# ---------------------------------------------------------------------------

def inspect_package(pkg_path: str, *,
                    core_version: Optional[str] = None,
                    known_effect_ids: Optional[set[str]] = None) -> dict:
    """只读元信息（不落盘解包）：返回 manifest + 兼容性判定。

    会读取包内 project.json（仅在内存解析，不写盘）以做未知效果检查。
    素材的实际校验和需在 unpack 阶段落地校验；此处只报告 manifest 声明。
    """
    pkg = Path(pkg_path)
    if not pkg.exists():
        raise ProjectPackError(f"包文件不存在：{pkg_path}")

    with zipfile.ZipFile(pkg, "r") as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        project_obj = None
        if known_effect_ids is not None and "project.json" in zf.namelist():
            project_obj = Project.from_dict(
                json.loads(zf.read("project.json").decode("utf-8"))
            )

    try:
        report = check_compatibility(
            manifest, core_version=core_version,
            project=project_obj, known_effect_ids=known_effect_ids,
        )
    except ProjectPackError as e:
        # 包格式过新属于"拒绝"，仍作为兼容性判定的一部分返回，不抛出。
        report = CompatibilityReport(acceptable=False, errors=[str(e)],
                                     package_format=manifest.get("packageFormat", -1))

    return {"manifest": manifest, "compatibility": report.to_dict()}


# ---------------------------------------------------------------------------
# 解包 / 迁移
# ---------------------------------------------------------------------------

def unpack_project(pkg_path: str, dest_dir: str, *,
                   overwrite: bool = False) -> tuple[Project, dict, list]:
    """把包解包到 dest_dir，并重写素材路径为新目录下绝对路径。

    返回 (project, 素材路径映射, 警告列表)：
      - project    : 路径已重写的工程对象（原包内 project.json 不被改动）。
      - mapping    : {原素材路径: 新绝对路径}。
      - warnings   : 软警告（素材缺失 / 校验和不符 / 未知效果等）。

    要求：
      - 包格式过新 → 抛 ProjectPackError（明确拒绝）。
      - 迁移动失败保留原件：所有写只落 dest_dir，源工程 / 源素材永不回写。
      - dest_dir 非空且 overwrite=False → 抛错，避免误覆盖。
    """
    pkg = Path(pkg_path)
    if not pkg.exists():
        raise ProjectPackError(f"包文件不存在：{pkg_path}")

    dest = Path(dest_dir)
    if dest.exists():
        if not overwrite and any(dest.iterdir()):
            raise ProjectPackError(
                f"目标目录非空：{dest_dir}（确认无误可传 overwrite=True）"
            )
    else:
        dest.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(pkg, "r") as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        project = Project.from_dict(
            json.loads(zf.read("project.json").decode("utf-8"))
        )

        # 兼容性硬判定（包格式过新会抛错）。
        report = check_compatibility(manifest)
        warnings: list[str] = list(report.warnings)

        # 素材落地 + 流式校验 sha256。
        asset_by_pkg: dict[str, dict] = {a["packagePath"]: a for a in manifest.get("assets", [])}
        media_dir = dest / "media"
        media_dir.mkdir(parents=True, exist_ok=True)

        mapping: dict[str, str] = {}
        for name in zf.namelist():
            if not name.startswith("media/"):
                continue
            entry = asset_by_pkg.get(name)
            if entry is None:
                warnings.append(f"包内存在 manifest 未记录的素材成员：{name}")
                continue
            target = media_dir / Path(name).name
            h = hashlib.sha256()
            with zf.open(name) as fsrc, open(target, "wb") as fdst:
                while True:
                    chunk = fsrc.read(_CHUNK)
                    if not chunk:
                        break
                    h.update(chunk)
                    fdst.write(chunk)
            actual = h.hexdigest()
            if actual != entry.get("sha256"):
                warnings.append(
                    f"素材校验和不符：{name} 期望 {entry.get('sha256')} 实际 {actual}"
                )
            # 路径映射：原工程声明路径 -> 新绝对路径。
            mapping[entry["originalPath"]] = str(target.resolve())

        # 重写工程里每个片段的素材路径（按 asset_id 定位新路径）。
        by_asset_id = {a["assetId"]: a for a in manifest.get("assets", [])}
        for track in project.sequence.tracks:
            for clip in track.clips:
                ref = clip.asset_ref
                entry = by_asset_id.get(ref.asset_id)
                if entry is None:
                    warnings.append(
                        f"片段引用了 manifest 未记录的素材：asset_id={ref.asset_id}"
                    )
                    continue
                new_path = mapping.get(entry["originalPath"])
                if new_path is None:
                    warnings.append(
                        f"素材解包后缺失，路径未重写：asset_id={ref.asset_id}"
                    )
                    continue
                ref.source_path = new_path

    return project, mapping, warnings
