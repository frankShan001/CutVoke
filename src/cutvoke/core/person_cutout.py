"""Local video person segmentation and reversible cutout jobs.

The optional MediaPipe runtime and model are kept out of the base install. A
completed job writes a transparent ProRes 4444 derivative; a separate
``asset.swap`` command applies it to the timeline so the operation is undoable.
"""

from __future__ import annotations

import copy
import contextlib
from functools import lru_cache
import hashlib
import importlib.util
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import sys
from fractions import Fraction
from pathlib import Path
from typing import Callable

from .model import Clip, Project
from .speech_recognition import source_signature


MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/image_segmenter/"
    "selfie_segmenter/float16/latest/selfie_segmenter.tflite"
)
MODEL_SHA256 = "191ac9529ae506ee0beefa6b2c945a172dab9d07d1e802a290a4e4038226658b"
MODEL_ENV = "CUTVOKE_PERSON_MODEL_PATH"
INTERACTIVE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/interactive_segmenter/"
    "magic_touch/float32/1/magic_touch.tflite"
)
INTERACTIVE_MODEL_SHA256 = "e24338a717c1b7ad8d159666677ef400babb7f33b8ad60c4d96db4ecf694cd25"
INTERACTIVE_MODEL_ENV = "CUTVOKE_INTERACTIVE_PERSON_MODEL_PATH"
SAM2_MODEL_URL = (
    "https://dl.fbaipublicfiles.com/segment_anything_2/092824/"
    "sam2.1_hiera_base_plus.pt"
)
SAM2_MODEL_SHA256 = "A2345AEDE8715AB1D5D31B4A509FB160C5A4AF1970F199D9054CCFB746C004C5".lower()
SAM2_MODEL_ENV = "CUTVOKE_SAM2_MODEL_PATH"
SAM2_PYTHON_ENV = "CUTVOKE_SAM2_PYTHON"
SAM2_GIT_REVISION = "2b90b9f5ceec907a1c18123530e92e794ad901a4"
SAM2_CONFIG = "configs/sam2.1/sam2.1_hiera_b+.yaml"
SAM2_RUNTIME_MARKER = ".cutvoke-sam2-ready.json"
SAM2_FRAME_MAX_EDGE = 1024
MAX_SELECTION_POINTS = 8
MAX_PENDING_JOBS = 2


class PersonCutoutError(RuntimeError):
    """A local person-cutout request cannot be completed."""


class PersonCutoutCancelled(PersonCutoutError):
    """The user cancelled a person-cutout job."""


def model_path() -> Path:
    configured = os.environ.get(MODEL_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".cutvoke" / "models" /
            "selfie_segmenter.tflite").resolve()


def interactive_model_path() -> Path:
    configured = os.environ.get(INTERACTIVE_MODEL_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".cutvoke" / "models" / "magic_touch.tflite").resolve()


def sam2_model_path() -> Path:
    configured = os.environ.get(SAM2_MODEL_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".cutvoke" / "models" / "sam2.1_hiera_base_plus.pt").resolve()


def sam2_runtime_root() -> Path:
    return (Path.home() / ".cutvoke" / "runtime" / "sam2-windows").resolve()


def sam2_runtime_python() -> Path:
    configured = os.environ.get(SAM2_PYTHON_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    root = sam2_runtime_root()
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def sam2_runtime_is_ready() -> bool:
    python = sam2_runtime_python()
    marker = python.parent.parent / SAM2_RUNTIME_MARKER
    if not python.is_file() or not marker.is_file():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("revision") == SAM2_GIT_REVISION and bool(data.get("torch"))


def _file_has_sha256(path: Path, expected: str) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    if not path.is_file() or stat.st_size <= 0:
        return False
    return _cached_file_sha256(str(path.resolve()), expected, stat.st_size,
                               stat.st_mtime_ns)


@lru_cache(maxsize=8)
def _cached_file_sha256(path: str, expected: str, size: int, mtime_ns: int) -> bool:
    del size, mtime_ns  # cache key binds the result to the current file identity
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return False
    return digest.hexdigest() == expected


def _model_is_valid(path: Path) -> bool:
    return _file_has_sha256(path, MODEL_SHA256)


def _interactive_model_is_valid(path: Path) -> bool:
    return _file_has_sha256(path, INTERACTIVE_MODEL_SHA256)


def availability() -> dict:
    opencv_installed = False
    try:
        opencv_installed = importlib.util.find_spec("cv2") is not None
        installed = (importlib.util.find_spec("mediapipe") is not None and
                     opencv_installed)
    except (ImportError, ValueError):
        installed = False
    path = model_path()
    model_ready = _model_is_valid(path)
    interactive_model_ready = _interactive_model_is_valid(interactive_model_path())
    sam2_runtime_ready = sam2_runtime_is_ready()
    sam2_model_ready = _file_has_sha256(sam2_model_path(), SAM2_MODEL_SHA256)
    sam2_ready = sam2_runtime_ready and sam2_model_ready and opencv_installed
    ffmpeg_ready = bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe"))
    mediapipe_ready = installed and model_ready and ffmpeg_ready
    if sam2_ready and not model_ready:
        message = "SAM2 视频身份跟踪已就绪；保留所有人物仍需准备 MediaPipe 模型。"
    elif not installed:
        message = "人物抠像组件未安装：请安装 cutvoke[person]（含 MediaPipe 与 OpenCV）。"
    elif not ffmpeg_ready:
        message = "人物抠像需要 FFmpeg 与 ffprobe；请先安装 FFmpeg 并加入 PATH。"
    elif not model_ready:
        message = "人物分割模型未准备：运行 cutvoke-person-model。"
    else:
        message = "本机人物分割已就绪（MediaPipe Selfie Segmentation，Apache-2.0）；视频只在本机处理。"
    return {
        "installed": installed,
        "opencvInstalled": opencv_installed,
        "modelReady": model_ready,
        "interactiveModelReady": interactive_model_ready,
        "mediapipeReady": mediapipe_ready,
        "sam2RuntimeReady": sam2_runtime_ready,
        "sam2ModelReady": sam2_model_ready,
        "sam2Ready": sam2_ready,
        "ready": mediapipe_ready or sam2_ready and ffmpeg_ready,
        "message": message,
    }


def find_video_clip(project: Project, clip_id: str) -> Clip:
    for track in project.sequence.tracks:
        if track.kind != "video" or track.role == "sticker":
            continue
        for clip in track.clips:
            if clip.id != clip_id:
                continue
            if clip.role == "sticker" or clip.nested is not None:
                raise PersonCutoutError("请先选中普通视频片段；贴纸和复合片段不能直接抠像")
            source = clip.asset_ref.source_path
            if not source or not Path(source).is_file():
                raise PersonCutoutError("所选视频源文件不可用，请先重新链接素材")
            return clip
    raise PersonCutoutError("请选择视频轨道上的视频片段")


def _probe_video(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise PersonCutoutError("找不到 ffprobe；请先安装 FFmpeg 并加入 PATH")
    try:
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(path)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        data = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise PersonCutoutError(f"无法读取视频信息：{exc}") from exc
    videos = [item for item in data.get("streams", [])
              if item.get("codec_type") == "video"]
    if not videos:
        raise PersonCutoutError("源文件没有视频画面")
    video = videos[0]
    try:
        nominal = float(Fraction(video.get("r_frame_rate", "0/1")))
        average = float(Fraction(video.get("avg_frame_rate", "0/1")))
    except (ValueError, ZeroDivisionError):
        nominal = average = 0
    if nominal > 0 and average > 0 and abs(nominal - average) > max(0.05, average * 0.01):
        raise PersonCutoutError(
            "暂不支持可变帧率视频；请先将素材转换为恒定帧率再抠像")
    return {
        "video": video,
        "fps": video.get("avg_frame_rate") or video.get("r_frame_rate") or "",
        "hasAudio": any(item.get("codec_type") == "audio"
                         for item in data.get("streams", [])),
    }


def _keep_large_components(confidence, cv2, np):
    binary = (confidence >= 0.35).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    if count <= 1:
        return np.zeros_like(confidence)
    areas = stats[1:, cv2.CC_STAT_AREA]
    largest = int(areas.max())
    minimum = max(64, int(largest * 0.002))
    keep = np.zeros(count, dtype=np.uint8)
    for label in range(1, count):
        if int(stats[label, cv2.CC_STAT_AREA]) >= minimum:
            keep[label] = 1
    return confidence * keep[labels]


def _person_components(confidence, cv2, np):
    """Return separated foreground components large enough to be selectable."""
    binary = (confidence >= 0.35).astype(np.uint8)
    count, labels, stats, centers = cv2.connectedComponentsWithStats(binary, 8)
    if count <= 1:
        return labels, []
    largest = int(stats[1:, cv2.CC_STAT_AREA].max())
    minimum = max(64, int(largest * 0.0005))
    components = []
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < minimum:
            continue
        left = int(stats[label, cv2.CC_STAT_LEFT])
        top = int(stats[label, cv2.CC_STAT_TOP])
        width = int(stats[label, cv2.CC_STAT_WIDTH])
        height = int(stats[label, cv2.CC_STAT_HEIGHT])
        components.append({
            "label": label,
            "area": area,
            "center": (float(centers[label][0]), float(centers[label][1])),
            "bounds": (left, top, left + width - 1, top + height - 1),
        })
    return labels, components


def _select_person_component(confidence, cv2, np, point: tuple[float, float]):
    """Select a foreground component by normalized click position."""
    labels, components = _person_components(confidence, cv2, np)
    if not components:
        return None
    height, width = confidence.shape
    x = int(round(point[0] * (width - 1)))
    y = int(round(point[1] * (height - 1)))
    label = int(labels[y, x])
    selected = next((item for item in components if item["label"] == label), None)
    if selected is None:
        radius = max(4, round(min(width, height) * 0.025))
        nearby = [item for item in components if
                  max(item["bounds"][0] - x, 0, x - item["bounds"][2]) ** 2 +
                  max(item["bounds"][1] - y, 0, y - item["bounds"][3]) ** 2 <= radius ** 2]
        selected = min(nearby, key=lambda item: item["area"], default=None)
    if selected is None:
        return None
    mask = labels == selected["label"]
    return confidence * mask, mask, selected["center"], len(components)


def _track_person_component(confidence, cv2, np, previous_mask, previous_center):
    """Follow the selected separated component by mask overlap and center motion."""
    labels, components = _person_components(confidence, cv2, np)
    if not components:
        return None
    height, width = confidence.shape
    diagonal = math.hypot(width, height)
    previous_area = max(1, int(np.count_nonzero(previous_mask)))
    candidates = []
    for item in components:
        mask = labels == item["label"]
        overlap = int(np.count_nonzero(mask & previous_mask))
        overlap_ratio = overlap / max(1, min(previous_area, item["area"]))
        distance = math.hypot(item["center"][0] - previous_center[0],
                              item["center"][1] - previous_center[1])
        score = 0.75 * overlap_ratio + 0.25 * max(0.0, 1.0 - distance / (diagonal * 0.06))
        if overlap_ratio >= 0.06 or distance <= diagonal * 0.018:
            candidates.append((score, item, mask))
    if not candidates:
        return None
    _, selected, mask = max(candidates, key=lambda entry: entry[0])
    return confidence * mask, mask, selected["center"], len(components)


def _track_prompt_point(previous_gray, current_gray, previous_mask,
                        previous_point, cv2, np):
    """Propagate a click with local optical flow; fail closed when it is ambiguous."""
    height, width = previous_gray.shape[:2]
    x = int(round(previous_point[0] * (width - 1)))
    y = int(round(previous_point[1] * (height - 1)))
    flow_mask = (previous_mask.astype(np.uint8) * 255)
    points = cv2.goodFeaturesToTrack(
        previous_gray, maxCorners=80, qualityLevel=0.01,
        minDistance=4, mask=flow_mask, blockSize=5,
    )
    if points is None or len(points) < 2:
        return None
    next_points, status, errors = cv2.calcOpticalFlowPyrLK(
        previous_gray, current_gray, points, None,
        winSize=(21, 21), maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
    )
    if next_points is None or status is None:
        return None
    valid = status.reshape(-1).astype(bool)
    if errors is not None:
        valid &= np.isfinite(errors.reshape(-1)) & (errors.reshape(-1) <= 40)
    old = points.reshape(-1, 2)[valid]
    new = next_points.reshape(-1, 2)[valid]
    minimum_tracks = max(2, min(5, int(math.ceil(len(points) * 0.12))))
    if len(new) < minimum_tracks:
        return None
    displacement = np.median(new - old, axis=0)
    if not np.isfinite(displacement).all():
        return None
    if math.hypot(float(displacement[0]), float(displacement[1])) > math.hypot(width, height) * 0.12:
        return None
    tracked_x = x + float(displacement[0])
    tracked_y = y + float(displacement[1])
    if not (0 <= tracked_x < width and 0 <= tracked_y < height):
        return None
    return tracked_x / max(1, width - 1), tracked_y / max(1, height - 1)


PROMPT_REACQUIRE_WINDOW_SECONDS = 0.2


def _prompt_reacquisition_allowed(frame_index: int, lost_at_frame: int | None,
                                  fps: float) -> bool:
    """Retry local flow briefly from the last confident frame, then stay transparent."""
    if lost_at_frame is None:
        return True
    max_gap = max(1, int(round(fps * PROMPT_REACQUIRE_WINDOW_SECONDS)))
    return frame_index - lost_at_frame <= max_gap


def _select_prompted_person(prompt_confidence, person_confidence, point, cv2, np):
    """Keep the prompted object only where the person model also sees a person."""
    prompted = _select_person_component(prompt_confidence, cv2, np, point)
    if prompted is None:
        return None
    _, prompted_mask, _, component_count = prompted
    selected_mask = prompted_mask & (person_confidence >= 0.08)
    if int(np.count_nonzero(selected_mask)) < 64:
        return None
    confidence = np.minimum(prompt_confidence, person_confidence) * selected_mask
    ys, xs = np.nonzero(selected_mask)
    center = (float(xs.mean()), float(ys.mean()))
    return confidence, selected_mask, center, component_count


def _run_ffmpeg(command: list[str], error_log, *, stage: str) -> None:
    try:
        result = subprocess.run(
            command, check=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=error_log, timeout=1800,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PersonCutoutError(f"{stage}失败：{exc}") from exc
    if result.returncode:
        error_log.seek(0)
        detail = error_log.read().decode("utf-8", errors="replace")[-1200:]
        raise PersonCutoutError(f"{stage}失败：{detail or result.returncode}")


def _generate_sam2_masks(
    source: Path, width: int, height: int, expected_frames: int,
    prompt_sets: list[dict],
    work_dir: Path, *, progress=None, cancelled=None,
) -> Path:
    """Extract reduced frames and run the isolated SAM2 video predictor."""
    try:
        import cv2
    except (ImportError, OSError) as exc:
        raise PersonCutoutError("SAM2 跟踪需要 OpenCV；请安装 cutvoke[person]") from exc
    frames_dir = work_dir / "frames"
    masks_dir = work_dir / "masks"
    frames_dir.mkdir(parents=True)
    masks_dir.mkdir(parents=True)
    capture = cv2.VideoCapture(str(source), cv2.CAP_FFMPEG)
    if not capture.isOpened():
        raise PersonCutoutError("无法解码 SAM2 跟踪视频")
    scale = min(1.0, SAM2_FRAME_MAX_EDGE / max(width, height))
    inference_width = max(16, int(round(width * scale)))
    inference_height = max(16, int(round(height * scale)))
    frame_count = 0
    try:
        while True:
            if cancelled and cancelled():
                raise PersonCutoutCancelled("人物抠像已取消")
            ok, frame = capture.read()
            if not ok:
                break
            if (inference_width, inference_height) != (width, height):
                frame = cv2.resize(frame, (inference_width, inference_height),
                                   interpolation=cv2.INTER_AREA)
            frame_path = frames_dir / f"{frame_count:08d}.jpg"
            if not cv2.imwrite(str(frame_path), frame,
                               [cv2.IMWRITE_JPEG_QUALITY, 90]):
                raise PersonCutoutError(f"无法准备 SAM2 第 {frame_count + 1} 帧")
            frame_count += 1
            if progress and frame_count % 12 == 0:
                progress("正在准备 SAM2 跟踪帧", min(8, 1 + round(frame_count / max(1, expected_frames) * 7)))
    finally:
        capture.release()
    if not prompt_sets or any(
            item["frame"] < 0 or item["frame"] >= frame_count
            for item in prompt_sets):
        raise PersonCutoutError("SAM2 提示帧超出源视频范围")

    python = sam2_runtime_python()
    checkpoint = sam2_model_path()
    if not sam2_runtime_is_ready() or not _file_has_sha256(checkpoint, SAM2_MODEL_SHA256):
        raise PersonCutoutError("SAM2 跟踪组件或模型未准备：运行 cutvoke-person-model --sam2")
    progress_path = work_dir / "progress.json"
    selection_frame = prompt_sets[0]["frame"]
    total = max(1, frame_count - selection_frame)
    command = [
        str(python), "-m", "cutvoke.core.sam2_worker",
        "--frames-dir", str(frames_dir), "--masks-dir", str(masks_dir),
        "--checkpoint", str(checkpoint),
        "--prompt-sets-json", json.dumps(prompt_sets, separators=(",", ":")),
        "--progress-file", str(progress_path),
    ]
    env = os.environ.copy()
    package_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
    with tempfile.TemporaryFile() as error_log:
        try:
            process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=error_log, env=env,
            )
        except OSError as exc:
            raise PersonCutoutError(f"无法启动 SAM2 跟踪进程：{exc}") from exc
        last_progress = -1
        while process.poll() is None:
            if cancelled and cancelled():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise PersonCutoutCancelled("人物抠像已取消")
            if progress and progress_path.is_file():
                try:
                    state = json.loads(progress_path.read_text(encoding="utf-8"))
                    complete = int(state.get("completed", 0))
                    total = max(1, int(state.get("total", frame_count - selection_frame)))
                    percent = min(88, 8 + round(complete / total * 80))
                    if percent != last_progress:
                        progress(f"SAM2 跨帧跟踪（{state.get('device', 'GPU')}）", percent)
                        last_progress = percent
                except (OSError, ValueError, TypeError):
                    pass
            time.sleep(0.15)
        if process.returncode:
            error_log.seek(0)
            detail = error_log.read().decode("utf-8", errors="replace")[-1800:]
            raise PersonCutoutError(f"SAM2 人物跟踪失败：{detail or process.returncode}")
    return masks_dir


def _normalize_selection_prompts(selection_prompts) -> list[dict]:
    """Validate ordered source-time prompt frames for prompted person tracking."""
    if not isinstance(selection_prompts, list) or not 1 <= len(selection_prompts) <= 16:
        raise PersonCutoutError("人物提示帧数量必须在 1 到 16 帧之间")
    normalized_prompts = []
    previous_time = -1.0
    for prompt in selection_prompts:
        if isinstance(prompt, dict):
            at_seconds, points = prompt.get("atSeconds"), prompt.get("points")
        elif isinstance(prompt, (tuple, list)) and len(prompt) == 2:
            at_seconds, points = prompt
        else:
            raise PersonCutoutError("人物提示帧格式无效")
        if (isinstance(at_seconds, bool) or
                not isinstance(at_seconds, (int, float)) or
                not math.isfinite(at_seconds) or at_seconds < 0 or
                at_seconds <= previous_time):
            raise PersonCutoutError("人物提示帧时间必须是递增的非负有限数值")
        if not isinstance(points, (tuple, list)) or not 1 <= len(points) <= MAX_SELECTION_POINTS:
            raise PersonCutoutError(
                f"每帧人物提示点数量必须在 1 到 {MAX_SELECTION_POINTS} 个之间")
        normalized_points = []
        for point in points:
            if (not isinstance(point, (tuple, list)) or len(point) != 3 or
                    any(isinstance(value, bool) or not isinstance(value, (int, float)) or
                        not math.isfinite(value) or not 0 <= value <= 1
                        for value in point[:2]) or
                    isinstance(point[2], bool) or point[2] not in (0, 1)):
                raise PersonCutoutError(
                    "人物提示点必须是归一化坐标，标签为正向 1 或排除 0")
            normalized_points.append((float(point[0]), float(point[1]), int(point[2])))
        if not any(point[2] == 1 for point in normalized_points):
            raise PersonCutoutError("每帧人物提示至少需要一个正向点")
        normalized_prompts.append({
            "atSeconds": float(at_seconds), "points": normalized_points,
        })
        previous_time = float(at_seconds)
    return normalized_prompts


def generate_person_cutout(
    source_path: str | Path,
    output_path: str | Path,
    *,
    model_file: str | Path | None = None,
    edge_softness: float = 1.2,
    selection_point: tuple[float, float] | None = None,
    selection_points: list[tuple[float, float, int]] | None = None,
    selection_at_seconds: float = 0.0,
    selection_prompts: list[dict] | None = None,
    tracking_mode: str = "semantic",
    progress: Callable[[str, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Create a transparent ProRes 4444 MOV while preserving source audio."""
    source = Path(source_path).resolve()
    target = Path(output_path).resolve()
    model = Path(model_file or model_path()).resolve()
    interactive_model = interactive_model_path()
    if tracking_mode not in ("semantic", "magic_touch", "sam2"):
        raise PersonCutoutError("人物跟踪方式无效")
    if (tracking_mode in ("magic_touch", "sam2") and selection_point is None and
            not selection_points and not selection_prompts):
        raise PersonCutoutError("点提示跟踪需要先点选一个人物")
    if not source.is_file():
        raise PersonCutoutError("源视频文件不可用")
    if tracking_mode != "sam2" and not model.is_file():
        raise PersonCutoutError("人物分割模型未准备")
    if tracking_mode == "magic_touch" and not _interactive_model_is_valid(interactive_model):
        raise PersonCutoutError("MagicTouch 模型缺失或校验失败：运行 cutvoke-person-model --interactive")
    if not 0 <= float(edge_softness) <= 8:
        raise PersonCutoutError("边缘柔化值必须在 0 到 8 像素之间")
    if selection_point is not None:
        if (len(selection_point) != 2 or any(isinstance(value, bool) or
                not isinstance(value, (int, float)) or not math.isfinite(value) or
                not 0 <= value <= 1 for value in selection_point)):
            raise PersonCutoutError("人物选择坐标必须是 0 到 1 之间的有限数值")
        if (isinstance(selection_at_seconds, bool) or
                not isinstance(selection_at_seconds, (int, float)) or
                not math.isfinite(selection_at_seconds) or selection_at_seconds < 0):
            raise PersonCutoutError("人物选择时间必须是非负有限数值")
    if selection_prompts is not None:
        normalized_prompts = _normalize_selection_prompts(selection_prompts)
    elif selection_points is not None or selection_point is not None:
        legacy_points = selection_points or [
            (float(selection_point[0]), float(selection_point[1]), 1)]
        normalized_prompts = _normalize_selection_prompts([{
            "atSeconds": selection_at_seconds, "points": legacy_points,
        }])
    else:
        normalized_prompts = []
    if normalized_prompts:
        if tracking_mode != "sam2" and len(normalized_prompts) > 1:
            raise PersonCutoutError("多帧提示需要选择 SAM2 视频跟踪方式")
        selection_at_seconds = normalized_prompts[0]["atSeconds"]
        selection_points = normalized_prompts[0]["points"]
        selection_point = next((point[:2] for point in selection_points
                                if point[2] == 1))
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise PersonCutoutError("找不到 ffmpeg；请先安装 FFmpeg 并加入 PATH")
    media = _probe_video(source)
    rate = str(media["fps"])
    try:
        fps = float(Fraction(rate))
    except (ValueError, ZeroDivisionError):
        fps = 0
    if fps <= 0 or fps > 120:
        raise PersonCutoutError("源视频帧率无效或超过 120 fps")

    try:
        import cv2
        import numpy as np
        mp = None
        if tracking_mode != "sam2":
            import mediapipe as mp
    except (ImportError, OSError) as exc:
        raise PersonCutoutError("人物抠像组件未安装：请安装 cutvoke[person]") from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise PersonCutoutError("透明视频输出路径已存在，请重新提交任务")
    video_only = target.with_name(f".{target.stem}.video-only.mov")
    muxed = target.with_name(f".{target.stem}.building.mov")
    for temporary in (video_only, muxed):
        if temporary.exists():
            temporary.unlink()

    capture = cv2.VideoCapture(str(source), cv2.CAP_FFMPEG)
    if not capture.isOpened():
        raise PersonCutoutError("无法解码源视频")
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    expected_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if width < 16 or height < 16 or expected_frames <= 0:
        capture.release()
        raise PersonCutoutError("源视频尺寸或帧数无效")
    selection_frame = int(round(float(selection_at_seconds) * fps))
    if selection_point is not None and selection_frame >= expected_frames:
        capture.release()
        raise PersonCutoutError("人物选择时间超出源视频范围")

    output_rate = str(media["fps"])
    encoder = None
    frame_count = 0
    selection_started = selection_point is None
    selected_mask = None
    selected_center = None
    selected_frames = 0
    lost_frames = 0
    max_components = 0
    previous_prompt_gray = None
    previous_prompt_point = None
    prompt_tracking_lost_at = None
    error_log = tempfile.TemporaryFile()
    sam2_work_dir: Path | None = None
    try:
        if progress:
            progress("正在载入人物分割模型", 1)
        sam2_masks_dir = None
        if tracking_mode == "sam2":
            if not availability().get("sam2Ready"):
                raise PersonCutoutError(
                    "SAM2 跟踪组件或模型未准备：运行 cutvoke-person-model --sam2")
            sam2_work_dir = Path(tempfile.mkdtemp(prefix="cutvoke-sam2-"))
            sam2_prompt_sets = [{
                "frame": int(round(prompt["atSeconds"] * fps)),
                "points": [list(point) for point in prompt["points"]],
            } for prompt in normalized_prompts]
            sam2_masks_dir = _generate_sam2_masks(
                source, width, height, expected_frames, sam2_prompt_sets,
                sam2_work_dir, progress=progress, cancelled=cancelled,
            )
            capture.release()
            capture = cv2.VideoCapture(str(source), cv2.CAP_FFMPEG)
            if not capture.isOpened():
                raise PersonCutoutError("SAM2 处理完成后无法重新解码源视频")
        else:
            vision = mp.tasks.vision
            options = vision.ImageSegmenterOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(model)),
                running_mode=vision.RunningMode.VIDEO,
                output_category_mask=True,
                output_confidence_masks=True,
            )
        encoder_command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pixel_format", "rgba",
            "-video_size", f"{width}x{height}", "-framerate", output_rate,
            "-i", "pipe:0", "-an", "-c:v", "prores_ks", "-profile:v", "4",
            "-pix_fmt", "yuva444p10le", str(video_only),
        ]
        encoder = subprocess.Popen(
            encoder_command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=error_log,
        )
        with contextlib.ExitStack() as stack:
            segmenter = (stack.enter_context(
                vision.ImageSegmenter.create_from_options(options))
                if tracking_mode != "sam2" else None)
            prompt_segmenter = None
            if tracking_mode == "magic_touch":
                from mediapipe.tasks.python.components.containers.keypoint import NormalizedKeypoint

                prompt_options = vision.InteractiveSegmenterLegacyOptions(
                    base_options=mp.tasks.BaseOptions(
                        model_asset_path=str(interactive_model)),
                    output_confidence_masks=True,
                    output_category_mask=False,
                )
                prompt_segmenter = stack.enter_context(
                    vision.InteractiveSegmenterLegacy.create_from_options(prompt_options))

            def prompted_confidence(image, point):
                roi = vision.InteractiveSegmenterLegacyRegionOfInterest(
                    format=vision.InteractiveSegmenterLegacyRegionOfInterest.Format.KEYPOINT,
                    keypoint=NormalizedKeypoint(x=float(point[0]), y=float(point[1])),
                )
                result = prompt_segmenter.segment(image, roi)
                if not result.confidence_masks:
                    raise PersonCutoutError("MagicTouch 没有返回人物选区掩码")
                prompted = np.squeeze(
                    result.confidence_masks[0].numpy_view()).astype(np.float32)
                if prompted.shape != (height, width):
                    prompted = cv2.resize(prompted, (width, height),
                                          interpolation=cv2.INTER_LINEAR)
                if not np.isfinite(prompted).all():
                    raise PersonCutoutError("MagicTouch 返回了无效掩码")
                return np.clip(prompted, 0, 1)

            while True:
                if cancelled and cancelled():
                    raise PersonCutoutCancelled("人物抠像已取消")
                ok, bgr = capture.read()
                if not ok:
                    break
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                if tracking_mode == "sam2":
                    mask_path = sam2_masks_dir / f"{frame_count:08d}.png"
                    mask_image = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
                    if mask_image is None:
                        confidence = np.zeros((height, width), dtype=np.float32)
                        lost_frames += 1
                    else:
                        if mask_image.shape != (height, width):
                            mask_image = cv2.resize(mask_image, (width, height),
                                                    interpolation=cv2.INTER_LINEAR)
                        confidence = mask_image.astype(np.float32) / 255.0
                        if np.count_nonzero(confidence >= 0.5) >= 64:
                            selected_frames += 1
                        else:
                            confidence.fill(0)
                            lost_frames += 1
                    alpha = np.clip((confidence - 0.08) / 0.72, 0, 1)
                    softness = float(edge_softness)
                    if softness > 0:
                        kernel = max(3, int(round(softness * 4)) | 1)
                        alpha = cv2.GaussianBlur(alpha, (kernel, kernel), softness)
                    alpha = np.clip(alpha * 255, 0, 255).astype(np.uint8)
                    rgba = np.empty((height, width, 4), dtype=np.uint8)
                    rgba[:, :, :3] = rgb
                    rgba[:, :, 3] = alpha
                    rgba[alpha == 0, :3] = 0
                    try:
                        encoder.stdin.write(np.ascontiguousarray(rgba).tobytes())
                    except (BrokenPipeError, OSError) as exc:
                        error_log.seek(0)
                        detail = error_log.read().decode("utf-8", errors="replace")[-1200:]
                        raise PersonCutoutError(f"透明视频编码失败：{detail or exc}") from exc
                    frame_count += 1
                    if progress and (frame_count == 1 or frame_count % 5 == 0):
                        percent = min(90, 2 + round(frame_count / expected_frames * 88))
                        progress("正在编码 SAM2 抠像视频", percent)
                    continue
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                timestamp_ms = int(round(frame_count * 1000 / fps))
                result = segmenter.segment_for_video(image, timestamp_ms)
                if not result.confidence_masks:
                    raise PersonCutoutError("人物分割模型没有返回置信度掩码")
                confidence = np.squeeze(
                    result.confidence_masks[0].numpy_view()).astype(np.float32)
                if confidence.shape != (height, width):
                    confidence = cv2.resize(confidence, (width, height),
                                            interpolation=cv2.INTER_LINEAR)
                if not np.isfinite(confidence).all():
                    raise PersonCutoutError("人物分割模型返回了无效掩码")
                if selection_point is None:
                    confidence = _keep_large_components(confidence, cv2, np)
                elif frame_count < selection_frame:
                    confidence = _keep_large_components(confidence, cv2, np)
                elif frame_count == selection_frame:
                    if tracking_mode == "magic_touch":
                        prompt_mask = prompted_confidence(image, selection_point)
                        chosen = _select_prompted_person(
                            prompt_mask, confidence, selection_point, cv2, np)
                    else:
                        chosen = _select_person_component(
                            confidence, cv2, np, selection_point)
                    if chosen is None:
                        raise PersonCutoutError(
                            "所选位置附近未检测到人物；请在片段源入点的人物主体上点选")
                    confidence, selected_mask, selected_center, component_count = chosen
                    max_components = max(max_components, component_count)
                    selection_started = True
                    selected_frames += 1
                    if tracking_mode == "magic_touch":
                        previous_prompt_gray = gray
                        previous_prompt_point = selection_point
                elif selection_started and selected_mask is not None:
                    if tracking_mode == "magic_touch":
                        can_reacquire = _prompt_reacquisition_allowed(
                            frame_count, prompt_tracking_lost_at, fps)
                        tracked_point = (_track_prompt_point(
                            previous_prompt_gray, gray, selected_mask,
                            previous_prompt_point, cv2, np) if can_reacquire else None)
                        if tracked_point is None:
                            if prompt_tracking_lost_at is None:
                                prompt_tracking_lost_at = frame_count
                            tracked = None
                        else:
                            prompt_mask = prompted_confidence(image, tracked_point)
                            tracked = _select_prompted_person(
                                prompt_mask, confidence, tracked_point, cv2, np)
                            if tracked is None:
                                if prompt_tracking_lost_at is None:
                                    prompt_tracking_lost_at = frame_count
                            else:
                                previous_prompt_gray = gray
                                previous_prompt_point = tracked_point
                                prompt_tracking_lost_at = None
                    else:
                        tracked = _track_person_component(
                            confidence, cv2, np, selected_mask, selected_center)
                    if tracked is None:
                        confidence = np.zeros_like(confidence)
                        lost_frames += 1
                    else:
                        confidence, selected_mask, selected_center, component_count = tracked
                        max_components = max(max_components, component_count)
                        selected_frames += 1
                else:
                    confidence = _keep_large_components(confidence, cv2, np)
                alpha = np.clip((confidence - 0.08) / 0.72, 0, 1)
                softness = float(edge_softness)
                if softness > 0:
                    kernel = max(3, int(round(softness * 4)) | 1)
                    alpha = cv2.GaussianBlur(alpha, (kernel, kernel), softness)
                alpha = np.clip(alpha * 255, 0, 255).astype(np.uint8)
                rgba = np.empty((height, width, 4), dtype=np.uint8)
                rgba[:, :, :3] = rgb
                rgba[:, :, 3] = alpha
                rgba[alpha == 0, :3] = 0
                try:
                    encoder.stdin.write(np.ascontiguousarray(rgba).tobytes())
                except (BrokenPipeError, OSError) as exc:
                    error_log.seek(0)
                    detail = error_log.read().decode("utf-8", errors="replace")[-1200:]
                    raise PersonCutoutError(f"透明视频编码失败：{detail or exc}") from exc
                frame_count += 1
                if progress and (frame_count == 1 or frame_count % 5 == 0):
                    percent = min(90, 2 + round(frame_count / expected_frames * 88))
                    progress("正在逐帧分割人物", percent)
        capture.release()
        encoder.stdin.close()
        code = encoder.wait(timeout=1800)
        encoder = None
        if code:
            error_log.seek(0)
            detail = error_log.read().decode("utf-8", errors="replace")[-1200:]
            raise PersonCutoutError(f"透明视频编码失败：{detail or code}")
        if frame_count <= 0 or not video_only.is_file():
            raise PersonCutoutError("源视频没有可处理的画面")

        duration = frame_count / fps
        if progress:
            progress("正在保留源音频并封装透明视频", 94)
        mux_command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video_only), "-i", str(source),
            "-map", "0:v:0", "-map", "1:a?", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.9f}",
            "-movflags", "+faststart", str(muxed),
        ]
        _run_ffmpeg(mux_command, error_log, stage="透明视频封装")
        result = _probe_video(muxed)
        pixel_format = str(result["video"].get("pix_fmt", ""))
        if not pixel_format.startswith("yuva"):
            raise PersonCutoutError(
                f"透明通道未能写入导出文件（像素格式：{pixel_format or 'unknown'}）")
        if media["hasAudio"] and not result["hasAudio"]:
            raise PersonCutoutError("透明视频封装时未能保留源音频")
        os.replace(muxed, target)
        if progress:
            progress("人物抠像完成", 100)
        return {
            "outputPath": str(target),
            "frameCount": frame_count,
            "fps": output_rate,
            "durationSeconds": round(duration, 6),
            "audioPreserved": bool(media["hasAudio"]),
            "videoCodec": "prores_ks",
            "pixelFormat": pixel_format,
            "alpha": True,
            "selectionMode": "selected_person" if selection_point is not None else "all_people",
            "trackingMode": (tracking_mode if selection_point is not None else "semantic"),
            "selectionAtSeconds": (round(float(selection_at_seconds), 6)
                                    if selection_point is not None else None),
            "selectedFrames": selected_frames if selection_point is not None else None,
            "lostFrames": lost_frames if selection_point is not None else None,
            "maxSeparatedComponents": max_components if selection_point is not None else None,
        }
    except Exception:
        capture.release()
        if encoder is not None:
            try:
                encoder.kill()
                encoder.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
        for temporary in (video_only, muxed, target):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    finally:
        error_log.close()
        if video_only.exists():
            video_only.unlink(missing_ok=True)
        if sam2_work_dir is not None:
            shutil.rmtree(sam2_work_dir, ignore_errors=True)


class PersonCutoutJobManager:
    """Bounded in-process jobs for model loading and frame-by-frame rendering."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._slot = threading.Semaphore(1)
        self._closed = threading.Event()
        self._jobs: dict[str, dict] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._cancel: dict[str, threading.Event] = {}

    def submit(self, project: Project, clip_id: str, media_dir: str | Path,
               *, edge_softness: float = 1.2,
               selection_mode: str = "all",
               selection_point: tuple[float, float] | None = None,
               selection_points: list[tuple[float, float, int]] | None = None,
               selection_at_seconds: float = 0.0,
               selection_prompts: list[dict] | None = None,
               tracking_mode: str = "semantic") -> dict:
        status = availability()
        if tracking_mode == "sam2":
            if not status.get("sam2Ready"):
                raise PersonCutoutError(
                    "SAM2 跟踪组件或模型未准备：运行 cutvoke-person-model --sam2")
        elif not status.get("mediapipeReady", status["ready"]):
            raise PersonCutoutError(
                status["message"] if not status.get("sam2Ready") else
                "MediaPipe 人物分割未就绪；运行 cutvoke-person-model，或切换到 SAM2 视频身份跟踪")
        if not 0 <= float(edge_softness) <= 8:
            raise PersonCutoutError("边缘柔化值必须在 0 到 8 像素之间")
        if selection_mode not in ("all", "selected"):
            raise PersonCutoutError("人物选择模式无效")
        if tracking_mode not in ("semantic", "magic_touch", "sam2"):
            raise PersonCutoutError("人物跟踪方式无效")
        if tracking_mode in ("magic_touch", "sam2"):
            if selection_mode != "selected":
                raise PersonCutoutError("点提示跟踪仅适用于点选人物")
        if tracking_mode == "magic_touch":
            if not status.get("interactiveModelReady"):
                raise PersonCutoutError(
                    "MagicTouch 模型未准备：运行 cutvoke-person-model --interactive")
        if selection_mode == "selected":
            if selection_prompts is None:
                if selection_points is None:
                    selection_points = ([(selection_point[0], selection_point[1], 1)]
                                        if isinstance(selection_point, tuple) and
                                        len(selection_point) == 2 else None)
                if selection_points is None:
                    raise PersonCutoutError("请在片段预览画面中点选一个人物")
                selection_prompts = [{
                    "atSeconds": selection_at_seconds,
                    "points": selection_points,
                }]
            normalized_prompts = _normalize_selection_prompts(selection_prompts)
            if tracking_mode != "sam2" and len(normalized_prompts) > 1:
                raise PersonCutoutError("多帧纠正提示需要选择 SAM2 视频身份跟踪")
            if tracking_mode != "sam2" and len(normalized_prompts[0]["points"]) > 1:
                raise PersonCutoutError("多点提示需要选择 SAM2 视频跟踪方式")
            selection_at_seconds = normalized_prompts[0]["atSeconds"]
            selection_points = normalized_prompts[0]["points"]
            if not isinstance(selection_points, list):
                raise PersonCutoutError("请在片段源入点预览画面中点选一个人物")
            selection_point = next((point[:2] for point in selection_points if point[2] == 1))
        else:
            selection_point = None
            selection_points = None
            selection_at_seconds = 0.0
            normalized_prompts = []
        clip = find_video_clip(project, clip_id)
        if selection_mode == "selected":
            source_start = clip.source_start.to_fraction()
            source_end = source_start + clip.consumed_source_duration.to_fraction()
            if any(not (source_start <= Fraction(str(prompt["atSeconds"])) < source_end)
                   for prompt in normalized_prompts):
                raise PersonCutoutError("人物提示帧必须位于所选片段的源素材范围内")
        signature = source_signature(clip)
        source = clip.asset_ref.source_path
        snapshot = copy.deepcopy(clip)
        with self._lock:
            if self._closed.is_set():
                raise PersonCutoutError("人物抠像服务已关闭")
            pending = sum(job["status"] in ("queued", "running", "applying")
                          for job in self._jobs.values())
            self._threads = {key: value for key, value in self._threads.items()
                             if value.is_alive()}
            if pending >= MAX_PENDING_JOBS or len(self._threads) >= MAX_PENDING_JOBS:
                raise PersonCutoutError("已有两个抠像任务在等待或运行，请稍后重试")
            job_id = "person_" + uuid.uuid4().hex
            output = (Path(media_dir).resolve() / ".person-cutouts" /
                      f"{job_id}.mov")
            job = {
                "jobId": job_id, "projectId": project.project_id,
                "clipId": clip_id, "sourceSignature": signature,
                "status": "queued", "phase": "排队中", "progress": 0,
                "error": "", "result": None, "createdAt": time.time(),
                "selectionMode": "selected_person" if selection_mode == "selected" else "all_people",
                "trackingMode": tracking_mode,
                "selectionPoint": list(selection_point) if selection_point else None,
                "selectionPoints": [list(point) for point in selection_points]
                                    if selection_points else None,
                "selectionAtSeconds": (float(selection_at_seconds)
                                       if selection_mode == "selected" else None),
                "selectionPrompts": ([{
                    "atSeconds": prompt["atSeconds"],
                    "points": [list(point) for point in prompt["points"]],
                } for prompt in normalized_prompts] if normalized_prompts else None),
            }
            cancel = threading.Event()
            self._jobs[job_id] = job
            self._cancel[job_id] = cancel
            thread = threading.Thread(
                target=self._run,
                args=(job_id, snapshot, source, model_path(), output,
                      float(edge_softness), selection_point, selection_points,
                      float(selection_at_seconds), normalized_prompts,
                      tracking_mode, cancel),
                daemon=True,
                name=f"cutvoke-{job_id}",
            )
            self._threads[job_id] = thread
            thread.start()
        return self.get(job_id) or {}

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job is not None else None

    def cancel(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job["status"] in ("queued", "running"):
                self._cancel[job_id].set()
                job.update(status="cancelled", phase="已取消", error="")
            return copy.deepcopy(job)

    def claim_apply(self, job_id: str, project_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if (job is None or job["projectId"] != project_id or
                    job["status"] != "completed"):
                return None
            job.update(status="applying", phase="正在应用到时间线")
            return copy.deepcopy(job)

    def finish_apply(self, job_id: str, *, error: str = "") -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if error:
                job.update(status="completed", phase="已生成透明片段，可重试应用",
                           error=error)
            else:
                job.update(status="applied", phase="已应用；可撤销恢复原片",
                           progress=100, error="")
            return copy.deepcopy(job)

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            active = [key for key, job in self._jobs.items()
                      if job["status"] in ("queued", "running")]
            threads = [self._threads.get(key) for key in active]
            for key in active:
                self._cancel[key].set()
                self._jobs[key].update(status="cancelled", phase="服务已关闭")
        for thread in threads:
            if thread is not None:
                thread.join(timeout=1)

    def _update(self, job_id: str, **changes: object) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and job["status"] not in ("cancelled", "applied"):
                job.update(changes)

    def _run(self, job_id: str, clip: Clip, source: str, model: Path,
             output: Path, edge_softness: float,
             selection_point: tuple[float, float] | None,
             selection_points: list[tuple[float, float, int]] | None,
             selection_at_seconds: float,
             selection_prompts: list[dict],
             tracking_mode: str,
             cancel: threading.Event) -> None:
        with self._slot:
            if cancel.is_set() or self._closed.is_set():
                return
            self._update(job_id, status="running", phase="准备人物分割", progress=1)
            try:
                def report(phase: str, percent: int) -> None:
                    if cancel.is_set() or self._closed.is_set():
                        raise PersonCutoutCancelled("人物抠像已取消")
                    self._update(job_id, phase=phase, progress=percent)

                result = generate_person_cutout(
                    source, output, model_file=model,
                    edge_softness=edge_softness, progress=report,
                    selection_point=selection_point,
                    selection_points=selection_points,
                    selection_at_seconds=selection_at_seconds,
                    selection_prompts=selection_prompts or None,
                    tracking_mode=tracking_mode,
                    cancelled=cancel.is_set,
                )
                if cancel.is_set() or self._closed.is_set():
                    Path(result["outputPath"]).unlink(missing_ok=True)
                    return
                with self._lock:
                    job = self._jobs[job_id]
                    if job["status"] == "cancelled":
                        Path(result["outputPath"]).unlink(missing_ok=True)
                        return
                    job.update(status="completed", phase="透明片段已生成",
                               progress=100, result=result)
            except PersonCutoutCancelled:
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job is not None:
                        job.update(status="cancelled", phase="已取消", error="")
                output.unlink(missing_ok=True)
            except Exception as exc:
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job is not None and job["status"] != "cancelled":
                        job.update(status="failed", phase="抠像失败", error=str(exc))
                output.unlink(missing_ok=True)
