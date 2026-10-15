"""Reproducible local edit over stdio MCP; stdlib + an installed CutVoke + FFmpeg.

Creates its own illustrated media and synthetic score. Never reads the user's library.
Run with --help. Keep the output directory: it contains the editable database and media.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import queue
import time
import wave
import zlib
import urllib.parse
import urllib.request


WIDTH, HEIGHT, DURATION = 1280, 720, 12


def png(path: Path, variant: int):
    """Original vector-like landscape drawn into RGB pixels; no remote assets."""
    palettes = [((29, 34, 63), (195, 134, 124), (243, 196, 135)),
                ((25, 57, 73), (133, 183, 174), (244, 218, 159)),
                ((34, 35, 67), (127, 132, 182), (243, 186, 148))]
    top, bottom, sun = palettes[variant]
    pixels = bytearray(WIDTH * HEIGHT * 3)
    for y in range(HEIGHT):
        k = min(1, y / (HEIGHT * 0.82))
        color = bytes(round(a + (b - a) * k) for a, b in zip(top, bottom))
        pixels[y * WIDTH * 3:(y + 1) * WIDTH * 3] = color * WIDTH

    def span(y, left, right, color):
        if not 0 <= y < HEIGHT:
            return
        left, right = max(0, left), min(WIDTH, right)
        if right > left:
            pixels[(y * WIDTH + left) * 3:(y * WIDTH + right) * 3] = bytes(color) * (right - left)

    def circle(cx, cy, radius, color):
        for y in range(cy - radius, cy + radius + 1):
            dx = int(math.sqrt(max(0, radius * radius - (y - cy) ** 2)))
            span(y, cx - dx, cx + dx, color)

    def polygon(points, color):
        for y in range(max(0, min(p[1] for p in points)), min(HEIGHT, max(p[1] for p in points))):
            edges = []
            for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
                if min(y1, y2) <= y < max(y1, y2):
                    edges.append(round(x1 + (y - y1) * (x2 - x1) / (y2 - y1)))
            edges.sort()
            for a, b in zip(edges[::2], edges[1::2]):
                span(y, a, b, color)

    circle(960 - variant * 150, 265 + variant * 24, 67, sun)
    polygon([(0, 440), (150, 365), (280, 425), (500, 300), (760, 430), (980, 340),
             (1280, 420), (1280, 720), (0, 720)], (71, 82, 111))
    polygon([(0, 520), (230, 450), (400, 495), (660, 380), (920, 500), (1120, 448),
             (1280, 505), (1280, 720), (0, 720)], (43, 61, 79))
    for y in range(554, HEIGHT):
        shade = tuple(round(a + (y - 554) * 0.10) for a in (26, 53, 67))
        span(y, 0, WIDTH, shade)
    for i in range(15):
        y = 575 + i * 9
        x = (i * 127 + variant * 81) % 1000
        span(y, x, x + 180, (59, 82, 96))
    polygon([(0, 643), (140, 603), (340, 636), (510, 720), (0, 720)], (22, 39, 47))
    # Foreground pines leave the centre free for editable titles.
    for cx, cy, size in ((100, 604, 92), (200, 630, 68), (1150, 645, 104)):
        polygon([(cx, cy - size), (cx - size // 3, cy), (cx + size // 3, cy)], (20, 39, 46))
        polygon([(cx, cy - size * 2 // 3), (cx - size // 2, cy + 20), (cx + size // 2, cy + 20)], (20, 39, 46))
    raw = b"".join(b"\x00" + pixels[y * WIDTH * 3:(y + 1) * WIDTH * 3] for y in range(HEIGHT))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">2I5B", WIDTH, HEIGHT, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def score(path: Path):
    rate = 48000
    chords = [(261.63, 329.63, 392.00), (220.00, 261.63, 329.63),
              (174.61, 220.00, 261.63), (196.00, 246.94, 293.66)]
    samples = bytearray()
    for i in range(rate * DURATION):
        t = i / rate
        chord = chords[min(3, int(t / 3))]
        local = t % 3
        envelope = min(1, local / 0.12, (3 - local) / 0.25)
        signal = sum(math.sin(2 * math.pi * frequency * t) for frequency in chord) * 0.055 * envelope
        value = round(signal * 32767)
        samples.extend(struct.pack("<hh", value, value))
    with wave.open(str(path), "wb") as output:
        output.setparams((2, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(samples)


class MCP:
    def __init__(self, command, database, output, effects):
        temporary = output / "temp"
        temporary.mkdir()
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "CUTVOKE_FONT_CACHE": str(output / "font-cache"),
               "TEMP": str(temporary), "TMP": str(temporary), "TMPDIR": str(temporary)}
        if effects:
            env["CUTVOKE_EFFECTS_PATH"] = str(effects.resolve())
        self.log = (output / "mcp.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen([*command, "mcp", "--data", str(database)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            text=True, encoding="utf-8", env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.serial, self.transcript = 0, []
        self.responses = queue.Queue()
        def read():
            for line in self.process.stdout:
                self.responses.put(line)
            self.responses.put(None)
        threading.Thread(target=read, daemon=True).start()
        try:
            self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                     "clientInfo": {"name": "cutvoke-skill-demo", "version": "1.0.0"}})
            self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            self.process.stdin.flush()
        except BaseException:
            self.close(output)
            raise

    def rpc(self, method, params):
        self.serial += 1
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.serial,
                                            "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        line = self.responses.get(timeout=180)
        if not line:
            raise RuntimeError("MCP closed unexpectedly; inspect mcp.log")
        response = json.loads(line)
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["result"]

    def call(self, tool_name, output_frames=None, **arguments):
        result = self.rpc("tools/call", {"name": tool_name, "arguments": arguments})
        data = json.loads(next(c["text"] for c in result["content"] if c["type"] == "text"))
        self.transcript.append({"tool": tool_name, "arguments": arguments, "ok": not result.get("isError", False)})
        if result.get("isError") or data.get("ok") is False:
            raise RuntimeError({"tool": tool_name, "result": data})
        if output_frames:
            for i, content in enumerate(c for c in result["content"] if c["type"] == "image"):
                (output_frames / f"frame-{i + 1}.png").write_bytes(base64.b64decode(content["data"]))
        return data

    def wait(self, name, project, job, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.call(name, projectId=project, jobId=job)
            state = result.get("state", result.get("status"))
            if state in ("succeeded", "completed"):
                return result
            if state in ("failed", "cancelled", "interrupted"):
                raise RuntimeError(result)
            time.sleep(1)
        raise TimeoutError(f"{name} timed out; job {job} may still be running")

    def close(self, output):
        (output / "calls.json").write_text(json.dumps(self.transcript, indent=2), encoding="utf-8")
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.log.close()


class WebUploads:
    """Exercise the optional loopback Web upload route with owned demo media."""
    def __init__(self, command, database, output, effects):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "CUTVOKE_FONT_CACHE": str(output / "font-cache"),
               "TEMP": str(output / "temp"), "TMP": str(output / "temp"), "TMPDIR": str(output / "temp")}
        if effects:
            env["CUTVOKE_EFFECTS_PATH"] = str(effects.resolve())
        self.log = (output / "upload-service.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen([*command, "serve", "--host", "127.0.0.1", "--port", str(port),
            "--data", str(database), "--log", str(output / "upload-service.jsonl")], stdout=self.log, stderr=self.log,
            env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), start_new_session=(os.name != "nt"))
        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    runtime = self.request("/api/v1/runtime")
                    if Path(runtime["dataPath"]).resolve() != database.resolve():
                        raise RuntimeError("The chosen port belongs to another data directory; use a fresh run")
                    break
                except urllib.error.HTTPError as error:
                    raise RuntimeError(f"Local upload API returned HTTP {error.code}; inspect the service configuration, do not bypass authentication") from error
                except urllib.error.URLError:
                    if self.process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Local upload service did not start; inspect upload-service.log")
                    time.sleep(0.2)
        except BaseException:
            self.close()
            raise

    def request(self, route, data=None):
        if not route.startswith("/api/v1/") or route.startswith("//"):
            raise ValueError("Only this helper's local CutVoke API routes are accepted")
        request = urllib.request.Request(self.url + route, data=data,
            headers={"Content-Type": "application/octet-stream"} if data is not None else {})
        with self.opener.open(request, timeout=20) as response:
            return json.load(response)

    def import_files(self, paths):
        ids = [self.request("/api/v1/assets?" + urllib.parse.urlencode({"name": path.name}), path.read_bytes())["assetId"] for path in paths]
        ledger = {a["assetId"]: a for a in self.request("/api/v1/assets")["assets"]}
        assets = [ledger[asset_id] for asset_id in ids]
        if not all(a.get("available") for a in assets):
            raise RuntimeError("Supported browser upload did not produce available demo assets")
        return assets

    def close(self):
        # Use our Popen handle/tree, never a PID supplied by an HTTP response.
        if self.process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log.close()


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="NEW directory for isolated data, media, MP4 and evidence")
    parser.add_argument("--engine", type=Path, help="optional packaged cutvoke-engine executable; otherwise use this Python")
    parser.add_argument("--effects-dir", type=Path, help="one category directory containing effect folders")
    parser.add_argument("--effect-id", default="cutvoke.transition.crossfade")
    parser.add_argument("--import-mode", choices=("upload", "mcp"), default="mcp",
                        help="mcp: persistent import shared with the editor; upload: loopback Web upload API")
    args = parser.parse_args()
    if args.engine and not args.engine.is_file():
        parser.error("--engine must name an existing trusted CutVoke executable")
    if args.effects_dir and not args.effects_dir.is_dir():
        parser.error("--effects-dir must be an existing category directory")
    for dependency in ("ffmpeg", "ffprobe"):
        if not shutil.which(dependency):
            parser.error(f"{dependency} is missing from PATH; install FFmpeg, then rerun")
    output = args.output.resolve()
    if output.exists():
        parser.error("--output must be a new directory; use a different name to preserve existing results")
    output.mkdir(parents=True)
    media, frames = output / "sources", output / "frames"
    media.mkdir()
    frames.mkdir()
    for i, name in enumerate(("dawn", "lake", "dusk")):
        png(media / f"{name}.png", i)
    score(media / "quiet-score.wav")
    command = [str(args.engine.resolve())] if args.engine else [sys.executable, "-m", "cutvoke"]
    mcp, uploads = None, None
    project, lease = "trail-notes", None
    try:
        mcp = MCP(command, output / "data" / "projects.sqlite", output, args.effects_dir)
        mcp.call("editor_help")
        mcp.call("runtime")
        if mcp.call("project_list").get("projects"):
            raise RuntimeError("Expected an empty isolated project library")
        tools = mcp.rpc("tools/list", {})["tools"]
        # Query the running engine, rather than copying its API schemas into this skill.
        for name in ("edit.batch", "clip.insert", "clip.keyframe", "clip.audio", "caption.add", "export.enqueue"):
            mcp.call("editor_help", topic="command", command=name)
        mcp.call("editor_help", topic="command", command="effect.setTransition", effectId=args.effect_id)
        paths = [media / name for name in ("dawn.png", "lake.png", "dusk.png", "quiet-score.wav")]
        uploads = WebUploads(command, output / "data" / "projects.sqlite", output, args.effects_dir)
        if args.import_mode == "upload":
            imported = uploads.import_files(paths)
        else:
            imported = mcp.call("media_import", paths=[str(path) for path in paths])["assets"]
        browser_assets = {a["assetId"]: a for a in uploads.request("/api/v1/assets")["assets"]}
        browser_available = all(browser_assets.get(a["assetId"], {}).get("available") for a in imported)
        if not browser_available:
            raise RuntimeError("Imported demo media is unavailable to the human editor")
        for asset in imported:
            mcp.call("media_inspect", assetId=asset["assetId"])
        created = mcp.call("create_project", projectId=project, name="Trail Notes · editable demo", width=WIDTH, height=HEIGHT, fps=30)
        revision = created["revision"]
        lease = mcp.call("edit_lock", projectId=project, action="acquire", owner="cutvoke-skill-demo", ttlSeconds=120)["lease"]["leaseId"]
        rat = lambda n: {"num": str(n), "den": "1"}
        edits = []
        for i, asset in enumerate(imported[:3]):
            clip = ("dawn", "lake", "dusk")[i]
            edits.append({"type": "clip.insert", "payload": {"trackId": "visual", **({"createTrackKind": "video"} if i == 0 else {}), "clipId": clip,
                "assetId": asset["assetId"], "timelineStart": rat(i * 4), "timelineEnd": rat((i + 1) * 4)}})
            if i:
                edits.append({"type": "effect.setTransition", "payload": {"clipId": clip, "effectId": args.effect_id, "params": {"duration": 0.6}}})
            for t, scale in ((0, 1.0), (4, 1.045)):
                edits.append({"type": "clip.keyframe", "payload": {"clipId": clip, "action": "add", "param": "scale", "time": rat(t), "value": scale}})
        edits.extend([
            {"type": "clip.insert", "payload": {"trackId": "score", "createTrackKind": "audio", "clipId": "quiet-score",
                "assetId": imported[3]["assetId"], "timelineStart": rat(0), "timelineEnd": rat(DURATION)}},
            {"type": "clip.audio", "payload": {"clipId": "quiet-score", "volume": 0.75, "fadeIn": 0.6, "fadeOut": 0.8}},
            {"type": "clip.insert", "payload": {"trackId": "titles", "createTrackKind": "text", "clipId": "headline",
                "timelineStart": rat(0), "timelineEnd": rat(DURATION), "text": {"content": "TRAIL NOTES", "fontSize": 76, "color": "#fff1d6", "x": 0.5, "y": 0.25}}},
            {"type": "clip.insert", "payload": {"trackId": "titles-small", "createTrackKind": "text", "clipId": "subhead",
                "timelineStart": rat(0), "timelineEnd": rat(DURATION), "text": {"content": "A quieter weekend", "fontSize": 28, "color": "#fff1d6", "x": 0.5, "y": 0.35}}}
        ])
        for i, text in enumerate(("Leave the noise behind.", "Follow a slower rhythm.", "Bring a little calm home.")):
            edits.append({"type": "caption.add", "payload": {"captionId": f"caption-{i+1}", "text": text,
                "start": rat(i * 4), "end": rat((i + 1) * 4), "fontSize": 34, "x": 0.5, "y": 0.87}})
        payload = {"commands": edits}
        preview = mcp.call("command_preview", projectId=project, type="edit.batch", payload=payload, expectedRevision=revision)
        result = mcp.call("command_apply", projectId=project, type="edit.batch", payload=payload, expectedRevision=revision,
                          editLeaseId=lease, actorId="cutvoke-skill-demo", commandId="assemble-trail-notes")
        repeated = mcp.call("command_apply", projectId=project, type="edit.batch", payload=payload, expectedRevision=revision,
                            editLeaseId=lease, actorId="cutvoke-skill-demo", commandId="assemble-trail-notes")
        if repeated["revision"] != result["revision"]:
            raise RuntimeError("Idempotent replay unexpectedly advanced the revision")
        revision = result["revision"]
        mcp.call("edit_lock", projectId=project, action="release", leaseId=lease)
        lease = None
        mcp.call("preview_frames", projectId=project, expectedRevision=revision, times=[1, 3.8, 4.2, 7.8, 8.2, 11], width=1280, output_frames=frames)
        preflight = mcp.call("command_preview", projectId=project, type="project.preflight", payload={}, expectedRevision=revision)
        report = preflight["changedEntities"][0]["report"]
        if not report["readyToRender"]:
            raise RuntimeError(report)
        prepared = mcp.call("preview_prepare", projectId=project, expectedRevision=revision)
        ready = mcp.wait("preview_job", project, prepared["jobId"])
        job = mcp.call("command_apply", projectId=project, expectedRevision=revision, type="export.enqueue",
                       commandId="export-trail-notes", payload={"outPath": str(output / "trail-notes.mp4"), "quality": "high", "overwrite": False})
        exported = mcp.wait("export_job", project, job["changedEntities"][0]["jobId"])
        mcp.call("project_package", projectId=project, expectedRevision=revision, outPath=str(output / "trail-notes.cvkpkg"))
        video = output / "trail-notes.mp4"
        probe = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)]))
        streams = probe["streams"]
        v, a = next(s for s in streams if s["codec_type"] == "video"), next(s for s in streams if s["codec_type"] == "audio")
        if (v["width"], v["height"], v["r_frame_rate"], v["codec_name"], a["codec_name"]) != (WIDTH, HEIGHT, "30/1", "h264", "aac"):
            raise RuntimeError("Unexpected output streams")
        if abs(float(probe["format"]["duration"]) - DURATION) > 0.1:
            raise RuntimeError("Unexpected output duration")
        run(["ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"])
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-f", "s16le", "-ac", "1", "-ar", "48000", "-"], capture_output=True, timeout=120, check=True).stdout
        values = [item[0] / 32768 for item in struct.iter_unpack("<h", pcm)]
        peak = max(abs(x) for x in values)
        rms = math.sqrt(sum(x*x for x in values) / len(values))
        interval_rms = lambda start, end: math.sqrt(sum(x*x for x in values[int(start*48000):int(end*48000)]) / max(1, int((end-start)*48000)))
        if not 0.005 < rms < 0.5 or peak >= 0.98:
            raise RuntimeError("Audio is silent or clips")
        audio = {"peakDbfs": round(20 * math.log10(peak), 2), "rmsDbfs": round(20 * math.log10(rms), 2),
                 "startRms": interval_rms(0, 0.1), "middleRms": interval_rms(1, 1.1), "endRms": interval_rms(11.9, 12)}
        if not (audio["startRms"] < audio["middleRms"] and audio["endRms"] < audio["middleRms"]):
            raise RuntimeError("Expected endpoint audio fades")
        evidence = {"projectId": project, "revision": revision, "duration": DURATION, "size": [WIDTH, HEIGHT],
                    "fps": "30/1", "codecs": ["h264", "aac"], "toolCount": len(tools), "effectId": args.effect_id,
                    "preflightReady": report["readyToRender"], "idempotentReplay": True, "fullDecode": True,
                    "importMode": args.import_mode, "browserAssetsAvailable": browser_available,
                    "sha256": hashlib.sha256(video.read_bytes()).hexdigest(), "audio": audio,
                    "visualReview": "Inspect frames/frame-*.png and play trail-notes.mp4 before accepting artistic quality.",
                    "sources": "Original procedural illustrations and synthesized chords; no user data or external service.",
                    "previewState": ready.get("state", ready.get("status")), "exportState": exported.get("state", exported.get("status"))}
        (output / "result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps(evidence, indent=2))
    finally:
        try:
            if mcp:
                try:
                    if lease:
                        mcp.call("edit_lock", projectId=project, action="release", leaseId=lease)
                finally:
                    mcp.close(output)
        finally:
            if uploads:
                uploads.close()


if __name__ == "__main__":
    main()
