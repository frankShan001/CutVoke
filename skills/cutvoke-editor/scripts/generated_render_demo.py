#!/usr/bin/env python3
"""Reproduce verified cache reuse and absolute-frame repair over real stdio MCP.

Only the explicitly invoked, reviewed repository reference renderer runs code.
Generated request files remain data to CutVoke. Synthetic original inputs only.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import time

import numpy as np
from PIL import Image, __version__ as pillow_version

from demo import MCP, run, score
from generated_media_demo import rat, write_json, digest, verify_source_handoff
from cutvoke.storage.project_directory import ProjectLibraryStore
from cutvoke.storage.projectpack import unpack_project


def summary(samples):
    return {"samples": samples, "median": statistics.median(samples), "max": max(samples)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New isolated directory")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[3]
    renderer = repo / "examples/generated-media/render_request.py"
    project = output / "project"
    mcp = MCP([sys.executable, "-m", "cutvoke"], output / "library.sqlite", output, None)
    try:
        mcp.call("editor_help", topic="generated-render")
        mcp.call("create_project", projectId="cache-demo", directory=str(project), width=640, height=360, fps=24)
        evidence = project / "evidence"
        evidence.mkdir()
        runtime = {"python": platform.python_version(), "Pillow": pillow_version,
                   "ffmpeg": run(["ffmpeg", "-version"]).splitlines()[0]}
        requests, roots = {}, {}
        for kind in ("title", "chart", "annotation"):
            root = project / "sources" / kind
            root.mkdir(parents=True)
            for name in ("scene.py", "render_request.py"):
                shutil.copy2(renderer.parent / name, root / name)
            shutil.copy2(repo / "LICENSE", root / "LICENSE")
            write_json(root / "runtime.json", runtime)
            (root / "requirements.txt").write_text(f"Pillow=={pillow_version}\n", encoding="utf8")
            request = {"kind": "cutvoke.generated-render-request", "schemaVersion": 1,
                       "renderer": {"name": "reviewed-original-pillow", "version": "1", "build": json.dumps(runtime, sort_keys=True)},
                       "entrypoint": "scene.py", "files": [
                           {"path": name, "role": role, "sha256": digest(root / name)} for name, role in (
                               ("scene.py", "source"), ("render_request.py", "source"), ("runtime.json", "asset"),
                               ("requirements.txt", "lockfile"), ("LICENSE", "license"))],
                       "bindings": {}, "seed": 0, "profile": "final",
                       "paramsSchema": {"type": "object", "additionalProperties": False, "required": ["kind", "accent"], "properties": {
                           "kind": {"type": "string", "minLength": 1, "maxLength": 10, "enum": ["title", "chart", "annotation"]},
                           "accent": {"type": "array", "minItems": 3, "maxItems": 3,
                                      "items": {"type": "integer", "minimum": 0, "maximum": 255}}}},
                       "params": {"kind": kind, "accent": [61, 232, 208]},
                       "output": {"width": 640, "height": 360, "fps": {"num": 24, "den": 1}, "durationFrames": 72,
                                  "format": "prores4444" if kind == "annotation" else "mp4",
                                  "alphaMode": "straight" if kind == "annotation" else "opaque", "colorSpace": "bt709", "audioMode": "none"},
                       "frameRange": {"startFrame": 0, "frameCount": 72}}
            requests[kind] = root / "request.json"
            roots[kind] = root
            write_json(requests[kind], request)
        def plan(kind, **extra):
            return mcp.call("generated_render", projectId="cache-demo", operation="plan", requestPath=str(requests[kind]), **extra)
        def render(kind, planned, tag):
            # Effective overrides stay beside their declared inputs. The fixed
            # renderer checks source hashes and never executes request code.
            request = roots[kind] / (tag + ".json")
            write_json(request, planned["request"])
            video = roots[kind] / (tag + (".mov" if kind == "annotation" else ".mp4"))
            proof = roots[kind] / (tag + "-proof.json")
            started = time.monotonic()
            run([sys.executable, str(renderer), "--request", str(request), "--output", str(video), "--proof", str(proof)])
            return video, proof, time.monotonic() - started
        def register(kind, planned, video, **extra):
            started = time.monotonic()
            receipt = mcp.call("generated_render", projectId="cache-demo", operation="register", requestPath=str(requests[kind]),
                               expectedRequestKey=planned["requestKey"], renderPath=str(video), renderSha256=digest(video), **extra)
            return receipt, time.monotonic() - started
        planned = {kind: plan(kind) for kind in requests}
        assert all(item["requiresExternalRender"] and not item["sourceExecution"] for item in planned.values())
        cold, paths = [], {}
        for trial in range(3):
            elapsed = 0
            for kind in requests:
                video, proof, seconds = render(kind, planned[kind], "cold")
                paths[kind] = (video, proof)
                elapsed += seconds
            cold.append(elapsed)
        receipts, registration = {}, {}
        for kind in requests:
            receipts[kind], registration[kind] = register(kind, planned[kind], paths[kind][0])
            # Original external output can disappear: the verified managed
            # asset and separate archive are what the cache checks.
            paths[kind][0].unlink()
        warm = []
        for _ in range(3):
            started = time.monotonic()
            hits = [plan(kind) for kind in requests]
            warm.append(time.monotonic() - started)
            assert all(hit["cache"]["kind"] == "exact" and not hit["requiresExternalRender"] for hit in hits)
        covering = plan("chart", frameRange={"startFrame": 24, "frameCount": 24})
        assert covering["cache"]["kind"] == "covering" and covering["mediaSourceStart"] == rat(1)
        preview = evidence / "covering"
        preview.mkdir()
        mcp.call(covering["previewCall"]["tool"], output_frames=preview, **covering["previewCall"]["arguments"])
        override = {"params": {"accent": [255, 166, 77]}, "frameRange": {"startFrame": 24, "frameCount": 24}}
        revised = plan("chart", previousRequestKey=planned["chart"]["requestKey"], **override)
        assert revised["cache"]["hit"] is False and "params.accent" in revised["changedFields"]
        full_reference = plan("chart", params=override["params"])
        partial_times, full_times = [], []
        for _ in range(3):
            partial, partial_proof, seconds = render("chart", revised, "repair")
            partial_times.append(seconds)
            reference, reference_proof, seconds = render("chart", full_reference, "full-reference")
            full_times.append(seconds)
        partial_hashes = json.loads(partial_proof.read_text())["rgbaFrameHashes"]
        full_hashes = json.loads(reference_proof.read_text())["rgbaFrameHashes"]
        assert partial_hashes == full_hashes[24:48]  # Same absolute animation clock.
        repair, repair_registration = register("chart", revised, partial, **override)
        assert plan("chart", **override)["cache"]["kind"] == "exact"
        background = project / "sources/background.png"
        Image.new("RGB", (640, 360), (12, 18, 30)).save(background)
        audio = project / "sources/score.wav"
        score(audio)
        assets = mcp.call("media_import", projectId="cache-demo", paths=[str(background), str(audio)])["assets"]
        commands = [{"type": "clip.insert", "payload": {"trackId": "base", "createTrackKind": "video", "clipId": "background",
                    "assetId": assets[0]["assetId"], "timelineStart": rat(0), "timelineEnd": rat(9)}}]
        for index, kind in enumerate(requests):
            payload = {"trackId": "code", "clipId": kind, "assetId": receipts[kind]["assetId"],
                       "timelineStart": rat(index * 3), "timelineEnd": rat(index * 3 + 3)}
            if not index: payload["createTrackKind"] = "video"
            commands.append({"type": "clip.insert", "payload": payload})
        commands += [{"type": "clip.insert", "payload": {"trackId": "titles", "createTrackKind": "text", "clipId": "native-title",
                     "timelineStart": rat(0), "timelineEnd": rat(9),
                     "text": {"content": "CODE + EDITABLE TIMELINE", "fontSize": 100, "color": "#ffffff", "x": .5, "y": .15}}},
                     {"type": "clip.insert", "payload": {"trackId": "sound", "createTrackKind": "audio", "clipId": "score",
                     "assetId": assets[1]["assetId"], "timelineStart": rat(0), "timelineEnd": rat(9)}}]
        lease = mcp.call("edit_lock", projectId="cache-demo", action="acquire", owner="cache-demo")["lease"]["leaseId"]
        revision = "0"
        def apply(kind, payload, command_id):
            nonlocal revision
            result = mcp.call("command_apply", projectId="cache-demo", expectedRevision=revision,
                              editLeaseId=lease, commandId=command_id, type=kind, payload=payload)
            revision = result["revision"]
            return result
        def preview(commands):
            return mcp.call("command_preview", projectId="cache-demo", expectedRevision=revision, type="edit.batch", payload={"commands": commands})
        try:
            preview(commands)
            apply("edit.batch", {"commands": commands}, "assemble")
            apply("clip.audio", {"clipId": "score", "fadeIn": rat(1), "fadeOut": rat(1), "volume": .6}, "fades")
            split = [{"type": "clip.split", "payload": {"clipId": "chart", "at": rat(4)}}]
            right = next(item["id"] for item in preview(split)["changedEntities"] if item.get("change") == "created")
            split.append({"type": "clip.split", "payload": {"clipId": right, "at": rat(5)}})
            assert len([item for item in preview(split)["changedEntities"] if item.get("change") == "created"]) == 2
            repair_commands = split + [
                {"type": "clip.trim", "payload": {"clipId": right, "sourceStart": rat(0)}},
                {"type": "asset.swap", "payload": {"clipIds": [right], "assetId": repair["assetId"]}}]
            preview(repair_commands)
            apply("edit.batch", {"commands": repair_commands}, "repair-one-second")
            def project_clips():
                library = ProjectLibraryStore(str(output / "library.sqlite"))
                try:
                    return {clip.id: clip.to_dict() for track in library.load("cache-demo").sequence.tracks for clip in track.clips}
                finally:
                    library.close()
            after = project_clips()
            assert after[right]["assetRef"]["assetId"] == repair["assetId"]
            apply("history.undo", {}, "undo-repair")
            assert right not in project_clips()
            apply("history.redo", {}, "redo-repair")
            assert project_clips() == after
        finally:
            mcp.call("edit_lock", projectId="cache-demo", action="release", leaseId=lease)
        final = evidence / "final"
        final.mkdir()
        mcp.call("preview_frames", projectId="cache-demo", expectedRevision=revision, times=[1.5, 3.5, 4.5, 5.5, 7.5], width=640, output_frames=final)
        export = mcp.call("command_apply", projectId="cache-demo", expectedRevision=revision, commandId="export", type="export.enqueue",
                          payload={"outPath": str(project / "exports/final.mp4"), "quality": "high"})
        job = mcp.wait("export_job", "cache-demo", export["changedEntities"][0]["jobId"])
        video = Path(job["result"]["output_path"])
        run(["ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"])
        info = json.loads(run(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", str(video)]))
        visual = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
        assert int(visual["nb_read_frames"]) == 216 and (visual["width"], visual["height"]) == (640, 360)
        assert abs(float(info["format"]["duration"]) - 9) < .1
        def pixels(second):
            data = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(second), "-i", str(video), "-frames:v", "1",
                                   "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], capture_output=True, check=True).stdout
            return np.frombuffer(data, dtype=np.uint8).reshape(360, 640, 3)
        for second in (3.5, 5.5):
            assert pixels(second)[280, 110, 1] > 180 and pixels(second)[280, 110, 0] < 100
        assert pixels(4.5)[280, 110, 0] > 220 and pixels(4.5)[280, 110, 1] > 110
        assert np.max(np.abs(pixels(7.5)[330, 20].astype(int) - [12, 18, 30])) < 12
        # A transparent corner alone cannot detect the expired chart lingering
        # behind the annotation. Check the previous bar's occupied region too.
        assert np.max(np.abs(pixels(7.5)[280, 110].astype(int) - [12, 18, 30])) < 12
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1"],
                             capture_output=True, check=True).stdout
        samples = np.frombuffer(pcm, dtype="<f4")
        rms = lambda values: float(np.sqrt(np.mean(values ** 2)))
        middle = rms(samples[48000:96000])
        assert .01 < middle < .2 and float(np.max(np.abs(samples))) < .5
        assert rms(samples[:4800]) < middle * .2 and rms(samples[-4800:]) < middle * .2
        package = mcp.call("project_package", projectId="cache-demo", expectedRevision=revision, outPath="editable.cvkpkg")
        restored, _, _ = unpack_project(package["path"], str(project / "package-check"))
        source_handoff = verify_source_handoff(mcp, "cache-demo", project, project / "package-check")
        assert restored.sequence.tracks[2].clips[0].effects[0]["params"]["content"] == "CODE + EDITABLE TIMELINE"
        assert sum(len(track.clips) for track in restored.sequence.tracks) == 8
        result = {"output": str(video), "projectPackage": package["path"], "runtime": runtime,
                  "benchmark": {"trials": 3, "dimensions": [640, 360], "fps": 24, "profile": "final",
                      "cold216FramesRenderSeconds": summary(cold), "warmZeroFramesThreePlansSeconds": summary(warm),
                      "repair24FramesRenderSeconds": summary(partial_times), "fullChart72FramesRenderSeconds": summary(full_times),
                      "registrationSeconds": registration, "repairRegistrationSeconds": repair_registration,
                      "scope": "Render timings include Python/FFmpeg startup and raw-frame hashing; exclude plan/register/edit/export. Warm timings include three plan RPCs and input/cache hashing. No cross-machine speed claim."},
                  "checks": {"warmCacheAfterExternalOutputDeletion": True, "coveringOffset": True, "changedFields": revised["changedFields"],
                      "absoluteRawFrameHashesEqualFullSlice": True, "atomicLocalRepairUndoRedo": True,
                      "unchangedOutsideRepairPixels": True, "repairedPixels": True, "alphaCompositeCorner": True,
                      "expiredShotClearedBehindAlpha": True,
                      "nativeTitlePreserved": True, "audioNonSilentUnclippedFades": True, "fullDecode216Frames": True, "packageRoundtrip": True, "sourceHandoff": source_handoff},
                  "unassessed": ["continuous visual playback", "subjective listening", "artistic quality", "hostile renderer isolation", "cross-machine reproducibility"]}
        write_json(evidence / "result.json", result)
        print(json.dumps({"video": str(video), "evidence": str(evidence / "result.json"), "benchmark": result["benchmark"]}))
    finally:
        mcp.close(output)


if __name__ == "__main__":
    main()
