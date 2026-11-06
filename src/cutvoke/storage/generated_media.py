"""Static, content-addressed handoff from external renderers. Never executes source.

The source archive is a separate handoff artifact, not a native procedural clip or
a render cache. Timeline edits continue to use the existing editing commands.
"""
from __future__ import annotations

from contextlib import contextmanager
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import zipfile


MAX_MANIFEST_BYTES = 256 * 1024
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_RENDER_BYTES = 512 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{64}\Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _object(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"{label} requires exactly: {', '.join(fields)}")


def _integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer in {minimum}..{maximum}")


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError(f"{label} must be nonempty text, at most 200 characters")


def _file_entry(value, root, label):
    name = value.get("path")
    if (not isinstance(name, str) or not name or len(name) > 240 or "\\" in name
            or ":" in name or "\x00" in name
            or any(part in ("", ".", "..") for part in name.split("/"))
            or PurePosixPath(name).is_absolute()):
        raise ValueError(f"{label}.path must be a normalized relative POSIX file path")
    expected = value.get("sha256")
    if not isinstance(expected, str) or not SHA.fullmatch(expected):
        raise ValueError(f"{label}.sha256 must be lowercase SHA256")
    path = root / name
    if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root):
        raise ValueError(f"{label}: symbolic links are not allowed")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f"{label}: file must stay inside the manifest directory")
    return resolved


def load_manifest(path):
    path = Path(path).expanduser().resolve(strict=True)
    with path.open("rb") as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("generated-media manifest exceeds 256 KiB")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate manifest key: {key}")
            result[key] = value
        return result
    manifest = json.loads(raw, object_pairs_hook=unique)
    _object(manifest, ("schemaVersion", "renderer", "entrypoint", "files", "params", "seed", "output"), "manifest")
    if type(manifest["schemaVersion"]) is not int or manifest["schemaVersion"] != 1:
        raise ValueError("unsupported generated-media schemaVersion; expected 1")
    _object(manifest["renderer"], ("name", "version", "build"), "renderer")
    for key, value in manifest["renderer"].items():
        _text(value, f"renderer.{key}")
    if not isinstance(manifest["params"], dict):
        raise ValueError("params must be a JSON object")
    _integer(manifest["seed"], 0, 2**32 - 1, "seed")
    files = manifest["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= 128:
        raise ValueError("files must contain 1..128 declared inputs")
    names = set()
    for item in files:
        _object(item, ("path", "sha256", "role"), "files item")
        _file_entry(item, path.parent, "input")
        if item["path"] in names or item["path"] == "manifest.json":
            raise ValueError("duplicate or reserved input path")
        names.add(item["path"])
        if item["role"] not in ("source", "lockfile", "asset", "font", "license"):
            raise ValueError("invalid input role")
    if not any(item["path"] == manifest["entrypoint"] and item["role"] == "source" for item in files):
        raise ValueError("entrypoint must be a declared source file")
    out = manifest["output"]
    _object(out, ("path", "sha256", "width", "height", "fps", "durationFrames", "alphaMode", "colorSpace", "audioMode"), "output")
    _file_entry(out, path.parent, "output")
    if out["path"] in names or out["path"] == "manifest.json":
        raise ValueError("output must be separate from source inputs")
    for key in ("width", "height"):
        _integer(out[key], 16, 4096, f"output.{key}")
    _integer(out["durationFrames"], 1, 18000, "output.durationFrames")
    _object(out["fps"], ("num", "den"), "output.fps")
    _integer(out["fps"]["num"], 1, 120000, "fps.num")
    _integer(out["fps"]["den"], 1, 10000, "fps.den")
    fps = Fraction(out["fps"]["num"], out["fps"]["den"])
    if not 1 <= fps <= 120 or out["durationFrames"] / fps > 600:
        raise ValueError("output must be 1..120 fps and at most 600 seconds")
    if out["alphaMode"] not in ("opaque", "straight") or out["colorSpace"] != "bt709" or out["audioMode"] != "none":
        raise ValueError("v1 requires opaque/straight alpha, bt709, and separate audio (audioMode=none)")
    suffix = Path(out["path"]).suffix.lower()
    if suffix not in (".mov", ".mp4") or (out["alphaMode"] == "straight" and suffix != ".mov"):
        raise ValueError("v1 accepts MP4 or MOV; straight alpha requires ProRes 4444 MOV")
    # Reject NaN/Infinity and bound serialized parameters; source bytes are inert.
    canonical(manifest)
    return manifest, path.parent


def _copy_checked(source, destination, expected, limit):
    digest = hashlib.sha256()
    total = 0
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        while chunk := incoming.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise ValueError("generated-media file size limit exceeded")
            digest.update(chunk)
            outgoing.write(chunk)
    if digest.hexdigest() != expected:
        raise ValueError(f"SHA256 mismatch: {source.name}")
    return total


@contextmanager
def snapshot(manifest_path):
    """Hash while copying, then validate only the frozen bytes; clean up on failure."""
    manifest, root = load_manifest(manifest_path)
    with tempfile.TemporaryDirectory(prefix="cutvoke-generated-") as folder:
        stage = Path(folder)
        budget = MAX_INPUT_BYTES
        for item in manifest["files"]:
            target = stage / "inputs" / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            budget -= _copy_checked(_file_entry(item, root, "input"), target, item["sha256"], budget)
        out = manifest["output"]
        rendered = stage / ("render" + Path(out["path"]).suffix.lower())
        _copy_checked(_file_entry(out, root, "output"), rendered, out["sha256"], MAX_RENDER_BYTES)
        yield manifest, stage, rendered


def inspect_render(manifest, path, ffprobe):
    """Count decoded frames, validate stream semantics. No artistic/alpha acceptance."""
    args = [ffprobe, "-v", "error", "-protocol_whitelist", "file", "-count_frames", "-show_frames",
            "-read_intervals", "%+#18001",
            "-show_entries", "stream=codec_type,codec_name,profile,width,height,pix_fmt,avg_frame_rate,r_frame_rate,nb_read_frames,start_time,color_space,color_transfer,color_primaries,sample_aspect_ratio",
            "-show_entries", "frame=best_effort_timestamp_time,color_space,color_transfer,color_primaries,width,height:format=format_name:stream_side_data=rotation",
            "-of", "json", str(path)]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as error:
        raise ValueError("generated-media inspection exceeded 30 seconds") from error
    if result.returncode or result.stderr.strip():
        raise ValueError(f"generated-media decode failed: {result.stderr[-400:]}")
    metadata = json.loads(result.stdout)
    streams = metadata.get("streams", [])
    if "mov" not in metadata.get("format", {}).get("format_name", "").split(","):
        raise ValueError("generated-media output must use an MP4/MOV container")
    if len(streams) != 1 or streams[0].get("codec_type") != "video":
        raise ValueError("v1 requires one video stream and no audio; keep audio on native tracks")
    stream = streams[0]
    out = manifest["output"]
    fps = Fraction(out["fps"]["num"], out["fps"]["den"])
    for field in ("width", "height"):
        if stream.get(field) != out[field]:
            raise ValueError(f"rendered {field} does not match manifest")
    if (Fraction(stream.get("avg_frame_rate", "0")) != fps
            or Fraction(stream.get("r_frame_rate", "0")) != fps
            or int(stream.get("nb_read_frames", 0)) != out["durationFrames"]):
        raise ValueError("rendered fps/frame count does not match manifest (CFR required)")
    frames = metadata.get("frames", [])
    if any(frame.get(key) != out[key] for frame in frames for key in ("width", "height")):
        raise ValueError("rendered frame dimensions must remain constant")
    if any(item.get("rotation", 0) != 0 for item in stream.get("side_data_list", [])):
        raise ValueError("rendered video must bake orientation into pixels (no rotation metadata)")
    if len(frames) != out["durationFrames"] or any(
            "best_effort_timestamp_time" not in frame or abs(
                Fraction(frame["best_effort_timestamp_time"]) - Fraction(index, 1) / fps
            ) > Fraction(1, 1000000) for index, frame in enumerate(frames)):
        raise ValueError("rendered frame timestamps do not match the zero-based CFR clock")
    if Fraction(stream.get("start_time", "0")) != 0:
        raise ValueError("rendered video must start at time zero")
    if stream.get("sample_aspect_ratio", "1:1") not in ("1:1", "N/A"):
        raise ValueError("rendered video requires square pixels")
    if any(frame.get(key, stream.get(key)) != "bt709" for frame in frames
           for key in ("color_space", "color_transfer", "color_primaries")):
        raise ValueError("rendered video must explicitly declare BT.709 primaries, transfer and matrix")
    if out["alphaMode"] == "straight" and (stream.get("codec_name") != "prores"
            or stream.get("profile") not in ("4444", "XQ")
            or not stream.get("pix_fmt", "").startswith("yuva")):
        raise ValueError("straight alpha requires ProRes 4444 with an alpha pixel format")
    spec = {key: value for key, value in out.items() if key not in ("path", "sha256")}
    identity = {key: value for key, value in manifest.items() if key != "output"}
    identity["output"] = spec
    return {"bundleId": hashlib.sha256(canonical(manifest)).hexdigest(),
            "renderKey": hashlib.sha256(canonical(identity)).hexdigest(),
            "assetId": "asset_" + out["sha256"], "spec": spec, "stream": stream,
            "validation": {"contentHashes": "verified", "frameContract": "verified",
                           "alphaPixels": "not_assessed", "creativeAcceptance": "not_assessed",
                           "runtimeExecuted": False, "dependencyCompleteness": "declared_only"}}


def publish_source_archive(manifest, stage, directory, bundle_id):
    """An inert source handoff, independently portable from the editable media package."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{bundle_id}.cvsource.zip"
    temporary = stage / "source.zip"
    # Stored entries and explicit platform/permissions avoid changing archive
    # identity with the host OS or zlib version when the declared bytes agree.
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
        def write(name, payload):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)
        write("manifest.json", canonical(manifest))
        for item in sorted(manifest["files"], key=lambda item: item["path"]):
            write(item["path"], (stage / "inputs" / item["path"]).read_bytes())
    digest = file_hash(temporary)
    # Temporary file on the destination volume; exclusive link publication keeps
    # concurrent imports from exposing partial archives or replacing old bytes.
    fd, pending_name = tempfile.mkstemp(dir=directory, prefix="source-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as pending, temporary.open("rb") as incoming:
            shutil.copyfileobj(incoming, pending)
            pending.flush()
            os.fsync(pending.fileno())
        try:
            os.link(pending_name, target)
        except FileExistsError:
            if target.is_symlink() or file_hash(target) != digest:
                raise ValueError("existing generated source archive failed integrity check")
    finally:
        Path(pending_name).unlink(missing_ok=True)
    return {"path": str(target), "sha256": digest,
            "includesRenderedMedia": False, "includedInProjectPackage": False}
