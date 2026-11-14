#!/usr/bin/env python3
"""Render original code scenes externally, bridge them over real MCP, edit/export."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import shutil
import struct
import sys
import time
import wave

from PIL import Image, __version__ as pillow_version

from demo import MCP, run
from cutvoke.storage.project_directory import ProjectLibraryStore
from cutvoke.storage.projectpack import unpack_project


def rat(value):
    return {"num": str(value), "den": "1"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")



def verify_source_handoff(mcp, project_id, project_root, package_directory):
    """Exercise explicit recovery without importing or running restored code."""
    archives = mcp.call("generated_source", projectId=project_id, operation="list")["archives"]
    assert archives
    assert not list(package_directory.rglob("scene.py"))
    for item in archives:
        bundle = item["bundleId"]
        archived = package_directory / "media" / ".generated" / (bundle + ".cvsource.zip")
        assert digest(archived) == item["sha256"]
        inspected = mcp.call("generated_source", projectId=project_id, operation="inspect", bundleId=bundle)
        target = project_root / "exports" / "recovered-sources" / bundle
        restored = mcp.call("generated_source", projectId=project_id, operation="restore", bundleId=bundle, outPath=str(target))
        assert not restored["runtimeExecuted"]
        manifest = inspected["sourceArchive"]["manifest"]
        assert json.loads((target / "manifest.json").read_bytes()) == manifest
        for entry in manifest["files"]:
            assert digest(target / entry["path"]) == entry["sha256"]
        assert not (target / manifest["output"]["path"]).exists()
    return {"archives": len(archives), "zipAndInputHashesVerified": True,
            "unpackKeptInputsUnexpanded": True, "explicitRestoreWithoutExecution": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New isolated directory")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[3]
    project_root = output / "project"
    # Bind all media, source, review and delivery artifacts to this project root.
    evidence = project_root / "evidence"
    mcp = MCP([sys.executable, "-m", "cutvoke"], output / "library.sqlite", output, None)
    try:
        mcp.call("editor_help")
        mcp.call("create_project", projectId="code-bridge", name="Code scenes + editable CutVoke",
                 directory=str(project_root), width=640, height=360, fps=24)
        evidence.mkdir()
        runtime = {"python": platform.python_version(), "Pillow": pillow_version,
                   "ffmpeg": run(["ffmpeg", "-version"]).splitlines()[0]}
        def render(kind, accent, name):
            root = project_root / "sources" / name
            root.mkdir(parents=True)
            shutil.copy2(repo / "examples/generated-media/scene.py", root / "scene.py")
            shutil.copy2(repo / "LICENSE", root / "LICENSE")
            params = {"kind": kind, "accent": accent}
            write_json(root / "params.json", params)
            write_json(root / "runtime.json", runtime)
            (root / "requirements.txt").write_text(f"Pillow=={pillow_version}\n", encoding="utf8")
            video = root / ("render.mov" if kind == "annotation" else "render.mp4")
            started = time.monotonic()
            # Explicit trusted demo code; generated_media never starts a renderer.
            run([sys.executable, str(root / "scene.py"), "--params", str(root / "params.json"), "--output", str(video)])
            manifest = {"schemaVersion": 1, "renderer": {"name": "original-pillow-scenes", "version": "1",
                        "build": f"Python {runtime['python']}; Pillow {pillow_version}"},
                        "entrypoint": "scene.py", "params": params, "seed": 0,
                        "files": [{"path": name, "sha256": digest(root / name), "role": role} for name, role in
                                  (("scene.py", "source"), ("params.json", "asset"), ("runtime.json", "asset"),
                                   ("requirements.txt", "lockfile"), ("LICENSE", "license"))],
                        "output": {"path": video.name, "sha256": digest(video), "width": 640, "height": 360,
                                   "fps": {"num": 24, "den": 1}, "durationFrames": 72,
                                   "alphaMode": "straight" if kind == "annotation" else "opaque",
                                   "colorSpace": "bt709", "audioMode": "none"}}
            write_json(root / "manifest.json", manifest)
            return root, time.monotonic() - started
        receipts, timings = {}, {}
        for kind in ("title", "chart", "annotation"):
            root, elapsed = render(kind, [61, 232, 208], kind)
            timings[kind] = elapsed
            receipt = mcp.call("generated_media", projectId="code-bridge", operation="import", manifestPath=str(root / "manifest.json"))
            receipts[kind] = receipt
            repeated = mcp.call("generated_media", projectId="code-bridge", operation="import", manifestPath=str(root / "manifest.json"))
            assert receipt == repeated
        background = project_root / "sources/background.png"
        Image.new("RGB", (640, 360), (12, 18, 30)).save(background)
        audio = project_root / "sources/score.wav"
        with wave.open(str(audio), "wb") as stream:
            stream.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            stream.writeframes(b"".join(struct.pack("<h", round(2400 * math.sin(2 * math.pi * 220 * n / 48000))) for n in range(48000 * 9)))
        imported = mcp.call("media_import", projectId="code-bridge", paths=[str(background), str(audio)])["assets"]
        commands = [{"type": "clip.insert", "payload": {"trackId": "base", "createTrackKind": "video", "clipId": "background",
                    "assetId": imported[0]["assetId"], "timelineStart": rat(0), "timelineEnd": rat(9)}}]
        for index, kind in enumerate(("title", "chart", "annotation")):
            payload = {"trackId": "code", "clipId": kind, "assetId": receipts[kind]["assetId"],
                       "timelineStart": rat(index * 3), "timelineEnd": rat(index * 3 + 3)}
            if not index: payload["createTrackKind"] = "video"
            commands.append({"type": "clip.insert", "payload": payload})
            title = {"trackId": "titles", "clipId": "text-" + kind, "timelineStart": rat(index * 3), "timelineEnd": rat(index * 3 + 3),
                     "text": {"content": {"title": "CODE IN MOTION", "chart": "NATIVE EDITS + CODE", "annotation": "ALPHA OVERLAY"}[kind],
                              "fontSize": 140, "color": "#ffffff", "x": .5, "y": .15}}
            if not index: title["createTrackKind"] = "text"
            commands.append({"type": "clip.insert", "payload": title})
        commands.append({"type": "clip.insert", "payload": {"trackId": "sound", "createTrackKind": "audio", "clipId": "score",
                         "assetId": imported[1]["assetId"], "timelineStart": rat(0), "timelineEnd": rat(9)}})
        lease = mcp.call("edit_lock", projectId="code-bridge", action="acquire", owner="code-bridge-demo")["lease"]["leaseId"]
        revision = "0"
        def apply(kind, payload, command_id):
            nonlocal revision
            result = mcp.call("command_apply", projectId="code-bridge", expectedRevision=revision, editLeaseId=lease,
                              commandId=command_id, type=kind, payload=payload)
            revision = result["revision"]
            return result
        try:
            mcp.call("command_preview", projectId="code-bridge", expectedRevision=revision, type="edit.batch", payload={"commands": commands})
            apply("edit.batch", {"commands": commands}, "assemble")
            apply("clip.audio", {"clipId": "score", "fadeIn": rat(1), "fadeOut": rat(1), "volume": .6}, "fade")
            mcp.call("preview_frames", projectId="code-bridge", expectedRevision=revision, times=[1.5, 4.5, 7.5], width=640, output_frames=evidence)
            # Rerender only the chart; the other source outputs remain unchanged.
            root, elapsed = render("chart", [255, 166, 77], "chart-revised")
            timings["chart-revised"] = elapsed
            receipts["chart-revised"] = mcp.call("generated_media", projectId="code-bridge", operation="import", manifestPath=str(root / "manifest.json"))
            payload = {"clipIds": ["chart"], "assetId": receipts["chart-revised"]["assetId"]}
            mcp.call("command_preview", projectId="code-bridge", expectedRevision=revision, type="asset.swap", payload=payload)
            apply("asset.swap", payload, "replace-chart-only")
            apply("history.undo", {}, "undo-chart")
            apply("history.redo", {}, "redo-chart")
        finally:
            mcp.call("edit_lock", projectId="code-bridge", action="release", leaseId=lease)
        # These commands do not write the timeline and need no edit lease.
        def delivery(kind, payload, command_id):
            return mcp.call("command_apply", projectId="code-bridge", expectedRevision=revision,
                            commandId=command_id, type=kind, payload=payload)
        final_frames = evidence / "final"
        final_frames.mkdir()
        mcp.call("preview_frames", projectId="code-bridge", expectedRevision=revision, times=[1.5, 4.5, 7.5], width=640, output_frames=final_frames)
        delivery("project.preflight", {}, "preflight")
        export = delivery("export.enqueue", {"outPath": str(project_root / "exports/final.mp4"), "quality": "high"}, "export")
        job = mcp.wait("export_job", "code-bridge", export["changedEntities"][0]["jobId"])
        video = Path(job["result"]["output_path"])
        run(["ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"])
        info = json.loads(run(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", str(video)]))
        visual = next(item for item in info["streams"] if item["codec_type"] == "video")
        assert visual["width"] == 640 and visual["height"] == 360 and int(visual["nb_read_frames"]) == 216
        assert abs(float(info["format"]["duration"]) - 9) < .1
        # Check actual alpha bytes from the external ProRes source, separately
        # from the bridge's deliberately unassessed alpha/creative status.
        import subprocess
        alpha = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.5", "-i", str(project_root / "sources/annotation/render.mov"),
                                "-frames:v", "1", "-vf", "alphaextract", "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"], capture_output=True, check=True).stdout
        assert alpha[0] == 0 and min(alpha) == 0 and max(alpha) >= 250
        import numpy as np
        pictures = []
        for second in (1.5, 4.5, 7.5):
            raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(second), "-i", str(video),
                                  "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], capture_output=True, check=True).stdout
            pictures.append(np.frombuffer(raw, dtype=np.uint8).reshape(360, 640, 3))
        assert pictures[1][280, 110, 0] > 220 and pictures[1][280, 110, 1] > 110  # revised orange chart
        assert np.max(np.abs(pictures[2][330, 20].astype(int) - [12, 18, 30])) < 12  # transparent corner reveals base
        assert pictures[2][180, 548, 1] > 180  # annotation survives final compositing
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "48000",
                              "-f", "f32le", "pipe:1"], capture_output=True, check=True).stdout
        samples = np.frombuffer(pcm, dtype="<f4")
        def rms(values): return float(np.sqrt(np.mean(values ** 2)))
        middle = rms(samples[48000:96000])
        assert .01 < middle < .1 and float(np.max(np.abs(samples))) < .2
        assert rms(samples[:4800]) < middle * .2 and rms(samples[-4800:]) < middle * .2
        package = mcp.call("project_package", projectId="code-bridge", expectedRevision=revision, outPath="editable.cvkpkg")
        restored, _, _ = unpack_project(package["path"], str(project_root / "package-check"))
        source_handoff = verify_source_handoff(mcp, "code-bridge", project_root, project_root / "package-check")
        assert len(restored.sequence.tracks) == 4
        assert all(Path(clip.asset_ref.source_path).is_file() for track in restored.sequence.tracks if track.kind != "text" for clip in track.clips)
        # Read the persisted project after all edits, verify other code slots and native text survived.
        library = ProjectLibraryStore(str(output / "library.sqlite"))
        try:
            project = library.load("code-bridge")
            clips = {clip.id: clip for track in project.sequence.tracks for clip in track.clips}
            for kind in ("title", "annotation"):
                assert clips[kind].asset_ref.asset_id == receipts[kind]["assetId"]
            assert clips["chart"].asset_ref.asset_id == receipts["chart-revised"]["assetId"]
            assert clips["text-chart"].effects[0]["params"]["content"] == "NATIVE EDITS + CODE"
        finally:
            library.close()
        write_json(evidence / "result.json", {"projectId": "code-bridge", "revision": revision, "output": str(video),
                   "projectPackage": package["path"], "receipts": receipts, "renderSeconds": timings, "runtime": runtime,
                   "checks": {"frames": 216, "durationSeconds": 9, "fullDecode": True, "sourceAlphaPixels": True,
                              "finalCompositePixels": True, "audioNonSilentUnclippedFades": True,
                              "selectedSwapUndoRedo": True, "nativeTitles": True, "packageRoundtrip": True, "sourceHandoff": source_handoff},
                   "unassessed": ["continuous visual playback", "subjective listening", "artistic quality", "cross-machine reproducibility"],
                   "limitations": ["render cache index and undeclared dependencies are not packaged", "no arbitrary-code runtime or local frame cache"]})
        print(json.dumps({"projectRoot": str(project_root), "video": str(video), "evidence": str(evidence / "result.json")}, ensure_ascii=False))
    finally:
        mcp.close(output)


if __name__ == "__main__":
    main()
