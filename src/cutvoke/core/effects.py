"""效果注册表与发现机制（任务书 T05 效果接入 / T17 效果 SDK / T23 能力查询）。

设计目标（对应验收 AC18 / AC19 / AC29）：
  * 效果是**声明式**的：一个效果 = 一份 manifest.json（id / 版本 / 参数 schema /
    实现声明）。内核不写死具体效果的分支逻辑，因此"新增一个转场"只需新增清单，
    不改内核 —— 这是社区可贡献的前提（AC18）。
  * 发现是**多来源**的：内置效果 + 用户目录 + 环境变量目录。UI / CLI / HTTP / MCP
    全部通过本模块拿到同一份能力清单。
  * 参数是**可校验**的：effect.add 提交时按 manifest 的 JSON Schema 子集校验，
    非法参数立即拒绝，不写进工程（AC19 严格拒绝）。
  * 引擎桥接是**通用**的：engine == "ffmpeg-xfade" 的效果由 implementation 声明
    xfade 的 transition 名，渲染器据此生成滤镜，不识别具体 effectId。

零第三方依赖（仅标准库）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------------------
# 分类常量（与 manifest.category 对应）
# ---------------------------------------------------------------------------
CATEGORY_TRANSITION = "transition"
CATEGORY_TRANSFORM = "transform"
CATEGORY_COLOR = "color"
CATEGORY_ANIMATION = "animation"
CATEGORY_FX = "fx"
CATEGORY_TEXT = "text"

# 引擎标识
ENGINE_XFADE = "ffmpeg-xfade"
ENGINE_INTERNAL = "internal"

# manifest 必需字段
REQUIRED_FIELDS = ("id", "version", "name", "category", "parameters", "implementation")

# 效果 ID 命名约束：小写字母/数字/点/短横线，必须以字母开头
_ID_RE = None  # 延迟构造，避免在模块导入期引入 re（保持导入轻量）

# 效果目录扫描顺序（后注册的不覆盖先注册的，除非显式 override）
_ENV_DIRS = "CUTVOKE_EFFECTS_PATH"


class EffectError(Exception):
    """效果注册表相关错误的基类。"""


class EffectNotFound(EffectError):
    """请求的效果 ID 未注册。"""


class EffectParamInvalid(EffectError):
    """效果参数不符合 manifest 声明的 schema。"""


class ManifestInvalid(EffectError):
    """manifest.json 结构或字段非法。"""


# ---------------------------------------------------------------------------
# 轻量 JSON Schema 子集校验
#
# 只支持效果清单实际会用到的关键字，避免引入 jsonschema 依赖：
#   type / properties / required / additionalProperties / minimum / maximum /
#   exclusiveMinimum / exclusiveMaximum / enum / items / minItems / maxItems
# ---------------------------------------------------------------------------
_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "boolean": (bool,),
    "integer": (int,),
    "number": (int, float),
}


def _type_matches(value: Any, type_name: str) -> bool:
    if type_name == "integer":
        # bool 是 int 的子类，必须显式排除
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    expected = _TYPE_MAP.get(type_name)
    if expected is None:
        return True  # 未知类型不阻断（前向兼容）
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    return isinstance(value, expected)


def validate_against_schema(schema: dict, value: Any, path: str = "") -> list[str]:
    """按 JSON Schema 子集校验 value，返回错误信息列表（空列表 = 通过）。

    path 是当前校验位置的人类可读路径，便于用户定位是哪个参数写错了。
    """
    errors: list[str] = []
    if not isinstance(schema, dict):
        return errors

    where = path or "params"

    # type
    t = schema.get("type")
    if isinstance(t, str) and not _type_matches(value, t):
        errors.append(f"{where}: 期望类型 {t}，实际 {type(value).__name__}")
        return errors  # 类型不对，后续约束无意义
    if isinstance(t, list):
        if not any(_type_matches(value, x) for x in t if isinstance(x, str)):
            errors.append(f"{where}: 期望类型 {t} 之一，实际 {type(value).__name__}")
            return errors

    # enum
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{where}: 取值必须是 {enum} 之一，实际 {value!r}")

    # 数值区间
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        mn, mx = schema.get("minimum"), schema.get("maximum")
        if isinstance(mn, (int, float)) and value < mn:
            errors.append(f"{where}: 小于最小值 {mn}（实际 {value}）")
        if isinstance(mx, (int, float)) and value > mx:
            errors.append(f"{where}: 大于最大值 {mx}（实际 {value}）")
        emn, emx = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
        if isinstance(emn, (int, float)) and value <= emn:
            errors.append(f"{where}: 必须大于 {emn}（实际 {value}）")
        if isinstance(emx, (int, float)) and value >= emx:
            errors.append(f"{where}: 必须小于 {emx}（实际 {value}）")

    # object
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                errors.append(f"{where}.{key}: 缺少必填参数")
        for key, sub in props.items():
            if key in value:
                errors.extend(validate_against_schema(sub, value[key], f"{where}.{key}"))
            else:
                # 有默认值的参数允许缺省（由 resolve_defaults 补齐）
                if isinstance(sub, dict) and "default" not in sub and key in required:
                    pass  # 上面已报
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append(f"{where}.{key}: 未知参数（manifest 声明 additionalProperties=false）")

    # array
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(value):
                errors.extend(validate_against_schema(items, item, f"{where}[{i}]"))
        mn_i, mx_i = schema.get("minItems"), schema.get("maxItems")
        if isinstance(mn_i, int) and len(value) < mn_i:
            errors.append(f"{where}: 元素数少于 {mn_i}")
        if isinstance(mx_i, int) and len(value) > mx_i:
            errors.append(f"{where}: 元素数多于 {mx_i}")

    return errors


def resolve_defaults(schema: dict, value: Any) -> Any:
    """按 schema 的 default 补齐缺省参数（递归；不修改入参）。"""
    if not isinstance(schema, dict):
        return value

    t = schema.get("type")
    if isinstance(t, str) and not _type_matches(value, t):
        return value

    if isinstance(value, dict):
        props = schema.get("properties") or {}
        out = dict(value)
        for key, sub in props.items():
            if key in out:
                out[key] = resolve_defaults(sub, out[key])
            elif isinstance(sub, dict) and "default" in sub:
                out[key] = sub["default"]
        return out

    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            return [resolve_defaults(items, x) for x in value]
    return value


# ---------------------------------------------------------------------------
# 效果规格
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EffectSpec:
    """一个效果的声明式规格（来自内置定义或 manifest.json）。"""

    id: str
    version: str
    name: dict[str, str]
    category: str
    parameters: dict
    implementation: dict
    description: dict[str, str] = field(default_factory=dict)
    license: str = ""
    inputs: tuple[str, ...] = ()
    output: str = "video"
    source: str = "builtin"          # "builtin" 或 manifest 文件路径
    compat_min_core: str = ""        # 声明的内核最低版本（空 = 不限制）
    # ---- J01 资源共同基础（计划 5.1：关键词 / 适用对象 / 预览 / 资源依赖）----
    keywords: tuple[str, ...] = ()   # 中文检索词（搜索、分类、AI 发现共用）
    applies_to: tuple[str, ...] = ("video",)   # 适用对象：image / video / text
    preview: str = ""                # 预览提示（缩略/试用的展示说明）
    dependencies: tuple[str, ...] = ()  # 资源依赖（如 font / face-detection / lut）

    # ---- 便捷属性 ----
    @property
    def engine(self) -> str:
        return str(self.implementation.get("engine", ENGINE_INTERNAL))

    @property
    def is_transition(self) -> bool:
        return self.category == CATEGORY_TRANSITION

    @property
    def xfade_transition(self) -> Optional[str]:
        """若该效果由 ffmpeg xfade 桥接，返回 xfade 的 transition 名。

        crossfade(叠化) 在 xfade 里叫 fade —— 名字差异由 manifest 声明，
        内核不再硬编码。返回 None 表示不是 xfade 桥接的效果。
        """
        if self.engine != ENGINE_XFADE:
            return None
        name = self.implementation.get("transition")
        return str(name) if name else None

    def label(self, lang: str = "zh-CN") -> str:
        return self.name.get(lang) or self.name.get("en") or self.id

    def describe(self, lang: str = "zh-CN") -> str:
        return self.description.get(lang) or self.description.get("en") or ""

    def param_schema(self, param: str) -> dict:
        props = self.parameters.get("properties") or {}
        sub = props.get(param)
        return sub if isinstance(sub, dict) else {}

    def default_params(self) -> dict:
        """该效果所有带 default 的参数构成的默认参数字典。"""
        out: dict[str, Any] = {}
        props = self.parameters.get("properties") or {}
        for key, sub in props.items():
            if isinstance(sub, dict) and "default" in sub:
                out[key] = sub["default"]
        return out

    def animatable_params(self) -> list[str]:
        """声明了 x-cutvoke-animatable 的参数名（可打关键帧）。"""
        props = self.parameters.get("properties") or {}
        return [k for k, v in props.items()
                if isinstance(v, dict) and v.get("x-cutvoke-animatable")]

    def matches(self, q: str) -> bool:
        """关键词/名称/ID/描述模糊匹配（大小写不敏感），供资源检索使用。"""
        if not q:
            return True
        needle = q.strip().lower()
        if not needle:
            return True
        hay = [self.id.lower(), self.label("zh-CN").lower(),
               self.label("en").lower(), self.describe("zh-CN").lower(),
               self.category.lower()]
        hay.extend(k.lower() for k in self.keywords)
        return any(needle in h for h in hay)

    def to_dict(self, lang: str = "zh-CN") -> dict:
        """对外能力清单条目（UI / CLI / HTTP / MCP 共用同一结构）。"""
        return {
            "effectId": self.id,
            "version": self.version,
            "name": self.label(lang),
            "category": self.category,
            "description": self.describe(lang),
            "license": self.license,
            "inputs": list(self.inputs),
            "output": self.output,
            "parameters": self.parameters,
            "defaults": self.default_params(),
            "animatable": self.animatable_params(),
            "implementation": {"engine": self.engine},
            "source": self.source,
            "keywords": list(self.keywords),
            "appliesTo": list(self.applies_to),
            "preview": self.preview,
            "dependencies": list(self.dependencies),
        }

    @classmethod
    def from_manifest(cls, data: dict, source: str = "") -> "EffectSpec":
        """从 manifest 字典构造，缺字段/字段非法立即报错。"""
        if not isinstance(data, dict):
            raise ManifestInvalid(f"manifest 必须是对象: {source}")
        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            raise ManifestInvalid(f"manifest 缺少字段 {missing}: {source}")

        eid = data["id"]
        if not isinstance(eid, str) or not eid.strip():
            raise ManifestInvalid(f"manifest.id 必须是非空字符串: {source}")

        name = data["name"]
        if isinstance(name, str):
            name = {"zh-CN": name}
        if not isinstance(name, dict):
            raise ManifestInvalid(f"manifest.name 必须是字符串或语言映射: {source}")

        desc = data.get("description", {})
        if isinstance(desc, str):
            desc = {"zh-CN": desc}
        if not isinstance(desc, dict):
            raise ManifestInvalid(f"manifest.description 必须是字符串或语言映射: {source}")

        params = data["parameters"]
        if not isinstance(params, dict):
            raise ManifestInvalid(f"manifest.parameters 必须是 JSON Schema 对象: {source}")
        if params.get("type") not in (None, "object"):
            raise ManifestInvalid(f"manifest.parameters.type 必须是 object: {source}")

        impl = data["implementation"]
        if not isinstance(impl, dict):
            raise ManifestInvalid(f"manifest.implementation 必须是对象: {source}")

        inputs = data.get("inputs", [])
        if not isinstance(inputs, list):
            raise ManifestInvalid(f"manifest.inputs 必须是数组: {source}")

        keywords = data.get("keywords", [])
        if not isinstance(keywords, list):
            raise ManifestInvalid(f"manifest.keywords 必须是数组: {source}")
        applies_to = data.get("appliesTo", data.get("applies_to", ["video"]))
        if isinstance(applies_to, str):
            applies_to = [applies_to]
        if not isinstance(applies_to, list):
            raise ManifestInvalid(f"manifest.appliesTo 必须是数组: {source}")
        dependencies = data.get("dependencies", [])
        if isinstance(dependencies, str):
            dependencies = [dependencies]
        if not isinstance(dependencies, list):
            raise ManifestInvalid(f"manifest.dependencies 必须是数组: {source}")

        return cls(
            id=eid,
            version=str(data["version"]),
            name={str(k): str(v) for k, v in name.items()},
            category=str(data["category"]),
            parameters=params,
            implementation=impl,
            description={str(k): str(v) for k, v in desc.items()},
            license=str(data.get("license", "")),
            inputs=tuple(str(x) for x in inputs),
            output=str(data.get("output", "video")),
            source=source,
            compat_min_core=str(data.get("x-cutvoke-min-core", "")),
            keywords=tuple(str(x) for x in keywords),
            applies_to=tuple(str(x) for x in applies_to),
            preview=str(data.get("preview", "")),
            dependencies=tuple(str(x) for x in dependencies),
        )


# ---------------------------------------------------------------------------
# 内置效果（内核自带，随包分发）
#
# 这三个效果的渲染路径在 render.py 里实现；清单在此声明，
# 使 UI / CLI / HTTP / MCP 能像发现外部效果一样发现它们（AC18 的前提）。
# ---------------------------------------------------------------------------
def _builtin_specs() -> list[EffectSpec]:
    from .model import (EFFECT_TRANSFORM, EFFECT_COLOR, EFFECT_CROSSFADE,
                        EFFECT_FADE, EFFECT_WIPE, EFFECT_SLIDE,
                        EFFECT_BLUR, EFFECT_ZOOM,
                        EFFECT_ANIM_FADE_IN, EFFECT_ANIM_ZOOM_IN,
                        EFFECT_ANIM_SLIDE_IN, EFFECT_ANIM_FADE_OUT,
                        EFFECT_ANIM_ZOOM_OUT, EFFECT_ANIM_BREATHE,
                        EFFECT_ANIM_SLIDE_UP, EFFECT_ANIM_SLIDE_DOWN,
                        EFFECT_ANIM_SLIDE_RIGHT, EFFECT_ANIM_ROTATE_IN,
                        EFFECT_ANIM_FLIP_IN, EFFECT_ANIM_BACK_IN,
                        EFFECT_ANIM_REVEAL, EFFECT_ANIM_FLOAT, EFFECT_ANIM_SWAY,
                        EFFECT_ANIM_COMBO,
                        EFFECT_ANIM_COMBO_PUSH_RIGHT, EFFECT_ANIM_COMBO_PULL_UP,
                        EFFECT_ANIM_COMBO_ROTATE_ZOOM,
                        EFFECT_ANIM_COMBO_BANNER_IN, EFFECT_ANIM_COMBO_CARD_IN,
                        EFFECT_ANIM_COMBO_DRIFT_LEFT,
                        EFFECT_FX_BLUR, EFFECT_GRAYSCALE, EFFECT_SEPIA,
                        EFFECT_VIGNETTE, EFFECT_FX_GLOW, EFFECT_FX_CHROMATIC,
                        EFFECT_FX_GLITCH, EFFECT_FX_SHARPEN, EFFECT_FX_EDGE,
                        EFFECT_FX_INVERT, EFFECT_FX_MIRROR, EFFECT_FX_VINTAGE,
                        EFFECT_FX_POSTERIZE,
                        EFFECT_FX_CHROMAKEY, EFFECT_FX_LUT, EFFECT_FX_CURVES,
                        EFFECT_FX_DENOISE, EFFECT_FX_LOUDNORM, EFFECT_FX_RGBSPLIT,
                        EFFECT_FX_EQ, EFFECT_FX_COMPRESSOR,
                        EFFECT_FX_FLICKER, EFFECT_FX_PAN,
                        EFFECT_FX_CROP,
                        EFFECT_FX_TRAIL, EFFECT_FX_HSL, EFFECT_FX_COLORBALANCE,
                        EFFECT_FX_MASK, EFFECT_FX_SHAPE,
                        EFFECT_FADE_WHITE, EFFECT_SLIDE_UP, EFFECT_SLIDE_DOWN,
                        EFFECT_WIPE_UP, EFFECT_WIPE_DOWN, EFFECT_DISSOLVE,
                        EFFECT_CIRCLE_OPEN, EFFECT_DIAGONAL, EFFECT_PIXELIZE,
                        EFFECT_SMOOTH_LEFT, EFFECT_RADIAL, EFFECT_SQUEEZE,
                        EFFECT_TEXT,
                        TRANSFORM_DEFAULTS, COLOR_DEFAULTS)

    transform = EffectSpec(
        id=EFFECT_TRANSFORM,
        version="1.0.0",
        name={"zh-CN": "画面变换", "en": "Transform"},
        category=CATEGORY_TRANSFORM,
        description={
            "zh-CN": "调整画面的位置、缩放、旋转与不透明度，用于画中画与多轨合成。",
            "en": "Position, scale, rotate and fade a clip for picture-in-picture and compositing.",
        },
        license="MIT",
        inputs=("video",),
        output="video",
        parameters={
            "type": "object",
            "properties": {
                "position": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer", "default": TRANSFORM_DEFAULTS["position"]["x"]},
                        "y": {"type": "integer", "default": TRANSFORM_DEFAULTS["position"]["y"]},
                    },
                    "additionalProperties": False,
                },
                "scale": {"type": "number", "exclusiveMinimum": 0.0,
                          "default": TRANSFORM_DEFAULTS["scale"]},
                "rotation": {"type": "number", "default": TRANSFORM_DEFAULTS["rotation"]},
                "opacity": {"type": "number", "minimum": 0.0, "maximum": 1.0,
                            "default": TRANSFORM_DEFAULTS["opacity"],
                            "x-cutvoke-animatable": True},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_INTERNAL, "filter": "scale+overlay"},
        source="builtin",
        keywords=("变换", "位置", "缩放", "旋转", "不透明度", "画中画", "transform"),
    )

    color = EffectSpec(
        id=EFFECT_COLOR,
        version="1.0.0",
        name={"zh-CN": "基础调色", "en": "Color grade"},
        category=CATEGORY_COLOR,
        description={
            "zh-CN": "调节亮度、对比度与饱和度（ffmpeg eq）。",
            "en": "Adjust brightness, contrast and saturation (ffmpeg eq).",
        },
        license="MIT",
        inputs=("video",),
        output="video",
        parameters={
            "type": "object",
            "properties": {
                "brightness": {"type": "number", "minimum": -1.0, "maximum": 1.0,
                               "default": COLOR_DEFAULTS["brightness"]},
                "contrast": {"type": "number", "minimum": 0.0, "maximum": 3.0,
                             "default": COLOR_DEFAULTS["contrast"]},
                "saturation": {"type": "number", "minimum": 0.0, "maximum": 3.0,
                               "default": COLOR_DEFAULTS["saturation"]},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_INTERNAL, "filter": "eq"},
        source="builtin",
        keywords=("调色", "亮度", "对比度", "饱和度", "color", "eq"),
    )

    crossfade = EffectSpec(
        id=EFFECT_CROSSFADE,
        version="1.0.0",
        name={"zh-CN": "叠化转场", "en": "Crossfade"},
        category=CATEGORY_TRANSITION,
        description={
            "zh-CN": "两段画面之间以均匀透明度叠加的方式平滑过渡（crossfade / dissolve），无方向性，适合场景切换。",
            "en": "Smoothly blend from the first clip to the second with an even-opacity dissolve. Directionless, good for scene changes.",
        },
        license="Apache-2.0",
        inputs=("video:a", "video:b"),
        output="video",
        parameters={
            "type": "object",
            "properties": {
                "duration": {"type": "number", "minimum": 0, "maximum": 5.0,
                             "default": 0.5, "unit": "s",
                             "x-cutvoke-animatable": False,
                             "description": {
                                 "zh-CN": "转场持续时长（秒）。时长越长，叠化越慢。",
                                 "en": "Transition duration in seconds. Longer means a slower dissolve.",
                             }},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_XFADE, "filter": "xfade", "transition": "fade"},
        source="builtin",
        keywords=("叠化", "溶接", "渐变", "过渡", "crossfade", "dissolve"),
    )

    fade = EffectSpec(
        id=EFFECT_FADE,
        version="1.0.0",
        name={"zh-CN": "淡入淡出", "en": "Fade through black"},
        category=CATEGORY_TRANSITION,
        description={
            "zh-CN": "前段淡出、后段淡入，中间经过黑场（fade to black）。经典段落分隔手法。",
            "en": "Fade out to black then fade in from black. A classic section divider.",
        },
        license="Apache-2.0",
        inputs=("video:a", "video:b"),
        output="video",
        parameters={
            "type": "object",
            "properties": {
                "duration": {"type": "number", "minimum": 0, "maximum": 5.0,
                             "default": 0.5, "unit": "s",
                             "x-cutvoke-animatable": False,
                             "description": {
                                 "zh-CN": "转场持续时长（秒）。时长越长，黑场越明显。",
                                 "en": "Transition duration in seconds. Longer means more black-screen time.",
                             }},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_XFADE, "filter": "xfade", "transition": "fadeblack"},
        source="builtin",
        keywords=("淡入淡出", "黑场", "渐黑", "过渡", "fade"),
    )

    wipe = EffectSpec(
        id=EFFECT_WIPE,
        version="1.0.0",
        name={"zh-CN": "擦除转场", "en": "Wipe"},
        category=CATEGORY_TRANSITION,
        description={
            "zh-CN": "后段从左侧像帘幕一样擦入覆盖前段（wipeleft）。适合硬切的利落转场。",
            "en": "The next clip wipes in from the left like a curtain. A crisp, editorial transition.",
        },
        license="Apache-2.0",
        inputs=("video:a", "video:b"),
        output="video",
        parameters={
            "type": "object",
            "properties": {
                "duration": {"type": "number", "minimum": 0, "maximum": 5.0,
                             "default": 0.5, "unit": "s",
                             "x-cutvoke-animatable": False,
                             "description": {
                                 "zh-CN": "转场持续时长（秒）。",
                                 "en": "Transition duration in seconds.",
                             }},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_XFADE, "filter": "xfade", "transition": "wipeleft"},
        source="builtin",
        keywords=("擦除", "帘幕", "左擦", "过渡", "wipe"),
    )

    slide = EffectSpec(
        id=EFFECT_SLIDE,
        version="1.0.0",
        name={"zh-CN": "滑动转场", "en": "Slide"},
        category=CATEGORY_TRANSITION,
        description={
            "zh-CN": "后段画面从右侧滑入，推动前段离开（slideright）。流畅的并列感。",
            "en": "The next clip slides in from the right, pushing the previous one away.",
        },
        license="Apache-2.0",
        inputs=("video:a", "video:b"),
        output="video",
        parameters={
            "type": "object",
            "properties": {
                "duration": {"type": "number", "minimum": 0, "maximum": 5.0,
                             "default": 0.5, "unit": "s",
                             "x-cutvoke-animatable": False,
                             "description": {
                                 "zh-CN": "转场持续时长（秒）。",
                                 "en": "Transition duration in seconds.",
                             }},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_XFADE, "filter": "xfade", "transition": "slideright"},
        source="builtin",
        keywords=("滑动", "推移", "右滑", "过渡", "slide"),
    )

    blur = EffectSpec(
        id=EFFECT_BLUR,
        version="1.0.0",
        name={"zh-CN": "模糊转场", "en": "Blur"},
        category=CATEGORY_TRANSITION,
        description={
            "zh-CN": "横向拖影式模糊过渡（hblur），速度感强。注意：xfade 没有名为 blur 的转场，"
                     "此处映射到真实存在的 hblur。",
            "en": "Horizontal motion-blur transition (hblur).",
        },
        license="Apache-2.0",
        inputs=("video:a", "video:b"),
        output="video",
        parameters={
            "type": "object",
            "properties": {
                "duration": {"type": "number", "minimum": 0, "maximum": 5.0,
                             "default": 0.5, "unit": "s",
                             "x-cutvoke-animatable": False,
                             "description": {
                                 "zh-CN": "转场持续时长（秒）。",
                                 "en": "Transition duration in seconds.",
                             }},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_XFADE, "filter": "xfade", "transition": "hblur"},
        source="builtin",
        keywords=("模糊", "拖影", "拉伸", "速度", "blur"),
        applies_to=("video",),
    )

    zoom = EffectSpec(
        id=EFFECT_ZOOM,
        version="1.0.0",
        name={"zh-CN": "缩放转场", "en": "Zoom"},
        category=CATEGORY_TRANSITION,
        description={
            "zh-CN": "画面缩放推进式切换，增强节奏感和冲击力。",
            "en": "A zoom push between shots for a dynamic, punchy cut.",
        },
        license="Apache-2.0",
        inputs=("video:a", "video:b"),
        output="video",
        parameters={
            "type": "object",
            "properties": {
                "duration": {"type": "number", "minimum": 0, "maximum": 5.0,
                             "default": 0.5, "unit": "s",
                             "x-cutvoke-animatable": False,
                             "description": {
                                 "zh-CN": "转场持续时长（秒）。",
                                 "en": "Transition duration in seconds.",
                             }},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_XFADE, "filter": "xfade", "transition": "zoomin"},
        source="builtin",
        keywords=("缩放", "推进", "冲击", "过渡", "zoom"),
    )

    # ---- 转场家族扩充（1.5-B 计划 5.2）：全部由 ffmpeg xfade 桥接 ----
    def _trans(tid, cn, en, xfade_name, desc_cn, keywords):
        return EffectSpec(
            id=tid, version="1.0.0",
            name={"zh-CN": cn, "en": en},
            category=CATEGORY_TRANSITION,
            description={"zh-CN": desc_cn, "en": f"Transition: {en}"},
            license="Apache-2.0",
            inputs=("video:a", "video:b"), output="video",
            parameters={
                "type": "object",
                "properties": {
                    "duration": {"type": "number", "minimum": 0, "maximum": 5.0,
                                 "default": 0.5, "unit": "s",
                                 "description": {"zh-CN": "转场持续时长（秒）",
                                                 "en": "Transition duration in seconds"}},
                },
                "additionalProperties": False,
            },
            implementation={"engine": ENGINE_XFADE, "filter": "xfade",
                            "transition": xfade_name},
            source="builtin", keywords=tuple(keywords),
            applies_to=("video",),
        )

    fade_white = _trans(EFFECT_FADE_WHITE, "闪白转场", "Fade through white",
                        "fadewhite", "前段泛白淡出、后段从白场淡入，比闪黑更轻盈明亮。",
                        ["闪白", "白场", "过曝", "明亮", "fadewhite"])
    slide_up = _trans(EFFECT_SLIDE_UP, "上滑转场", "Slide up", "slideup",
                      "后段自下方向上滑入推动前段。", ["上滑", "向上", "推移", "slide"])
    slide_down = _trans(EFFECT_SLIDE_DOWN, "下滑转场", "Slide down", "slidedown",
                        "后段自上方向下滑入推动前段。", ["下滑", "向下", "推移", "slide"])
    wipe_up = _trans(EFFECT_WIPE_UP, "上擦转场", "Wipe up", "wipeup",
                     "后段自下方向上擦入覆盖前段。", ["上擦", "擦除", "wipe"])
    wipe_down = _trans(EFFECT_WIPE_DOWN, "下擦转场", "Wipe down", "wipedown",
                       "后段自上方向下擦入覆盖前段。", ["下擦", "擦除", "wipe"])
    dissolve = _trans(EFFECT_DISSOLVE, "溶解转场", "Dissolve", "dissolve",
                      "以颗粒溶解方式过渡，比叠化更有质感。", ["溶解", "颗粒", "噪点"])
    circle_open = _trans(EFFECT_CIRCLE_OPEN, "圆形张开", "Circle open", "circleopen",
                         "以圆形遮罩从中心张开揭示后段。", ["圆形", "遮罩", "张开", "光环"])
    diagonal = _trans(EFFECT_DIAGONAL, "对角分割", "Diagonal", "diagtl",
                      "沿左上对角方向分割推进，硬朗有节奏。", ["对角", "分割", "斜向"])
    pixelize = _trans(EFFECT_PIXELIZE, "像素化转场", "Pixelize", "pixelize",
                      "画面像素化后重组为后段，数字感强。", ["像素", "马赛克", "形变", "数码"])
    smooth_left = _trans(EFFECT_SMOOTH_LEFT, "平滑推移", "Smooth left", "smoothleft",
                         "自左向右平滑推移，比滑动更柔和。", ["推移", "平滑", "柔和"])
    radial = _trans(EFFECT_RADIAL, "径向旋转", "Radial", "radial",
                    "以径向扫描方式旋转切换，适合动感段落。", ["径向", "旋转", "扫描", "动感"])
    squeeze = _trans(EFFECT_SQUEEZE, "横向挤压", "Squeeze horizontal", "squeezeh",
                     "后段从两侧向中间挤压式切入，节奏干脆有力。",
                     ["挤压", "压缩", "横向", "力度", "squeeze"])

    # ---- 入场动画（1.5-A 图片动画）：声明式参数化，渲染时展开成关键帧 ----
    anim_fade = EffectSpec(
        id=EFFECT_ANIM_FADE_IN,
        version="1.0.0",
        name={"zh-CN": "淡入", "en": "Fade in"},
        category=CATEGORY_ANIMATION,
        description={
            "zh-CN": "画面从透明淡入到不透明（入场）。",
            "en": "Fade the clip in from transparent.",
        },
        license="MIT",
        inputs=("video",),
        output="video",
        parameters={
            "type": "object",
            "properties": {
                "duration": {"type": "number", "minimum": 0.1, "maximum": 5.0,
                             "default": 1.0, "unit": "s",
                             "x-cutvoke-animatable": False,
                             "description": {"zh-CN": "动画时长（秒）", "en": "Duration in seconds"}},
                "easing": {"type": "string", "enum": ["linear", "ease-in", "ease-out"],
                           "default": "linear"},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_INTERNAL, "kind": "anim-fade-in"},
        source="builtin",
        keywords=("淡入", "入场", "渐显", "渐入", "fade"),
    )

    anim_zoom = EffectSpec(
        id=EFFECT_ANIM_ZOOM_IN,
        version="1.0.0",
        name={"zh-CN": "缩放推近", "en": "Zoom in"},
        category=CATEGORY_ANIMATION,
        description={
            "zh-CN": "画面从小尺寸推近到满屏（入场）。",
            "en": "Zoom the clip in from smaller to full frame.",
        },
        license="MIT",
        inputs=("video",),
        output="video",
        parameters={
            "type": "object",
            "properties": {
                "duration": {"type": "number", "minimum": 0.1, "maximum": 5.0,
                             "default": 1.0, "unit": "s"},
                "fromScale": {"type": "number", "minimum": 0.1, "maximum": 1.0,
                              "default": 0.8,
                              "description": {"zh-CN": "起始缩放（0.1~1.0）"}},
                "easing": {"type": "string", "enum": ["linear", "ease-in", "ease-out"],
                           "default": "ease-out"},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_INTERNAL, "kind": "anim-zoom-in"},
        source="builtin",
        keywords=("缩放", "推近", "放大", "入场", "zoom"),
    )

    anim_slide = EffectSpec(
        id=EFFECT_ANIM_SLIDE_IN,
        version="1.0.0",
        name={"zh-CN": "左滑入", "en": "Slide in from left"},
        category=CATEGORY_ANIMATION,
        description={
            "zh-CN": "画面从左侧滑入到位（入场）。",
            "en": "Slide the clip in from the left.",
        },
        license="MIT",
        inputs=("video",),
        output="video",
        parameters={
            "type": "object",
            "properties": {
                "duration": {"type": "number", "minimum": 0.1, "maximum": 5.0,
                             "default": 1.0, "unit": "s"},
                "easing": {"type": "string", "enum": ["linear", "ease-in", "ease-out"],
                           "default": "ease-out"},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_INTERNAL, "kind": "anim-slide-in"},
        source="builtin",
        keywords=("左滑", "滑入", "方向移动", "入场", "slide"),
    )

    anim_fade_out = EffectSpec(
        id=EFFECT_ANIM_FADE_OUT,
        version="1.0.0",
        name={"zh-CN": "淡出", "en": "Fade out"},
        category=CATEGORY_ANIMATION,
        description={"zh-CN": "片段末尾淡出到透明（出场）。", "en": "Fade the clip out to transparent."},
        license="MIT",
        inputs=("video",),
        output="video",
        parameters={
            "type": "object",
            "properties": {
                "duration": {"type": "number", "minimum": 0.1, "maximum": 5.0,
                             "default": 1.0, "unit": "s"},
                "easing": {"type": "string", "enum": ["linear", "ease-in", "ease-out"],
                           "default": "linear"},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_INTERNAL, "kind": "anim-fade-out"},
        source="builtin",
        keywords=("淡出", "出场", "渐隐", "渐出", "fade"),
    )

    anim_zoom_out = EffectSpec(
        id=EFFECT_ANIM_ZOOM_OUT,
        version="1.0.0",
        name={"zh-CN": "缩放拉远", "en": "Zoom out"},
        category=CATEGORY_ANIMATION,
        description={"zh-CN": "片段末尾从满屏拉远（出场）。", "en": "Zoom out at the end of the clip."},
        license="MIT",
        inputs=("video",),
        output="video",
        parameters={
            "type": "object",
            "properties": {
                "duration": {"type": "number", "minimum": 0.1, "maximum": 5.0,
                             "default": 1.0, "unit": "s"},
                "toScale": {"type": "number", "minimum": 0.1, "maximum": 1.0,
                            "default": 0.8},
                "easing": {"type": "string", "enum": ["linear", "ease-in", "ease-out"],
                           "default": "ease-in"},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_INTERNAL, "kind": "anim-zoom-out"},
        source="builtin",
        keywords=("缩放", "拉远", "缩小", "出场", "zoom"),
    )

    anim_breathe = EffectSpec(
        id=EFFECT_ANIM_BREATHE,
        version="1.0.0",
        name={"zh-CN": "呼吸", "en": "Breathe"},
        category=CATEGORY_ANIMATION,
        description={"zh-CN": "画面轻微缩放循环（呼吸感）。", "en": "A gentle breathing scale-loop."},
        license="MIT",
        inputs=("video",),
        output="video",
        parameters={
            "type": "object",
            "properties": {
                "amplitude": {"type": "number", "minimum": 0.01, "maximum": 0.2,
                              "default": 0.03,
                              "description": {"zh-CN": "缩放幅度（如 0.03=±3%）"}},
                "period": {"type": "number", "minimum": 0.5, "maximum": 5.0,
                           "default": 2.0, "unit": "s",
                           "description": {"zh-CN": "一个呼吸周期（秒）"}},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_INTERNAL, "kind": "anim-breathe"},
        source="builtin",
        keywords=("呼吸", "缩放", "循环", "律动", "breathe"),
    )

    # ---- 动画家族扩充（1.5-B 计划 5.2）：方向移动/旋转/翻转/弹性/遮罩/漂浮/摇摆 ----
    # 全部复用渲染层同一条「子段时间插值」链，按 kind 解释，内核不新增分支。
    def _anim(aid, cn, en, kind, desc_cn, keywords, extra_props=None):
        props = {
            "duration": {"type": "number", "minimum": 0.1, "maximum": 5.0,
                         "default": 1.0, "unit": "s",
                         "description": {"zh-CN": "动画时长（秒）"}},
            "easing": {"type": "string", "enum": ["linear", "ease-in", "ease-out"],
                       "default": "ease-out"},
        }
        if extra_props:
            props.update(extra_props)
        return EffectSpec(
            id=aid, version="1.0.0",
            name={"zh-CN": cn, "en": en}, category=CATEGORY_ANIMATION,
            description={"zh-CN": desc_cn, "en": f"Animation: {en}"},
            license="MIT", inputs=("video",), output="video",
            parameters={"type": "object", "properties": props,
                        "additionalProperties": False},
            implementation={"engine": ENGINE_INTERNAL, "kind": kind},
            source="builtin", keywords=tuple(keywords),
            applies_to=("image", "video"),
        )

    def _anim_loop(aid, cn, en, kind, desc_cn, keywords):
        return EffectSpec(
            id=aid, version="1.0.0",
            name={"zh-CN": cn, "en": en}, category=CATEGORY_ANIMATION,
            description={"zh-CN": desc_cn, "en": f"Animation: {en}"},
            license="MIT", inputs=("video",), output="video",
            parameters={
                "type": "object",
                "properties": {
                    "amplitude": {"type": "number", "minimum": 0.005, "maximum": 0.15,
                                  "default": 0.03,
                                  "description": {"zh-CN": "运动幅度（占画面比例）"}},
                    "period": {"type": "number", "minimum": 0.5, "maximum": 8.0,
                               "default": 3.0, "unit": "s",
                               "description": {"zh-CN": "一个循环周期（秒）"}},
                },
                "additionalProperties": False,
            },
            implementation={"engine": ENGINE_INTERNAL, "kind": kind},
            source="builtin", keywords=tuple(keywords),
            applies_to=("image", "video"),
        )

    distance_prop = {"distance": {"type": "number", "minimum": 0.05, "maximum": 0.6,
                                  "default": 0.3,
                                  "description": {"zh-CN": "入场位移距离（占画面比例）"}}}

    anim_slide_up = _anim(EFFECT_ANIM_SLIDE_UP, "上滑入", "Slide up",
                          "anim-slide-up", "画面自下方向上滑入到位（入场）。",
                          ["上滑", "向上", "方向移动", "slide"], dict(distance_prop))
    anim_slide_down = _anim(EFFECT_ANIM_SLIDE_DOWN, "下滑入", "Slide down",
                            "anim-slide-down", "画面自上方向下滑入到位（入场）。",
                            ["下滑", "向下", "方向移动", "slide"], dict(distance_prop))
    anim_slide_right = _anim(EFFECT_ANIM_SLIDE_RIGHT, "右滑入", "Slide in from right",
                             "anim-slide-right", "画面自右侧滑入到位（入场）。",
                             ["右滑", "从右", "方向移动", "slide"], dict(distance_prop))
    anim_rotate_in = _anim(EFFECT_ANIM_ROTATE_IN, "旋转入场", "Rotate in",
                           "anim-rotate-in", "画面边旋转边淡入到位（入场）。",
                           ["旋转", "转动", "回正", "rotate"],
                           {"fromAngle": {"type": "number", "minimum": -360.0,
                                          "maximum": 360.0, "default": -12.0,
                                          "description": {"zh-CN": "起始角度（度）"}}})
    anim_flip_in = _anim(EFFECT_ANIM_FLIP_IN, "翻牌入场", "Flip in",
                         "anim-flip-in", "画面沿水平轴由窄翻展到满幅（入场）。",
                         ["翻转", "翻牌", "翻开", "flip"])
    anim_back_in = _anim(EFFECT_ANIM_BACK_IN, "回弹入场", "Back in",
                         "anim-back-in", "画面先过冲再回落到满幅，带轻微弹性（入场）。",
                         ["回弹", "弹性", "过冲", "bounce", "back"],
                         {"overshoot": {"type": "number", "minimum": 0.02,
                                        "maximum": 0.3, "default": 0.08,
                                        "description": {"zh-CN": "过冲幅度（占画面比例）"}}})
    anim_reveal = _anim(EFFECT_ANIM_REVEAL, "遮罩显现", "Reveal",
                        "anim-reveal", "画面像被遮罩自左向右揭示出来（入场）。",
                        ["遮罩", "显现", "揭示", "擦入", "mask", "reveal"],
                        {"direction": {"type": "string",
                                       "enum": ["left", "right", "up", "down"],
                                       "default": "left",
                                       "description": {"zh-CN": "揭示方向"}}})
    anim_float = _anim_loop(EFFECT_ANIM_FLOAT, "漂浮", "Float", "anim-float",
                            "画面缓慢上下浮动循环，适合图片与封面。",
                            ["漂浮", "浮动", "缓慢", "float", "循环"])
    anim_sway = _anim_loop(EFFECT_ANIM_SWAY, "摇摆", "Sway", "anim-sway",
                           "画面左右轻微摇摆循环，比漂浮更有节奏。",
                           ["摇摆", "左右", "轻摆", "sway", "循环"])

    # ---- 组合动画（1.5 批次2 E04）：一次叠加两个入场动画 ----
    anim_combo = EffectSpec(
        id=EFFECT_ANIM_COMBO, version="1.0.0",
        name={"zh-CN": "组合动画", "en": "Combo animation"},
        category=CATEGORY_ANIMATION,
        description={
            "zh-CN": "同时执行两个入场动画并叠加（如淡入+上滑），参数 primary/"
                     "secondary 指定基础动画，可含各自动画参数（duration/距离等）。",
            "en": "Run two entrance animations simultaneously and composite."},
        license="MIT", inputs=("video",), output="video",
        parameters={
            "type": "object",
            "properties": {
                "duration": {"type": "number", "minimum": 0.1, "maximum": 5.0,
                             "default": 1.0, "unit": "s",
                             "description": {"zh-CN": "动画时长（秒）"}},
                "easing": {"type": "string", "enum": ["linear", "ease-in", "ease-out"],
                           "default": "ease-out"},
                "primary": {"type": "string",
                            "enum": ["fadeIn", "zoomIn", "slideIn", "slideUp",
                                     "slideDown", "slideRight", "rotateIn", "flipIn"],
                            "default": "fadeIn",
                            "description": {"zh-CN": "主动画"}},
                "secondary": {"type": "string",
                              "enum": ["fadeIn", "zoomIn", "slideIn", "slideUp",
                                       "slideDown", "slideRight", "rotateIn", "flipIn"],
                              "default": "slideUp",
                              "description": {"zh-CN": "次动画"}},
                "distance": {"type": "number", "minimum": 0.05, "maximum": 0.6,
                             "default": 0.3,
                             "description": {"zh-CN": "位移类次动画的距离（占画面比例）"}},
                "fromScale": {"type": "number", "minimum": 0.2, "maximum": 0.99,
                              "default": 0.8,
                              "description": {"zh-CN": "zoomIn 起始缩放"}},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_INTERNAL, "kind": "anim-combo"},
        source="builtin", keywords=("组合", "叠加", "同屏", "入场", "combo"),
        applies_to=("image", "video"),
    )

    # ---- 组合运镜预设（D04）：把两个入场动画固化成「起点→终点」复合运动 ----
    # 全部复用 anim.combo 的渲染合成逻辑：spec 里把 primary/secondary 固化为预设值，
    # 渲染层按 kind="anim-combo" 走同一 _combo_base（零新增内核分支）。用户仍可调
    # distance/fromScale/toScale/easing 微调力度。primary/secondary 的 enum 锁成单值，
    # 确保预设不可被改写成其它组合。
    def _combo_preset(eid, cn, en, desc_cn, keywords, primary, secondary,
                      distance=0.3, from_scale=0.8, to_scale=None):
        props = {
            "duration": {"type": "number", "minimum": 0.1, "maximum": 5.0,
                         "default": 1.0, "unit": "s",
                         "description": {"zh-CN": "动画时长（秒）"}},
            "easing": {"type": "string",
                       "enum": ["linear", "ease-in", "ease-out"], "default": "ease-out"},
            "distance": {"type": "number", "minimum": 0.05, "maximum": 0.6,
                         "default": distance,
                         "description": {"zh-CN": "位移类次动画的距离（占画面比例）"}},
            "primary": {"type": "string", "enum": [primary], "default": primary,
                        "description": {"zh-CN": f"主动画（预设固化：{primary}）"}},
            "secondary": {"type": "string", "enum": [secondary], "default": secondary,
                          "description": {"zh-CN": f"次动画（预设固化：{secondary}）"}},
        }
        if from_scale is not None:
            props["fromScale"] = {"type": "number", "minimum": 0.2, "maximum": 0.99,
                                 "default": from_scale,
                                 "description": {"zh-CN": "zoomIn 起始缩放"}}
        if to_scale is not None:
            props["toScale"] = {"type": "number", "minimum": 0.2, "maximum": 0.99,
                                "default": to_scale,
                                "description": {"zh-CN": "zoomOut 终止缩放"}}
        return EffectSpec(
            id=eid, version="1.0.0",
            name={"zh-CN": cn, "en": en},
            category=CATEGORY_ANIMATION,
            description={"zh-CN": desc_cn,
                         "en": f"Combo camera-move preset: {primary}+{secondary}"},
            license="MIT", inputs=("video",), output="video",
            parameters={"type": "object", "properties": props,
                        "additionalProperties": False},
            implementation={"engine": ENGINE_INTERNAL, "kind": "anim-combo"},
            source="builtin", keywords=tuple(keywords),
            applies_to=("image", "video"),
        )

    anim_combo_push_right = _combo_preset(
        EFFECT_ANIM_COMBO_PUSH_RIGHT, "推近+右移", "Push right",
        "画面由远及近（放大入场）同时从左侧滑入右侧：推近与右移叠加，镜头感强。",
        ["推近", "右移", "放大", "滑入", "运镜", "组合", "push"],
        "zoomIn", "slideIn", from_scale=0.8)
    anim_combo_pull_up = _combo_preset(
        EFFECT_ANIM_COMBO_PULL_UP, "拉远+上移", "Pull up",
        "画面由近及远（缩小）同时自下而上入场：拉远与上移叠加，适合收尾或退场感。",
        ["拉远", "上移", "缩小", "运镜", "组合", "pull"],
        "zoomOut", "slideUp", to_scale=0.8)
    anim_combo_rotate_zoom = _combo_preset(
        EFFECT_ANIM_COMBO_ROTATE_ZOOM, "旋转+放大", "Rotate zoom",
        "旋转入场同时放大：旋转与推近叠加，动感强烈，适合重点强调。",
        ["旋转", "放大", "运镜", "组合", "rotate"],
        "rotateIn", "zoomIn", from_scale=0.8)
    anim_combo_banner_in = _combo_preset(
        EFFECT_ANIM_COMBO_BANNER_IN, "横幅滑入", "Banner in",
        "横幅式从左侧滑入并淡入：平移与淡入叠加，适合标题条/字幕条入场。",
        ["横幅", "滑入", "淡入", "运镜", "组合", "banner"],
        "slideIn", "fadeIn")
    anim_combo_card_in = _combo_preset(
        EFFECT_ANIM_COMBO_CARD_IN, "卡片飞入", "Card in",
        "卡片翻转入场并从左滑入：翻牌与平移叠加，适合卡片/封面飞入。",
        ["卡片", "翻入", "滑入", "运镜", "组合", "card"],
        "flipIn", "slideIn")
    anim_combo_drift_left = _combo_preset(
        EFFECT_ANIM_COMBO_DRIFT_LEFT, "推近+左移", "Drift left",
        "画面由远及近（放大入场）同时自右向左滑入：推近与左移叠加。",
        ["推近", "左移", "放大", "滑入", "运镜", "组合", "drift"],
        "zoomIn", "slideRight", from_scale=0.8)

    # ---- 视频特效（1.5-B）：可浏览的画面滤镜（ffmpeg 现成滤镜，全部真渲染）----
    def _fx(cn, en, fxid, filter_name, desc_cn, keywords, props=None,
            dependencies=None):
        return EffectSpec(
            id=fxid, version="1.0.0",
            name={"zh-CN": cn, "en": en},
            category=CATEGORY_FX,
            description={"zh-CN": desc_cn, "en": f"Video effect: {en}"},
            license="MIT", inputs=("video",), output="video",
            parameters={"type": "object", "properties": props or {},
                        "additionalProperties": False},
            implementation={"engine": ENGINE_INTERNAL, "filter": filter_name},
            source="builtin", keywords=tuple(keywords),
            applies_to=("image", "video"),
            dependencies=tuple(dependencies) if dependencies else (),
        )

    pix_props = lambda k, lo, hi, dv, label: {   # noqa: E731
        k: {"type": "number", "minimum": lo, "maximum": hi, "default": dv,
            "description": {"zh-CN": label}}}

    fx_blur = _fx("高斯模糊", "Gaussian blur", EFFECT_FX_BLUR, "gblur",
                  "整体高斯模糊，用于背景虚化与柔化。",
                  ["模糊", "虚化", "柔化", "blur"],
                  pix_props("sigma", 1.0, 20.0, 5.0, "模糊强度（sigma）"))
    fx_glow = _fx("发光", "Glow", EFFECT_FX_GLOW, "glow",
                  "高光溢出式发光，营造梦幻与空气感。",
                  ["发光", "柔光", "光晕", "梦幻", "glow"],
                  pix_props("intensity", 0.1, 3.0, 1.0, "发光强度"))
    fx_chromatic = _fx("色彩分离", "Chromatic aberration", EFFECT_FX_CHROMATIC,
                       "chromatic",
                       "RGB 通道错位产生色边，制造故障与镜头畸变感。",
                       ["色彩分离", "色差", "RGB", "错位", "色边"],
                       pix_props("offset", 1.0, 20.0, 4.0, "通道偏移像素"))
    fx_glitch = _fx("故障", "Glitch", EFFECT_FX_GLITCH, "glitch",
                    "噪点与色带扰动叠加，模拟信号故障画面。",
                    ["故障", "glitch", "噪点", "数字", "赛博"],
                    pix_props("amount", 0.05, 1.0, 0.4, "故障强度"))
    fx_sepia = _fx("复古棕褐", "Sepia", EFFECT_SEPIA,
                   "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131",
                   "经典棕褐色调，怀旧胶片观感。",
                   ["复古", "棕褐", "怀旧", "胶片", "sepia"])
    fx_vintage = _fx("复古褪色", "Vintage fade", EFFECT_FX_VINTAGE,
                     "colorchannelmixer=.9:.1:0:0:.1:.9:.1:0:0:.1:.8:.1",
                     "整体降饱和并偏暖，褪色老照片质感。",
                     ["复古", "褪色", "老照片", "旧", "vintage"])
    fx_gray = _fx("灰度", "Grayscale", EFFECT_GRAYSCALE, "grayscale",
                  "去除全部色彩，转为黑白画面。",
                  ["黑白", "灰度", "去色", "mono", "gray"])
    fx_invert = _fx("反色", "Invert", EFFECT_FX_INVERT, "invert",
                    "颜色取反，负片效果。",
                    ["反色", "负片", "反转", "invert"])
    fx_posterize = _fx("海报化", "Posterize", EFFECT_FX_POSTERIZE, "posterize",
                       "压缩色阶形成色块，波普海报质感。",
                       ["海报", "色块", "波普", "色阶", "poster"],
                       pix_props("levels", 2.0, 16.0, 6.0, "色阶数（越小色块越明显）"))
    fx_sharpen = _fx("锐化", "Sharpen", EFFECT_FX_SHARPEN, "sharpen",
                     "提升边缘清晰度，突出细节。",
                     ["锐化", "清晰", "细节", "sharpen"],
                     pix_props("amount", 0.1, 3.0, 1.0, "锐化强度"))
    fx_edge = _fx("描边", "Edge detect", EFFECT_FX_EDGE, "edge",
                  "提取画面边缘线条，线稿与科技感风格。",
                  ["描边", "边缘", "线稿", "轮廓", "edge"],
                  {"mode": {"type": "string",
                            "enum": ["colormix", "wire", "canny"],
                            "default": "colormix",
                            "description": {"zh-CN": "描边模式"}}})
    fx_mirror = _fx("水平镜像", "Mirror", EFFECT_FX_MIRROR, "mirror",
                    "左右镜像拼接，制造对称构图。",
                    ["镜像", "对称", "左右", "mirror"])
    fx_vig = _fx("暗角", "Vignette", EFFECT_VIGNETTE, "vignette",
                 "四周压暗聚焦视线，适合强调主体。",
                 ["暗角", "聚焦", "压暗", "vignette"],
                 {"angle": {"type": "string", "enum": ["PI/4", "PI/5", "PI/6"],
                            "default": "PI/5",
                            "description": {"zh-CN": "暗角强度（PI/4 最重）"}}})

    # ---- J06 进阶画面/音频特效（真实可渲染的 ffmpeg 滤镜，声明式接入）----
    # 这些滤镜键已在 render.py 的 _FX_STEPS / _AUDIO_FX_STEPS 登记，
    # 渲染层按 implementation.filter 生成滤镜，内核不新增分支。
    fx_chromakey = _fx("色度抠像", "Chroma key", EFFECT_FX_CHROMAKEY, "chromakey",
                       "把指定颜色（默认绿幕）抠成透明，用于合成与换背景。",
                       ["抠像", "绿幕", "透明", "背景", "chroma", "key"],
                       {"color": {"type": "string", "default": "0x00FF00",
                                  "description": {"zh-CN": "要抠除的键色（十六进制）"}},
                        "similarity": {"type": "number", "minimum": 0.01,
                                       "maximum": 1.0, "default": 0.2,
                                       "description": {"zh-CN": "相似度容差（越大抠得越多）"}},
                        "blend": {"type": "number", "minimum": 0.0,
                                  "maximum": 1.0, "default": 0.0,
                                  "description": {"zh-CN": "边缘羽化混合量"}}})
    fx_lut = _fx("LUT 调色", "LUT grade", EFFECT_FX_LUT, "lut3d",
                 "用内置 3D 查找表（cool/warm/retro）做电影感调色。",
                 ["LUT", "调色", "电影感", "冷色", "暖色", "复古", "lut"],
                 {"preset": {"type": "string", "enum": ["cool", "warm", "retro"],
                             "default": "cool",
                             "description": {"zh-CN": "预设 LUT（对应内置 .cube 资源）"}}},
                 dependencies=("lut",))
    fx_curves = _fx("调色曲线", "Color curves", EFFECT_FX_CURVES, "curves",
                    "用 ffmpeg curves 的预设曲线调整对比度与色调。",
                    ["曲线", "调色", "对比度", "色调", "curves"],
                    {"preset": {"type": "string",
                                "enum": ["none", "increase_contrast",
                                         "medium_contrast", "strong_contrast",
                                         "vintage", "linear"],
                                "default": "none",
                                "description": {"zh-CN": "曲线预设"}}})

    fx_crop = _fx("自由裁切", "Crop", EFFECT_FX_CROP, "crop",
                  "自由比例裁切画面（x/y/w/h，相对画面比例 0~1）。适配 C02 图片裁切/构图，图片视频通用。",
                  ["裁切", "构图", "裁剪", "crop"],
                  pix_props("x", 0.0, 1.0, 0.0, "裁切起点 X（比例）")
                  | pix_props("y", 0.0, 1.0, 0.0, "裁切起点 Y（比例）")
                  | pix_props("w", 0.1, 1.0, 1.0, "裁切宽度（比例）")
                  | pix_props("h", 0.1, 1.0, 1.0, "裁切高度（比例）"))
    fx_denoise = _fx("视频降噪", "Denoise", EFFECT_FX_DENOISE, "hqdn3d",
                     "轻量空域降噪（hqdn3d），压制画面颗粒与噪点。",
                     ["降噪", "去噪", "噪点", "颗粒", "denoise"],
                     {"luma_spatial": {"type": "number", "minimum": 1.0,
                                       "maximum": 30.0, "default": 4.0,
                                       "description": {"zh-CN": "亮度空间降噪强度"}}})
    fx_rgbsplit = _fx("色差分离", "RGB split", EFFECT_FX_RGBSPLIT, "rgbsplit",
                      "RGB 三通道错位，制造镜头色差与故障边缘。",
                      ["色差", "RGB", "错位", "分离", "rgb", "色边"],
                      {"offset": {"type": "number", "minimum": 1.0,
                                  "maximum": 30.0, "default": 4.0,
                                  "description": {"zh-CN": "通道偏移像素"}}})
    # 响度标准化：loudnorm 是**音频**滤镜，作用于片段的嵌入音轨；
    # applies_to 仅 video（图片无音频，应用无意义），在渲染层音频链逐段生效。
    fx_loudnorm = EffectSpec(
        id=EFFECT_FX_LOUDNORM,
        version="1.0.0",
        name={"zh-CN": "响度标准化", "en": "Loudness normalize"},
        category=CATEGORY_FX,
        description={
            "zh-CN": "用 loudnorm 把片段音量统一到目标响度（单遍近似），适合多段音量不一致。",
            "en": "Normalize clip loudness with ffmpeg loudnorm (single-pass).",
        },
        license="MIT", inputs=("video",), output="video",
        parameters={
            "type": "object",
            "properties": {
                "I": {"type": "number", "minimum": -70.0, "maximum": -5.0,
                      "default": -16.0,
                      "description": {"zh-CN": "目标 integrated 响度（LUFS）"}},
                "TP": {"type": "number", "minimum": -9.0, "maximum": 0.0,
                       "default": -1.5,
                       "description": {"zh-CN": "最大真峰（dB TP）"}},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_INTERNAL, "filter": "loudnorm"},
        source="builtin",
        keywords=("响度", "音量", "标准化", "loudnorm", "LUFS"),
        applies_to=("video",),
    )

    # L01 均衡器：单段 peaking EQ（声明式音频滤镜，走音频编译链）
    fx_equalizer = EffectSpec(
        id=EFFECT_FX_EQ, version="1.0.0",
        name={"zh-CN": "均衡器", "en": "Equalizer"},
        category=CATEGORY_FX,
        description={
            "zh-CN": "单段 peaking EQ：按中心频率/增益/带宽调节音频（L01 基础混音）。",
            "en": "Single-band peaking EQ for audio.",
        },
        license="MIT", inputs=("video",), output="video",
        parameters={
            "type": "object",
            "properties": {
                "frequency": {"type": "number", "minimum": 20.0, "maximum": 20000.0,
                              "default": 1000.0,
                              "description": {"zh-CN": "中心频率（Hz）"}},
                "gainDb": {"type": "number", "minimum": -30.0, "maximum": 30.0,
                           "default": 0.0,
                           "description": {"zh-CN": "增益（dB）"}},
                "width": {"type": "number", "minimum": 0.1, "maximum": 10.0,
                          "default": 1.0,
                          "description": {"zh-CN": "带宽 Q 值"}},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_INTERNAL, "filter": "equalizer"},
        source="builtin",
        keywords=("均衡", "EQ", "音色", "混音", "equalizer"),
        applies_to=("video",),
    )

    # L01 压限器：阈值/压缩比/起音/释音（声明式音频滤镜）
    fx_compressor = EffectSpec(
        id=EFFECT_FX_COMPRESSOR, version="1.0.0",
        name={"zh-CN": "压限器", "en": "Compressor"},
        category=CATEGORY_FX,
        description={
            "zh-CN": "压限动态：超过阈值的信号按比例压缩（L01 基础混音）。",
            "en": "Dynamic range compression for audio.",
        },
        license="MIT", inputs=("video",), output="video",
        parameters={
            "type": "object",
            "properties": {
                "threshold": {"type": "number", "minimum": -60.0, "maximum": 0.0,
                              "default": -20.0,
                              "description": {"zh-CN": "阈值（dB）"}},
                "ratio": {"type": "number", "minimum": 1.0, "maximum": 20.0,
                          "default": 4.0,
                          "description": {"zh-CN": "压缩比"}},
                "attack": {"type": "number", "minimum": 1.0, "maximum": 100.0,
                           "default": 20.0,
                           "description": {"zh-CN": "起音（ms）"}},
                "release": {"type": "number", "minimum": 50.0, "maximum": 2000.0,
                            "default": 250.0,
                            "description": {"zh-CN": "释音（ms）"}},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_INTERNAL, "filter": "acompressor"},
        source="builtin",
        keywords=("压限", "压缩", "动态", "compressor", "limiter"),
        applies_to=("video",),
    )

    # F02 闪烁：eq 亮度按时间表达式振荡（2*PI*t*Hz，真实逐帧变化）
    fx_flicker = EffectSpec(
        id=EFFECT_FX_FLICKER, version="1.0.0",
        name={"zh-CN": "闪烁", "en": "Flicker"},
        category=CATEGORY_FX,
        description={
            "zh-CN": "画面亮度按频率正弦振荡（老电影/故障灯效果）。",
            "en": "Oscillate brightness at frequency (flicker effect).",
        },
        license="MIT", inputs=("video",), output="video",
        parameters={
            "type": "object",
            "properties": {
                "hz": {"type": "number", "minimum": 0.5, "maximum": 20.0,
                       "default": 3.0,
                       "description": {"zh-CN": "闪烁频率（Hz）"}},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_INTERNAL, "filter": "flicker"},
        source="builtin",
        keywords=("闪烁", "故障", "老电影", "flicker", "flashing"),
        applies_to=("image", "video"),
    )

    # F02 拖影：tmix 多帧混合，权重几何衰减形成运动残影
    fx_trail = _fx("拖影", "Motion trail", EFFECT_FX_TRAIL, "trail",
                   "把连续多帧按衰减权重叠加，形成运动残影拖尾（tmix）。适合快动作、卡点与速度感画面。",
                   ["拖影", "残影", "运动模糊", "轨迹", "拖尾", "trail"],
                   {"frames": {"type": "number", "minimum": 2, "maximum": 20,
                               "default": 4,
                               "description": {"zh-CN": "混合帧数（越大拖尾越长）"}},
                    "decay": {"type": "number", "minimum": 0.0, "maximum": 1.0,
                              "default": 0.6,
                              "description": {"zh-CN": "逐帧权重衰减（越小残影越淡）"}}})

    # O02 HSL：色相 / 饱和度 / 明度三轴调节
    fx_hsl = _fx("HSL 调节", "HSL adjust", EFFECT_FX_HSL, "hsl",
                 "色相、饱和度、明度三轴精细调节（huesaturation），并可选保持亮度。",
                 ["HSL", "色相", "饱和度", "明度", "hue", "saturation"],
                 {"hue": {"type": "number", "minimum": -180, "maximum": 180,
                          "default": 0,
                          "description": {"zh-CN": "色相偏移（度，-180~180）"}},
                  "saturation": {"type": "number", "minimum": -1, "maximum": 1,
                                 "default": 0,
                                 "description": {"zh-CN": "饱和度增减（-1~1）"}},
                  "intensity": {"type": "number", "minimum": -1, "maximum": 1,
                                "default": 0,
                                "description": {"zh-CN": "明度增减（-1~1）"}},
                  "lightness": {"type": "boolean", "default": False,
                                "description": {"zh-CN": "是否保持原亮度"}}})

    # O02 色彩平衡：阴影/中间调/高光 三档 x RGB
    fx_colorbalance = _fx("色彩平衡", "Color balance", EFFECT_FX_COLORBALANCE,
                          "colorbalance",
                          "按阴影、中间调、高光三档分别增减 RGB，修正偏色与做风格化调色。",
                          ["色彩平衡", "偏色", "阴影", "中间调", "高光",
                           "colorbalance"],
                          {"shadowsR": {"type": "number", "minimum": -1,
                                        "maximum": 1, "default": 0,
                                        "description": {"zh-CN": "阴影 红/青"}},
                           "shadowsG": {"type": "number", "minimum": -1,
                                        "maximum": 1, "default": 0,
                                        "description": {"zh-CN": "阴影 绿/品红"}},
                           "shadowsB": {"type": "number", "minimum": -1,
                                        "maximum": 1, "default": 0,
                                        "description": {"zh-CN": "阴影 蓝/黄"}},
                           "midtonesR": {"type": "number", "minimum": -1,
                                         "maximum": 1, "default": 0,
                                         "description": {"zh-CN": "中间调 红/青"}},
                           "midtonesG": {"type": "number", "minimum": -1,
                                         "maximum": 1, "default": 0,
                                         "description": {"zh-CN": "中间调 绿/品红"}},
                           "midtonesB": {"type": "number", "minimum": -1,
                                         "maximum": 1, "default": 0,
                                         "description": {"zh-CN": "中间调 蓝/黄"}},
                           "highlightsR": {"type": "number", "minimum": -1,
                                           "maximum": 1, "default": 0,
                                           "description": {"zh-CN": "高光 红/青"}},
                           "highlightsG": {"type": "number", "minimum": -1,
                                           "maximum": 1, "default": 0,
                                           "description": {"zh-CN": "高光 绿/品红"}},
                           "highlightsB": {"type": "number", "minimum": -1,
                                           "maximum": 1, "default": 0,
                                           "description": {"zh-CN": "高光 蓝/黄"}},
                           "preserveLightness": {"type": "boolean",
                                                 "default": False,
                                                 "description": {"zh-CN": "保持亮度"}}})

    # N02 几何蒙版：矩形/椭圆框选，其余压黑（就地改亮度，不产生 alpha）
    fx_mask = _fx("几何蒙版", "Shape mask", EFFECT_FX_MASK, "mask",
                  "只保留画面中指定的矩形或椭圆区域，其余压黑；支持羽化与反转。"
                  "用 geq 就地改亮度，不产生 alpha，因此可安全叠加其它特效与转场。",
                  ["蒙版", "遮罩", "矩形", "椭圆", "圆形", "羽化", "mask"],
                  {"shape": {"type": "string", "enum": ["rect", "circle"],
                             "default": "rect",
                             "description": {"zh-CN": "形状：矩形 rect / 椭圆 circle"}},
                   "x": {"type": "number", "minimum": 0.0, "maximum": 1.0,
                         "default": 0.15,
                         "description": {"zh-CN": "矩形左上角或椭圆中心的 X（比例 0~1）"}},
                   "y": {"type": "number", "minimum": 0.0, "maximum": 1.0,
                         "default": 0.15,
                         "description": {"zh-CN": "矩形左上角或椭圆中心的 Y（比例 0~1）"}},
                   "w": {"type": "number", "minimum": 0.01, "maximum": 1.0,
                         "default": 0.7,
                         "description": {"zh-CN": "矩形宽或椭圆横向直径（比例）"}},
                   "h": {"type": "number", "minimum": 0.01, "maximum": 1.0,
                         "default": 0.7,
                         "description": {"zh-CN": "矩形高或椭圆纵向直径（比例）"}},
                   "feather": {"type": "number", "minimum": 0.0, "maximum": 0.5,
                               "default": 0.05,
                               "description": {"zh-CN": "边缘羽化（0 为硬边）"}},
                   "invert": {"type": "boolean", "default": False,
                              "description": {"zh-CN": "反转：保留外部、遮掉内部"}}})

    # R02 形状标注：在画面上叠矩形/椭圆/直线/箭头（填充或描边），用于圈重点/画框/指引
    fx_shape = _fx("形状标注", "Shape annotation", EFFECT_FX_SHAPE, "shape",
                  "在画面上叠加矩形、椭圆、直线或箭头，支持填充/描边、线宽、颜色与"
                  "不透明度，用于圈重点、画框、连线、步骤指引等标注。矩形/直线走 "
                  "drawbox（无损、不转色彩空间）；椭圆走 geq 写 RGB；箭头 = 直线段"
                  " + 箭头三角（geq 写 RGB，尖端在长边末端）。直线/箭头恒为实心笔"
                  "画（mode 对其无额外语义），其余参数（strokeWidth/color/opacity）"
                  "对四种形状均生效；透明导出时请勿叠加本效果（geq 路径会丢 alpha）。",
                  ["形状", "标注", "矩形", "椭圆", "直线", "箭头", "画框",
                   "圈重点", "连线", "指引", "shape", "line", "arrow"],
                  {"shape": {"type": "string",
                             "enum": ["rect", "ellipse", "line", "arrow"],
                             "default": "rect",
                             "description": {"zh-CN": "形状：矩形 rect / 椭圆 "
                                            "ellipse / 直线 line / 箭头 arrow"}},
                   "mode": {"type": "string", "enum": ["outline", "fill"],
                            "default": "outline",
                            "description": {"zh-CN": "模式：描边 outline / 填充 "
                                           "fill（直线/箭头恒为实心，mode 无语义）"}},
                    "x": {"type": "number", "minimum": 0.0, "maximum": 1.0,
                          "default": 0.5,
                          "description": {"zh-CN": "中心 X（比例 0~1）"}},
                    "y": {"type": "number", "minimum": 0.0, "maximum": 1.0,
                          "default": 0.5,
                          "description": {"zh-CN": "中心 Y（比例 0~1）"}},
                    "w": {"type": "number", "minimum": 0.01, "maximum": 1.0,
                          "default": 0.3,
                          "description": {"zh-CN": "宽度（比例）"}},
                    "h": {"type": "number", "minimum": 0.01, "maximum": 1.0,
                          "default": 0.3,
                          "description": {"zh-CN": "高度（比例）"}},
                    "strokeWidth": {"type": "number", "minimum": 1.0,
                                    "maximum": 40.0, "default": 4.0,
                                    "description": {"zh-CN": "描边线宽（像素）"}},
                    "color": {"type": "string",
                              "enum": ["#FF3B30", "#FFD60A", "#34C759", "#0A84FF",
                                       "#FF9F0A", "#32ADE6", "#FFFFFF", "#000000"],
                              "default": "#FF3B30",
                              "description": {"zh-CN": "颜色（固定色板）"}},
                    "opacity": {"type": "number", "minimum": 0.05, "maximum": 1.0,
                                "default": 1.0,
                                "description": {"zh-CN": "不透明度"}}})

    # L01 声像（pan）：双声道平衡（-1 全左 / 0 中 / +1 全右），音频滤镜
    fx_pan = EffectSpec(
        id=EFFECT_FX_PAN, version="1.0.0",
        name={"zh-CN": "声像平衡", "en": "Pan"},
        category=CATEGORY_FX,
        description={
            "zh-CN": "双声道声像调整：balance=-1 全左、0 居中、+1 全右（L01 基础混音）。",
            "en": "Stereo pan balance (-1 left, 0 center, +1 right).",
        },
        license="MIT", inputs=("video",), output="video",
        parameters={
            "type": "object",
            "properties": {
                "balance": {"type": "number", "minimum": -1.0, "maximum": 1.0,
                            "default": 0.0,
                            "description": {"zh-CN": "声像平衡（-1 左 / 0 中 / 1 右）"}},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_INTERNAL, "filter": "pan"},
        source="builtin",
        keywords=("声像", "平衡", "左右", "pan", "balance"),
        applies_to=("video",),
    )

    # ---- 文字图层轨（J04）：text 轨片段的内容标记效果 ----
    # 纯描述性（不入画面滤镜链）：渲染层把带此效果的 text 轨 clip 按
    # clip 时间与参数转成 Caption 进 ASS 字幕轨道（字体/颜色/描边/对齐）。
    # 有真实渲染语义（进成片），不是空适配器。
    fx_text = EffectSpec(
        id=EFFECT_TEXT,
        version="1.0.0",
        name={"zh-CN": "文字图层", "en": "Text layer"},
        category=CATEGORY_TEXT,
        description={
            "zh-CN": "文字图层轨上的文本片段：把文字叠到画面上（字体/字号/颜色/描边/对齐可调）。",
            "en": "Text clip on a text layer: render text overlays onto the frame.",
        },
        license="MIT", inputs=("video",), output="video",
        parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "default": "",
                            "description": {"zh-CN": "文本内容"}},
                "fontSize": {"type": "number", "minimum": 8.0, "maximum": 200.0,
                             "default": 48.0,
                             "description": {"zh-CN": "字号（像素）"}},
                "color": {"type": "string", "default": "#ffffff",
                          "description": {"zh-CN": "文字颜色"}},
                "strokeColor": {"type": "string", "default": "#000000",
                                "description": {"zh-CN": "描边颜色"}},
                "strokeWidth": {"type": "number", "minimum": 0.0, "maximum": 8.0,
                                "default": 2.0,
                                "description": {"zh-CN": "描边宽度"}},
                "align": {"type": "string", "enum": ["left", "center", "right"],
                          "default": "center",
                          "description": {"zh-CN": "水平对齐"}},
                "bold": {"type": "boolean", "default": False,
                         "description": {"zh-CN": "是否加粗"}},
            },
            "additionalProperties": False,
        },
        implementation={"engine": ENGINE_INTERNAL, "filter": ""},
        source="builtin",
        keywords=("文字", "标题", "字幕", "叠加", "text", "ass"),
        applies_to=("video",),
    )

    specs = [transform, color, crossfade, fade, wipe, slide, blur, zoom,
             fade_white, slide_up, slide_down, wipe_up, wipe_down, dissolve,
             circle_open, diagonal, pixelize, smooth_left, radial, squeeze,
             anim_fade, anim_zoom, anim_slide,
             anim_slide_up, anim_slide_down, anim_slide_right,
             anim_rotate_in, anim_flip_in, anim_back_in, anim_reveal,
             anim_fade_out, anim_zoom_out, anim_breathe, anim_float, anim_sway,
             anim_combo,
             anim_combo_push_right, anim_combo_pull_up, anim_combo_rotate_zoom,
             anim_combo_banner_in, anim_combo_card_in, anim_combo_drift_left,
             fx_blur, fx_glow, fx_chromatic, fx_glitch, fx_sepia, fx_vintage,
             fx_gray, fx_invert, fx_posterize, fx_sharpen, fx_edge, fx_mirror,
             fx_vig,
             fx_chromakey, fx_lut, fx_curves, fx_denoise, fx_rgbsplit,
             fx_loudnorm, fx_crop,
             fx_equalizer, fx_compressor, fx_flicker,
             fx_pan, fx_trail, fx_hsl, fx_colorbalance, fx_mask, fx_shape,
             fx_text]

    # 统一补齐元数据：动画与画面特效同时适用于图片和视频（1.5 六类能力里
    # 「图片动画」「视频动画」「图片特效」「视频特效」共用同一批效果）；
    # 转场只适用于视频（它是两段之间的关系，图片没有「相邻段」概念）。
    from dataclasses import replace as _replace
    out: list[EffectSpec] = []
    for s in specs:
        if s.category in (CATEGORY_ANIMATION, CATEGORY_FX) and \
                s.id != EFFECT_FX_LOUDNORM:
            # 画面特效 + 动画默认同时适用于图片与视频；loudnorm 是音频滤镜，
            # 仅作用于视频片段的嵌入音轨（图片无音频），保留其声明的 video 适用。
            s = _replace(s, applies_to=("image", "video"))
        elif s.category == CATEGORY_TRANSITION:
            s = _replace(s, applies_to=("video",))
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

class EffectRegistry:
    """效果注册表：内置 + 外部目录发现，统一查询与参数校验。"""

    def __init__(self, *, with_builtin: bool = True) -> None:
        self._specs: dict[str, EffectSpec] = {}
        self._load_errors: list[str] = []
        if with_builtin:
            for spec in _builtin_specs():
                self._specs[spec.id] = spec

    # ---- 注册与发现 ----

    def register(self, spec: EffectSpec, *, override: bool = False) -> None:
        if spec.id in self._specs and not override:
            raise ManifestInvalid(
                f"效果 ID 重复: {spec.id}（已注册来源 {self._specs[spec.id].source}）")
        self._specs[spec.id] = spec

    def load_manifest_file(self, path: str | Path, *, override: bool = False) -> EffectSpec:
        p = Path(path)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ManifestInvalid(f"无法读取 manifest: {p} ({e})") from e
        spec = EffectSpec.from_manifest(raw, source=str(p))
        self.register(spec, override=override)
        return spec

    def discover(self, dirs: Iterable[str | Path], *, override: bool = False,
                 strict: bool = False) -> list[str]:
        """扫描目录，加载 `<dir>/*/manifest.json`。返回成功加载的效果 ID 列表。

        单个 manifest 损坏不会中断整体发现（记入 load_errors 供诊断查询）；
        strict=True 时改为直接抛出，用于 CI / 发布前完整性检查。
        """
        loaded: list[str] = []
        for d in dirs:
            root = Path(d)
            if not root.is_dir():
                continue
            for manifest in sorted(root.glob("*/manifest.json")):
                try:
                    spec = self.load_manifest_file(manifest, override=override)
                    loaded.append(spec.id)
                except ManifestInvalid as e:
                    if strict:
                        raise
                    self._load_errors.append(str(e))
        return loaded

    # ---- 查询 ----

    def find(self, effect_id: str) -> Optional[EffectSpec]:
        return self._specs.get(effect_id)

    def get(self, effect_id: str) -> EffectSpec:
        spec = self._specs.get(effect_id)
        if spec is None:
            known = ", ".join(sorted(self._specs)) or "(空)"
            raise EffectNotFound(f"未注册的效果: {effect_id}（已注册: {known}）")
        return spec

    def all(self) -> list[EffectSpec]:
        return [self._specs[k] for k in sorted(self._specs)]

    def by_category(self, category: str) -> list[EffectSpec]:
        return [s for s in self.all() if s.category == category]

    def search(self, *, q: Optional[str] = None, category: Optional[str] = None,
               applies_to: Optional[str] = None,
               ids: Optional[Iterable[str]] = None) -> list[EffectSpec]:
        """资源检索（J01 / 计划 5.1）：按关键词 + 分类 + 适用对象筛选。

        ids 用于把筛选范围限制在一个集合内（收藏、最近使用、某预设引用）。
        返回按 effectId 稳定排序，保证 UI / AI 看到同一顺序。
        """
        id_set = None if ids is None else set(ids)
        out: list[EffectSpec] = []
        for s in self.all():
            if category and s.category != category:
                continue
            if applies_to and applies_to not in s.applies_to:
                continue
            if id_set is not None and s.id not in id_set:
                continue
            if q and not s.matches(q):
                continue
            out.append(s)
        return out

    def ids(self) -> frozenset[str]:
        return frozenset(self._specs)

    @property
    def load_errors(self) -> list[str]:
        return list(self._load_errors)

    # ---- 参数处理 ----

    def validate_params(self, effect_id: str, params: Optional[dict]) -> dict:
        """校验并返回补齐默认值后的参数副本。非法参数抛 EffectParamInvalid。"""
        spec = self.get(effect_id)
        value = {} if params is None else params
        if not isinstance(value, dict):
            raise EffectParamInvalid(f"{effect_id}: params 必须是对象")
        errors = validate_against_schema(spec.parameters, value)
        if errors:
            raise EffectParamInvalid(f"{effect_id} 参数非法: " + "; ".join(errors))
        resolved = resolve_defaults(spec.parameters, value)
        return resolved if isinstance(resolved, dict) else value

    def default_params(self, effect_id: str) -> dict:
        return self.get(effect_id).default_params()

    def unknown_ids(self, used: Iterable[str]) -> list[str]:
        """返回 used 里未注册的 ID（AC19：缺失效果需明确报告而非静默）。"""
        return sorted({i for i in used if i not in self._specs})

    # ---- 能力清单 ----

    def capabilities(self, lang: str = "zh-CN") -> dict:
        """给 UI / CLI / HTTP / MCP 的统一能力描述。"""
        items = [s.to_dict(lang) for s in self.all()]
        categories: dict[str, int] = {}
        for s in self.all():
            categories[s.category] = categories.get(s.category, 0) + 1
        return {
            "effects": items,
            "count": len(items),
            "categories": categories,
            "loadErrors": self.load_errors,
        }


# ---------------------------------------------------------------------------
# 默认注册表（进程级单例）
#
# 扫描来源（按此顺序，先注册者优先）：
#   1. 内置效果
#   2. CUTVOKE_EFFECTS_PATH 环境变量里的目录（os.pathsep 分隔，便于测试与多环境）
#   3. 用户目录 ~/.cutvoke/effects/<id>/manifest.json（用户自行安装的效果）
# ---------------------------------------------------------------------------

_DEFAULT: Optional[EffectRegistry] = None


def default_effects_dirs() -> list[Path]:
    dirs: list[Path] = []
    env = os.environ.get(_ENV_DIRS, "")
    if env:
        dirs.extend(Path(p) for p in env.split(os.pathsep) if p.strip())
    home = Path(os.path.expanduser("~"))
    dirs.append(home / ".cutvoke" / "effects")
    return dirs


def default_registry(*, refresh: bool = False) -> EffectRegistry:
    """返回进程级默认注册表（首次调用时执行目录发现）。"""
    global _DEFAULT
    if _DEFAULT is None or refresh:
        reg = EffectRegistry()
        reg.discover(default_effects_dirs())
        _DEFAULT = reg
    return _DEFAULT


def set_default_registry(registry: Optional[EffectRegistry]) -> None:
    """替换进程级注册表（测试与嵌入式场景使用）。"""
    global _DEFAULT
    _DEFAULT = registry


# ---------------------------------------------------------------------------
# 片段级便捷函数（供渲染器与查询接口使用）
# ---------------------------------------------------------------------------

def _spec_for(reg: EffectRegistry, effect_id: str) -> Optional[EffectSpec]:
    if reg is None:
        return None
    return reg.find(effect_id)


def clip_effects(clip: Any, reg: Optional[EffectRegistry] = None) -> list[dict]:
    """片段上的效果实例列表（统一入口，避免各处直接摸 clip.effects）。"""
    return list(getattr(clip, "effects", []) or [])


def find_transition(clip: Any, reg: Optional[EffectRegistry] = None) -> Optional[dict]:
    """返回片段上的转场效果实例（第一个 category == transition 的实例）。

    与旧的 Clip.transition() 的差别：这里按**注册表的分类**判定，因此社区新增的
    转场（例如 slideleft）无需改内核即可被渲染器识别（AC18）。
    找不到注册信息时回退到旧的硬编码 ID 判定，保证老工程仍可渲染。
    """
    reg = reg or default_registry()
    for e in clip_effects(clip, reg):
        eid = e.get("effectId")
        spec = _spec_for(reg, eid)
        if spec is not None:
            if spec.is_transition:
                return e
            continue
        # 未注册的效果：回退旧判定，不静默丢弃
        if isinstance(eid, str) and eid.endswith("transition.crossfade"):
            return e
    return None


def xfade_transition_name(clip: Any, reg: Optional[EffectRegistry] = None,
                          default: str = "fade") -> str:
    """片段转场对应的 xfade transition 名（未声明时回退 fade）。"""
    reg = reg or default_registry()
    tr = find_transition(clip, reg)
    if tr is None:
        return default
    spec = _spec_for(reg, tr.get("effectId"))
    if spec is None:
        return default
    return spec.xfade_transition or default


def collect_used_effect_ids(project: Any) -> set[str]:
    """遍历工程，收集全部被引用的 effectId（用于兼容性与贡献检查）。"""
    used: set[str] = set()
    seq = getattr(project, "sequence", None)
    if seq is None:
        return used
    for track in getattr(seq, "tracks", []) or []:
        for clip in getattr(track, "clips", []) or []:
            for e in getattr(clip, "effects", []) or []:
                eid = e.get("effectId")
                if isinstance(eid, str):
                    used.add(eid)
    return used
