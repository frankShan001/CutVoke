"""字幕 SRT / WebVTT 导入导出（任务书 F23 / T25）。

权威时间一律用 core/rational.Rational（有理数），禁止浮点秒累加。
SRT 时间戳 HH:MM:SS,mmm 与 WebVTT 时间戳 HH:MM:SS.mmm 均经有理数毫秒
精确转 Rational（分母 1000 约分后保存），往返不引入浮点漂移。
本模块同时覆盖 SRT（parse_srt / export_srt）与 WebVTT（parse_vtt / to_vtt）。
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import List

from .model import Caption, new_id
from .rational import Rational


# ---------------------------------------------------------------------------
# 时间戳 <-> Rational 转换（SRT 是 HH:MM:SS,mmm，转 Rational 秒）
# ---------------------------------------------------------------------------

def srt_time_to_rational(ts: str) -> Rational:
    """将 SRT 时间戳 "HH:MM:SS,mmm" 转为 Rational 秒。

    直接以毫秒为分子构造有理数（分母 1000），由 Fraction 自动约分，
    完全不碰浮点，避免 29.97/毫秒精度漂移。
    """
    h_s, m_s, rest = ts.split(":")
    s_s, ms_s = rest.split(",")
    total_ms = int(h_s) * 3_600_000 + int(m_s) * 60_000 + int(s_s) * 1000 + int(ms_s)
    return Rational.of(total_ms, 1000)


def rational_to_srt_time(r: Rational) -> str:
    """将 Rational 秒转为 SRT 时间戳 "HH:MM:SS,mmm"。

    以 Fraction 精确乘 1000 后四舍五入到最近毫秒（SRT 毫秒分辨率），
    再处理进位，保证 99:59:59,999 这类边界不溢出截断。
    """
    total_ms = int(round(Fraction(r.num, r.den) * 1000))
    if total_ms < 0:
        total_ms = 0
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    m = (total_s // 60) % 60
    h = total_s // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ---------------------------------------------------------------------------
# SRT 解析 / 导出
# ---------------------------------------------------------------------------

# 按空行切分块；容忍 \r\n 与 \r；可含 BOM
_BLOCK_SPLIT = re.compile(r"\n[ \t]*\n")


def parse_srt(srt_text: str) -> List[Caption]:
    """解析标准 SRT 文本为 Caption 列表。

    - 自动跳过序号行（任意在 "-->" 之前的行）
    - 支持多行字幕文本（块内按 \n 合并）
    - 忽略时间轴后可能附带的定位坐标（如 "X1:... Y1:..."）
    - 每个字幕生成新的稳定 captionId（导入即新建实体）
    """
    text = srt_text.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):  # 去 BOM
        text = text[1:]

    captions: List[Caption] = []
    for block in _BLOCK_SPLIT.split(text.strip()):
        if not block.strip():
            continue
        lines = block.split("\n")
        # 找到时间轴行（含 "-->"）
        timing_idx = None
        for i, line in enumerate(lines):
            if "-->" in line:
                timing_idx = i
                break
        if timing_idx is None:
            continue  # 无时间轴的块跳过
        parts = lines[timing_idx].split("-->")
        if len(parts) != 2:
            continue
        start_ts = parts[0].strip().split(" ")[0]   # 去掉可能的坐标后缀
        end_ts = parts[1].strip().split(" ")[0]
        start = srt_time_to_rational(start_ts)
        end = srt_time_to_rational(end_ts)
        caption_text = "\n".join(lines[timing_idx + 1:])
        captions.append(Caption(id=new_id("cap"), text=caption_text,
                                start=start, end=end))
    return captions


def export_srt(captions: List[Caption]) -> str:
    """将 Caption 列表导出为标准 SRT 文本。

    - 按 start 升序排列（并列时按 id 稳定排序），重新编号 1..N
    - 时间经 rational_to_srt_time 精确写到毫秒
    - 多行文本以 \n 原样保留
    """
    ordered = sorted(captions, key=lambda c: (c.start, c.id))
    blocks = []
    for i, cap in enumerate(ordered, start=1):
        start_ts = rational_to_srt_time(cap.start)
        end_ts = rational_to_srt_time(cap.end)
        blocks.append(f"{i}\n{start_ts} --> {end_ts}\n{cap.text}")
    return "\n\n".join(blocks) + "\n"


# ---------------------------------------------------------------------------
# WebVTT 解析 / 导出（K01）
# ---------------------------------------------------------------------------

# 内联标签：<c.xxx> 类/颜色、<v Speaker> 说话人、<b>/<i>/<u> 等；剥离保留纯文本
_INLINE_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
# 元数据块头（大小写不敏感地以这些词开头）
_META_PREFIXES = ("WEBVTT", "NOTE", "STYLE", "REGION")


def _strip_vtt_tags(s: str) -> str:
    """去掉 WebVTT 内联标签（<c>/<v>/<b>...），保留纯文本。"""
    return _INLINE_TAG.sub("", s).strip()


def _vtt_timestamp_to_rational(raw: str) -> Rational:
    """将 WebVTT 时间戳（HH:MM:SS.mmm，逗号也可）转为 Rational 秒。

    支持省略小时（MM:SS.mmm / SS.mmm）；自动剥离尾部 cue 设置（如 line:90%）；
    毫秒补零到 3 位（不足补、超出截断），完全不碰浮点。
    """
    raw = raw.strip().split(" ")[0]   # 去掉 cue 设置（出现在结束时间戳之后）
    if "." in raw:
        main, ms = raw.rsplit(".", 1)
    elif "," in raw:
        main, ms = raw.rsplit(",", 1)
    else:
        main, ms = raw, "0"
    ms = (ms + "000")[:3]
    parts = main.split(":")
    if len(parts) == 3:
        h, m, s = parts[0], parts[1], parts[2]
    elif len(parts) == 2:
        h, m, s = "0", parts[0], parts[1]
    else:
        h, m, s = "0", "0", parts[0]
    total_ms = int(h) * 3_600_000 + int(m) * 60_000 + int(s) * 1000 + int(ms)
    return Rational.of(total_ms, 1000)


def _rational_to_vtt_time(r: Rational) -> str:
    """将 Rational 秒转为 WebVTT 时间戳 HH:MM:SS.mmm（毫秒分辨率）。"""
    total_ms = int(round(Fraction(r.num, r.den) * 1000))
    if total_ms < 0:
        total_ms = 0
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    m = (total_s // 60) % 60
    h = total_s // 3600
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def parse_vtt(vtt_text: str) -> List[Caption]:
    """解析 WebVTT 文本为 Caption 列表（K01）。

    - 跳过 WEBVTT 头、NOTE/STYLE/REGION 元数据块
    - 块可含可选 cue 标识行（任意非时间戳首行），忽略
    - 时间轴时间戳支持 HH:MM:SS.mmm（逗号亦可）、可省略小时
    - 剥离 <c>/<v> 等内联标签，保留纯文本
    - 每条字幕生成新的稳定 captionId（导入即新建实体）
    """
    text = vtt_text.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):  # 去 BOM
        text = text[1:]

    captions: List[Caption] = []
    for block in _BLOCK_SPLIT.split(text.strip()):
        if not block.strip():
            continue
        lines = block.split("\n")
        first = lines[0].strip()
        # 跳过元数据块（WEBVTT 头 / NOTE / STYLE / REGION）
        if first.upper().startswith(_META_PREFIXES) and "-->" not in first:
            # 单块首行若为 WEBVTT 头或 NOTE/STYLE/REGION，整块跳过
            # 但 cue 标识行也可能以 NOTE 开头？标准 NOTE 块以 NOTE 起头，安全跳过
            continue
        # 找时间轴行
        timing_idx = None
        for i, line in enumerate(lines):
            if "-->" in line:
                timing_idx = i
                break
        if timing_idx is None:
            continue
        parts = lines[timing_idx].split("-->")
        if len(parts) != 2:
            continue
        start = _vtt_timestamp_to_rational(parts[0])
        end = _vtt_timestamp_to_rational(parts[1])
        caption_text = _strip_vtt_tags("\n".join(lines[timing_idx + 1:]))
        if not caption_text:
            continue
        captions.append(Caption(id=new_id("cap"), text=caption_text,
                                start=start, end=end))
    return captions


def to_vtt(captions: List[Caption]) -> str:
    """将 Caption 列表导出为 WebVTT 文本（K01）。

    - 头部 WEBVTT
    - 按 start 升序排列（并列按 id 稳定排序），每条 cue 带顺序标识行
    - 时间戳经 _rational_to_vtt_time 精确写到毫秒（点分隔）
    - 多行文本以 \n 原样保留
    """
    ordered = sorted(captions, key=lambda c: (c.start, c.id))
    parts = ["WEBVTT"]
    for i, cap in enumerate(ordered, start=1):
        start_ts = _rational_to_vtt_time(cap.start)
        end_ts = _rational_to_vtt_time(cap.end)
        # cue 之间以空行分隔（WebVTT 块边界），cue 内多行文本保持单 \n
        parts.append(f"{i}\n{start_ts} --> {end_ts}\n{cap.text}")
    return "\n\n".join(parts) + "\n"
