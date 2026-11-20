"""Exercise the talking-head prompt guide over real stdio MCP using synthetic media.

Run from an installed checkout with FFmpeg/ffprobe. No speech model, network,
private library or user media is used. Outputs stay in a new explicit directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import runpy
import struct
import subprocess
import sys
import wave
from fractions import Fraction

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
MCP = runpy.run_path(str(ROOT / "skills/cutvoke-editor/scripts/demo.py"))["MCP"]


def run(args):
    return subprocess.run(args, capture_output=True, check=True, timeout=180).stdout


def seconds(value):
    return float(Fraction(int(value["num"]), int(value["den"])))


def rational(value):
    value = Fraction(str(value))
    return {"num": str(value.numerator), "den": str(value.denominator)}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def source_media(output):
    """A center marker, three color sections, tones and known short/long pauses."""
    media = output / "sources"
    media.mkdir()
    colors = [(150, 35, 35), (30, 135, 55), (35, 60, 150)]
    for i, color in enumerate(colors):
        image = Image.new("RGB", (640, 360), color)
        draw = ImageDraw.Draw(image)
        draw.rectangle((296, 0, 344, 359), fill=(245, 220, 30))
        image.save(media / f"section-{i}.png")
    samples = bytearray()
    for i in range(48000 * 10):
        t = i / 48000
        silent = 2 <= t < 3.5 or 6 <= t < 7.5 or 4.8 <= t < 5
        frequency = 440 if t < 3.5 else 550 if t < 7.5 else 660
        value = 0 if silent else round(6000 * math.sin(2 * math.pi * frequency * t))
        samples.extend(struct.pack("<h", value))
    with wave.open(str(media / "tones.wav"), "wb") as stream:
        stream.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        stream.writeframes(samples)
    video = media / "synthetic-interview.mp4"
    command = ["ffmpeg", "-v", "error", "-n"]
    for i in range(3):
        command += ["-loop", "1", "-framerate", "30", "-t", str((3.5, 4, 2.5)[i]), "-i", str(media / f"section-{i}.png")]
    command += ["-i", str(media / "tones.wav"), "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
                "-map", "[v]", "-map", "3:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "10", str(video)]
    run(command)
    return video


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory, normally under .artifacts/")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    # This is a checkout verifier, even if Python's installed distribution is older.
    os.environ["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")
    source = source_media(output)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    mcp = MCP([sys.executable, "-m", "cutvoke"], output / "data/projects.sqlite", output, None)
    project, lease, revision = "synthetic-interview", None, "0"
    evidence = {"prompts": {}, "media": "Original color cards, center marker and synthetic tones; no speech or user data."}
    counter = 0

    def apply(kind, payload, preview=False):
        nonlocal revision, counter
        mcp.call("editor_help", topic="command", command=kind)
        arguments = dict(projectId=project, expectedRevision=revision, type=kind, payload=payload)
        if preview:
            mcp.call("command_preview", **arguments)
        counter += 1
        result = mcp.call("command_apply", **arguments, commandId=f"prompt-check-{counter}",
                          actorId="talking-head-guide-check", editLeaseId=lease)
        revision = mcp.call("project_summary", projectId=project)["revision"]
        return result

    def sequence():
        return mcp.call("project_query", projectId=project)["project"]["sequence"]

    def duration(seq):
        return max(seconds(c["timelineEnd"]) for t in seq["tracks"] for c in t["clips"])

    try:
        mcp.call("editor_help")
        mcp.call("runtime")
        require(not mcp.call("project_list")["projects"], "Expected an isolated empty library")
        mcp.call("create_project", projectId=project, name="Synthetic interview", width=640, height=360, fps=30, directory=str(output / "project"))
        asset = mcp.call("media_import", projectId=project, paths=[str(source)])["assets"][0]
        inspected = mcp.call("media_inspect", assetId=asset["assetId"])
        require((inspected["width"], inspected["height"]) == (640, 360) and inspected["has_audio"], "Unexpected input streams")
        require(abs(inspected["duration"] - 10) < 0.05, "Unexpected input duration")
        evidence["prompts"]["1"] = {"result": "imported and inspected", "assetId": asset["assetId"]}
        lease = mcp.call("edit_lock", projectId=project, action="acquire", owner="talking-head-guide-check", ttlSeconds=300)["lease"]["leaseId"]
        apply("clip.insert", {"trackId": "video", "createTrackKind": "video", "clipId": "source", "assetId": asset["assetId"],
              "timelineStart": rational(0), "timelineEnd": rational(10)}, preview=True)
        analysis_revision = revision
        analysis = apply("caption.autoSegment", {"audioPath": str(source), "minSilence": 0.8, "applyToProject": False})["changedEntities"][0]
        require(revision == analysis_revision, "Silence analysis unexpectedly changed the project")
        segments = analysis["segments"]
        require(len(segments) == 3, f"Expected three tone regions, got {segments}")
        gaps = [(seconds(a["end"]), seconds(b["start"])) for a, b in zip(segments, segments[1:])]
        for actual, expected in zip(gaps, [(2, 3.5), (6, 7.5)]):
            require(max(abs(a-b) for a, b in zip(actual, expected)) < 0.03, "Unexpected detected silence")
        # Use the fixture's reviewed boundaries, leaving 0.15s at each end.
        apply("edit.batch", {"commands": [{"type": "sequence.deleteRange", "payload": {"start": start, "end": end}}
              for start, end in [(6.15, 7.35), (2.15, 3.35)]]}, preview=True)
        require(abs(duration(sequence()) - 7.6) < 1e-8, "Pause cleanup duration is incorrect")
        evidence["prompts"]["2"] = {"result": "two long pauses shortened; 0.2s pause retained", "detectedGaps": gaps, "duration": 7.6}
        # Known fixture text/timing, deliberately not a claimed ASR transcript.
        apply("caption.add", {"captionId": "fixture-caption", "text": "Keep remove this", "start": rational(0), "end": rational(1.5),
              "words": [{"text": text, "start": rational(start), "end": rational(end)}
                        for text, start, end in [("Keep", 0, 0.5), ("remove", 0.5, 1), ("this", 1, 1.5)]]})
        before = sequence()
        apply("transcript.delete", {"captionId": "fixture-caption", "wordIndices": [1]}, preview=True)
        after = sequence()
        require(abs(duration(after) - 7.1) < 1e-8, "Word deletion did not cut media")
        require([w["text"] for w in after["captions"][0]["words"]] == ["Keep", "this"], "Unexpected retained words")
        evidence["prompts"]["3"] = {"result": "word selection cut 0.5s from media and captions", "duration": 7.1}
        apply("history.undo", {})
        require(sequence() == before, "Undo did not restore the exact sequence")
        evidence["prompts"]["9"] = {"result": "undo restored sequence including source offsets and caption words"}
        # A text-only spelling correction must preserve media, caption times and duration.
        apply("caption.update", {"captionId": "fixture-caption", "text": "Synthetic speaker"}, preview=True)
        corrected = sequence()
        require(corrected["tracks"] == before["tracks"], "Caption correction changed media")
        require(corrected["captions"][0]["text"] == "Synthetic speaker", "Caption correction missing")
        require(all(corrected["captions"][0][key] == before["captions"][0][key] for key in ("start", "end")), "Caption correction changed its interval")
        require(not corrected["captions"][0].get("words"), "Text correction retained stale word timings")
        evidence["prompts"]["5"] = {"result": "text corrected without changing media timing"}
        wide = sequence()
        apply("sequence.duplicate", {"sequenceId": "portrait", "name": "Portrait", "width": 360, "height": 640, "reframe": "cover"}, preview=True)
        portrait = sequence()
        require((portrait["width"], portrait["height"]) == (360, 640), "Wrong portrait canvas")
        all_sequences = mcp.call("project_query", projectId=project)["project"]["sequences"]
        require(any(s == wide for s in all_sequences), "Original wide sequence changed")
        evidence["prompts"]["6"] = {"result": "new 360x640 cover sequence; original 640x360 preserved"}
        frames = output / "frames"
        frames.mkdir()
        frame_result = mcp.call("preview_frames", projectId=project, expectedRevision=revision,
                               times=[2.1, 2.4, 4.9, 5.2], width=360, output_frames=frames)
        require(len(frame_result["frames"]) == 4 and len(list(frames.glob("*.png"))) == 4, "Expected four real preview images")
        for path, expected_color in zip(sorted(frames.glob("*.png")), [(150, 35, 35), (30, 135, 55), (30, 135, 55), (35, 60, 150)]):
            with Image.open(path) as image:
                require(image.size == (360, 640), "Unexpected preview dimensions")
                actual_color = image.convert("RGB").getpixel((20, 320))
                require(max(abs(a-b) for a, b in zip(actual_color, expected_color)) < 10, "Wrong source section at a cut")
                r, g, b = image.convert("RGB").getpixel((180, 320))
                require(r > 200 and g > 180 and b < 80, "Center marker lost during cover reframe")
        evidence["prompts"]["7"] = {"result": "four real cut-adjacent PNGs retain centered marker", "times": [2.1, 2.4, 4.9, 5.2]}
        preflight = apply("project.preflight", {})["changedEntities"][0]["report"]
        require(preflight["readyToRender"], f"Preflight failed: {preflight}")
        target = output / "project/exports/synthetic-interview-cleaned.mp4"
        job = apply("export.enqueue", {"outPath": str(target), "quality": "high", "overwrite": False})["changedEntities"][0]["jobId"]
        mcp.wait("export_job", project, job)
        info = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(target)]))
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        require((video["width"], video["height"]) == (360, 640), "Unexpected export dimensions")
        require(abs(float(info["format"]["duration"]) - 7.6) < 0.05, "Unexpected export duration")
        require(any(s["codec_type"] == "audio" for s in info["streams"]), "Audio stream missing")
        run(["ffmpeg", "-v", "error", "-xerror", "-i", str(target), "-f", "null", "-"])
        pcm = run(["ffmpeg", "-v", "error", "-i", str(target), "-vn", "-f", "s16le", "-ac", "1", "-ar", "48000", "-"])
        values = [n[0] / 32768 for n in struct.iter_unpack("<h", pcm)]
        def rms(start, end):
            interval = values[round(start*48000):round(end*48000)]
            return math.sqrt(sum(v*v for v in interval) / len(interval))
        require(rms(3.65, 3.75) < 0.01 and rms(3.3, 3.5) > 0.05, "Short pause or neighboring tone was lost")
        target_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        refusal = mcp.rpc("tools/call", {"name": "export", "arguments": {
            "projectId": project, "expectedRevision": revision, "outPath": str(target), "overwrite": False}})
        refusal_data = json.loads(next(c["text"] for c in refusal["content"] if c["type"] == "text"))
        require(refusal.get("isError") and refusal_data.get("error", {}).get("code") == "EXECUTION_FAILED"
                and "exists" in refusal_data["error"]["message"], "Existing export was not refused for a filename collision")
        require(hashlib.sha256(target.read_bytes()).hexdigest() == target_hash, "Existing export changed")
        require(hashlib.sha256(source.read_bytes()).hexdigest() == source_hash, "Source changed")
        evidence["prompts"]["8"] = {"result": "7.6s portrait export fully decoded with audio; repeat export refused", "sha256": target_hash,
                                    "shortPauseRms": rms(3.65, 3.75), "neighborToneRms": rms(3.3, 3.5)}
        mcp.call("edit_lock", projectId=project, action="release", leaseId=lease)
        lease = None
        require(not mcp.call("project_summary", projectId=project).get("editLease"), "Lease was not released")
        evidence["revision"] = revision
        evidence["boundaries"] = "No ASR, natural speech/listening, face tracking, host UI or artistic-quality validation. Inspect frames and play export."
        (output / "result.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
    finally:
        try:
            if lease:
                mcp.call("edit_lock", projectId=project, action="release", leaseId=lease)
        finally:
            mcp.close(output)


if __name__ == "__main__":
    main()
