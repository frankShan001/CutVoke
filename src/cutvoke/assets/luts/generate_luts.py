"""生成 CutVoke 内置 LUT 预设（.cube 格式，供 ffmpeg lut3d 滤镜读取）。

运行：python generate_luts.py
产物：同目录下 cool.cube / warm.cube / retro.cube（LUT_3D_SIZE=33）。

这些预设是「真实可渲染」的 3D 查找表：lut3d 滤镜直接读取 .cube 文件，
不需要外部下载资源（dependencies 标注 lut 仅表示「需要随包分发的 .cube 资源」）。

预设定义（RGB 逐点变换，全部钳制到 [0,1]）：
  * cool  : 偏冷——提升蓝、轻微压红，适合清新/科技感。
  * warm  : 偏暖——提升红、压蓝，适合夕阳/温馨。
  * retro : 复古褪色——轻微去饱和 + 暖调 + 抬黑（fade），老胶片质感。
"""

from __future__ import annotations

import math
from pathlib import Path

SIZE = 33  # 33^3 = 35937 个采样点，过渡平滑，文件约 1.1MB/个

# 预设变换：输入 (r,g,b) -> 输出 (r,g,b)，各分量已在 [0,1]
PRESETS: dict[str, "callable"] = {}


def _clamp(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _cool(r: float, g: float, b: float):
    return (_clamp(r * 0.92), _clamp(g * 1.0), _clamp(b * 1.12))


def _warm(r: float, g: float, b: float):
    return (_clamp(r * 1.12), _clamp(g * 1.02), _clamp(b * 0.88))


def _retro(r: float, g: float, b: float):
    # 1) 轻微去饱和（向亮度混合）
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    r = r * 0.85 + lum * 0.15
    g = g * 0.85 + lum * 0.15
    b = b * 0.85 + lum * 0.15
    # 2) 暖调
    r = r * 1.08
    b = b * 0.90
    # 3) 抬黑（fade / 褪色）
    lift = 0.03
    r = r * (1 - lift) + lift
    g = g * (1 - lift) + lift
    b = b * (1 - lift) + lift
    return (_clamp(r), _clamp(g), _clamp(b))


PRESETS["cool"] = _cool
PRESETS["warm"] = _warm
PRESETS["retro"] = _retro


def _write_cube(path: Path, fn: "callable") -> None:
    lines: list[str] = []
    lines.append(f"# Created by CutVoke generate_luts.py")
    lines.append(f"TITLE {path.stem}")
    lines.append("LUT_3D_SIZE %d" % SIZE)
    lines.append("DOMAIN_MIN 0.0 0.0 0.0")
    lines.append("DOMAIN_MAX 1.0 1.0 1.0")
    step = 1.0 / (SIZE - 1)
    for b_i in range(SIZE):
        b = b_i * step
        for g_i in range(SIZE):
            g = g_i * step
            for r_i in range(SIZE):
                r = r_i * step
                ro, go, bo = fn(r, g, b)
                lines.append(f"{ro:.6f} {go:.6f} {bo:.6f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {path} ({len(lines)} lines)")


def main() -> None:
    here = Path(__file__).resolve().parent
    for name, fn in PRESETS.items():
        _write_cube(here / f"{name}.cube", fn)


if __name__ == "__main__":
    main()
