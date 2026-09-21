"""字幕渲染（任务书 F22 / F24 / M1 基础字幕进入成片与预览）。

方案（经团队确认）：生成 ASS 文件 + libass `ass=` 滤镜 + cwd 相对文件名。

选定该方案而非逐条 drawtext 的理由：
  1. 任意用户字幕文本落在文件里，彻底规避 filtergraph 字符串转义/注入隐患
     （文本若进 filtergraph 需转义 \\ : ' % 与换行）；
  2. 任务书 P1 性能基准含 100 条字幕，ASS 单次滤镜即可，远优于 100 段 drawtext 串联；
  3. 描边/阴影/对齐天然对应 ASS 的 Style 行，drawtext 要手搓参数；
  4. libass 自己处理中文断行。

字体决策（F24）：使用可再分发的 Noto Sans SC（OFL-1.1）。四级可诊断回退，
缺字体时抛 CaptionFontError（绝不静默导出一条没有字幕的成片）。

Windows 关键陷阱：subtitles / ass 滤镜传 Windows 绝对路径（含 C:）时，盘符冒号
会与 filter 选项分隔符冲突而报错。故 ASS 文件与字体副本都放在一个临时目录，
通过 subprocess 的 cwd= 切到该目录，滤镜里只写相对文件名（ass=cutvoke_captions.ass
且 fontsdir=.），彻底规避绝对路径冒号冲突。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from fractions import Fraction
from typing import List, Optional

from .rational import Rational
from .model import Caption

# 冻结的字体族名（M1 基础字幕固定 Noto Sans SC；逐字幕字体选择留 M2）
CAPTION_FONT_FAMILY = "Noto Sans SC"

# 包内随发行打包的字体资源目录（相对 src/cutvoke/）。不存在则跳过探测——
# 打包接线留 TODO（M2 发行打包时把 Noto Sans SC 复制到此目录即可）。
_PACKAGE_FONTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")

# 系统已知路径探测（Windows）
_SYSTEM_FONT_CANDIDATES_WIN = [
    r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
    r"C:\Windows\Fonts\NotoSansSC-Regular.otf",
]

# 系统已知根目录（Linux / macOS）
_SYSTEM_FONT_ROOTS = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    "/System/Library/Fonts",
    "/Library/Fonts",
]


class CaptionFontError(Exception):
    """缺可再分发 CJK 字体时抛出（F24：缺字体可诊断，绝不静默丢字幕）。"""


def resolve_font() -> str:
    """四级可诊断回退，返回字体文件绝对路径；都找不到抛 CaptionFontError。

    1) 环境变量 CUTVOKE_FONT（最高优先，便于测试注入与自定义）
    2) 包内随发行打包的字体资源（src/cutvoke/assets/fonts/，无需存在）
    3) 系统已知路径探测（Windows / Linux / macOS）
    4) 都找不到 -> 抛明确错误，列出已尝试路径与如何用 CUTVOKE_FONT 覆盖
    """
    tried: List[str] = []

    # 1) 环境变量（显式指定：存在即用，不存在即报错，绝不静默回退误导）
    env_font = os.environ.get("CUTVOKE_FONT")
    if env_font:
        tried.append(env_font)
        if os.path.isfile(env_font):
            return os.path.abspath(env_font)
        raise CaptionFontError(
            "CUTVOKE_FONT 指定的字体文件不存在: " + env_font + "\n"
            "如不确定字体路径，请设置 CUTVOKE_FONT 为可再分发的 CJK 字体"
            "（如 Noto Sans SC）的绝对路径。")

    # 2) 包内资源目录探测（目录不存在则跳过，不做任何假设）
    if os.path.isdir(_PACKAGE_FONTS_DIR):
        for name in ("NotoSansSC-VF.ttf", "NotoSansSC-Regular.otf",
                     "NotoSansCJK-Regular.ttc", "NotoSansSC.ttf"):
            p = os.path.join(_PACKAGE_FONTS_DIR, name)
            tried.append(p)
            if os.path.isfile(p):
                return os.path.abspath(p)
    else:
        tried.append("(<package fonts dir not found> " + _PACKAGE_FONTS_DIR + ")")

    # 3) 系统已知路径探测
    for p in _SYSTEM_FONT_CANDIDATES_WIN:
        tried.append(p)
        if os.path.isfile(p):
            return os.path.abspath(p)
    # Linux / macOS 通配
    for root in _SYSTEM_FONT_ROOTS:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                low = fn.lower()
                if "notosanscjk" in low or "notosanssc" in low:
                    p = os.path.join(dirpath, fn)
                    tried.append(p)
                    if os.path.isfile(p):
                        return os.path.abspath(p)

    # 4) 都找不到 -> 明确错误
    raise CaptionFontError(
        "未找到可再分发的 CJK 字体（任务书 F24 要求字幕字体可再分发且来源明确）。\n"
        "已尝试的路径:\n  - " + "\n  - ".join(tried) + "\n"
        "解决：安装 Noto Sans SC（OFL-1.1，可再分发），或设置环境变量 "
        "CUTVOKE_FONT 指向其绝对路径，例如\n"
        "  set CUTVOKE_FONT=C:/Windows/Fonts/NotoSansSC-VF.ttf")


def rational_to_ass_time(r: Rational) -> str:
    """Rational 秒 -> ASS 时间戳 H:MM:SS.cc（厘秒精度）。

    Fraction 精确转换，绝不浮点秒累加；进位处理避免 99:59:59 边界截断。
    """
    f = Fraction(r.num, r.den)
    cs = int(round(f * 100))
    if cs < 0:
        cs = 0
    cc = cs % 100
    total_s = cs // 100
    s = total_s % 60
    m = (total_s // 60) % 60
    h = total_s // 3600
    return f"{h}:{m:02d}:{s:02d}.{cc:02d}"


def _ass_escape(text: str) -> str:
    """转义 ASS 文本内控制字符；换行 -> \\N（ASS 硬换行）。

    中文标点（，。：；！？""''）与普通汉字无需转义，libass + harfbuzz 正常渲染。
    """
    text = text.replace("\\", "\\\\")
    text = text.replace("{", "\\{").replace("}", "\\}")
    text = text.replace("\n", "\\N")
    return text


def _ass_color_hex_to_bgr(hex_color: str) -> str:
    """把 #RRGGBB 转成 ASS 的 &HAABBGGRR 格式（BGR 顺序 + 00 alpha）。

    非法输入回退为白色 #ffffff。
    """
    c = (hex_color or "#ffffff").strip().lstrip("#")
    if len(c) != 6:
        c = "ffffff"
    try:
        r, g, b = c[0:2], c[2:4], c[4:6]
        return f"&H00{b}{g}{r}".upper()
    except Exception:
        return "&H00FFFFFF"


def build_ass(captions: List[Caption], width: int, height: int,
              font_family: str = CAPTION_FONT_FAMILY) -> str:
    """由字幕列表生成 ASS 文件内容（UTF-8，无 BOM，LF 换行）。

    - PlayResX/Y = 序列画布，字号随画布高度比例缩放（1080p 基准 32）
    - 逐字幕样式（1.5-C F22/F24）：fontSize/color/strokeColor/strokeWidth/
      background/align/bold 每字幕独立 Style，缺省走基础样式（白字黑描边）
    - 时间用 Rational 精确映射到厘秒，零宽/负宽字幕跳过
    """
    lines: List[str] = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, FontName, FontSize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
    ]
    base_size = max(12, round(32 * height / 1080))

    # 收集用到的样式（去重），Name 用稳定 id；不同样式组合生成独立 Style 行
    style_names: dict[str, str] = {}
    _style_seq = 0

    def _ensure_style(cap: Caption) -> str:
        nonlocal _style_seq
        key = (cap.fontSize, cap.color, cap.strokeColor, cap.strokeWidth,
               cap.background, cap.align, cap.bold, cap.shadow)
        if key not in style_names:
            name = f"C{_style_seq}"
            _style_seq += 1
            style_names[key] = name
            # 字号随画布高度比例缩放（1080p 基准）
            fs = max(8, round(cap.fontSize * height / 1080))
            primary = _ass_color_hex_to_bgr(cap.color or "#ffffff")
            outline = _ass_color_hex_to_bgr(cap.strokeColor or "#000000")
            back = _ass_color_hex_to_bgr(cap.background or "#000000")
            bold = "-1" if cap.bold else "0"
            outline_w = cap.strokeWidth if cap.strokeWidth else 2
            align_map = {"left": "1", "center": "2", "right": "3"}
            align = align_map.get(cap.align, "2")
            # 有 background 时用 BackColour + BorderStyle=3（不透明背景盒）
            border_style = "3" if cap.background else "1"
            lines.append(
                f"Style: {name},{font_family},{fs},"
                f"{primary},&H000000FF,{outline},{back},"
                f"{bold},0,0,0,100,100,0,0,{border_style},{outline_w},1,"
                f"{align},20,20,40,{cap.shadow}")
        return style_names[key]

    lines.append("")
    lines.append("[Events]")
    lines.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
                 "MarginV, Effect, Text")
    for cap in sorted(captions, key=lambda c: (c.start, c.id)):
        if cap.duration <= Rational.of(0, 1):
            continue
        start = rational_to_ass_time(cap.start)
        end = rational_to_ass_time(cap.end)
        text = _ass_escape(cap.text)
        style = _ensure_style(cap)
        # 文字动画（1.5-C）：\fad(淡入ms,淡出ms) 参数化入出场
        if cap.animIn or cap.animOut:
            text = f"{{\\fad({cap.animIn},{cap.animOut})}}{text}"
        # 文字几何（H01 画布编辑）：仅非默认才发标签——默认值（x=0.5/y=0.5
        # 居中、scale=1、rotation=0）不发任何标签，保证旧工程逐字节不变。
        # \pos 用 PlayRes 像素（x/y 为归一化比例 → 居中=0.5 映射到 50% 处，
        # 对齐已由 Style 的 Alignment 处理，\pos 是文本锚点坐标）；
        # \fscx\fscy 为百分比缩放，\frz 为旋转角。
        has_geo = False
        if (cap.x != 0.5 or cap.y != 0.5):
            px = round(cap.x * width)
            py = round(cap.y * height)
            text = f"{{\\pos({px},{py})}}{text}"
            has_geo = True
        if cap.scale != 1.0:
            pct = round(cap.scale * 100)
            text = f"{{\\fscx{pct}\\fscy{pct}}}{text}"
            has_geo = True
        if cap.rotation != 0.0:
            text = f"{{\\frz({cap.rotation:.2f})}}{text}"
            has_geo = True
        lines.append(f"Dialogue: 0,{start},{end},{style},,0,0,0,,{text}")
    lines.append("")
    return "\n".join(lines)


def prepare_caption_render(captions: List[Caption], width: int, height: int) -> str:
    """写 ASS 文件并把字体副本放入临时目录，返回该目录绝对路径（用作 ffmpeg cwd）。

    调用方用完须清理该目录。滤镜里用相对文件名 `ass=cutvoke_captions.ass` 且
    `fontsdir=.`（cwd 相对），彻底规避 Windows 绝对路径冒号冲突。
    缺字体抛 CaptionFontError。
    """
    font = resolve_font()
    cwd = tempfile.mkdtemp(prefix="cutvoke_ass_")
    ass_rel = "cutvoke_captions.ass"
    ass_path = os.path.join(cwd, ass_rel)
    with open(ass_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(build_ass(captions, width, height))
    # 复制字体到 cwd，fontsdir=. 让 libass 按族名找到（规避绝对路径冒号陷阱/空格路径）
    try:
        shutil.copyfile(font, os.path.join(cwd, "cutvoke_font.ttf"))
    except OSError:
        # 复制失败不致命：若字体恰在系统 fontsdir 仍可被 libass 找到；
        # 但若连原始字体都不可用，下面 ffmpeg 会因无字幕字体而失败并被 _verify 捕获。
        pass
    return cwd
