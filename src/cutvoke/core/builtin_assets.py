"""CutVoke 内置声音资产登记（J07 素材与模板内容）。

首次服务启动时，把包内 src/cutvoke/assets/audio/* 的真实音频文件注册进素材
账本（assets 表），与用户上传素材共存同表、互不删除。

幂等设计：
- 每个内置资产用**确定性的 asset_id**（`builtin_audio_<文件 stem>`），重跑
  ensure_builtin_audio() 走 add_asset 的 ON CONFLICT upsert，不会新增重复行。
- 路径/时长/声道等元数据每次启动重新探测并刷新（包移动后路径自动自愈）。
- 只动 builtin 资产，绝不触碰用户资产（用户资产 asset_id 不以 builtin_audio_
  前缀，且本函数只 upsert 自己生成的 id）。

内置标识（最小方案，不破坏现有显示）：
- assets 表 builtin 列 = 1；asset 记录里返回 builtin: true，前端可据此显示徽标。
- name 带「内置·」前缀，即使不重建前端也能在素材库里直观区分。

用法（在 serve / mcp 启动处调用一次）：
    from .core.builtin_assets import ensure_builtin_audio
    ensure_builtin_audio(store, render=RenderService())
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .render import RenderService, RenderError
from .builtin_stickers import load_builtin_stickers

# 包内音频目录：src/cutvoke/assets/audio（本文件在 src/cutvoke/core/ 下）
AUDIO_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "assets", "audio"))
# 包内贴纸目录：src/cutvoke/assets/stickers（J07 内容资产）
STICKER_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "assets", "stickers"))
# 包内背景图目录：src/cutvoke/assets/backgrounds（J07 内容资产扩展）
BACKGROUND_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "assets", "backgrounds"))

_AUDIO_EXTS = (".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg")
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")

_BUILTIN_BACKGROUND_NAMES: dict[str, str] = {
    "bg_vignette": "暗角渐层",
    "bg_solid_white": "纯白背景",
    "bg_solid_black": "纯黑背景",
    "bg_paper": "纸张纹理",
    "bg_grid_dark": "深色网格",
    "bg_gradient_teal": "青绿渐变",
    "bg_gradient_sunset": "日落渐变",
    "bg_gradient_blue": "蓝色渐变",
    "bg_checker": "透明棋盘格",
    "bg_bokeh": "柔焦光斑",
    "bgfx_amber_film_burn": "琥珀胶片烧光",
    "bgfx_cyan_scanline": "青色扫描线干扰",
    "bgfx_magenta_chromatic": "洋红色差晕影",
    "bgfx_violet_holographic": "紫色全息衍射",
    "bgfx_gold_dust": "金色漂浮尘光",
    "bgfx_blue_raindrops": "蓝调雨窗光斑",
    "bgfx_snow_crystals": "雪晶飘落",
    "bgfx_ember_sparks": "橙红余烬",
    "bgfx_cream_paper": "奶油纸纤维",
    "bgfx_graphite_scratches": "石墨刮痕",
    "bgfx_vintage_halftone": "复古印刷网点",
    "bgfx_linen_weave": "亚麻织纹",
    "bgfx_iridescent_caustics": "虹彩水纹折射",
    "bgfx_underwater_rays": "水下体积光束",
    "bgfx_rose_prism_flare": "玫瑰金棱镜光",
    "bgfx_blue_electric_arcs": "蓝白电弧纹理",
    "bgmat_cotton_ivory": "米白棉纸",
    "bgmat_watercolor_blue": "浅蓝水彩纸",
    "bgmat_handmade_rose": "玫瑰手工纸",
    "bgmat_linen_sage": "鼠尾草亚麻",
    "bgmat_velvet_navy": "深蓝丝绒",
    "bgmat_concrete_charcoal": "炭灰水泥",
    "bgmat_marble_teal": "青绿云石",
    "bgmat_wood_walnut": "胡桃木纹",
    "bgmat_terrazzo_cream": "奶油水磨石",
    "bgmat_vellum_lavender": "淡紫描图纸",
    "bgmat_plaster_peach": "蜜桃灰泥",
    "bgmat_frosted_mint": "薄荷磨砂玻璃",
    "bgmat_denim_indigo": "靛蓝牛仔布",
    "bgmat_canvas_sand": "沙色画布",
    "bgmat_satin_emerald": "翡翠缎面",
    "bgmat_slate_bluegray": "蓝灰板岩",
}

# 文件 stem -> (显示名前缀「内置·」+ 场景类别)，用于素材库可读展示。
_BUILTIN_META: dict[str, tuple[str, str]] = {
    "chime":        ("内置·提示音",    "音效"),
    "click":        ("内置·机械声",    "音效"),
    "wind":         ("内置·风声",      "音效"),
    "water_drop":    ("内置·水滴",      "音效"),
    "blip":         ("内置·电子闪烁",  "音效"),
    "clap":         ("内置·鼓掌",      "音效"),
    "ambient_pad":  ("内置·环境氛围",  "垫乐"),
    "groove":       ("内置·轻快节奏",  "垫乐"),
    "tech_minimal": ("内置·极简科技",  "垫乐"),
    # —— 以下为内容扩展新增（generate_builtin_audio_extra.py 合成） ——
    "whoosh":       ("内置·转场扫频",  "音效"),
    "ding":         ("内置·叮咚",      "音效"),
    "riser":        ("内置·上升音阶",  "音效"),
    "faller":       ("内置·下降音阶",  "音效"),
    "drum_loop":    ("内置·鼓点循环",  "垫乐"),
    "applause":     ("内置·掌声",      "音效"),
    "low_rumble":   ("内置·低频轰鸣",  "音效"),
    "success":      ("内置·成功提示",  "音效"),
    "error_buzz":   ("内置·错误蜂鸣",  "音效"),
    "coin":         ("内置·金币音",    "音效"),
}


def ensure_builtin_audio(store, render: Optional[RenderService] = None) -> list[dict]:
    """把包内音频目录的真实文件登记进素材账本。

    返回本次 upsert 的 asset 描述列表（含 builtin=true）。store 为 None（内存态
    无持久化）时直接返回空列表，不报错。
    """
    if store is None:
        return []
    if not os.path.isdir(AUDIO_DIR):
        return []
    if render is None:
        render = RenderService()

    registered: list[dict] = []
    for fname in sorted(os.listdir(AUDIO_DIR)):
        lower = fname.lower()
        if not lower.endswith(_AUDIO_EXTS):
            continue  # 跳过 generate_builtin_audio.py 等非音频文件
        path = os.path.join(AUDIO_DIR, fname)
        if not os.path.isfile(path):
            continue
        stem = os.path.splitext(fname)[0]
        asset_id = f"builtin_audio_{stem}"
        disp_name, category = _BUILTIN_META.get(stem, (f"内置·{stem}", "音频"))
        audio_role = {"音效": "sound_effect", "垫乐": "music"}.get(
            category, "unclassified")

        size = os.path.getsize(path)
        duration: Optional[float] = None
        has_audio = True
        try:
            info = render.probe_media(path)
            duration = info.get("duration") or None
            has_audio = bool(info.get("has_audio", True))
        except RenderError:
            # 探测失败不阻断登记：kind 固定 audio，duration 交由 UI 探测补全。
            pass

        asset = store.add_asset(
            asset_id=asset_id,
            name=disp_name,
            path=path,
            size=size,
            kind="audio",
            duration=duration,
            has_audio=has_audio,
            builtin=True,
            audio_role=audio_role,
        )
        registered.append(asset)
    return registered


def ensure_builtin_stickers(store, immutable_root: Optional[Path] = None,
                           package_root: Optional[Path] = None) -> list[dict]:
    """把包内贴纸目录的真实 PNG 登记进素材账本（J07 内容资产，kind=image）。

    素材按资源包版本和 SHA-256 保存在不可变目录；工程引用因此不会因后续
    资源包更新而指向新贴图。资源缺失或与清单不符时跳过登记，由目录 API 提示恢复。
    """
    if store is None:
        return []
    from .resource_pack import (
        install_immutable_resource_file,
        load_resource_pack_manifest,
    )

    manifest = load_resource_pack_manifest(package_root)
    pack_id = manifest["packId"]
    pack_version = manifest["version"]
    files = {item["path"]: item for item in manifest["files"]
             if isinstance(item, dict) and isinstance(item.get("path"), str)}
    resources = {item["resourceId"]: item for item in manifest["resources"]
                 if isinstance(item, dict) and isinstance(item.get("resourceId"), str)}
    registered: list[dict] = []
    for sticker in load_builtin_stickers(include_quality=False, package_root=package_root):
        if sticker["kind"] != "static":
            continue
        source = Path(sticker["path"]).resolve()
        resource = resources.get(sticker["stickerId"])
        relative_path = next((item for item in (resource or {}).get("mediaFiles", [])
                              if item.startswith("assets/stickers/") and item.lower().endswith(".png")), None)
        file_record = files.get(relative_path)
        if (not source.is_file() or not isinstance(relative_path, str) or
                not isinstance(file_record, dict) or
                not isinstance(file_record.get("sha256"), str)):
            continue
        path = source
        if immutable_root is not None:
            try:
                path = install_immutable_resource_file(
                    source, immutable_root, pack_id=pack_id,
                    pack_version=pack_version, relative_path=relative_path,
                    expected_sha256=file_record["sha256"],
                    expected_size=file_record.get("sizeBytes"),
                )
            except (OSError, ValueError):
                continue
        asset = store.add_asset(
            asset_id=sticker["assetId"],
            name=f"内置·贴纸·{sticker['name']}",
            path=str(path),
            size=os.path.getsize(path),
            kind="image",
            duration=None,
            has_audio=False,
            builtin=True,
        )
        registered.append(asset)
    return registered


def ensure_builtin_backgrounds(
    store,
    render: Optional[RenderService] = None,
    *,
    immutable_root: Optional[Path] = None,
    package_root: Optional[Path] = None,
) -> list[dict]:
    """Register built-in and active-pack composition backgrounds as image assets."""
    if store is None:
        return []
    if render is None:
        render = RenderService()

    from .resource_pack import (
        file_matches_sha256,
        install_immutable_resource_file,
        load_resource_pack_manifest,
    )

    builtin_root = Path(__file__).resolve().parent.parent
    roots = list(dict.fromkeys((builtin_root, Path(package_root).resolve()
                                if package_root is not None else builtin_root)))
    resources_by_id: dict[str, tuple[Path, dict, dict]] = {}
    for root in roots:
        try:
            manifest = load_resource_pack_manifest(root)
        except (OSError, ValueError):
            continue
        files = {item["path"]: item for item in manifest.get("files", [])
                 if isinstance(item, dict) and isinstance(item.get("path"), str)}
        for resource in manifest.get("resources", []):
            if not isinstance(resource, dict) or resource.get("kind") != "background":
                continue
            asset_id = resource.get("resourceId")
            relative_path = next((path for path in resource.get("mediaFiles", [])
                                  if isinstance(path, str)
                                  and path.startswith("assets/backgrounds/")
                                  and path.lower().endswith(_IMAGE_EXTS)), None)
            record = files.get(relative_path)
            if (not isinstance(asset_id, str) or not isinstance(resource.get("name"), str)
                    or not isinstance(relative_path, str) or not isinstance(record, dict)
                    or not isinstance(record.get("sha256"), str)):
                continue
            source = (root / relative_path).resolve()
            if not source.is_relative_to(root.resolve()) or not file_matches_sha256(
                    source, record["sha256"], record.get("sizeBytes")):
                continue
            resources_by_id[asset_id] = (root, resource, record)

    registered: list[dict] = []
    for asset_id, (root, resource, record) in sorted(resources_by_id.items()):
        relative_path = next(path for path in resource["mediaFiles"]
                             if isinstance(path, str)
                             and path.startswith("assets/backgrounds/")
                             and path.lower().endswith(_IMAGE_EXTS))
        source = (root / relative_path).resolve()
        path = source
        if immutable_root is not None:
            try:
                path = install_immutable_resource_file(
                    source, immutable_root,
                    pack_id=load_resource_pack_manifest(root)["packId"],
                    pack_version=load_resource_pack_manifest(root)["version"],
                    relative_path=relative_path,
                    expected_sha256=record["sha256"],
                    expected_size=record.get("sizeBytes"),
                )
            except (OSError, ValueError):
                continue
        size = os.path.getsize(path)
        width = height = None
        try:
            info = render.probe_media(str(path))
            width = info.get("width")
            height = info.get("height")
        except RenderError:
            pass
        asset = store.add_asset(
            asset_id=asset_id,
            name=resource["name"],
            path=str(path),
            size=size,
            kind="image",
            duration=None,
            has_audio=False,
            width=width,
            height=height,
            builtin=True,
        )
        registered.append(asset)
    return registered
