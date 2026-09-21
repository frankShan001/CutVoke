"""CutVoke 核心工程模型（符合任务书第 7 章契约）。

权威时间用 Rational（有理数，见 core/rational.py），禁止浮点秒累加。
对象用稳定 ID，数组索引/排序/路径不能代替身份。
区间一律左闭右开 [start, end)。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .rational import Rational
from .keyframes import Keyframe


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# 内置效果 ID（F14 变换 / F18 调色 / F20 转场）
#
# 统一入口：EditService 的 effect.add 命令向 clip.effects 追加
#   {"effectId": <下面对应常量>, "version": "1.0.0", "params": {...}}
# 参数集中在 params dict，不新增加一堆专用命令。
#   - cutvoke.transform : {"position": {"x":int,"y":int},
#                          "scale": float, "rotation": float(度), "opacity": float[0,1]}
#   - cutvoke.color     : {"brightness": float, "contrast": float, "saturation": float}
#   - cutvoke.transition.crossfade : {"duration": float(秒)}  挂在「被切入」的片段上
# ---------------------------------------------------------------------------
EFFECT_TRANSFORM = "cutvoke.transform"
EFFECT_COLOR = "cutvoke.color"
EFFECT_CROSSFADE = "cutvoke.transition.crossfade"
EFFECT_FADE = "cutvoke.transition.fade"
EFFECT_WIPE = "cutvoke.transition.wipe"
EFFECT_SLIDE = "cutvoke.transition.slide"
EFFECT_BLUR = "cutvoke.transition.blur"
EFFECT_ZOOM = "cutvoke.transition.zoom"

# 图片/视频入场动画（1.5-A 图片动画 J01）：声明式关键帧模板，
# 渲染时展开成 opacity/scale/position 关键帧（时长按片段比例或固定秒）。
EFFECT_ANIM_FADE_IN = "cutvoke.anim.fadeIn"
EFFECT_ANIM_ZOOM_IN = "cutvoke.anim.zoomIn"
EFFECT_ANIM_SLIDE_IN = "cutvoke.anim.slideIn"
# 出场 / 循环动画（1.5-B）：与入场同一条关键帧展开链。
EFFECT_ANIM_FADE_OUT = "cutvoke.anim.fadeOut"
EFFECT_ANIM_ZOOM_OUT = "cutvoke.anim.zoomOut"
EFFECT_ANIM_BREATHE = "cutvoke.anim.breathe"

# 动画家族扩充（1.5-B 计划 5.2：方向移动/旋转/弹性/翻转/遮罩显现/漂浮/摇摆/组合）。
# 全部复用同一条「子段时间插值」渲染链，渲染层按 kind 解释，不新增内核分支。
EFFECT_ANIM_SLIDE_UP = "cutvoke.anim.slideUp"        # 自下而上入场
EFFECT_ANIM_SLIDE_DOWN = "cutvoke.anim.slideDown"    # 自上而下入场
EFFECT_ANIM_SLIDE_RIGHT = "cutvoke.anim.slideRight"  # 自右向左入场
EFFECT_ANIM_ROTATE_IN = "cutvoke.anim.rotateIn"      # 旋转+淡入入场
EFFECT_ANIM_FLIP_IN = "cutvoke.anim.flipIn"          # 翻牌入场（水平轴缩放）
EFFECT_ANIM_BACK_IN = "cutvoke.anim.backIn"          # 回弹（先过冲再归位）
EFFECT_ANIM_REVEAL = "cutvoke.anim.reveal"           # 遮罩显现（自左向右揭示）
EFFECT_ANIM_FLOAT = "cutvoke.anim.float"             # 漂浮循环（缓慢上下位移）
EFFECT_ANIM_SWAY = "cutvoke.anim.sway"               # 摇摆循环（左右轻摆）
# 组合动画（E04）：一次叠加两个入场动画（如淡入+上滑），渲染层合成状态相乘/相加。
EFFECT_ANIM_COMBO = "cutvoke.anim.combo"
# 组合运镜预设（D04）：把两个入场动画固化成「起点→终点」复合运动预设，
# 渲染层复用 anim.combo 的 _combo_base 合成逻辑（primary/secondary 固化为预设值，
# 不新增内核分支）。覆盖不同方向与力度：推近/拉远 × 上下左右 + 旋转 + 翻牌。
EFFECT_ANIM_COMBO_PUSH_RIGHT = "cutvoke.anim.comboPushRight"   # 推近 + 右移
EFFECT_ANIM_COMBO_PULL_UP = "cutvoke.anim.comboPullUp"         # 拉远 + 上移
EFFECT_ANIM_COMBO_ROTATE_ZOOM = "cutvoke.anim.comboRotateZoom" # 旋转 + 放大
EFFECT_ANIM_COMBO_BANNER_IN = "cutvoke.anim.comboBannerIn"     # 横幅滑入（左滑 + 淡入）
EFFECT_ANIM_COMBO_CARD_IN = "cutvoke.anim.comboCardIn"         # 卡片飞入（翻牌 + 左滑）
EFFECT_ANIM_COMBO_DRIFT_LEFT = "cutvoke.anim.comboDriftLeft"   # 推近 + 左移

# 视频特效（1.5-B F01/F02）：可浏览的画面滤镜（ffmpeg 现成滤镜）。
EFFECT_FX_BLUR = "cutvoke.fx.blur"
EFFECT_GRAYSCALE = "cutvoke.fx.grayscale"
EFFECT_SEPIA = "cutvoke.fx.sepia"
EFFECT_VIGNETTE = "cutvoke.fx.vignette"
# 特效家族扩充（1.5-B 计划 5.2：光影/发光/镜头/色彩分离/故障/形变/复古/边框/局部）。
EFFECT_FX_GLOW = "cutvoke.fx.glow"           # 发光
EFFECT_FX_CHROMATIC = "cutvoke.fx.chromatic"  # 色彩分离（RGB 错位）
EFFECT_FX_GLITCH = "cutvoke.fx.glitch"        # 故障
EFFECT_FX_SHARPEN = "cutvoke.fx.sharpen"      # 锐化（清晰化）
EFFECT_FX_EDGE = "cutvoke.fx.edge"            # 描边（边缘检测）
EFFECT_FX_INVERT = "cutvoke.fx.invert"        # 反色
EFFECT_FX_MIRROR = "cutvoke.fx.mirror"        # 水平镜像
EFFECT_FX_VINTAGE = "cutvoke.fx.vintage"      # 复古褪色
EFFECT_FX_POSTERIZE = "cutvoke.fx.posterize"  # 海报化（色阶压缩）

# 进阶画面/音频特效（J06 合成、调色与画质）：真实可渲染的 ffmpeg 滤镜，
# 全部声明式接入（见 effects.py 的 _builtin_specs + render.py 的 _FX_STEPS）。
EFFECT_FX_CHROMAKEY = "cutvoke.fx.chromakey"  # 色度抠像（绿幕）
EFFECT_FX_LUT = "cutvoke.fx.lut"              # LUT 3D 调色（内置 .cube 预设）
EFFECT_FX_CURVES = "cutvoke.fx.curves"        # 调色曲线（ffmpeg curves）
EFFECT_FX_DENOISE = "cutvoke.fx.denoise"      # 视频降噪（hqdn3d）
EFFECT_FX_LOUDNORM = "cutvoke.fx.loudnorm"    # 响度标准化（loudnorm，音频滤镜）
EFFECT_FX_EQ = "cutvoke.fx.equalizer"          # 音频均衡器（equalizer，L01）
EFFECT_FX_COMPRESSOR = "cutvoke.fx.compressor" # 音频压限器（acompressor，L01）
EFFECT_FX_FLICKER = "cutvoke.fx.flicker"       # 亮度闪烁（eq 时间表达式，F02）
EFFECT_FX_PAN = "cutvoke.fx.pan"               # 声像（pan 双声道平衡，L01）
EFFECT_FX_RGBSPLIT = "cutvoke.fx.rgbsplit"    # 色差 RGB 偏移（rgbashift）
EFFECT_FX_CROP = "cutvoke.fx.crop"            # 自由裁切（crop 滤镜，图片/视频通用）
EFFECT_FX_TRAIL = "cutvoke.fx.trail"          # 拖影（tmix 多帧混合，F02）
EFFECT_FX_HSL = "cutvoke.fx.hsl"              # HSL 调节（huesaturation，O02）
EFFECT_FX_COLORBALANCE = "cutvoke.fx.colorbalance"  # 色彩平衡（colorbalance，O02）
EFFECT_FX_MASK = "cutvoke.fx.mask"            # 几何蒙版（geq 矩形/椭圆，N02）
EFFECT_FX_SHAPE = "cutvoke.fx.shape"          # 形状标注（drawbox 矩形 / geq 椭圆，R02）

# 文字图层轨（J04）：text 轨片段上的内容标记效果。参数含文本内容与样式，
# 渲染层把带此效果的 text 轨 clip 转成 Caption 进 ASS（不入画面滤镜链）。
EFFECT_TEXT = "cutvoke.text"

# 转场家族扩充（1.5-B 计划 5.2：叠化/闪黑闪白/滑动/推移/缩放/旋转/模糊/遮罩/分割/形变）。
# 全部由 ffmpeg xfade 桥接（58 种内置），内核只按 transition 名生成滤镜。
EFFECT_FADE_WHITE = "cutvoke.transition.fadewhite"    # 闪白
EFFECT_SLIDE_UP = "cutvoke.transition.slideup"        # 上滑
EFFECT_SLIDE_DOWN = "cutvoke.transition.slidedown"    # 下滑
EFFECT_WIPE_UP = "cutvoke.transition.wipeup"          # 上擦
EFFECT_WIPE_DOWN = "cutvoke.transition.wipedown"      # 下擦
EFFECT_DISSOLVE = "cutvoke.transition.dissolve"       # 溶解
EFFECT_CIRCLE_OPEN = "cutvoke.transition.circleopen"  # 圆形张开（遮罩）
EFFECT_DIAGONAL = "cutvoke.transition.diagtl"         # 对角分割
EFFECT_PIXELIZE = "cutvoke.transition.pixelize"       # 像素化（形变）
EFFECT_SMOOTH_LEFT = "cutvoke.transition.smoothleft"  # 平滑推移
EFFECT_RADIAL = "cutvoke.transition.radial"           # 径向（旋转感）
EFFECT_SQUEEZE = "cutvoke.transition.squeeze"         # 横向挤压

# 全部转场效果 ID（用于遍历/判断 & 前后端统一）
ALL_TRANSITIONS = (
    EFFECT_CROSSFADE,
    EFFECT_FADE,
    EFFECT_FADE_WHITE,
    EFFECT_WIPE,
    EFFECT_WIPE_UP,
    EFFECT_WIPE_DOWN,
    EFFECT_SLIDE,
    EFFECT_SLIDE_UP,
    EFFECT_SLIDE_DOWN,
    EFFECT_SMOOTH_LEFT,
    EFFECT_BLUR,
    EFFECT_SQUEEZE,
    EFFECT_ZOOM,
    EFFECT_RADIAL,
    EFFECT_DISSOLVE,
    EFFECT_CIRCLE_OPEN,
    EFFECT_DIAGONAL,
    EFFECT_PIXELIZE,
)

# 全部动画效果 ID（1.5-B 计划 5.2 动画家族）
ALL_ANIMATIONS = (
    EFFECT_ANIM_FADE_IN,
    EFFECT_ANIM_ZOOM_IN,
    EFFECT_ANIM_SLIDE_IN,
    EFFECT_ANIM_SLIDE_UP,
    EFFECT_ANIM_SLIDE_DOWN,
    EFFECT_ANIM_SLIDE_RIGHT,
    EFFECT_ANIM_ROTATE_IN,
    EFFECT_ANIM_FLIP_IN,
    EFFECT_ANIM_BACK_IN,
    EFFECT_ANIM_REVEAL,
    EFFECT_ANIM_FADE_OUT,
    EFFECT_ANIM_ZOOM_OUT,
    EFFECT_ANIM_BREATHE,
    EFFECT_ANIM_FLOAT,
    EFFECT_ANIM_SWAY,
    EFFECT_ANIM_COMBO,
    EFFECT_ANIM_COMBO_PUSH_RIGHT,
    EFFECT_ANIM_COMBO_PULL_UP,
    EFFECT_ANIM_COMBO_ROTATE_ZOOM,
    EFFECT_ANIM_COMBO_BANNER_IN,
    EFFECT_ANIM_COMBO_CARD_IN,
    EFFECT_ANIM_COMBO_DRIFT_LEFT,
)

# 全部视频特效 ID（1.5-B 计划 5.2 特效家族）
ALL_FX = (
    EFFECT_FX_BLUR,
    EFFECT_FX_GLOW,
    EFFECT_FX_CHROMATIC,
    EFFECT_FX_GLITCH,
    EFFECT_SEPIA,
    EFFECT_FX_VINTAGE,
    EFFECT_GRAYSCALE,
    EFFECT_FX_INVERT,
    EFFECT_FX_POSTERIZE,
    EFFECT_FX_SHARPEN,
    EFFECT_FX_EDGE,
    EFFECT_FX_MIRROR,
    EFFECT_VIGNETTE,
    # J06 进阶特效（真实 ffmpeg 滤镜，声明式接入）
    EFFECT_FX_CHROMAKEY,
    EFFECT_FX_LUT,
    EFFECT_FX_CURVES,
    EFFECT_FX_DENOISE,
    EFFECT_FX_RGBSPLIT,
    EFFECT_FX_CROP,
    EFFECT_FX_FLICKER,
    EFFECT_FX_TRAIL,
    EFFECT_FX_HSL,
    EFFECT_FX_COLORBALANCE,
    EFFECT_FX_MASK,
    EFFECT_FX_SHAPE,
    # 备注：EFFECT_FX_LOUDNORM 为音频滤镜，不计入画面特效 ALL_FX，
    # 仅在音频渲染链生效（见 render.py 的 _AUDIO_FX_STEPS）。
)

# 变换 / 调色参数的默认值（渲染时按此补齐，保证可查询、可组合）
TRANSFORM_DEFAULTS = {
    "position": {"x": 0, "y": 0},
    "scale": 1.0,
    "rotation": 0.0,
    "opacity": 1.0,
}
COLOR_DEFAULTS = {
    "brightness": 0.0,   # eq: -1..1
    "contrast": 1.0,     # eq: 默认 1（约 -2..2 常用区间）
    "saturation": 1.0,   # eq: 默认 1
}


# ---------------------------------------------------------------------------
# 时间相关辅助
# ---------------------------------------------------------------------------

def sec_rational(x: float) -> Rational:
    """从秒（浮点，仅外部输入）转为 Rational。权威运算不用浮点。"""
    return Rational.from_float(x)


@dataclass
class AssetReference:
    """素材引用：内容身份 + 定位信息（任务书 7.1/11.2）。"""

    asset_id: str
    source_path: str = ""
    fingerprint: str = ""  # 内容身份（哈希策略 M1 冻结）

    def to_dict(self) -> dict:
        return {"assetId": self.asset_id, "sourcePath": self.source_path,
                "fingerprint": self.fingerprint}

    @classmethod
    def from_dict(cls, d: dict) -> "AssetReference":
        return cls(asset_id=d["assetId"], source_path=d.get("sourcePath", ""),
                   fingerprint=d.get("fingerprint", ""))


@dataclass
class Clip:
    """片段：素材引用 + 工程时间范围 + 素材时间映射 + 效果栈 + 属性（7.1）。"""

    id: str
    asset_ref: AssetReference
    timeline_start: Rational      # 工程时间起点（inclusive）
    timeline_end: Rational        # 工程时间终点（exclusive）
    source_start: Rational        # 素材时间起点
    speed: Rational = field(default_factory=lambda: Rational.of(1, 1))
    effects: list[dict] = field(default_factory=list)
    linked: bool = True           # 音视频联动
    hidden: bool = False
    # 参数关键帧：按参数名分组（先支持 opacity）。渲染生效留 M2 完整版（T22）。
    keyframes: dict[str, list[Keyframe]] = field(default_factory=dict)
    # 音频（D06 F27/F28）：相对音量（1.0=原声）、淡入淡出时长（秒，0=无）
    volume: Rational = field(default_factory=lambda: Rational.of(1, 1))
    fade_in: Rational = field(default_factory=lambda: Rational.of(0, 1))
    fade_out: Rational = field(default_factory=lambda: Rational.of(0, 1))
    # 音高（1.5-C 变声）：1.0=原调，>1 升调（变尖），<1 降调（变低沉）
    pitch: Rational = field(default_factory=lambda: Rational.of(1, 1))
    # 定格帧（B06）：非 None 时，片段全程渲染为源窗口冻结在 freeze_at 时刻的单帧。
    freeze_at: Optional["Rational"] = None
    # 复合片段（J08 高级时间线）：嵌套子序列。非 None 表示该片段是复合片段，
    # 内部是一组轨道/片段的完整子序列（子序列时间从 0 起）。复合片段的
    # asset_ref 允许为空（内容在 nested 内）。统一用 Optional[Sequence]，
    # 向后兼容：旧工程无 nested 字段时取 None，照常序列化。
    nested: Optional["Sequence"] = None

    @property
    def duration(self) -> Rational:
        return self.timeline_end - self.timeline_start

    # ------------------------------------------------------------------
    # 效果查询（F14/F18/F20）：从 effects 列表里按 effectId 提取并合并参数
    # ------------------------------------------------------------------
    def effects_by_id(self, effect_id: str) -> list[dict]:
        """返回本片段所有指定 effectId 的效果实例（已追加顺序）。"""
        return [e for e in self.effects if e.get("effectId") == effect_id]

    def transform(self) -> dict:
        """合并所有 cutvoke.transform 效果，补齐默认值。

        后追加的效果覆盖先前同名参数（与 effect 栈顺序一致）。
        返回 {position:{x,y}, scale, rotation, opacity}。
        """
        t = {
            "position": dict(TRANSFORM_DEFAULTS["position"]),
            "scale": TRANSFORM_DEFAULTS["scale"],
            "rotation": TRANSFORM_DEFAULTS["rotation"],
            "opacity": TRANSFORM_DEFAULTS["opacity"],
        }
        for e in self.effects_by_id(EFFECT_TRANSFORM):
            p = e.get("params", {}) or {}
            if isinstance(p.get("position"), dict):
                pos = p["position"]
                t["position"] = {
                    "x": int(pos.get("x", t["position"]["x"])),
                    "y": int(pos.get("y", t["position"]["y"])),
                }
            if "scale" in p:
                t["scale"] = float(p["scale"])
            if "rotation" in p:
                t["rotation"] = float(p["rotation"])
            if "opacity" in p:
                t["opacity"] = float(p["opacity"])
        return t

    def color_grade(self) -> dict:
        """合并所有 cutvoke.color 效果，补齐默认值。

        返回 {brightness, contrast, saturation}。
        """
        c = dict(COLOR_DEFAULTS)
        for e in self.effects_by_id(EFFECT_COLOR):
            p = e.get("params", {}) or {}
            if "brightness" in p:
                c["brightness"] = float(p["brightness"])
            if "contrast" in p:
                c["contrast"] = float(p["contrast"])
            if "saturation" in p:
                c["saturation"] = float(p["saturation"])
        return c

    def transition(self) -> dict | None:
        """返回本片段的转场效果（首个 cutvoke.transition.crossfade）。

        转场挂在「被切入」的片段上，表示它与前一段落之间的过渡。
        无转场返回 None。
        """
        fx = self.effects_by_id(EFFECT_CROSSFADE)
        return fx[0] if fx else None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "assetRef": self.asset_ref.to_dict(),
            "timelineStart": self.timeline_start.to_json(),
            "timelineEnd": self.timeline_end.to_json(),
            "sourceStart": self.source_start.to_json(),
            "speed": self.speed.to_json(),
            "effects": self.effects,
            "linked": self.linked,
            "hidden": self.hidden,
            "volume": self.volume.to_json(),
            "fadeIn": self.fade_in.to_json(),
            "fadeOut": self.fade_out.to_json(),
            "pitch": self.pitch.to_json(),
            "freezeAt": self.freeze_at.to_json() if self.freeze_at else None,
            "nested": self.nested.to_dict() if self.nested is not None else None,
            "keyframes": {
                name: [kf.to_dict() for kf in kfs]
                for name, kfs in self.keyframes.items()
            },
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Clip":
        raw_kf = d.get("keyframes", {})
        keyframes = {
            name: [Keyframe.from_dict(k) for k in kfs]
            for name, kfs in raw_kf.items()
        } if raw_kf else {}
        return cls(
            id=d["id"],
            asset_ref=AssetReference.from_dict(d["assetRef"]),
            timeline_start=Rational.from_json(**d["timelineStart"]),
            timeline_end=Rational.from_json(**d["timelineEnd"]),
            source_start=Rational.from_json(**d["sourceStart"]),
            speed=Rational.from_json(**d["speed"]) if d.get("speed") else Rational.of(1, 1),
            effects=d.get("effects", []),
            linked=d.get("linked", True),
            hidden=d.get("hidden", False),
            volume=Rational.from_json(**d["volume"]) if d.get("volume") else Rational.of(1, 1),
            fade_in=Rational.from_json(**d["fadeIn"]) if d.get("fadeIn") else Rational.of(0, 1),
            fade_out=Rational.from_json(**d["fadeOut"]) if d.get("fadeOut") else Rational.of(0, 1),
            pitch=Rational.from_json(**d["pitch"]) if d.get("pitch") else Rational.of(1, 1),
            freeze_at=(Rational.from_json(**d["freezeAt"])
                       if d.get("freezeAt") else None),
            keyframes=keyframes,
            nested=Sequence.from_dict(d["nested"]) if d.get("nested") else None,
        )


@dataclass
class Track:
    """轨道：kind（video/audio/text/caption）、片段列表、锁定/静音/可见性（7.3）。"""

    id: str
    kind: str
    clips: list[Clip] = field(default_factory=list)
    locked: bool = False
    muted: bool = False
    visible: bool = True

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind,
                "clips": [c.to_dict() for c in self.clips],
                "locked": self.locked, "muted": self.muted, "visible": self.visible}

    @classmethod
    def from_dict(cls, d: dict) -> "Track":
        return cls(id=d["id"], kind=d["kind"],
                   clips=[Clip.from_dict(c) for c in d.get("clips", [])],
                   locked=d.get("locked", False), muted=d.get("muted", False),
                   visible=d.get("visible", True))


@dataclass
class EffectInstance:
    """效果实例：效果 ID + 精确版本 + 参数（任务书 7.1）。"""

    effect_id: str
    version: str = "1.0.0"
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"effectId": self.effect_id, "version": self.version, "params": self.params}

    @classmethod
    def from_dict(cls, d: dict) -> "EffectInstance":
        return cls(effect_id=d["effectId"], version=d.get("version", "1.0.0"),
                   params=d.get("params", {}))


@dataclass
class Caption:
    """字幕：文本 + 时间范围 + 样式（任务书 7.1 字幕/文本对象，F22/F24）。

    样式字段为 D05 文字字幕工作台补充，全部带默认值，向后兼容旧数据
    （from_dict 缺省字段回默认，无需迁移）。
    """

    id: str
    text: str
    start: Rational
    end: Rational
    # 样式（D05；默认值 = 后端起渲染 baseline，对齐旧 ASS 白字黑描边 32 号）
    fontSize: int = 32
    color: str = "#ffffff"
    strokeColor: str = "#000000"
    strokeWidth: int = 2
    background: str = ""          # 空 = 无背景；否则为背景色
    align: str = "center"         # left / center / right
    bold: bool = False
    # 文字动画（1.5-C 参数化入出场）：淡入/淡出时长（毫秒，0=无）
    animIn: int = 0
    animOut: int = 0
    # 文字几何（H01 画布文字编辑）：x/y 为画布归一化比例（0~1，默认 0.5=水平/垂直居中），
    # scale 缩放（0.1~5，默认 1）；rotation 旋转角（-180~180，默认 0）。
    # 默认值 = 旧渲染行为（居中、原始字号、不旋转），旧工程反序列化零变化，
    # 渲染端默认不发 ASS 几何标签，保证既有成片逐字节不变。
    x: float = 0.5
    y: float = 0.5
    scale: float = 1.0
    rotation: float = 0.0
    # 文字阴影（H04）：0=无阴影，>0 为 ASS Shadow 宽度（像素，默认 1 对齐旧渲染）
    shadow: int = 1

    @property
    def duration(self) -> Rational:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text,
                "start": self.start.to_json(), "end": self.end.to_json(),
                "fontSize": self.fontSize, "color": self.color,
                "strokeColor": self.strokeColor, "strokeWidth": self.strokeWidth,
                "background": self.background, "align": self.align,
                "bold": self.bold, "animIn": self.animIn, "animOut": self.animOut,
                "x": self.x, "y": self.y,
                "scale": self.scale, "rotation": self.rotation,
                "shadow": self.shadow}

    @classmethod
    def from_dict(cls, d: dict) -> "Caption":
        return cls(id=d["id"], text=d["text"],
                   start=Rational.from_json(**d["start"]),
                   end=Rational.from_json(**d["end"]),
                   fontSize=d.get("fontSize", 32),
                   color=d.get("color", "#ffffff"),
                   strokeColor=d.get("strokeColor", "#000000"),
                   strokeWidth=d.get("strokeWidth", 2),
                   background=d.get("background", ""),
                   align=d.get("align", "center"),
                   bold=d.get("bold", False),
                   animIn=d.get("animIn", 0),
                   animOut=d.get("animOut", 0),
                   x=d.get("x", 0.5), y=d.get("y", 0.5),
                   scale=d.get("scale", 1.0), rotation=d.get("rotation", 0.0),
                   shadow=d.get("shadow", 1))


@dataclass
class Marker:
    """标记：轨道或区间标记，可命名、查询、定位（任务书 F11）。"""

    id: str
    name: str
    time: Rational

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "time": self.time.to_json()}

    @classmethod
    def from_dict(cls, d: dict) -> "Marker":
        return cls(id=d["id"], name=d["name"], time=Rational.from_json(**d["time"]))


@dataclass
class Sequence:
    """序列：画布、帧率、采样率 + 轨道 + 字幕 + 标记（7.1）。"""

    id: str
    width: int
    height: int
    fps: Rational
    audio_sample_rate: int = 48000
    tracks: list[Track] = field(default_factory=list)
    captions: list[Caption] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    # 序列名称（J08 复合片段的 nested 子序列可附带名称；向后兼容默认空）
    name: str = ""
    # J08 多机位（multicam）：一条序列级元数据 + 切换命令，描述一组时间对齐、
    # 不同机位的同源轨（内容时间齐平），当前输出选 activeTrackId 那轨。
    # 单序列只允许 0 或 1 个机位组（要建新组先 remove）。向后兼容：旧工程
    # 无该字段时取 None，序列化/反序列化都默认 None，照常往返。
    # 结构：{"groupId": str, "trackIds": [trackId...],
    #        "activeTrackId": str, "sync": "time"}
    multicam: Optional[dict] = None

    def to_dict(self) -> dict:
        return {"id": self.id, "width": self.width, "height": self.height,
                "fps": self.fps.to_json(), "audioSampleRate": self.audio_sample_rate,
                "tracks": [t.to_dict() for t in self.tracks],
                "captions": [c.to_dict() for c in self.captions],
                "markers": [m.to_dict() for m in self.markers],
                "name": self.name,
                "multicam": self.multicam}

    @classmethod
    def from_dict(cls, d: dict) -> "Sequence":
        return cls(id=d["id"], width=d["width"], height=d["height"],
                   fps=Rational.from_json(**d["fps"]),
                   audio_sample_rate=d.get("audioSampleRate", 48000),
                   tracks=[Track.from_dict(t) for t in d.get("tracks", [])],
                   captions=[Caption.from_dict(c) for c in d.get("captions", [])],
                   markers=[Marker.from_dict(m) for m in d.get("markers", [])],
                   name=d.get("name", ""),
                   multicam=d.get("multicam", None))


@dataclass
class Project:
    """工程：schemaVersion + projectId + revision + 序列(可多个) + 依赖锁定（7.1）。

    1.5-D 多时间线（F49）：一个工程可持多个序列（Sequence），
    `sequence` 是「活动序列」的兼容访问器（指向 active_sequence_id），
    旧代码不破坏；`sequences` 列表 + `active_sequence_id` 供多序列管理。
    """

    schema_version: str
    project_id: str
    revision: str
    sequence: Sequence
    name: str = ""
    sequences: list[Sequence] = field(default_factory=list)
    active_sequence_id: str = ""
    # J01 资源共同基础：收藏 / 最近使用 / 个人预设（工程级，随工程保存与撤销，
    # AI 与 Web 共用同一份数据，换机器后仍可复现——计划 6「工程可复现」）
    favorites: list[str] = field(default_factory=list)
    recent_effects: list[str] = field(default_factory=list)
    presets: list[dict] = field(default_factory=list)

    def __post_init__(self):
        # 兼容旧构造（只传了 sequence）：填充 sequences 与 active_sequence_id
        if not self.sequences:
            self.sequences = [self.sequence]
        if not self.active_sequence_id:
            self.active_sequence_id = self.sequence.id
        # 保持 sequence 指向活动序列
        if self.sequence.id != self.active_sequence_id:
            for s in self.sequences:
                if s.id == self.active_sequence_id:
                    self.sequence = s
                    break

    @property
    def active_sequence(self) -> Sequence:
        """活动序列（多序列下显式取）；向后兼容单序列。"""
        for s in self.sequences:
            if s.id == self.active_sequence_id:
                return s
        return self.sequence

    def to_dict(self) -> dict:
        return {"schemaVersion": self.schema_version, "projectId": self.project_id,
                "revision": self.revision, "sequence": self.sequence.to_dict(),
                "name": self.name,
                "sequences": [s.to_dict() for s in self.sequences],
                "activeSequenceId": self.active_sequence_id,
                "favorites": list(self.favorites),
                "recentEffects": list(self.recent_effects),
                "presets": [dict(p) for p in self.presets]}

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        seqs = [Sequence.from_dict(s) for s in d.get("sequences", [])]
        seq = Sequence.from_dict(d["sequence"])
        if not seqs:
            seqs = [seq]
        active_id = d.get("activeSequenceId", seq.id)
        # 确保活动序列在列表里
        if not any(s.id == active_id for s in seqs):
            active_id = seqs[0].id
        active_seq = next((s for s in seqs if s.id == active_id), seq)
        return cls(schema_version=d["schemaVersion"], project_id=d["projectId"],
                   revision=d["revision"], sequence=active_seq,
                   name=d.get("name", ""), sequences=seqs,
                   active_sequence_id=active_id,
                   favorites=list(d.get("favorites", []) or []),
                   recent_effects=list(d.get("recentEffects", []) or []),
                   presets=[dict(p) for p in (d.get("presets", []) or [])])