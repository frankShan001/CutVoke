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
from typing import Optional

from .render import RenderService, RenderError

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
        disp_name, _category = _BUILTIN_META.get(stem, (f"内置·{stem}", "音频"))

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
        )
        registered.append(asset)
    return registered


def ensure_builtin_stickers(store) -> list[dict]:
    """把包内贴纸目录的真实 PNG 登记进素材账本（J07 内容资产，kind=image）。

    幂等设计同音频：确定性 asset_id（builtin_sticker_<stem>）ON CONFLICT
    upsert；每次启动重 probe 刷新路径/尺寸。返回登记列表。
    """
    if store is None:
        return []
    if not os.path.isdir(STICKER_DIR):
        return []
    registered: list[dict] = []
    for fname in sorted(os.listdir(STICKER_DIR)):
        lower = fname.lower()
        if not lower.endswith(_IMAGE_EXTS):
            continue
        path = os.path.join(STICKER_DIR, fname)
        if not os.path.isfile(path):
            continue
        stem = os.path.splitext(fname)[0]
        asset_id = f"builtin_sticker_{stem}"
        asset = store.add_asset(
            asset_id=asset_id,
            name=f"内置·贴纸·{stem}",
            path=path,
            size=os.path.getsize(path),
            kind="image",
            duration=None,
            has_audio=False,
            builtin=True,
        )
        registered.append(asset)
    return registered


def ensure_builtin_backgrounds(store, render: Optional[RenderService] = None) -> list[dict]:
    """把包内背景图目录的真实图片登记进素材账本（J07 内容资产扩展，kind=image）。

    幂等设计同音频/贴纸：确定性 asset_id（builtin_background_<stem>）走 add_asset
    的 ON CONFLICT upsert，重跑不产生重复行；每次启动重 probe 刷新路径/尺寸。
    返回登记列表。store 为 None 时直接返回空列表。
    """
    if store is None:
        return []
    if not os.path.isdir(BACKGROUND_DIR):
        return []
    if render is None:
        render = RenderService()

    registered: list[dict] = []
    for fname in sorted(os.listdir(BACKGROUND_DIR)):
        lower = fname.lower()
        if not lower.endswith(_IMAGE_EXTS):
            continue
        path = os.path.join(BACKGROUND_DIR, fname)
        if not os.path.isfile(path):
            continue
        stem = os.path.splitext(fname)[0]
        asset_id = f"builtin_background_{stem}"
        size = os.path.getsize(path)
        width = height = None
        try:
            info = render.probe_media(path)
            width = info.get("width")
            height = info.get("height")
        except RenderError:
            # 探测失败不阻断登记：尺寸交由 UI 补全
            pass
        asset = store.add_asset(
            asset_id=asset_id,
            name=f"内置·背景·{stem}",
            path=path,
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
