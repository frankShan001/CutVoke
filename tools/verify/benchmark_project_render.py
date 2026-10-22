"""Benchmark an immutable real project with unchanged render settings.

Run the baseline and candidate sequentially in fresh output directories. Pass a
frozen src directory with --source-root to load that complete Python package.
Pixel/audio decoding and environment discovery happen outside render timing.
"""
from __future__ import annotations

import argparse
import contextlib
import cProfile
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
import threading
from unittest.mock import patch


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="Read-only UTF-8 project JSON snapshot")
    parser.add_argument("output_directory", type=Path, help="Fresh output directory")
    parser.add_argument("--source-root", type=Path,
                        default=Path(__file__).resolve().parents[2] / "src",
                        help="Python source root containing cutvoke/ (inserted before imports)")
    parser.add_argument("--mode", choices=("poster", "preview", "export"), default="preview")
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--duration", type=float, help="Required for preview; optional export range")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--cpu-effects", action="store_true", help="Explicitly disable GPU effects")
    parser.add_argument("--software-encoder", action="store_true", help="Explicitly force libx264")
    parser.add_argument("--cold", action="store_true",
                        help="Fresh renderer and isolated disposable tempfile cache for every repeat")
    parser.add_argument("--profile", action="store_true", help="Save per-render cProfile data")
    args = parser.parse_args()
    if not math.isfinite(args.start) or args.start < 0:
        parser.error("--start must be finite and >= 0")
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
        parser.error("--duration must be finite and > 0")
    if args.mode == "preview" and (args.duration is None or args.duration > 8):
        parser.error("preview requires --duration in (0, 8]")
    if args.mode == "export" and args.start and args.duration is None:
        parser.error("an export with --start requires --duration")
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")
    if not args.project.is_file():
        parser.error("project does not exist")
    if not (args.source_root / "cutvoke").is_dir():
        parser.error("--source-root must contain the cutvoke package")
    if args.output_directory.exists():
        parser.error("output directory already exists; choose a fresh directory")
    return args


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_identity(root):
    """Hash Python source only; asset junctions and cache files are excluded."""
    digest = hashlib.sha256()
    count = 0
    for directory, names, filenames in os.walk(root / "cutvoke", followlinks=False):
        names[:] = sorted(name for name in names if name not in {"assets", "__pycache__"})
        for name in sorted(filenames):
            path = Path(directory) / name
            if path.suffix == ".py":
                digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
                digest.update(path.read_bytes())
                count += 1
    return dict(path=str(root), pythonFiles=count, pythonSourceSha256=digest.hexdigest())


def tool_version(executable):
    result = subprocess.run([executable, "-version"], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, errors="replace", timeout=15)
    return dict(path=shutil.which(executable) or str(executable),
                version=(result.stdout or result.stderr).splitlines()[0] if
                (result.stdout or result.stderr) else "", returnCode=result.returncode)


def stream_hash(command, directory, timeout=300):
    """Drain decoding stdout without retaining an entire uncompressed movie."""
    digest, size = hashlib.sha256(), 0
    # A file avoids stderr pipe deadlocks and bounds retained diagnostic output.
    with tempfile.TemporaryFile(dir=directory) as errors:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=errors)
        timed_out = threading.Event()
        def expire():
            if process.poll() is None:
                timed_out.set()
                with contextlib.suppress(OSError):
                    process.kill()
        timer = threading.Timer(timeout, expire)
        timer.daemon = True
        timer.start()
        try:
            for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
            return_code = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            timer.cancel()
            process.stdout.close()
        if timed_out.is_set():
            raise TimeoutError(f"decoded verification exceeded {timeout} seconds")
        if return_code:
            errors.seek(max(0, errors.tell() - 4000))
            raise RuntimeError("decode failed: " + errors.read().decode("utf-8", "replace"))
    return dict(sha256=digest.hexdigest(), decodedBytes=size)


def decoded_identity(path, renderer):
    probe = subprocess.run([renderer.ffprobe, "-v", "error", "-show_streams",
                            "-show_format", "-of", "json", str(path)],
                           stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=30)
    info = json.loads(probe.stdout)
    streams = info.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    result = dict(fileSha256=file_sha256(path), fileBytes=path.stat().st_size,
                  format=info.get("format", {}), streams=streams)
    if video:
        pixels = stream_hash([renderer.ffmpeg, "-v", "error", "-nostdin", "-threads", "2",
                              "-i", str(path), "-map", "0:v:0", "-an", "-fps_mode", "passthrough",
                              "-threads", "2", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"],
                             path.parent)
        pixels.update(format="rgba", width=video["width"], height=video["height"])
        frame_bytes = pixels["width"] * pixels["height"] * 4
        if pixels["decodedBytes"] % frame_bytes:
            raise RuntimeError("decoded video contains a partial frame")
        pixels["frameCount"] = pixels["decodedBytes"] // frame_bytes
        result["pixels"] = pixels
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if audio:
        samples = stream_hash([renderer.ffmpeg, "-v", "error", "-nostdin", "-threads", "2",
                               "-i", str(path), "-map", "0:a:0", "-vn", "-c:a", "pcm_s16le",
                               "-f", "s16le", "pipe:1"], path.parent)
        samples.update(format="s16le", channels=audio["channels"], sampleRate=int(audio["sample_rate"]))
        sample_bytes = samples["channels"] * 2
        if samples["decodedBytes"] % sample_bytes:
            raise RuntimeError("decoded audio contains a partial sample")
        samples["sampleCount"] = samples["decodedBytes"] // sample_bytes
        result["audio"] = samples
    else:
        result["audio"] = None
    return result


def measured_renderer_class(base):
    class MeasuredRenderer(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.phases = []

        def _record(self, name, function, *args, **kwargs):
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                self.phases.append(dict(phase=name, seconds=time.perf_counter() - started))

        def _compile(self, *args, **kwargs):
            started = time.perf_counter()
            phase = dict(phase="compile")
            try:
                result = super()._compile(*args, **kwargs)
                phase.update(inputCount=len(result[1]), graphBytes=len(result[0].encode("utf-8")),
                             effectProcessor="opencl" if "program_opencl=" in result[0] else "cpu")
                return result
            finally:
                phase["seconds"] = time.perf_counter() - started
                self.phases.append(phase)

        def probe_media(self, *args, **kwargs):
            return self._record("probeMedia", super().probe_media, *args, **kwargs)

        def _verify(self, *args, **kwargs):
            return self._record("verifyOutput", super()._verify, *args, **kwargs)

        def _run_ffmpeg(self, seq, graph, inputs, out_path, quality, *args, **kwargs):
            started = time.perf_counter()
            try:
                return super()._run_ffmpeg(seq, graph, inputs, out_path, quality, *args, **kwargs)
            finally:
                gpu = getattr(self._render_ctx, "gpu", None)
                self.phases.append(dict(phase="runFfmpeg", seconds=time.perf_counter() - started,
                    inputCount=len(inputs), graphBytes=len(graph.encode("utf-8")), quality=quality,
                    start=kwargs.get("output_start"), duration=kwargs.get("output_duration"),
                    lossless=kwargs.get("lossless", False),
                    encoder=getattr(self._render_ctx, "video_encoder", None),
                    effectProcessor="opencl" if gpu is not None and "program_opencl=" in graph else "cpu"))
    return MeasuredRenderer


def main():
    args = parse_args()
    source_root = args.source_root.resolve()
    # No cutvoke imports may precede this insertion: a baseline must use its own
    # complete package, including effect helpers and the original render module.
    sys.path.insert(0, str(source_root))
    output = args.output_directory.resolve()
    output.mkdir(parents=True, exist_ok=False)
    project_path = args.project.resolve()
    original_sha = file_sha256(project_path)
    report = dict(schemaVersion=1, status="running", project=str(project_path),
                  projectSha256=original_sha,
                  settings=dict(mode=args.mode, start=args.start, duration=args.duration,
                                repeat=args.repeat, cold=args.cold, cpuEffects=args.cpu_effects,
                                softwareEncoder=args.software_encoder, profile=args.profile),
                  environment=dict(python=sys.version, executable=sys.executable,
                                   platform=platform.platform(), cpuCount=os.cpu_count()), runs=[])
    report_path = output / "report.json"

    def save():
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    renderer = None
    old_tempdir = tempfile.tempdir
    code = 0
    try:
        from cutvoke.domain.model import Project
        from cutvoke.rendering import render as render_module

        report["source"] = source_identity(source_root)
        report["environment"].update(ffmpeg=tool_version(render_module.DEFAULT_FFMPEG),
                                     ffprobe=tool_version(render_module.DEFAULT_FFPROBE))
        data = json.loads(project_path.read_text(encoding="utf-8-sig"))
        if "sequence" not in data and isinstance(data.get("project"), dict):
            data = data["project"]
        project = Project.from_dict(data)
        immutable_project = project.to_dict()
        report["projectName"] = project.name
        report["sequence"] = dict(width=project.sequence.width, height=project.sequence.height,
                                  fps=str(project.sequence.fps.to_fraction()))
        renderer_type = measured_renderer_class(render_module.RenderService)
        encoder_context = patch.object(render_module, "_nvenc_available", return_value=False) if args.software_encoder else contextlib.nullcontext()
        with encoder_context:
            for index in range(args.repeat):
                run = dict(index=index, status="running", output=f"{args.mode}-{index + 1:02d}." +
                           ("png" if args.mode == "poster" else "mp4"))
                report["runs"].append(run)
                target = output / run["output"]
                with tempfile.TemporaryDirectory(prefix="cutvoke-project-benchmark-") as directory:
                    try:
                        if args.cold:
                            tempfile.tempdir = directory
                        if renderer is None or args.cold:
                            renderer = renderer_type(gpu_effects=not args.cpu_effects)
                        renderer.phases = []
                        profiler = cProfile.Profile() if args.profile else None
                        if profiler:
                            profiler.enable()
                        started = time.perf_counter()
                        try:
                            if args.mode == "poster":
                                result = renderer.extract_frame(project, args.start, str(target))
                            elif args.mode == "preview":
                                result = renderer.render_preview_window(project, str(target), args.start, args.duration)
                            else:
                                options = dict(output_start=args.start, output_duration=args.duration) if args.duration is not None else {}
                                result = renderer.render(project, str(target), quality="high", **options)
                        finally:
                            run["seconds"] = time.perf_counter() - started
                            if profiler:
                                profiler.disable()
                                profile_path = output / f"profile-{index + 1:02d}.pstats"
                                profiler.dump_stats(str(profile_path))
                                run["profile"] = profile_path.name
                            run["phases"] = renderer.phases
                        run["result"] = result
                        # Decode after timing. Keep all media for a reviewable A/B.
                        run["decoded"] = decoded_identity(target, renderer)
                        if project.to_dict() != immutable_project:
                            raise RuntimeError("renderer mutated the source project model")
                        run["status"] = "passed"
                    except BaseException:
                        run["status"] = "failed"
                        for partial in (target, Path(str(target) + ".tmp.mp4")):
                            partial.unlink(missing_ok=True)
                        raise
                    finally:
                        tempfile.tempdir = old_tempdir
                        save()
                print(json.dumps(run, ensure_ascii=False, default=str), flush=True)
        times = [run["seconds"] for run in report["runs"]]
        report["timing"] = dict(medianSeconds=statistics.median(times), minSeconds=min(times),
                                maxSeconds=max(times), totalSeconds=sum(times))
        report["status"] = "passed"
    except BaseException as exc:
        report["status"] = "failed"
        report["error"] = dict(type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc())
        code = 1
    finally:
        tempfile.tempdir = old_tempdir
        report["projectUnchanged"] = file_sha256(project_path) == original_sha
        if not report["projectUnchanged"]:
            report["status"] = "failed"
            code = 1
        save()
    print(json.dumps(dict(status=report["status"], report=str(report_path), timing=report.get("timing")), ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
