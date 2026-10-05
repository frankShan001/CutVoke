"""Validation for user imported 3D .cube lookup tables."""

from __future__ import annotations

import math


MAX_CUBE_BYTES = 16 * 1024 * 1024
MAX_CUBE_SIZE = 64


class CubeInvalid(ValueError):
    """The file is not a supported 3D .cube LUT."""


def validate_cube(raw: bytes) -> int:
    """Return LUT edge size after checking a bounded, finite 3D grid.

    The uploader accepts only 3D CUBE files. A 1D shaper or partial grid would
    fail in FFmpeg after a project edit, so reject it before saving the asset.
    """
    if not raw or len(raw) > MAX_CUBE_BYTES:
        raise CubeInvalid(f"LUT 文件必须为 1–{MAX_CUBE_BYTES} 字节")
    try:
        lines = raw.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as error:
        raise CubeInvalid("LUT 文件必须是 UTF-8 文本") from error

    edge: int | None = None
    count = 0
    for number, original in enumerate(lines, 1):
        line = original.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        directive = parts[0].upper()
        if directive == "TITLE":
            if count:
                raise CubeInvalid(f"第 {number} 行：TITLE 必须在数据前")
            continue
        if directive == "LUT_1D_SIZE":
            raise CubeInvalid("暂不支持带 1D shaper 的 LUT；请导出纯 3D .cube")
        if directive == "LUT_3D_SIZE":
            if edge is not None or count or len(parts) != 2:
                raise CubeInvalid(f"第 {number} 行：LUT_3D_SIZE 声明不合法")
            try:
                edge = int(parts[1])
            except ValueError as error:
                raise CubeInvalid(f"第 {number} 行：LUT_3D_SIZE 必须是整数") from error
            if not 2 <= edge <= MAX_CUBE_SIZE:
                raise CubeInvalid(f"LUT_3D_SIZE 必须在 2–{MAX_CUBE_SIZE} 之间")
            continue
        if directive in ("DOMAIN_MIN", "DOMAIN_MAX"):
            if count or len(parts) != 4:
                raise CubeInvalid(f"第 {number} 行：{directive} 必须在数据前包含三个数值")
            values = parts[1:]
        else:
            if edge is None or len(parts) != 3:
                raise CubeInvalid(f"第 {number} 行：需要先声明 LUT_3D_SIZE，再写 RGB 三元组")
            values = parts
        try:
            if not all(math.isfinite(float(value)) for value in values):
                raise ValueError("non-finite")
        except ValueError as error:
            raise CubeInvalid(f"第 {number} 行：RGB 值必须为有限数值") from error
        if directive not in ("DOMAIN_MIN", "DOMAIN_MAX"):
            count += 1
            if count > edge ** 3:
                raise CubeInvalid(f"LUT 数据超过 {edge ** 3} 行")

    if edge is None:
        raise CubeInvalid("缺少 LUT_3D_SIZE 声明")
    if count != edge ** 3:
        raise CubeInvalid(f"LUT 数据不足：期望 {edge ** 3} 行，实际 {count} 行")
    return edge
