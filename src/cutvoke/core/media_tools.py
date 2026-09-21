"""媒体文件级处理工具（J06 稳定 / 画质增强，真实 ffmpeg）。

与 audio_tools.py 同模式：文件级处理、不改工程。所有滤镜已在本机
ffmpeg 9.0.1 实测存在（deshake / vidstabdetect / vidstabtransform /
unsharp / hqdn3d / nlmeans）。
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional

from .render import DEFAULT_FFMPEG, DEFAULT_FFPROBE


def _ensure_out(src: str, out_path: Optional[str], suffix: str) -> str:
    """缺省输出：同目录 <stem><suffix>.<ext>。"""
    if out_path:
        return out_path
    base, ext = os.path.splitext(src)
    return f"{base}{suffix}{ext or '.mp4'}"


def _probe(path: str) -> dict:
    r = subprocess.run(
        [DEFAULT_FFPROBE, "-v", "error", "-show_entries",
         "format=duration:stream=width,height,r_frame_rate,codec_type",
         "-of", "json", path],
        capture_output=True, text=True, timeout=120)
    import json
    try:
        info = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        info = {}
    fmt = info.get("format", {})
    v = next((s for s in info.get("streams", [])
              if s.get("codec_type") == "video"), None)
    try:
        dur = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    return {
        "duration": dur,
        "width": int(v.get("width", 0)) if v else 0,
        "height": int(v.get("height", 0)) if v else 0,
    }


def stabilize_video(video_path: str, out_path: Optional[str] = None,
                    method: str = "vidstab") -> dict:
    """视频稳定（防抖）。

    method=vidstab（默认）：两遍 —— vidstabdetect 检测运动 → out.transforms
    传递文件 → vidstabtransform 应用（真实标准做法）。
    method=deshake：单遍 deshake（轻量，适合快速预览）。
    返回 {outPath, duration, width, height}。文件级处理，不改工程。
    """
    if not os.path.isfile(video_path):
        raise RuntimeError(f"stabilize: file not found: {video_path}")
    out = _ensure_out(video_path, out_path, "_stabilized")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    tmp_dir = out + ".stabdata"
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        if method == "deshake":
            cmd = [DEFAULT_FFMPEG, "-y", "-i", video_path,
                   "-vf", "deshake", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", out]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            if r.returncode != 0:
                raise RuntimeError(
                    "stabilize: deshake failed: " + (r.stderr or "")[-300:])
        else:
            # vidstab 两遍：transform 文件用相对名，ffmpeg cwd 切到 tmp_dir
            # （Windows 下绝对路径含冒号/反斜杠会被 filter 解析吃掉）。
            transforms_rel = "trf"
            cmd1 = [DEFAULT_FFMPEG, "-y", "-i", os.path.abspath(video_path),
                    "-vf", f"vidstabdetect=result={transforms_rel}",
                    "-f", "null", "-"]
            r1 = subprocess.run(cmd1, capture_output=True, text=True,
                                timeout=600, cwd=tmp_dir)
            if r1.returncode != 0:
                raise RuntimeError(
                    "stabilize: vidstabdetect failed: "
                    + (r1.stderr or "")[-300:])
            cmd = [DEFAULT_FFMPEG, "-y", "-i", os.path.abspath(video_path),
                   "-vf", f"vidstabtransform=input={transforms_rel}",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", os.path.abspath(out)]
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=900, cwd=tmp_dir)
            if r.returncode != 0:
                raise RuntimeError(
                    "stabilize: vidstabtransform failed: "
                    + (r.stderr or "")[-400:])
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    info = _probe(out)
    info["outPath"] = out
    return info


def enhance_video(video_path: str, out_path: Optional[str] = None,
                  sharpness: float = 1.0, denoise: float = 0.0) -> dict:
    """画质增强（锐化 + 可选降噪）。

    组合 ffmpeg 真实滤镜：unsharp 锐化（sharpness 0.0~3.0）+ hqdn3d 降噪
    （denoise 0~30）。返回 {outPath, duration, width, height}。
    """
    if not os.path.isfile(video_path):
        raise RuntimeError(f"enhance: file not found: {video_path}")
    out = _ensure_out(video_path, out_path, "_enhanced")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    chain: list[str] = []
    if denoise > 0:
        chain.append(f"hqdn3d=luma_spatial={max(1.0, min(30.0, denoise)):.2f}")
    if sharpness > 0:
        chain.append(f"unsharp=5:5:{max(0.0, min(3.0, sharpness)):.2f}:5:5:0")
    if not chain:
        raise RuntimeError("enhance: sharpness and denoise both zero, nothing to do")
    cmd = [DEFAULT_FFMPEG, "-y", "-i", video_path,
           "-vf", ",".join(chain),
           "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "aac", out]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise RuntimeError("enhance failed: " + (r.stderr or "")[-400:])
    info = _probe(out)
    info["outPath"] = out
    return info


def gif2video(gif_path: str, out_path: Optional[str] = None) -> dict:
    """动图转视频（A03）：ffmpeg gif → mp4（循环铺满，供时间线使用）。

    用 -stream_loop -1 把动图铺成可剪辑的视频；输出 libx264 yuv420p。
    返回 {outPath, duration}。文件级处理，不改工程。
    """
    if not os.path.isfile(gif_path):
        raise RuntimeError(f"gif2video: file not found: {gif_path}")
    if not gif_path.lower().endswith(".gif"):
        raise RuntimeError("gif2video: input must be .gif")
    out = out_path or os.path.splitext(gif_path)[0] + ".mp4"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    # 先探测 gif 时长
    r0 = subprocess.run([DEFAULT_FFPROBE, "-v", "error",
                         "-show_entries", "format=duration", "-of", "csv=p=0", gif_path],
                        capture_output=True, text=True, timeout=60)
    dur = 0.0
    try:
        dur = float(r0.stdout.strip())
    except (ValueError, TypeError):
        dur = 0.0
    cmd = [DEFAULT_FFMPEG, "-y", "-stream_loop", "-1", "-i", gif_path,
           "-t", "5.0" if dur <= 0 else f"{dur:.3f}",
           "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
           "-r", "25", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "none", out]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError("gif2video failed: " + (r.stderr or "")[-300:])
    if not os.path.isfile(out) or os.path.getsize(out) <= 0:
        raise RuntimeError("gif2video: produced empty file")
    return {"outPath": out, "duration": dur if dur > 0 else 5.0}


def video2gif(video_path: str, out_path: Optional[str] = None,
              fps: int = 10) -> dict:
    """视频转动图（V03 动图输出）：mp4 → gif，文件级输出。

    用 palettegen/paletteuse 提升画质；返回 {outPath, duration}。
    """
    if not os.path.isfile(video_path):
        raise RuntimeError(f"video2gif: file not found: {video_path}")
    out = out_path or os.path.splitext(video_path)[0] + ".gif"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    pal = out + ".palette.png"
    cmd1 = [DEFAULT_FFMPEG, "-y", "-i", video_path,
            "-vf", f"fps={int(fps)},palettegen", pal]
    r1 = subprocess.run(cmd1, capture_output=True, text=True, timeout=300)
    if r1.returncode != 0:
        raise RuntimeError("video2gif: palettegen failed: " + (r1.stderr or "")[-300:])
    cmd2 = [DEFAULT_FFMPEG, "-y", "-i", video_path, "-i", pal,
            "-lavfi", f"fps={int(fps)}[x];[x][1:v]paletteuse", out]
    r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=300)
    try:
        if os.path.exists(pal):
            os.remove(pal)
    except OSError:
        pass
    if r2.returncode != 0 or not os.path.isfile(out) or os.path.getsize(out) <= 0:
        raise RuntimeError("video2gif failed: " + (r2.stderr or "")[-300:])
    return {"outPath": out}
