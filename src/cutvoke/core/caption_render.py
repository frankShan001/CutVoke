"""字幕渲染（任务书 F22 / F24 / M1 基础字幕进入成片与预览）。

方案（经团队确认）：生成 ASS 文件 + libass `ass=` 滤镜 + cwd 相对文件名。

选定该方案而非逐条 drawtext 的理由：
  1. 任意用户字幕文本落在文件里，彻底规避 filtergraph 字符串转义/注入隐患
     （文本若进 filtergraph 需转义 \\ : ' % 与换行）；
  2. 任务书 P1 性能基准含 100 条字幕，ASS 单次滤镜即可，远优于 100 段 drawtext 串联；
  3. 描边/阴影/对齐天然对应 ASS 的 Style 行，drawtext 要手搓参数；
  4. libass 自己处理中文断行。

字体决策（F24）：字幕使用 Noto Sans SC，标题可选 Noto Sans SC / Noto Serif SC。
两者随发行包提供；缺字体时抛 CaptionFontError（绝不静默导出一条没有文字的成片）。

Windows 关键陷阱：subtitles / ass 滤镜传 Windows 绝对路径（含 C:）时，盘符冒号
会与 filter 选项分隔符冲突而报错。故 ASS 文件与字体副本都放在一个临时目录，
通过 subprocess 的 cwd= 切到该目录，滤镜里只写相对文件名（ass=cutvoke_captions.ass
且 fontsdir=.），彻底规避绝对路径冒号冲突。
"""

from __future__ import annotations

import os
import hashlib
import re
import shutil
import struct
import tempfile
import threading
from fractions import Fraction
from functools import lru_cache
from typing import List, Optional
from pathlib import Path

from .rational import Rational
from .model import Caption

# 冻结的字体族名（基础字幕默认 Noto Sans SC；标题可选两种字体）
CAPTION_FONT_FAMILY = "Noto Sans SC"
TITLE_FONT_FAMILIES = ("Noto Sans SC", "Noto Serif SC")

# 包内随发行打包的字体资源目录（相对 src/cutvoke/）。
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


def resolve_title_font(family: str) -> str:
    """Resolve one of the packaged title families without silent substitution."""
    if family == CAPTION_FONT_FAMILY:
        return resolve_font()
    if family == "Noto Serif SC":
        path = os.path.join(_PACKAGE_FONTS_DIR, "NotoSerifSC-VF.ttf")
        if os.path.isfile(path):
            return os.path.abspath(path)
        raise CaptionFontError("缺少随包 Noto Serif SC 字体: " + path)
    raise CaptionFontError(f"不支持的标题字体: {family!r}")


@lru_cache(maxsize=16)
def _font_ass_size_ratio(path: str, modified_ns: int, byte_size: int) -> float:
    """Convert CSS em pixels to libass's real-height font size.

    libass uses OS/2 Win ascent + descent, rather than unitsPerEm, when it
    requests the font size (libass/ass_font.c: set_font_metrics). Read the
    selected file, including a custom CUTVOKE_FONT, without a fontTools runtime
    dependency. The stat fields keep the cache valid when a font is replaced.
    """
    del modified_ns, byte_size
    try:
        with open(path, "rb") as font:
            header = font.read(12)
            start = 0
            if header[:4] == b"ttcf":
                start = struct.unpack(">I", font.read(4))[0]
                font.seek(start)
                header = font.read(12)
            count = struct.unpack(">H", header[4:6])[0]
            if not 0 < count <= 4096:
                raise ValueError("invalid SFNT table count")
            tables = {}
            for _ in range(count):
                tag, _checksum, offset, length = struct.unpack(">4sIII", font.read(16))
                tables[tag] = (offset, length)

            def read_table(tag: bytes, offset: int, length: int) -> bytes:
                position, table_length = tables[tag]
                if offset + length > table_length:
                    raise ValueError("truncated font metrics")
                font.seek(position + offset)
                return font.read(length)

            units = struct.unpack(">H", read_table(b"head", 18, 2))[0]
            if b"OS/2" in tables:
                ascent, descent = struct.unpack(">hh", read_table(b"OS/2", 74, 4))
                real_height = ascent + descent
            else:
                real_height = 0
            if real_height <= 0:
                ascent, descent = struct.unpack(">hh", read_table(b"hhea", 4, 4))
                real_height = ascent - descent
            if units <= 0 or real_height <= 0:
                raise ValueError("invalid font height")
            return real_height / units
    except (OSError, ValueError, KeyError, struct.error) as error:
        raise CaptionFontError(f"无法读取字幕字体度量 {path}: {error}") from error


def _css_font_size_to_ass(size: float, family: str, path: Optional[str] = None) -> float:
    path = path or resolve_title_font(family)
    stat = os.stat(path)
    return round(size * _font_ass_size_ratio(path, stat.st_mtime_ns, stat.st_size), 3)


_CAPTION_FONT_LOCK = threading.RLock()


@lru_cache(maxsize=16)
def _font_source_digest(path: str, modified_ns: int, byte_size: int) -> str:
    del modified_ns, byte_size
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _caption_font_instance(family: str, bold: bool) -> tuple[str, str]:
    """Return an atomic cached 400/700 instance for the browser caption lane.

    DirectWrite/libass can select a VF's Thin default even when requesting 400.
    A unique internal family keeps the corrected subtitle instance separate
    from existing text clips, whose variable font rendering remains unchanged.
    Static overrides are renamed for reliable selection, never instantiated.
    """
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    source = resolve_title_font(family)
    stat = os.stat(source)
    digest = _font_source_digest(source, stat.st_mtime_ns, stat.st_size)
    weight = 700 if bold else 400
    alias = f"CutVoke Caption {digest[:12]}"
    cache = (Path(os.environ["CUTVOKE_FONT_CACHE"]).expanduser().resolve()
             if os.environ.get("CUTVOKE_FONT_CACHE")
             else Path(tempfile.gettempdir()) / "cutvoke-caption-fonts")
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{digest}-{weight}-v1.ttf"
    temporary: Optional[str] = None
    with _CAPTION_FONT_LOCK:
        try:
            if target.is_file():
                with TTFont(target, lazy=True) as cached:
                    if "fvar" in cached or cached["name"].getDebugName(1) != alias:
                        raise ValueError("invalid cached caption font")
                _css_font_size_to_ass(32, family, str(target))
                return alias, str(target)
            with TTFont(source, fontNumber=0) as font:
                variable = "fvar" in font
                if variable:
                    axes = {axis.axisTag: axis.defaultValue for axis in font["fvar"].axes}
                    weight_axis = next((axis for axis in font["fvar"].axes
                                        if axis.axisTag == "wght"), None)
                    if weight_axis is None or not weight_axis.minValue <= weight <= weight_axis.maxValue:
                        raise ValueError(f"font does not support weight {weight}")
                    axes["wght"] = weight
                    instantiateVariableFont(font, axes, inplace=True, optimize=False, static=True)
                    if "fvar" in font:
                        raise ValueError("font instance is still variable")
                    font["OS/2"].usWeightClass = weight
                    font["OS/2"].fsSelection = ((font["OS/2"].fsSelection & ~(0x20 | 0x40))
                                               | (0x20 if bold else 0x40))
                    font["head"].macStyle = (font["head"].macStyle & ~1) | int(bold)
                style = "Bold" if bold and variable else "Regular"
                names = {1: alias, 16: alias, 2: style, 17: style,
                         4: f"{alias} {style}", 6: f"{alias.replace(' ', '')}-{style}"}
                locales = {(record.platformID, record.platEncID, record.langID)
                           for record in font["name"].names if record.nameID in names}
                locales.add((3, 1, 0x409))
                for name_id, value in names.items():
                    for platform, encoding, language in locales:
                        font["name"].setName(value, name_id, platform, encoding, language)
                fd, temporary = tempfile.mkstemp(prefix=".caption-", suffix=".ttf", dir=cache)
                os.close(fd)
                font.save(temporary)
            _css_font_size_to_ass(32, family, temporary)
            # Concurrent processes publish only complete fonts. A duplicate
            # first build may replace equivalent valid fonts, never a partial file.
            os.replace(temporary, target)
            temporary = None
            return alias, str(target)
        except Exception as error:
            raise CaptionFontError(f"无法准备字幕字体 {family} (weight {weight}): {error}") from error
        finally:
            if temporary and os.path.exists(temporary):
                os.remove(temporary)


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


def _caption_word_intervals(cap: Caption, text: str) -> list[tuple[Rational, Rational, Optional[tuple[int, int]]]]:
    """Return gap/highlight intervals after mapping timed tokens to exact text spans."""
    if not cap.words or not cap.wordHighlightColor or "\n" in text:
        return []
    spans: list[tuple[Rational, Rational, tuple[int, int]]] = []
    cursor = 0
    for word in cap.words:
        token = word.text.strip()
        if not token:
            continue
        start = text.find(token, cursor)
        if start < 0:
            return []
        spans.append((word.start, word.end, (start, start + len(token))))
        cursor = start + len(token)
    if not spans:
        return []

    intervals: list[tuple[Rational, Rational, Optional[tuple[int, int]]]] = []
    at = cap.start
    for start, end, span in spans:
        if at < start:
            intervals.append((at, start, None))
        intervals.append((start, end, span))
        at = end
    if at < cap.end:
        intervals.append((at, cap.end, None))
    return intervals


def _ass_color_hex_to_bgr(color: str) -> str:
    """把 #RRGGBB 或 rgba(...) 转成 ASS 的 &HAABBGGRR 颜色。

    ASS 的 alpha 与 CSS 相反（00=不透明）。花字预设可用半透明 CSS 背景，
    导出和网页预览必须保留同一透明度；非法输入仍回退为不透明白色。
    """
    value = (color or "#ffffff").strip()
    rgba = re.fullmatch(
        r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})"
        r"(?:\s*,\s*(0(?:\.\d+)?|1(?:\.0+)?))?\s*\)",
        value,
        flags=re.IGNORECASE,
    )
    if rgba:
        r, g, b = (int(rgba.group(i)) for i in range(1, 4))
        if all(0 <= channel <= 255 for channel in (r, g, b)):
            alpha = float(rgba.group(4) or "1")
            if 0 <= alpha <= 1:
                return f"&H{round((1 - alpha) * 255):02X}{b:02X}{g:02X}{r:02X}"

    c = value.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", c):
        return "&H00FFFFFF"
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H00{b}{g}{r}".upper()


def _text_animation_tags(cap: Caption, duration_ms: int, *,
                         offset_ms: int = 0, include_in: bool = True) -> str:
    """ASS tags for a title's independent entrance, exit and loop lanes."""
    remaining = max(0, duration_ms - offset_ms)
    in_ms = min(max(0, cap.animIn), duration_ms) if cap.animInStyle != "none" else 0
    out_ms = (min(max(0, cap.animOut), max(0, duration_ms - in_ms))
              if cap.animOutStyle != "none" else 0)
    base = round(cap.scale * 100)
    needs_scale = (cap.scale != 1.0 or cap.animInStyle == "scale" or
                   cap.animOutStyle == "scale" or cap.animLoopStyle == "pulse")
    tags = f"\\fscx{base}\\fscy{base}" if needs_scale else ""
    fade_in = max(0, in_ms - offset_ms) if include_in and cap.animInStyle == "fade" else 0
    fade_out = min(out_ms, remaining) if cap.animOutStyle == "fade" else 0
    if fade_in or fade_out:
        tags += f"\\fad({fade_in},{fade_out})"
    if include_in and cap.animInStyle == "scale" and in_ms > offset_ms:
        small = max(1, round(base * 0.6))
        tags += (f"\\fscx{small}\\fscy{small}\\alpha&HFF&"
                 f"\\t(0,{in_ms - offset_ms},\\fscx{base}\\fscy{base}\\alpha&H00&)")

    loop_start = max(0, in_ms - offset_ms)
    loop_end = max(loop_start, duration_ms - out_ms - offset_ms)
    cycle = min(3000, max(300, cap.animLoopMs))
    if cap.animLoopStyle in {"pulse", "blink"}:
        # ASS has no native repeat tag. Emit repeat transforms through long cues;
        # cap pathological input at about 20 minutes for the fastest valid cycle.
        loop_end = min(loop_end, loop_start + cycle * 4096)
        for start in range(loop_start, loop_end, cycle):
            if start + cycle > loop_end:
                break
            middle, end = start + cycle // 2, start + cycle
            if cap.animLoopStyle == "pulse":
                large = round(base * 1.22)
                tags += (f"\\t({start},{middle},\\fscx{large}\\fscy{large})"
                         f"\\t({middle},{end},\\fscx{base}\\fscy{base})")
            else:
                tags += (f"\\t({start},{middle},\\alpha&HBB&)"
                         f"\\t({middle},{end},\\alpha&H00&)")

    if cap.animOutStyle == "scale" and out_ms > 0:
        start = max(0, duration_ms - out_ms - offset_ms)
        small = max(1, round(base * 0.6))
        tags += (f"\\t({start},{remaining},"
                 f"\\fscx{small}\\fscy{small}\\alpha&HFF&)")
    return tags


def build_ass(captions: List[Caption], width: int, height: int,
              font_family: str = CAPTION_FONT_FAMILY, *,
              css_caption_ids: Optional[set[str]] = None,
              css_font_files: Optional[dict[tuple[str, bool], tuple[str, str]]] = None) -> str:
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
        css_metrics = css_caption_ids is not None and cap.id in css_caption_ids
        has_panel = bool(cap.background and cap.panelWidth > 0 and cap.panelHeight > 0)
        key = (cap.fontFamily, cap.fontSize, cap.color, cap.strokeColor, cap.strokeWidth,
               cap.background, cap.align, cap.bold, cap.shadow, has_panel, css_metrics)
        if key not in style_names:
            name = f"C{_style_seq}"
            _style_seq += 1
            style_names[key] = name
            # Domain字号是CSS em像素；ASS使用字体实际height，需按所选字体转换。
            css_size = max(8, round(cap.fontSize * height / 1080))
            # Only browser caption overlays use em metrics. Text clips use the
            # same ASS preview/export path and retain their existing size.
            selected_family = cap.fontFamily or font_family
            selected_path = None
            if css_metrics and css_font_files:
                selected_family, selected_path = css_font_files[(selected_family, cap.bold)]
            fs = (_css_font_size_to_ass(css_size, cap.fontFamily or font_family, selected_path)
                  if css_metrics else css_size)
            primary = _ass_color_hex_to_bgr(cap.color or "#ffffff")
            outline = _ass_color_hex_to_bgr(cap.strokeColor or "#000000")
            back = _ass_color_hex_to_bgr(cap.background or "#000000")
            bold = "-1" if cap.bold else "0"
            # 0 是用户明确选择的“无描边”，不能回退为默认 2px。
            # CSS stroke是总宽，stroke fill最后绘填色；ASS Outline是外扩半径。
            outline_w = max(0, round(cap.strokeWidth * height / 1080
                                    / (2 if css_metrics else 1), 2))
            shadow = max(0, round(cap.shadow * height / 1080, 2))
            align_map = {"left": "1", "center": "2", "right": "3"}
            align = align_map.get(cap.align, "2")
            # 有 background 时用 BackColour + BorderStyle=3（不透明背景盒）
            border_style = "3" if cap.background and not has_panel else "1"
            lines.append(
                f"Style: {name},{selected_family},{fs},"
                f"{primary},&H000000FF,{outline},{back},"
                f"{bold},0,0,0,100,100,0,0,{border_style},{outline_w},{shadow},"
                f"{align},20,20,40,1")
        return style_names[key]

    events: List[str] = []
    for cap in sorted(captions, key=lambda c: (c.start, c.id)):
        if cap.duration <= Rational.of(0, 1):
            continue
        start = rational_to_ass_time(cap.start)
        end = rational_to_ass_time(cap.end)
        style = _ensure_style(cap)
        # 文字几何（H01 画布编辑）：x/y 是归一化画布锚点。
        px = round(cap.x * width)
        py = round(cap.y * height)
        raw_lines = cap.text.replace("\r\n", "\n").split("\n")
        # ASS has no vertical line-spacing style property. For a customized
        # multiline title, emit one timed line per row with explicit baselines.
        separate = len(raw_lines) > 1 and cap.lineSpacing != 1.0
        displayed = raw_lines if separate else [cap.text]
        if cap.background and cap.panelWidth > 0 and cap.panelHeight > 0:
            bar_width = round(cap.panelWidth * width)
            bar_height = round(cap.panelHeight * height)
            if cap.align == "left":
                left = px - round(width * 0.025)
            elif cap.align == "right":
                left = px + round(width * 0.025) - bar_width
            else:
                left = px - bar_width // 2
            bottom = py + round(height * 0.025)
            top = bottom - bar_height
            right = left + bar_width
            color = _ass_color_hex_to_bgr(cap.background)
            alpha, bgr = color[2:4], color[4:10]
            panel_in = cap.animIn if cap.animInStyle != "none" else 0
            panel_out = cap.animOut if cap.animOutStyle != "none" else 0
            fade = f"\\fad({panel_in},{panel_out})" if panel_in or panel_out else ""
            drawing = (f"{{\\an7\\pos(0,0)\\p1\\1c&H{bgr}&\\1a&H{alpha}&{fade}}}"
                       f"m {left} {top} l {right} {top} {right} {bottom} {left} {bottom}")
            events.append(f"Dialogue: 0,{start},{end},Panel,,0,0,0,,{drawing}")
        line_step = round(max(8, cap.fontSize * height / 1080) * cap.scale
                          * cap.lineSpacing * 1.25)
        duration_ms = max(1, round(float(cap.duration.to_fraction()) * 1000))
        layer = 1 if cap.background and cap.panelWidth > 0 and cap.panelHeight > 0 else 0
        character_count = sum(len(row) for row in raw_lines)
        typewriter_ms = min(max(0, cap.animIn), duration_ms)
        typewriter_active = (cap.animInStyle == "typewriter" and
                             typewriter_ms > 0 and character_count > 1)
        typewriter_steps = (min(character_count, max(2, typewriter_ms // 33))
                            if typewriter_active else 0)

        def append_rows(rows: list[str], event_start: str, event_end: str,
                        offset_ms: int, *, final: bool,
                        typewriter_reveal: bool = False) -> None:
            for index, raw_text in enumerate(rows):
                if not raw_text:
                    continue
                line_y = py - (len(displayed) - 1 - index) * line_step
                if final:
                    animation = _text_animation_tags(cap, duration_ms,
                                                     offset_ms=offset_ms,
                                                     include_in=offset_ms == 0)
                else:
                    pct = round(cap.scale * 100)
                    animation = f"\\fscx{pct}\\fscy{pct}" if cap.scale != 1.0 else ""
                rotation = f"\\frz({cap.rotation:.2f})" if cap.rotation else ""

                # Use disjoint timed events rather than an approximate karaoke
                # sweep: each ASR word keeps its measured interval, and gaps keep
                # the whole cue in its base color. Typewriter reveal boundaries
                # are split into those same events so both effects compose.
                word_intervals = (
                    _caption_word_intervals(cap, raw_text)
                    if final else []
                )
                if word_intervals and typewriter_reveal:
                    reveal_boundaries = [
                        cap.start + Rational.of(
                            round((step + 1) * typewriter_ms / typewriter_steps), 1000)
                        for step in range(typewriter_steps)
                    ]
                    base_color = _ass_color_hex_to_bgr(cap.color or "#ffffff")
                    highlight_color = _ass_color_hex_to_bgr(cap.wordHighlightColor)
                    for interval_start, interval_end, span in word_intervals:
                        if interval_end <= interval_start:
                            continue
                        boundaries = [interval_start]
                        boundaries.extend(
                            boundary for boundary in reveal_boundaries
                            if interval_start < boundary < interval_end)
                        boundaries.append(interval_end)
                        for segment_start, segment_end in zip(boundaries, boundaries[1:]):
                            if segment_end <= segment_start:
                                continue
                            segment_start_ass = rational_to_ass_time(segment_start)
                            segment_end_ass = rational_to_ass_time(segment_end)
                            if segment_start_ass == segment_end_ass:
                                continue
                            elapsed_ms = (segment_start - cap.start).to_fraction() * 1000
                            step_index = max(0, min(
                                typewriter_steps - 1,
                                int(elapsed_ms * typewriter_steps / typewriter_ms)))
                            visible_chars = min(
                                len(raw_text),
                                (character_count * (step_index + 1)
                                 + typewriter_steps - 1) // typewriter_steps,
                            )
                            visible_text = raw_text[:visible_chars]
                            if span is None:
                                rendered_text = _ass_escape(visible_text)
                            else:
                                word_start, word_end = span
                                visible_word_start = min(max(0, word_start), visible_chars)
                                visible_word_end = min(max(visible_word_start, word_end),
                                                       visible_chars)
                                if visible_word_start == visible_word_end:
                                    rendered_text = _ass_escape(visible_text)
                                else:
                                    rendered_text = (
                                        _ass_escape(visible_text[:visible_word_start])
                                        + f"{{\\1c{highlight_color}&}}"
                                        + _ass_escape(visible_text[visible_word_start:visible_word_end])
                                        + f"{{\\1c{base_color}&}}"
                                        + _ass_escape(visible_text[visible_word_end:])
                                    )
                            relative_ms = max(0, round(float(
                                (segment_start - cap.start).to_fraction()) * 1000))
                            word_animation = _text_animation_tags(
                                cap, duration_ms, offset_ms=relative_ms,
                                include_in=relative_ms == 0)
                            text = (f"{{\\pos({px},{line_y}){word_animation}{rotation}}}"
                                    f"{rendered_text}")
                            events.append(
                                f"Dialogue: {layer},{segment_start_ass},{segment_end_ass},"
                                f"{style},,0,0,0,,{text}")
                    continue
                if word_intervals:
                    base_color = _ass_color_hex_to_bgr(cap.color or "#ffffff")
                    highlight_color = _ass_color_hex_to_bgr(cap.wordHighlightColor)
                    for interval_start, interval_end, span in word_intervals:
                        if interval_end <= interval_start:
                            continue
                        event_start = rational_to_ass_time(interval_start)
                        event_end = rational_to_ass_time(interval_end)
                        if event_start == event_end:
                            continue
                        relative_ms = max(0, round(float(
                            (interval_start - cap.start).to_fraction()) * 1000))
                        word_animation = _text_animation_tags(
                            cap, duration_ms, offset_ms=relative_ms,
                            include_in=relative_ms == 0)
                        if span is None:
                            rendered_text = _ass_escape(raw_text)
                        else:
                            word_start, word_end = span
                            rendered_text = (
                                _ass_escape(raw_text[:word_start])
                                + f"{{\\1c{highlight_color}&}}"
                                + _ass_escape(raw_text[word_start:word_end])
                                + f"{{\\1c{base_color}&}}"
                                + _ass_escape(raw_text[word_end:])
                            )
                        text = (f"{{\\pos({px},{line_y}){word_animation}{rotation}}}"
                                f"{rendered_text}")
                        events.append(
                            f"Dialogue: {layer},{event_start},{event_end},{style},,0,0,0,,{text}")
                    continue
                text = f"{{\\pos({px},{line_y}){animation}{rotation}}}{_ass_escape(raw_text)}"
                events.append(
                    f"Dialogue: {layer},{event_start},{event_end},{style},,0,0,0,,{text}")

        if typewriter_active and _caption_word_intervals(cap, cap.text):
            # Subtitle word timings remain authoritative. Split those intervals
            # at the typewriter's reveal steps so color emphasis and character
            # entry remain visible together in both preview and export.
            append_rows(displayed, start, end, 0, final=True, typewriter_reveal=True)
        elif typewriter_active:
            # Separate timed events reveal prefixes. This works with Chinese
            # characters and multiline titles without relying on a fake CSS
            # animation that would disappear from the exported video.
            steps = typewriter_steps
            for step in range(steps):
                offset_ms = round(step * typewriter_ms / steps)
                next_ms = round((step + 1) * typewriter_ms / steps)
                event_start = rational_to_ass_time(cap.start + Rational.of(offset_ms, 1000))
                event_end = (end if step == steps - 1 else
                             rational_to_ass_time(cap.start + Rational.of(next_ms, 1000)))
                if event_start == event_end:
                    continue
                visible = (character_count * (step + 1) + steps - 1) // steps
                remaining = visible
                prefixes = []
                for row in raw_lines:
                    take = min(len(row), remaining)
                    prefixes.append(row[:take])
                    remaining -= take
                rows = prefixes if separate else ["\n".join(
                    row for row in prefixes if row)]
                append_rows(rows, event_start, event_end, offset_ms,
                            final=step == steps - 1)
        else:
            append_rows(displayed, start, end, 0, final=True)
    if any(cap.background and cap.panelWidth > 0 and cap.panelHeight > 0 for cap in captions):
        lines.append(
            f"Style: Panel,{font_family},10,&H00FFFFFF,&H000000FF,&H00000000,"
            "&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1")
    lines.append("")
    lines.append("[Events]")
    lines.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
                 "MarginV, Effect, Text")
    lines.extend(events)
    lines.append("")
    return "\n".join(lines)


def prepare_caption_render(captions: List[Caption], width: int, height: int, *,
                           css_caption_ids: Optional[set[str]] = None) -> str:
    """写 ASS 文件并把字体副本放入临时目录，返回该目录绝对路径（用作 ffmpeg cwd）。

    调用方用完须清理该目录。滤镜里用相对文件名 `ass=cutvoke_captions.ass` 且
    `fontsdir=.`（cwd 相对），彻底规避 Windows 绝对路径冒号冲突。
    缺字体抛 CaptionFontError。
    """
    fonts = {family: resolve_title_font(family)
             for family in {cap.fontFamily for cap in captions} | {CAPTION_FONT_FAMILY}}
    cwd = tempfile.mkdtemp(prefix="cutvoke_ass_")
    ass_rel = "cutvoke_captions.ass"
    ass_path = os.path.join(cwd, ass_rel)
    try:
        css_fonts = {
            (cap.fontFamily, cap.bold): _caption_font_instance(cap.fontFamily, cap.bold)
            for cap in captions if css_caption_ids is not None and cap.id in css_caption_ids
        }
        with open(ass_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(build_ass(captions, width, height, css_caption_ids=css_caption_ids,
                              css_font_files=css_fonts))
        # Title fonts retain the original VF; caption aliases select real weights.
        for family, font in fonts.items():
            target = "cutvoke_serif.ttf" if family == "Noto Serif SC" else "cutvoke_sans.ttf"
            shutil.copyfile(font, os.path.join(cwd, target))
        for index, (_alias, font) in enumerate(css_fonts.values()):
            shutil.copyfile(font, os.path.join(cwd, f"caption-{index}.ttf"))
    except Exception as error:
        shutil.rmtree(cwd, ignore_errors=True)
        if isinstance(error, CaptionFontError):
            raise
        raise CaptionFontError(f"无法准备字幕字体: {error}") from error
    return cwd
