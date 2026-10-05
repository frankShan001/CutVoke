"""Run a local Chinese ASR-to-caption acceptance check on an existing video."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from cutvoke.core.asr_jobs import AsrJobManager
from cutvoke.core.model import Project
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore
from cutvoke.core.speech_recognition import availability


REFERENCE = "大家好今天我们来到公园先看看这片湖水再沿着小路往前走阳光很好风也很轻适合拍一段安静的短片"


def _edit(service: EditService, project_id: str, kind: str,
          payload: dict, number: int) -> None:
    project = service.get_project(project_id)
    service.execute(Command(
        type=kind, payload=payload, command_id=f"asr-acceptance-{number}",
        project_id=project_id, expected_revision=project.revision,
        actor=Actor("human", "local-asr-acceptance"),
    ))


def _probe_duration(path: Path) -> float:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path),
    ], capture_output=True, text=True, check=True, encoding="utf-8")
    return float(result.stdout.strip())


def _clean(text: str) -> str:
    return "".join(char for char in text if char.isalnum())


def _cer(reference: str, hypothesis: str) -> float:
    ref, hyp = _clean(reference), _clean(hypothesis)
    row = list(range(len(hyp) + 1))
    for i, ref_char in enumerate(ref, 1):
        next_row = [i]
        for j, hyp_char in enumerate(hyp, 1):
            next_row.append(min(
                next_row[-1] + 1, row[j] + 1,
                row[j - 1] + (ref_char != hyp_char),
            ))
        row = next_row
    return row[-1] / max(1, len(ref))


def _snapshot(project: Project, path: Path) -> None:
    path.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def run(source: Path, output_dir: Path, model: str, *, language: str = "zh",
        recognize_only: bool = False, reference_text: str | None = None,
        reference_provenance: str | None = None) -> dict:
    source = source.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if reference_text is not None:
        reference_text = reference_text.strip()
        if not reference_text:
            raise ValueError("reference text cannot be empty")
    elif language == "zh":
        reference_text = REFERENCE
        reference_provenance = reference_provenance or "built-in Chinese acceptance phrase"
    if reference_text is not None and not reference_provenance:
        reference_provenance = "caller-supplied reference; provenance not declared"
    if not availability()["installed"]:
        raise RuntimeError(availability()["message"])
    duration = _probe_duration(source)
    database = output_dir / "workflow.sqlite"
    project_id = "asr-acceptance"
    clip_id = "speech-source"
    recognition: dict | None = None
    manager = AsrJobManager()
    try:
        with ProjectStore(str(database)) as store:
            service = EditService(store)
            service.create_project(project_id, width=1280, height=720)
            _edit(service, project_id, "track.add",
                  {"trackId": "video", "kind": "video"}, 1)
            _edit(service, project_id, "clip.insert", {
                "trackId": "video", "clipId": clip_id, "sourcePath": str(source.resolve()),
                "timelineStart": Rational.of(0, 1).to_json(),
                "timelineEnd": Rational.from_float(duration).to_json(),
            }, 2)
            asr_started = time.perf_counter()
            queued = manager.submit(service.get_project(project_id), clip_id,
                                    model=model, language=language)
            job_id = queued["jobId"]
            deadline = time.time() + 3600
            while time.time() < deadline:
                job = manager.get(job_id)
                if job and job["status"] in ("completed", "failed", "cancelled"):
                    break
                time.sleep(0.25)
            else:
                raise TimeoutError("ASR job exceeded the 60 minute acceptance limit")
            if not job or job["status"] != "completed":
                raise RuntimeError((job or {}).get("error") or "ASR job did not complete")
            asr_elapsed = max(0.0, time.perf_counter() - asr_started)
            recognition = job["result"]
            (output_dir / "recognition.json").write_text(
                json.dumps(recognition, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            recognized = "".join(segment["text"] for segment in recognition["segments"])
            if recognize_only:
                result = {
                    "schemaVersion": 1,
                    "status": "completed" if recognition["segments"] else "no_speech_detected",
                    "source": str(source),
                    "model": recognition["model"],
                    "device": recognition.get("device"),
                    "computeType": recognition.get("computeType"),
                    "requestedLanguage": language,
                    "language": recognition["language"],
                    "duration": recognition["duration"],
                    "asrTiming": {
                        "elapsedSeconds": round(asr_elapsed, 3),
                        "audioSeconds": round(duration, 3),
                        "throughputAudioSecondsPerWallSecond": round(
                            duration / max(asr_elapsed, 1e-9), 3
                        ),
                        "scope": "submit_to_terminal_status_including_model_load_and_queue",
                    },
                    "segments": recognition["segmentCount"],
                    "wordTimings": sum(len(segment["words"])
                                        for segment in recognition["segments"]),
                    "recognizedText": recognized,
                    "referenceText": reference_text,
                    "referenceProvenance": reference_provenance,
                    "characterErrorRateIgnoringPunctuation": (
                        round(_cer(reference_text, recognized), 6)
                        if reference_text is not None else None
                    ),
                    "accuracyEvaluation": (
                        "scored_against_supplied_reference"
                        if reference_text is not None
                        else "not_scored_no_reference_transcript"
                    ),
                    "checks": ["local-asr-job", "language-detection", "word-timestamps"],
                }
                (output_dir / "acceptance.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                return result
            if not recognition["segments"]:
                raise RuntimeError("ASR returned no caption segments")
            _edit(service, project_id, "caption.bulkAdd", {
                "segments": recognition["segments"], "sourceClipId": clip_id,
                "sourceSignature": recognition["sourceSignature"],
                "wordHighlightColor": "#FFD54A",
                "style": {"fontSize": 32, "fontFamily": "Noto Sans SC",
                          "color": "#ffffff", "strokeColor": "#000000",
                          "strokeWidth": 2, "align": "center"},
            }, 3)
            _snapshot(service.get_project(project_id), output_dir / "project-after-caption.json")

        with ProjectStore(str(database)) as reopened_store:
            service = EditService(reopened_store)
            reopened = service.get_project(project_id)
            captions = reopened.sequence.captions
            if not captions or sum(len(caption.words) for caption in captions) == 0:
                raise RuntimeError("Saved project lost captions or word timings")
            before_undo_count = len(captions)
            _edit(service, project_id, "history.undo", {}, 4)
            undo_count = len(service.get_project(project_id).sequence.captions)
            _edit(service, project_id, "history.redo", {}, 5)
            restored = service.get_project(project_id)
            _snapshot(restored, output_dir / "project-reopened-after-redo.json")
            output = output_dir / "asr-caption-export.mp4"
            render_result = RenderService().render(restored, str(output), quality="low")

        decode = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(output),
            "-f", "null", "-",
        ], capture_output=True, text=True, encoding="utf-8")
        if decode.returncode != 0:
            raise RuntimeError(f"Full output decode failed: {decode.stderr[-1000:]}")
        frame = output_dir / "caption-frame.png"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "2.0",
            "-i", str(output), "-frames:v", "1", str(frame),
        ], check=True, capture_output=True)
        result = {
            "schemaVersion": 1,
            "source": str(source.resolve()),
            "model": recognition["model"],
            "device": recognition.get("device"),
            "computeType": recognition.get("computeType"),
            "language": recognition["language"],
            "duration": recognition["duration"],
            "asrTiming": {
                "elapsedSeconds": round(asr_elapsed, 3),
                "audioSeconds": round(duration, 3),
                "throughputAudioSecondsPerWallSecond": round(
                    duration / max(asr_elapsed, 1e-9), 3
                ),
                "scope": "submit_to_terminal_status_including_model_load_and_queue",
            },
            "segments": recognition["segmentCount"],
            "wordTimings": sum(len(segment["words"]) for segment in recognition["segments"]),
            "recognizedText": recognized,
            "referenceText": reference_text,
            "referenceProvenance": reference_provenance,
            "characterErrorRateIgnoringPunctuation": (
                round(_cer(reference_text, recognized), 6)
                if reference_text is not None else None
            ),
            "accuracyEvaluation": (
                "scored_against_supplied_reference"
                if reference_text is not None
                else "not_scored_no_reference_transcript"
            ),
            "checks": ["local-asr-job", "simplified-chinese", "caption.bulkAdd",
                       "sqlite-save-reopen", "undo", "redo", "mp4-video-audio-export",
                       "full-decode", "caption-frame-extracted"],
            "undoCaptionCount": undo_count,
            "redoCaptionCount": len(restored.sequence.captions),
            "saveCaptionCount": before_undo_count,
            "render": render_result,
            "decodeExitCode": decode.returncode,
            "export": output.name,
            "captionFrame": frame.name,
        }
        (output_dir / "acceptance.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return result
    finally:
        manager.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--model", choices=("tiny", "base", "small", "medium"),
                        default="base")
    parser.add_argument("--model-dir", type=Path,
                        help="existing Faster-Whisper model cache directory")
    parser.add_argument("--language", choices=("auto", "zh", "en", "ja", "ko"),
                        default="zh")
    parser.add_argument("--reference-file", type=Path,
                        help="optional UTF-8 reference transcript for CER reporting")
    parser.add_argument("--reference-provenance",
                        help="describe the supplied transcript source and verification level")
    parser.add_argument("--recognize-only", action="store_true",
                        help="save ASR output and timing evidence without editing/exporting captions")
    args = parser.parse_args()
    if args.model_dir:
        os.environ["CUTVOKE_ASR_MODEL_DIR"] = str(args.model_dir.resolve())
    reference_text = (
        args.reference_file.read_text(encoding="utf-8")
        if args.reference_file else None
    )
    print(json.dumps(run(args.source, args.output_dir, args.model,
                         language=args.language, recognize_only=args.recognize_only,
                         reference_text=reference_text,
                         reference_provenance=args.reference_provenance),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
