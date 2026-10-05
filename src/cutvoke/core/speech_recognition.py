"""Local speech recognition for editable, time-aligned project captions.

The optional faster-whisper dependency and model are loaded only after a user
starts recognition. Silence segmentation remains a separate, explicit tool.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Callable

from .model import Clip, Project
from .rational import Rational
from .render import DEFAULT_FFMPEG


MODEL_CHOICES = ("tiny", "base", "small", "medium")
ASR_DEVICE_CHOICES = ("auto", "cpu", "cuda")
LANGUAGE_CHOICES = ("auto", "zh", "en", "ja", "ko")
Progress = Callable[[str, int], None]


class SpeechRecognitionError(RuntimeError):
    pass


def _cuda_device_count() -> int:
    try:
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def _resolve_asr_runtime(preference: str, *, cuda_device_count: int | None = None
                         ) -> tuple[str, str]:
    if preference not in ASR_DEVICE_CHOICES:
        raise SpeechRecognitionError(
            "CUTVOKE_ASR_DEVICE 只支持 auto、cpu 或 cuda"
        )
    if preference == "cpu":
        return "cpu", "int8"
    count = _cuda_device_count() if cuda_device_count is None else cuda_device_count
    if preference == "cuda":
        if count <= 0:
            raise SpeechRecognitionError("未检测到可用的 CUDA 设备")
        return "cuda", "float16"
    return ("cuda", "float16") if count > 0 else ("cpu", "int8")


def _load_whisper_model(model_class, model_size: str, model_dir: str | None,
                        preference: str, report: Progress | None
                        ) -> tuple[object, str, str]:
    device, compute_type = _resolve_asr_runtime(preference)
    try:
        model = model_class(model_size, device=device, compute_type=compute_type,
                            download_root=model_dir)
        return model, device, compute_type
    except Exception as exc:
        if preference != "auto" or device != "cuda":
            raise SpeechRecognitionError(f"识别模型加载失败：{exc}") from exc
        if report is not None:
            report("GPU 初始化失败，正在回退到 CPU", 8)
        try:
            model = model_class(model_size, device="cpu", compute_type="int8",
                                download_root=model_dir)
            return model, "cpu", "int8"
        except Exception as cpu_exc:
            raise SpeechRecognitionError(
                f"GPU 与 CPU 识别模型均加载失败：{cpu_exc}"
            ) from cpu_exc


def _transcribe_with_model(model, audio: str, clip: Clip, duration: float,
                           language: str, report: Progress) -> tuple[list[dict], object]:
    raw_segments, info = model.transcribe(
        audio, language=None if language == "auto" else language,
        beam_size=5, vad_filter=True, word_timestamps=True,
        condition_on_previous_text=False,
    )
    detected_language = getattr(info, "language", language)
    normalize_chinese = detected_language == "zh" or language == "zh"
    if normalize_chinese:
        if importlib.util.find_spec("opencc") is None:
            raise SpeechRecognitionError(
                "中文识别规范化组件待配置：请安装 cutvoke[asr]"
            )
        from opencc import OpenCC
        simplify_chinese = OpenCC("tw2s.json").convert
    else:
        simplify_chinese = lambda value: value
    timeline_start = float(clip.timeline_start.to_fraction())
    timeline_end = float(clip.timeline_end.to_fraction())
    speed = float(clip.speed.to_fraction())

    def on_timeline(source_seconds: float) -> float:
        if clip.speed_curve is not None:
            return timeline_start + clip.speed_curve.timeline_at_source(source_seconds)
        return timeline_start + source_seconds / speed

    proposals: list[dict] = []
    for segment in raw_segments:
        text = simplify_chinese(str(segment.text)).strip()
        start = max(0.0, float(segment.start))
        end = min(duration, float(segment.end))
        if not text or end <= start:
            continue
        start_time = min(timeline_end, on_timeline(start))
        end_time = min(timeline_end, on_timeline(end))
        if end_time <= start_time:
            continue
        raw_words = segment.words or []
        raw_word_text = "".join(str(word.word) for word in raw_words)
        normalized_word_text = simplify_chinese(raw_word_text)
        preserve_word_offsets = len(normalized_word_text) == len(raw_word_text)
        word_offset = 0
        words = []
        for word in raw_words:
            if word.start is None or word.end is None:
                word_offset += len(str(word.word))
                continue
            word_start = min(timeline_end, on_timeline(max(0.0, float(word.start))))
            word_end = min(timeline_end, on_timeline(min(duration, float(word.end))))
            if word_end > word_start:
                raw_text = str(word.word)
                if preserve_word_offsets:
                    word_text = normalized_word_text[
                        word_offset:word_offset + len(raw_text)
                    ]
                else:
                    word_text = simplify_chinese(raw_text)
                words.append({"text": word_text,
                              "start": Rational.from_float(word_start).to_json(),
                              "end": Rational.from_float(word_end).to_json()})
            word_offset += len(str(word.word))
        proposals.append({
            "index": len(proposals) + 1,
            "text": text,
            "start": Rational.from_float(start_time).to_json(),
            "end": Rational.from_float(end_time).to_json(),
            "words": words,
        })
        report("识别语音", min(95, 15 + int(80 * end / duration)))
    return proposals, info


def availability() -> dict:
    whisper_installed = importlib.util.find_spec("faster_whisper") is not None
    opencc_installed = importlib.util.find_spec("opencc") is not None
    installed = whisper_installed and opencc_installed
    device_preference = os.environ.get("CUTVOKE_ASR_DEVICE", "auto").strip().lower()
    device_configured = device_preference in ASR_DEVICE_CHOICES
    if not whisper_installed:
        message = "自动识别待配置：请安装 cutvoke[asr]。"
    elif not opencc_installed:
        message = "中文识别规范化组件待配置：请重新安装 cutvoke[asr]。"
    elif not device_configured:
        message = "CUTVOKE_ASR_DEVICE 只支持 auto、cpu 或 cuda。"
    else:
        message = "首次识别会下载所选模型；也可设置 CUTVOKE_ASR_MODEL_DIR 指定模型缓存。"
    return {
        "installed": installed,
        "ready": installed and device_configured,
        "defaultModel": "base",
        "models": list(MODEL_CHOICES),
        "devicePreference": device_preference,
        "devices": list(ASR_DEVICE_CHOICES),
        "languages": list(LANGUAGE_CHOICES),
        "message": message,
    }


def find_source_clip(project: Project, clip_id: str) -> Clip:
    for track in project.sequence.tracks:
        if track.kind not in ("audio", "video"):
            continue
        for clip in track.clips:
            if clip.id == clip_id:
                if clip.nested is not None:
                    raise SpeechRecognitionError("请先展开复合片段，再识别其中的有声素材")
                if not clip.asset_ref.source_path:
                    raise SpeechRecognitionError("所选片段没有源文件")
                if not os.path.isfile(clip.asset_ref.source_path):
                    raise SpeechRecognitionError("所选片段的源文件不可用，请先重新链接素材")
                return clip
    raise SpeechRecognitionError("请选择视频或音频片段进行识别")


def source_signature(clip: Clip) -> str:
    """Fingerprint the time mapping and source file used by a proposal."""
    path = Path(clip.asset_ref.source_path)
    stat = path.stat()
    state = {
        "clipId": clip.id,
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtimeNs": stat.st_mtime_ns,
        "timelineStart": clip.timeline_start.to_json(),
        "timelineEnd": clip.timeline_end.to_json(),
        "sourceStart": clip.source_start.to_json(),
        "speed": clip.speed.to_json(),
        "speedCurve": clip.speed_curve.to_dict() if clip.speed_curve else None,
    }
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode("utf-8")).hexdigest()


def _extract_audio(clip: Clip, output: str, ffmpeg: str) -> float:
    timeline_duration = float((clip.timeline_end - clip.timeline_start).to_fraction())
    speed = float(clip.speed.to_fraction())
    source_duration = float(clip.consumed_source_duration.to_fraction())
    if source_duration <= 0 or speed <= 0:
        raise SpeechRecognitionError("片段时长或速度无效")
    source_start = float(clip.source_start.to_fraction())
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{source_start:.6f}", "-i", clip.asset_ref.source_path,
        "-t", f"{source_duration:.6f}", "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", output,
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=max(90, min(3600, source_duration * 4)))
    except subprocess.TimeoutExpired as exc:
        raise SpeechRecognitionError("提取音频超时") from exc
    if completed.returncode != 0 or not os.path.isfile(output):
        raise SpeechRecognitionError("所选片段没有可识别的音频流")
    with wave.open(output, "rb") as wav:
        duration = wav.getnframes() / wav.getframerate()
    if duration < 0.1:
        raise SpeechRecognitionError("所选片段的音频过短")
    return min(source_duration, duration)


def transcribe_clip(project: Project, clip_id: str, *, model_size: str = "base",
                    language: str = "auto", progress: Progress | None = None,
                    ffmpeg: str = DEFAULT_FFMPEG) -> dict:
    """Recognize one clip and map source timestamps to its timeline range.

    Returns proposals only. Applying them is a separate atomic edit command.
    """
    if model_size not in MODEL_CHOICES:
        raise SpeechRecognitionError(f"不支持的识别模型：{model_size}")
    if language not in LANGUAGE_CHOICES:
        raise SpeechRecognitionError(f"不支持的识别语言：{language}")
    device_preference = os.environ.get("CUTVOKE_ASR_DEVICE", "auto").strip().lower()
    if device_preference not in ASR_DEVICE_CHOICES:
        raise SpeechRecognitionError(
            "CUTVOKE_ASR_DEVICE 只支持 auto、cpu 或 cuda"
        )
    clip = find_source_clip(project, clip_id)
    signature = source_signature(clip)
    if importlib.util.find_spec("faster_whisper") is None:
        raise SpeechRecognitionError("自动识别待配置：请安装 cutvoke[asr]")
    from faster_whisper import WhisperModel

    def report(phase: str, percent: int) -> None:
        if progress is not None:
            progress(phase, percent)

    report("提取片段音频", 2)
    with tempfile.TemporaryDirectory(prefix="cutvoke_asr_") as temp_dir:
        audio = os.path.join(temp_dir, "source.wav")
        duration = _extract_audio(clip, audio, ffmpeg)
        report("加载识别模型", 8)
        model_dir = os.environ.get("CUTVOKE_ASR_MODEL_DIR") or None
        model, device, compute_type = _load_whisper_model(
            WhisperModel, model_size, model_dir, device_preference, progress
        )
        report("识别语音", 15)
        try:
            proposals, info = _transcribe_with_model(
                model, audio, clip, duration, language, report
            )
        except SpeechRecognitionError:
            raise
        except Exception as exc:
            if device_preference != "auto" or device != "cuda":
                raise SpeechRecognitionError(f"语音识别失败：{exc}") from exc
            report("GPU 推理失败，正在回退到 CPU", 8)
            model, device, compute_type = _load_whisper_model(
                WhisperModel, model_size, model_dir, "cpu", progress
            )
            report("识别语音", 15)
            try:
                proposals, info = _transcribe_with_model(
                    model, audio, clip, duration, language, report
                )
            except SpeechRecognitionError:
                raise
            except Exception as cpu_exc:
                raise SpeechRecognitionError(f"语音识别失败：{cpu_exc}") from cpu_exc

    report("识别完成", 100)
    return {
        "clipId": clip_id,
        "sourceSignature": signature,
        "model": model_size,
        "device": device,
        "computeType": compute_type,
        "language": getattr(info, "language", language),
        "duration": round(duration, 3),
        "segments": proposals,
        "segmentCount": len(proposals),
    }
