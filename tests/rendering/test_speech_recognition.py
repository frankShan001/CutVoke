"""ASR proposals map to timeline time and commit as editable captions."""

from __future__ import annotations

import importlib.util
import json
import math
import os
import shutil
import subprocess
import struct
import tempfile
import time
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from cutvoke.interfaces.httpapi import HttpApi
from cutvoke.domain.model import AssetReference, Clip, Rational
from cutvoke.domain.protocol import Actor, Command, ErrorCode
from cutvoke.rendering.render import RenderService
from cutvoke.editing.service import EditError, EditService
from cutvoke.rendering.speech_recognition import (
    SpeechRecognitionError,
    availability,
    _extract_audio,
    _load_whisper_model,
    _resolve_asr_runtime,
    transcribe_clip,
)
from cutvoke.storage.store import ProjectStore


def _rat(value: int) -> dict[str, str]:
    return {"num": str(value), "den": "1"}


def _edit(service: EditService, kind: str, payload: dict, number: int):
    project = service.get_project("speech")
    return service.execute(Command(
        type=kind, payload=payload, command_id=f"speech-{number}",
        project_id="speech", expected_revision=project.revision,
        actor=Actor("human", "speech-test"),
    ))


class _FakeModel:
    def __init__(self, model: str, **kwargs) -> None:
        assert model == "tiny"
        assert kwargs["compute_type"] == "int8"

    def transcribe(self, path: str, **kwargs):
        assert Path(path).is_file()
        assert kwargs["language"] == "en"
        assert kwargs["word_timestamps"] is True
        segment = SimpleNamespace(
            start=0.5, end=1.5, text=" Hello world",
            words=[SimpleNamespace(start=0.5, end=0.9, word="Hello"),
                   SimpleNamespace(start=0.9, end=1.5, word=" world")],
        )
        return iter([segment]), SimpleNamespace(language="en")


class _FakeChineseModel:
    def __init__(self, model: str, **kwargs) -> None:
        assert model == "tiny"
        assert kwargs["compute_type"] == "int8"

    def transcribe(self, path: str, **kwargs):
        assert Path(path).is_file()
        assert kwargs["language"] is None
        assert kwargs["word_timestamps"] is True
        segment = SimpleNamespace(
            start=0.5, end=1.5, text=" 大家好，今天我們沿著小路。",
            words=[
                SimpleNamespace(start=0.5, end=0.7, word=" 大家好，"),
                SimpleNamespace(start=0.7, end=0.8, word="今天"),
                SimpleNamespace(start=0.8, end=0.9, word="我們"),
                SimpleNamespace(start=0.9, end=1.0, word="沿"),
                SimpleNamespace(start=1.0, end=1.1, word="著"),
                SimpleNamespace(start=1.1, end=1.5, word="小路。"),
            ],
        )
        return iter([segment]), SimpleNamespace(language="zh")


class _CudaInferenceFailsModel:
    def __init__(self, model: str, *, device: str, compute_type: str,
                 download_root: str | None) -> None:
        assert model == "tiny"
        assert (device, compute_type) in (("cuda", "float16"), ("cpu", "int8"))
        self.device = device
        self.compute_type = compute_type

    def transcribe(self, path: str, **kwargs):
        if self.device == "cuda":
            raise RuntimeError("cublas runtime unavailable")
        return _FakeModel("tiny", device="cpu", compute_type="int8",
                          download_root=None).transcribe(path, **kwargs)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class SpeechRecognitionTests(unittest.TestCase):
    def test_extract_audio_uses_first_source_stream_not_default_disposition(self) -> None:
        for default in (0, 1):
            with self.subTest(default=default), tempfile.TemporaryDirectory() as temporary:
                master, extracted = Path(temporary) / "master.mp4", Path(temporary) / "source.wav"
                self._audio_source(master, (440, 880), default)
                probed = subprocess.run([
                    "ffprobe", "-v", "error", "-select_streams", "a",
                    "-show_entries", "stream=channels:stream_disposition=default:stream_tags=language",
                    "-of", "json", str(master),
                ], check=True, capture_output=True, timeout=15)
                audio = json.loads(probed.stdout)["streams"]
                self.assertEqual([(s["channels"], s["tags"]["language"], s["disposition"]["default"]) for s in audio],
                                 [(1, "eng", int(default == 0)), (1, "jpn", int(default == 1))])
                duration = _extract_audio(self._source_clip(master), str(extracted), "ffmpeg")
                self.assertAlmostEqual(duration, 1.25, delta=0.06)
                self._assert_extracted_tone(extracted, 440, 880)

    def test_extract_audio_single_and_missing_stream(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            single, silent = root / "single.mp4", root / "silent.mp4"
            self._audio_source(single, (880,), 0)
            extracted = root / "source.wav"
            duration = _extract_audio(self._source_clip(single), str(extracted), "ffmpeg")
            self.assertAlmostEqual(duration, 1.25, delta=0.06)
            self._assert_extracted_tone(extracted, 880, 440)
            self._audio_source(silent, (), 0)
            with self.assertRaisesRegex(SpeechRecognitionError, "没有可识别的音频流"):
                _extract_audio(self._source_clip(silent), str(root / "missing.wav"), "ffmpeg")

    @staticmethod
    def _audio_source(path: Path, tones: tuple[int, ...], default: int) -> None:
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                   "-f", "lavfi", "-i", "color=blue:size=160x90:rate=12:duration=1.25"]
        for frequency in tones:
            command += ["-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=1.25"]
        command += ["-map", "0:v:0", "-c:v", "libx264", "-threads:v", "1", "-pix_fmt", "yuv420p"]
        for index, language in enumerate(("eng", "jpn")[:len(tones)]):
            command += ["-map", f"{index + 1}:a:0", f"-metadata:s:a:{index}", f"language={language}",
                        f"-disposition:a:{index}", "default" if index == default else "0"]
        if tones:
            command += ["-c:a", "aac"]
        subprocess.run([*command, str(path)], check=True, capture_output=True, timeout=30)

    @staticmethod
    def _source_clip(path: Path) -> Clip:
        return Clip("voice", AssetReference("asset", str(path)),
                    Rational.of(0), Rational.of(5, 4), Rational.of(0))

    def _assert_extracted_tone(self, path: Path, expected: int, other: int) -> None:
        with wave.open(str(path), "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 16000))
            wav.setpos(4_000)
            raw = wav.readframes(8_000)
        self.assertEqual(len(raw), 16_000)
        samples = struct.unpack("<8000h", raw)

        def tone_strength(frequency: int) -> float:
            step = 2 * math.pi * frequency / 16000
            real = sum(value * math.cos(step * i) for i, value in enumerate(samples))
            imag = sum(value * math.sin(step * i) for i, value in enumerate(samples))
            return math.hypot(real, imag) / len(samples)

        self.assertGreater(tone_strength(expected), max(100, 8 * tone_strength(other)))

    def test_asr_runtime_selection_and_auto_fallback(self) -> None:
        self.assertEqual(_resolve_asr_runtime("auto", cuda_device_count=1),
                         ("cuda", "float16"))
        self.assertEqual(_resolve_asr_runtime("auto", cuda_device_count=0),
                         ("cpu", "int8"))
        self.assertEqual(_resolve_asr_runtime("cpu", cuda_device_count=1),
                         ("cpu", "int8"))
        with self.assertRaises(SpeechRecognitionError):
            _resolve_asr_runtime("cuda", cuda_device_count=0)
        with self.assertRaises(SpeechRecognitionError):
            _resolve_asr_runtime("other", cuda_device_count=1)

        calls: list[tuple[str, str]] = []

        class CudaUnavailableModel:
            def __init__(self, model: str, *, device: str, compute_type: str,
                         download_root: str | None) -> None:
                calls.append((device, compute_type))
                if device == "cuda":
                    raise RuntimeError("CUDA runtime is missing")

        # Exercise CUDA initialization failure even on CPU-only CI runners.
        with patch("cutvoke.rendering.speech_recognition._cuda_device_count", return_value=1):
            model, device, compute_type = _load_whisper_model(
                CudaUnavailableModel, "base", None, "auto", None
            )
        self.assertIsInstance(model, CudaUnavailableModel)
        self.assertEqual(calls, [("cuda", "float16"), ("cpu", "int8")])
        self.assertEqual((device, compute_type), ("cpu", "int8"))

    def test_invalid_asr_device_configuration_disables_recognition(self) -> None:
        with patch.dict(os.environ, {"CUTVOKE_ASR_DEVICE": "tpu"}):
            status = availability()
        self.assertFalse(status["ready"])
        self.assertEqual(status["devicePreference"], "tpu")
        self.assertIn("auto、cpu 或 cuda", status["message"])

    def test_chinese_is_simplified_with_context_and_word_times_preserved(self) -> None:
        if importlib.util.find_spec("opencc") is None:
            self.skipTest("OpenCC optional ASR dependency required")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "voice.wav"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-ar", "16000", str(source),
            ], check=True, capture_output=True)
            with ProjectStore(str(root / "projects.sqlite")) as store:
                service = EditService(store)
                service.create_project("speech", width=320, height=180)
                _edit(service, "track.add", {"trackId": "audio", "kind": "audio"}, 1)
                _edit(service, "clip.insert", {
                    "trackId": "audio", "clipId": "voice", "sourcePath": str(source),
                    "timelineStart": _rat(0), "timelineEnd": _rat(2),
                }, 2)
                with (patch.dict(os.environ, {"CUTVOKE_ASR_DEVICE": "cpu"}),
                      patch("faster_whisper.WhisperModel", _FakeChineseModel)):
                    proposal = transcribe_clip(service.get_project("speech"), "voice",
                                               model_size="tiny", language="auto")
            segment = proposal["segments"][0]
            self.assertEqual(proposal["language"], "zh")
            self.assertEqual(segment["text"], "大家好，今天我们沿着小路。")
            self.assertEqual("".join(word["text"] for word in segment["words"]),
                             " 大家好，今天我们沿着小路。")
            self.assertEqual(segment["words"][3]["start"],
                             {"num": "9", "den": "10"})
            self.assertEqual(segment["words"][4]["end"],
                             {"num": "11", "den": "10"})

    def test_recognized_text_is_one_undoable_edit_and_renders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "voice.wav"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                "-ar", "16000", str(source),
            ], check=True, capture_output=True)
            database = root / "projects.sqlite"
            with ProjectStore(str(database)) as store:
                service = EditService(store)
                service.create_project("speech", width=320, height=180)
                _edit(service, "track.add", {"trackId": "audio", "kind": "audio"}, 1)
                _edit(service, "clip.insert", {
                    "trackId": "audio", "clipId": "voice", "sourcePath": str(source),
                    "timelineStart": _rat(0), "timelineEnd": _rat(4),
                }, 2)
                _edit(service, "clip.speed", {"clipId": "voice", "speed": _rat(2)}, 3)
                with (patch.dict(os.environ, {"CUTVOKE_ASR_DEVICE": "auto"}),
                      patch("cutvoke.rendering.speech_recognition._cuda_device_count",
                            return_value=1),
                      patch("faster_whisper.WhisperModel", _CudaInferenceFailsModel)):
                    proposal = transcribe_clip(service.get_project("speech"), "voice",
                                               model_size="tiny", language="en")
                self.assertEqual(proposal["device"], "cpu")
                self.assertEqual(proposal["computeType"], "int8")
                self.assertEqual(proposal["segmentCount"], 1)
                self.assertEqual(proposal["segments"][0]["text"], "Hello world")
                self.assertEqual(proposal["segments"][0]["start"],
                                 {"num": "1", "den": "4"})
                self.assertEqual(proposal["segments"][0]["end"],
                                 {"num": "3", "den": "4"})
                previous = service.get_project("speech").revision
                _edit(service, "caption.bulkAdd", {
                    "segments": proposal["segments"], "sourceClipId": "voice",
                    "sourceSignature": proposal["sourceSignature"],
                    "style": {"fontSize": 96},
                }, 4)
                self.assertNotEqual(service.get_project("speech").revision, previous)
                self.assertEqual(len(service.get_project("speech").sequence.captions), 1)
                _edit(service, "history.undo", {}, 5)
                self.assertFalse(service.get_project("speech").sequence.captions)
                _edit(service, "history.redo", {}, 6)
                self.assertEqual(len(service.get_project("speech").sequence.captions), 1)

            with ProjectStore(str(database)) as reopened:
                service = EditService(reopened)
                project = service.get_project("speech")
                caption_id = project.sequence.captions[0].id
                _edit(service, "caption.update", {
                    "captionId": caption_id, "text": "Hello, world.",
                }, 7)
                self.assertIn("Hello, world.", service.export_srt("speech"))
                _edit(service, "history.undo", {}, 8)
                self.assertEqual(service.get_project("speech").sequence.captions[0].text,
                                 "Hello world")
                _edit(service, "history.redo", {}, 9)
                output = root / "captioned.mp4"
                RenderService().render(service.get_project("speech"), str(output), quality="low")
                frame = root / "captioned.png"
                subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", "0.5", "-i", str(output), "-frames:v", "1", str(frame),
                ], check=True, capture_output=True)
                with Image.open(frame) as image:
                    pixels = list(image.convert("RGB").get_flattened_data())
                    self.assertGreater(sum(max(pixel) > 100 for pixel in pixels), 100)

                old_revision = service.get_project("speech").revision
                _edit(service, "clip.speed", {"clipId": "voice", "speed": _rat(1)}, 10)
                with self.assertRaises(EditError) as stale:
                    _edit(service, "caption.bulkAdd", {
                        "segments": proposal["segments"], "sourceClipId": "voice",
                        "sourceSignature": proposal["sourceSignature"],
                    }, 11)
                self.assertEqual(stale.exception.code, ErrorCode.INVALID_ARGUMENT)
                self.assertEqual(len(service.get_project("speech").sequence.captions), 1)
                self.assertNotEqual(service.get_project("speech").revision, old_revision)

    def test_http_job_reports_progress_and_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "voice.wav"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                str(source),
            ], check=True, capture_output=True)
            with ProjectStore(str(root / "jobs.sqlite")) as store:
                service = EditService(store)
                service.create_project("speech")
                _edit(service, "track.add", {"trackId": "audio", "kind": "audio"}, 1)
                _edit(service, "clip.insert", {
                    "trackId": "audio", "clipId": "voice", "sourcePath": str(source),
                    "timelineStart": _rat(0), "timelineEnd": _rat(1),
                }, 2)
                api = HttpApi(service, media_dir=str(root / "media"))
                fake = lambda project, clip_id, **kw: {
                    "clipId": clip_id, "sourceSignature": "fake", "model": "tiny",
                    "language": "en", "duration": 1.0, "segments": [], "segmentCount": 0,
                }
                with patch("cutvoke.rendering.asr_jobs.transcribe_clip", fake), \
                     patch("cutvoke.rendering.asr_jobs.availability", return_value={"installed": True}):
                    status, body = api.handle("POST", "/api/v1/projects/speech/asr-jobs",
                                              {"clipId": "voice", "model": "tiny", "language": "en"})
                    self.assertEqual(status, 202)
                    job_id = body["jobId"]
                    for _ in range(100):
                        status, body = api.handle("GET", f"/api/v1/asr-jobs/{job_id}", {})
                        if body["status"] == "completed":
                            break
                        time.sleep(0.01)
                    self.assertEqual(status, 200)
                    self.assertEqual(body["status"], "completed")
                    self.assertEqual(body["progress"], 100)
                    self.assertEqual(body["result"]["clipId"], "voice")
                def slow(project, clip_id, **kwargs):
                    time.sleep(0.2)
                    return fake(project, clip_id, **kwargs)

                with patch("cutvoke.rendering.asr_jobs.transcribe_clip", slow), \
                     patch("cutvoke.rendering.asr_jobs.availability", return_value={"installed": True}):
                    status, pending = api.handle("POST", "/api/v1/projects/speech/asr-jobs",
                                                 {"clipId": "voice", "model": "tiny"})
                    self.assertEqual(status, 202)
                    cancelled, body = api.handle(
                        "POST", f"/api/v1/asr-jobs/{pending['jobId']}/cancel", {})
                    self.assertEqual(cancelled, 200)
                    self.assertEqual(body["status"], "cancelled")
                    time.sleep(0.25)
                    _, after = api.handle("GET", f"/api/v1/asr-jobs/{pending['jobId']}", {})
                    self.assertEqual(after["status"], "cancelled")
                    self.assertIsNone(after["result"])
                api.close()


if __name__ == "__main__":
    unittest.main()
