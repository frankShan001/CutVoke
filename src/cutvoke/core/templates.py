"""工程模板库（J12）——声明式模板加载与「构建计划」编译。

## 为什么是"计划"而不是直接改工程

本模块**只做纯数据**：把 `assets/templates/*.json` 解析成 `Template`，
再把模板 + 用户素材映射编译成一份 `build_plan`（轨道/片段/字幕的纯 alldict）。
由 `service.py` 把计划落成真实的 `Track/Clip/Caption`。

这样拆分的原因：模板层不需要 import model / Rational，可以脱离工程单测；
模型细节也不会渗进数据文件（JSON 里只有秒和归一化值，没有 num/den）。

## 设计要点

- **模板是数据不是代码**：新增模板 = 新增一个 JSON 文件，不改任何 .py。
- **坏模板跳过而不是崩模块**：`load_templates()` 返回 `(specs, skipped)`，
  `skipped` 带 `{path, reason}`；`template.list` 会把 skipped 一并暴露，
  便于发现写坏的模板。
- **零素材也能出片**：未提供素材的槽位用内置背景图占位（真实 PNG，
  `kind=image`，渲染时走 `-loop 1` 续帧）。所以模板套完就能直接导出成片，
  用户再逐个替换素材 —— 这才是模板对用户的价值。

## 为什么这里重复定义资产目录

`builtin_assets.py` 里也有同样的目录常量，但它顶部 `from .render import ...`。
本模块被 `service.py` 顶层 import，若在此 import builtin_assets 会把 render
拖进 service 的顶层导入链（service 目前是**惰性**引 render 的）。为了不改变
这条依赖关系，这里直接按相对路径算目录 —— 代价是两个常量，收益是无环依赖。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

# 本文件在 src/cutvoke/core/ 下，资产在 src/cutvoke/assets/
_ASSETS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "assets"))
TEMPLATE_DIR = os.path.join(_ASSETS_DIR, "templates")
BACKGROUND_DIR = os.path.join(_ASSETS_DIR, "backgrounds")
AUDIO_DIR = os.path.join(_ASSETS_DIR, "audio")

# 支持的槽位素材类型（模板 JSON 里 slot.kind 的合法取值）
_KINDS = ("image", "video", "audio")

_CACHE: Optional[tuple[list["Template"], list[dict]]] = None


@dataclass(frozen=True)
class Slot:
    """模板里的一个素材位（用户需要提供的素材）。"""

    key: str
    kind: str
    label: str
    duration: float

    def to_dict(self) -> dict:
        return {"key": self.key, "kind": self.kind,
                "label": self.label, "duration": self.duration}


@dataclass(frozen=True)
class Template:
    """一个工程模板的完整声明。"""

    id: str
    name: str
    description: str
    aspect: str
    category: str
    placeholders: tuple[str, ...]
    slots: tuple[Slot, ...]
    transition: Optional[dict] = None
    captions: tuple[dict, ...] = ()
    bgm: Optional[dict] = None
    stickers: tuple[dict, ...] = ()   # v1 预留：渲染定位语义未验证前不启用
    path: str = ""

    def to_dict(self) -> dict:
        """给 UI / Agent 看的摘要（不含实现细节）。"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "aspect": self.aspect,
            "category": self.category,
            "slotCount": len(self.slots),
            "slots": [s.to_dict() for s in self.slots],
            "hasBgm": self.bgm is not None,
            "captionCount": len(self.captions),
            "transition": (self.transition or {}).get("effectId"),
            "estimatedSeconds": round(sum(s.duration for s in self.slots), 3),
        }


# ----------------------------------------------------------------------
# 加载
# ----------------------------------------------------------------------

def load_templates(force: bool = False) -> tuple[list[Template], list[dict]]:
    """扫描模板目录，返回 (可用模板, 被跳过的模板)。

    缓存结果；`force=True` 重新扫描（测试用）。目录不存在返回空，不报错。
    单个模板解析失败只计入 skipped，不影响其它模板。
    """
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE

    specs: list[Template] = []
    skipped: list[dict] = []
    seen: set[str] = set()

    if os.path.isdir(TEMPLATE_DIR):
        for fname in sorted(os.listdir(TEMPLATE_DIR)):
            if not fname.lower().endswith(".json"):
                continue
            path = os.path.join(TEMPLATE_DIR, fname)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    raw = json.load(fh)
            except Exception as e:  # noqa: BLE001 — 坏 JSON 只跳过该文件
                skipped.append({"path": path, "reason": f"JSON 解析失败: {e}"})
                continue
            try:
                spec = _parse(raw, path)
            except ValueError as e:
                skipped.append({"path": path, "reason": str(e)})
                continue
            if spec.id in seen:
                skipped.append({"path": path, "reason": f"模板 id 重复: {spec.id}"})
                continue
            seen.add(spec.id)
            specs.append(spec)

    _CACHE = (specs, skipped)
    return _CACHE


def get_template(template_id: str) -> Optional[Template]:
    specs, _ = load_templates()
    return next((s for s in specs if s.id == template_id), None)


def _parse(raw: dict, path: str) -> Template:
    """校验并解析单个模板。任何不合法都抛 ValueError（由调用方转 skipped）。"""
    if not isinstance(raw, dict):
        raise ValueError("模板根必须是 JSON 对象")

    tid = raw.get("id")
    if not isinstance(tid, str) or not tid.strip():
        raise ValueError("缺少合法的 'id'")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("缺少合法的 'name'")

    slots_raw = raw.get("slots")
    if not isinstance(slots_raw, list) or not slots_raw:
        raise ValueError("'slots' 必须是非空数组（模板至少要有一个画面位）")

    slots: list[Slot] = []
    keys: set[str] = set()
    for i, s in enumerate(slots_raw):
        if not isinstance(s, dict):
            raise ValueError(f"slots[{i}] 必须是对象")
        key = s.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"slots[{i}] 缺少 'key'")
        if key in keys:
            raise ValueError(f"slots 里 key 重复: {key}")
        keys.add(key)
        kind = s.get("kind", "image")
        if kind not in _KINDS:
            raise ValueError(f"slots[{i}].kind 必须是 {'/'.join(_KINDS)}，得到 {kind!r}")
        dur = s.get("duration", 3.0)
        if not isinstance(dur, (int, float)) or isinstance(dur, bool) or dur <= 0:
            raise ValueError(f"slots[{i}].duration 必须是正数，得到 {dur!r}")
        slots.append(Slot(key=key, kind=kind,
                          label=str(s.get("label") or key), duration=float(dur)))

    ph = raw.get("placeholders") or []
    if not isinstance(ph, list):
        raise ValueError("'placeholders' 必须是数组（内置背景图 stem 列表）")
    placeholders = tuple(str(x) for x in ph if str(x).strip())

    trans = raw.get("transition")
    if trans is not None:
        if not isinstance(trans, dict) or not trans.get("effectId"):
            raise ValueError("'transition' 必须形如 {\"effectId\": \"...\", \"params\": {...}}")

    caps = raw.get("captions") or []
    if not isinstance(caps, list):
        raise ValueError("'captions' 必须是数组")
    captions: list[dict] = []
    for i, c in enumerate(caps):
        if not isinstance(c, dict) or not str(c.get("text") or "").strip():
            raise ValueError(f"captions[{i}] 缺少 'text'")
        at = c.get("at", 0.0)
        dur = c.get("duration", 2.0)
        if not isinstance(at, (int, float)) or at < 0:
            raise ValueError(f"captions[{i}].at 必须是非负数")
        if not isinstance(dur, (int, float)) or dur <= 0:
            raise ValueError(f"captions[{i}].duration 必须是正数")
        captions.append({"text": str(c["text"]), "at": float(at),
                         "duration": float(dur),
                         "style": dict(c.get("style") or {})})

    bgm = raw.get("bgm")
    if bgm is not None:
        if not isinstance(bgm, dict) or not (bgm.get("assetId") or bgm.get("sourcePath")):
            raise ValueError("'bgm' 需要 assetId 或 sourcePath")

    stickers = raw.get("stickers") or []
    if not isinstance(stickers, list):
        raise ValueError("'stickers' 必须是数组")

    aspect = str(raw.get("aspect") or "16:9")
    canvas_for_aspect(aspect)   # 提前校验，坏画幅要在加载期就暴露

    return Template(
        id=tid.strip(), name=name.strip(),
        description=str(raw.get("description") or ""),
        aspect=aspect,
        category=str(raw.get("category") or "通用"),
        placeholders=placeholders,
        slots=tuple(slots),
        transition=({"effectId": trans["effectId"],
                     "params": dict(trans.get("params") or {})} if trans else None),
        captions=tuple(captions),
        bgm=dict(bgm) if bgm else None,
        stickers=tuple(stickers),
        path=path,
    )


# ----------------------------------------------------------------------
# 画布与计划
# ----------------------------------------------------------------------

def canvas_for_aspect(aspect: str, base: int = 1080) -> tuple[int, int]:
    """把 '16:9' / '9:16' / '1:1' 转成画布像素尺寸。

    短边固定为 `base`（默认 1080），长边按比例放大，并取偶数（编码器要求）。
    非法画幅抛 ValueError。
    """
    try:
        w_s, h_s = str(aspect).split(":")
        w_r, h_r = float(w_s), float(h_s)
        if w_r <= 0 or h_r <= 0:
            raise ValueError
    except ValueError:
        raise ValueError(f"非法画幅 {aspect!r}（应形如 '16:9' / '9:16' / '1:1'）")

    if w_r >= h_r:
        h = base
        w = int(round(base * w_r / h_r))
    else:
        w = base
        h = int(round(base * h_r / w_r))
    return (w - w % 2, h - h % 2)


def builtin_background_path(stem: str) -> str:
    return os.path.normpath(os.path.join(BACKGROUND_DIR, f"{stem}.png"))


def builtin_audio_path(stem: str) -> str:
    return os.path.normpath(os.path.join(AUDIO_DIR, f"{stem}.wav"))


def _builtin_audio_stem(asset_id: str) -> str:
    """'builtin_audio_ambient_pad' -> 'ambient_pad'（内置音频命名约定）。"""
    return asset_id[len("builtin_audio_"):] if asset_id.startswith("builtin_audio_") else asset_id


def build_plan(spec: Template, sources: Optional[dict] = None,
               origin: float = 0.0) -> dict:
    """把模板 + 用户素材映射编译成构建计划（纯数据，不 import model）。

    参数：
      sources: `{slotKey: {"assetId": str, "sourcePath": str}}`，
               未提供的槽位用内置背景占位。
      origin:  时间线起点（秒）。不覆盖已有内容时传「现有内容末尾」，
               保证追加的片段不与既有片段重叠。

    返回：`{tracks, captions, durationSec, placeholderCount}`。
    sources 里出现未知 slotKey → ValueError。
    """
    sources = sources or {}
    slot_keys = {s.key for s in spec.slots}
    unknown = sorted(k for k in sources if k not in slot_keys)
    if unknown:
        raise ValueError(
            f"未知素材位 {', '.join(unknown)}；本模板可用素材位："
            f"{', '.join(sorted(slot_keys))}")

    cursor = float(origin)
    start = cursor
    clips: list[dict] = []
    placeholder_count = 0

    # 没有任何 placeholders 时兜底到一个必定存在的内置背景，避免占位无图。
    placeholders = spec.placeholders or ("bg_solid_black",)

    for i, slot in enumerate(spec.slots):
        dur = float(slot.duration)
        src = sources.get(slot.key)
        if src:
            asset_id = str(src.get("assetId") or "")
            source_path = str(src.get("sourcePath") or "")
            placeholder = False
        else:
            stem = placeholders[i % len(placeholders)]
            asset_id = f"builtin_background_{stem}"
            source_path = builtin_background_path(stem)
            placeholder = True
            placeholder_count += 1

        effects: list[dict] = []
        # 转场挂在「被切入」的片段上（模型约定），所以第一个片段不带。
        if i > 0 and spec.transition:
            effects.append({"effectId": spec.transition["effectId"],
                            "params": dict(spec.transition.get("params") or {})})

        clips.append({
            "slotKey": slot.key,
            "label": slot.label,
            "assetId": asset_id,
            "sourcePath": source_path,
            "start": cursor,
            "end": cursor + dur,
            "sourceStart": 0.0,
            "placeholder": placeholder,
            "volume": 1.0,
            "effects": effects,
        })
        cursor += dur

    tracks: list[dict] = [{"kind": "video", "clips": clips}]

    if spec.bgm and cursor > start:
        bgm_asset = str(spec.bgm.get("assetId") or "")
        bgm_path = str(spec.bgm.get("sourcePath") or "")
        if not bgm_path and bgm_asset:
            bgm_path = builtin_audio_path(_builtin_audio_stem(bgm_asset))
        try:
            volume = float(spec.bgm.get("volume", 1.0))
        except (TypeError, ValueError):
            volume = 1.0
        tracks.append({"kind": "audio", "clips": [{
            "slotKey": "bgm",
            "label": "背景音乐",
            "assetId": bgm_asset,
            "sourcePath": bgm_path,
            "start": start,
            "end": cursor,
            "sourceStart": 0.0,
            "placeholder": False,
            "volume": max(0.0, volume),
            "effects": [],
        }]})

    captions = [{
        "text": c["text"],
        "start": start + float(c["at"]),
        "end": start + float(c["at"]) + float(c["duration"]),
        "style": dict(c.get("style") or {}),
    } for c in spec.captions]

    return {
        "tracks": tracks,
        "captions": captions,
        "durationSec": round(cursor - start, 6),
        "placeholderCount": placeholder_count,
    }
