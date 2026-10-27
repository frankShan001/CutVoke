"""Auditable cut-list interchange. Unsupported editorial data is never silent.

OTIO JSON follows the ASWF schema; FCPXML follows Apple's 1.10 document model.
EDL is restricted to one integer-rate, non-drop-frame video cut list. This is
not an AAF writer, and none of these formats embeds source media.
"""
from __future__ import annotations
from fractions import Fraction
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse, unquote
from urllib.request import url2pathname
import xml.etree.ElementTree as ET

from cutvoke.domain.model import Sequence, Track, Clip, AssetReference, new_id
from cutvoke.domain.rational import Rational


def diagnostic(items, code, path, message, severity="error"):
    items.append(dict(code=code, path=path, message=message, severity=severity))


def _seconds(value): return float(value.to_fraction())
def _rat(value):
    fraction = value if isinstance(value, Fraction) else Fraction(str(value)).limit_denominator(1_000_000_000)
    return Rational.of(fraction.numerator, fraction.denominator)
def _time(value, rate): return {"OTIO_SCHEMA": "RationalTime.1", "value": float(value) * rate, "rate": rate}
def _range(start, duration, rate): return {"OTIO_SCHEMA": "TimeRange.1", "start_time": _time(start, rate), "duration": _time(duration, rate)}
def _duration(value): return Fraction(str(value["value"])) / Fraction(str(value["rate"]))
def _fcptime(value): return Fraction(value[:-1]) if value.endswith("s") else Fraction(value)
def _fcpstr(value): return str(value.to_fraction()) + "s"


def _fcpxml_asset_audio(source):
    """Declare verified embedded audio, never guess that a video is silent.

    Apple's FCPXML asset attributes describe sources, channels per source and
    sample rate. This cut-list adapter maps one mono/stereo source; a complex
    source layout must be handled explicitly rather than silently flattened.
    """
    from cutvoke.rendering.render import DEFAULT_FFPROBE
    result = subprocess.run([DEFAULT_FFPROBE, "-v", "error", "-show_entries",
        "stream=codec_type,channels,sample_rate", "-of", "json", str(source)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    if result.returncode:
        raise ValueError("Cannot verify source audio: " + result.stderr[-300:])
    data = json.loads(result.stdout)
    streams = data.get("streams")
    if not isinstance(streams, list) or not streams or any(
            not isinstance(stream, dict) or stream.get("codec_type") not in
            ("video", "audio", "subtitle", "data", "attachment") for stream in streams):
        raise ValueError("Source probe did not return any media streams")
    audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not audio:
        return {"hasAudio": "0"}
    if len(audio) != 1:
        raise ValueError("Embedded audio with multiple source streams is unsupported; export a single mono/stereo source first")
    channels, rate = int(audio[0].get("channels", 0)), int(audio[0].get("sample_rate", 0))
    if channels not in (1, 2) or not 1 <= rate <= 384000:
        raise ValueError("Embedded audio requires a verified mono/stereo channel count and sample rate")
    return {"hasAudio": "1", "audioSources": "1", "audioChannels": str(channels), "audioRate": str(rate)}


def local_path(url):
    parsed = urlparse(url)
    if parsed.scheme == "file":
        if parsed.netloc not in ("", "localhost"): raise ValueError("Remote file authorities are unsupported")
        return url2pathname(unquote(parsed.path))
    if parsed.scheme and not (len(parsed.scheme) == 1 and len(url) > 2 and url[1] == ":"):
        raise ValueError("Only local media references are supported")
    return str(Path(url).resolve())


def _issues(sequence, format, items):
    if sequence.captions: diagnostic(items, "CAPTIONS_UNMAPPED", "captions", "Caption text/style is not represented in this cut-list subset")
    if sequence.markers: diagnostic(items, "MARKERS_UNMAPPED", "markers", "Timeline markers are not mapped")
    if sequence.multicam: diagnostic(items, "MULTICAM_UNMAPPED", "multicam", "Bake the camera decisions before interchange")
    if sequence.audio_buses or sequence.master_audio: diagnostic(items, "AUDIO_MIX_UNMAPPED", "audioMix", "Bus/master processing is not mapped")
    if sequence.reframe != "stretch": diagnostic(items, "REFRAME_UNMAPPED", "reframe", "Canvas reframe is CutVoke metadata; other editors do not reproduce it", "warning" if format == "otio" else "error")
    if sequence.exposed_properties: diagnostic(items, "EXPOSED_PROPERTIES_UNMAPPED", "exposedProperties", "Exposed composition controls require the CutVoke project/package format")
    for track in sequence.tracks:
        if track.kind not in ("video", "audio"): diagnostic(items, "TRACK_UNMAPPED", track.id, "Only media tracks are supported")
        if track.audio_mix: diagnostic(items, "TRACK_MIX_UNMAPPED", track.id, "Track audio processing is not mapped")
        if format != "otio" and (track.muted or not track.visible): diagnostic(items, "TRACK_STATE_UNMAPPED", track.id, "Track mute/visibility is not mapped in this adapter")
        for clip in track.clips:
            if clip.compositing or clip.sequence_ref or clip.property_links or clip.motion_path or clip.property_overrides or clip.role == 'adjustment':
                diagnostic(items, "COMPOSITION_UNMAPPED", clip.id, "Matte references, adjustments, shared instances, overrides, spatial paths and property links require baking before cut-list interchange")
            if format != "otio" and clip.hidden: diagnostic(items, "HIDDEN_CLIP_UNMAPPED", clip.id, "Hidden clips are not mapped in this adapter")
            if clip.nested or clip.effects or clip.keyframes or clip.speed_curve or clip.speed.to_fraction() != 1 or clip.freeze_at is not None:
                diagnostic(items, "CLIP_PROCESSING_UNMAPPED", clip.id, "Nested clips, effects, keyframes, speed changes and freezes are not mapped")
            if clip.volume.to_fraction() != 1 or _seconds(clip.fade_in) or _seconds(clip.fade_out):
                diagnostic(items, "CLIP_AUDIO_UNMAPPED", clip.id, "Clip gain and fades are not mapped")
    if format in ("edl", "fcpxml") and (len(sequence.tracks) != 1 or sequence.tracks[0].kind != "video"):
        diagnostic(items, "SINGLE_VIDEO_TRACK_REQUIRED", "tracks", "This adapter supports one video cut list; additional tracks are omitted only when strict=false")


def export_timeline(sequence, format="otio", strict=True):
    if format not in ("otio", "edl", "fcpxml"):
        return {"format": format, "content": None, "diagnostics": [dict(code="UNSUPPORTED_FORMAT", path="format", severity="error", message="Supported: OTIO JSON, CMX3600 EDL and FCPXML cut subsets. AAF is not implemented.")]}
    issues = []
    _issues(sequence, format, issues)
    fps = float(sequence.fps.to_fraction())
    if format == "edl" and (sequence.fps.den != 1 or not 1 <= fps <= 60):
        diagnostic(issues, "NDF_INTEGER_RATE_REQUIRED", "fps", "EDL supports integer non-drop-frame rates from 1 to 60 only")
    if strict and any(issue["severity"] == "error" for issue in issues):
        return {"format": format, "content": None, "diagnostics": issues}
    tracks = [track for track in sequence.tracks if track.kind in ("video", "audio")]
    if format == "otio":
        children = []
        for track in tracks:
            clips, position = [], 0.0
            for clip in sorted(track.clips, key=lambda item: item.timeline_start.to_fraction()):
                start, end = _seconds(clip.timeline_start), _seconds(clip.timeline_end)
                if start < position - 1e-8:
                    diagnostic(issues, "OVERLAP_UNMAPPED", clip.id, "Overlapping clips cannot be represented in an OTIO Track")
                    continue
                if start > position + 1e-8:
                    clips.append({"OTIO_SCHEMA": "Gap.1", "name": "Gap", "source_range": _range(0, start-position, fps), "effects": [], "markers": [], "metadata": {}})
                clips.append({"OTIO_SCHEMA": "Clip.2", "name": clip.id, "metadata": {}, "enabled": not clip.hidden,
                    "source_range": _range(_seconds(clip.source_start), end-start, fps), "effects": [], "markers": [],
                    "media_references": {"DEFAULT_MEDIA": {"OTIO_SCHEMA": "ExternalReference.1", "name": "", "metadata": {},
                        "target_url": Path(clip.asset_ref.source_path).resolve().as_uri(), "available_range": None}},
                    "active_media_reference_key": "DEFAULT_MEDIA"})
                position = end
            children.append({"OTIO_SCHEMA": "Track.1", "name": track.id, "kind": "Video" if track.kind == "video" else "Audio",
                "enabled": track.visible and (track.kind != "audio" or not track.muted),
                "metadata": {"cutvoke": {"muted": track.muted, "visible": track.visible}}, "source_range": None,
                "effects": [], "markers": [], "children": clips})
        document = {"OTIO_SCHEMA": "Timeline.1", "name": sequence.name or sequence.id, "global_start_time": _time(0, fps),
            "metadata": {"cutvoke": {"width": sequence.width, "height": sequence.height, "fps": sequence.fps.to_json(), "reframe": sequence.reframe}},
            "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "metadata": {}, "source_range": None,
                "effects": [], "markers": [], "children": children}}
        content = json.dumps(document, ensure_ascii=False, indent=2)
    elif format == "edl":
        rate = round(fps)
        def tc(value):
            frames = round(_seconds(value)*rate)
            if abs(frames/rate - _seconds(value)) > 1e-7: raise ValueError("EDL edit times must fall on sequence frames")
            return f'{frames//(3600*rate):02}:{frames//(60*rate)%60:02}:{frames//rate%60:02}:{frames%rate:02}'
        lines = ["TITLE: " + (sequence.name or sequence.id).replace("\n", " "), "FCM: NON-DROP FRAME", f"* FPS: {rate}"]
        for index, clip in enumerate(next((track.clips for track in tracks if track.kind == "video"), []), 1):
            if index > 999: raise ValueError("CMX3600 is limited to 999 events")
            lines += [f'{index:03}  AX       V     C        {tc(clip.source_start)} {tc(clip.source_start+clip.duration)} {tc(clip.timeline_start)} {tc(clip.timeline_end)}',
                "* FROM CLIP NAME: " + Path(clip.asset_ref.source_path).name,
                "* SOURCE FILE: " + Path(clip.asset_ref.source_path).resolve().as_uri()]
        content = "\n".join(lines) + "\n"
    else:
        # Cache only within this export so repeated edits of one source do not
        # re-probe it, while a later export always inspects the current file.
        embedded_audio = {}
        video_clips = next((track.clips for track in tracks if track.kind == "video"), [])
        for clip in video_clips:
            source = str(Path(clip.asset_ref.source_path).resolve())
            if source in embedded_audio:
                continue
            try:
                embedded_audio[source] = _fcpxml_asset_audio(source)
            except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
                diagnostic(issues, "FCPXML_EMBEDDED_AUDIO_UNVERIFIED", clip.id,
                    "Cannot safely map embedded audio for " + source + ": " + str(exc))
        # Even opt-in lossy export must not invent a silent declaration for a
        # source whose audio is unknown or whose layout cannot be represented.
        if len(embedded_audio) != len({str(Path(clip.asset_ref.source_path).resolve()) for clip in video_clips}):
            return {"format": format, "content": None, "diagnostics": issues}
        root = ET.Element("fcpxml", version="1.10")
        resources = ET.SubElement(root, "resources")
        ET.SubElement(resources, "format", id="r1", frameDuration=str(Fraction(sequence.fps.den, sequence.fps.num))+"s",
            width=str(sequence.width), height=str(sequence.height), colorSpace="1-1-1 (Rec. 709)")
        project = ET.SubElement(ET.SubElement(ET.SubElement(root, "library"), "event", name="CutVoke"), "project", name=sequence.name or sequence.id)
        clips = sorted(next((track.clips for track in tracks if track.kind == "video"), []), key=lambda clip: clip.timeline_start.to_fraction())
        duration = max((clip.timeline_end for clip in clips), default=Rational.of(0))
        spine = ET.SubElement(ET.SubElement(project, "sequence", format="r1", duration=_fcpstr(duration), tcStart="0s", tcFormat="NDF"), "spine")
        position = Rational.of(0)
        for index, clip in enumerate(clips, 2):
            ref = f'r{index}'
            asset = ET.SubElement(resources, "asset", id=ref, name=Path(clip.asset_ref.source_path).name, start="0s",
                duration=_fcpstr(clip.source_start + clip.duration), hasVideo="1", format="r1")
            asset.attrib.update(embedded_audio[str(Path(clip.asset_ref.source_path).resolve())])
            ET.SubElement(asset, "media-rep", kind="original-media", src=Path(clip.asset_ref.source_path).resolve().as_uri())
            if clip.timeline_start > position:
                ET.SubElement(spine, "gap", name="Gap", offset=_fcpstr(position), start="0s", duration=_fcpstr(clip.timeline_start-position))
            ET.SubElement(spine, "asset-clip", name=clip.id, ref=ref, offset=_fcpstr(clip.timeline_start),
                start=_fcpstr(clip.source_start), duration=_fcpstr(clip.duration), format="r1")
            position = clip.timeline_end
        ET.indent(root)
        content = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
    if strict and any(issue["severity"] == "error" for issue in issues): content = None
    return {"format": format, "content": content, "diagnostics": issues}


def import_timeline(content, format="otio", strict=True):
    if not isinstance(content, str) or len(content.encode("utf-8")) > 4 * 1024**2:
        raise ValueError("Interchange content must be a text document up to 4 MiB")
    issues, sequence = [], Sequence(new_id("seq"), 1920, 1080, Rational.of(30))
    def clip(track, path, start, end, source_start, name=""):
        if len(track.clips) >= 5000: raise ValueError("Interchange supports at most 5000 clips per track")
        source = local_path(path)
        if not Path(source).is_file(): diagnostic(issues, "MEDIA_MISSING", name, "Relink missing local media: " + source, "warning")
        track.clips.append(Clip(id=new_id("clip"), asset_ref=AssetReference("", source_path=source),
            timeline_start=_rat(start), timeline_end=_rat(end), source_start=_rat(source_start)))
    if format == "otio":
        document = json.loads(content)
        if document.get("OTIO_SCHEMA") != "Timeline.1": raise ValueError("Expected an OTIO Timeline.1 document")
        metadata = document.get("metadata", {}).get("cutvoke", {})
        sequence.width, sequence.height = int(metadata.get("width", 1920)), int(metadata.get("height", 1080))
        raw_fps = metadata.get("fps")
        sequence.fps = Rational.from_json(**raw_fps) if raw_fps else _rat(document.get("global_start_time", {}).get("rate", 30) if document.get("global_start_time") else 30)
        sequence.name, sequence.reframe = document.get("name", ""), metadata.get("reframe", "stretch")
        stack = document.get("tracks", {})
        if stack.get("OTIO_SCHEMA") != "Stack.1": raise ValueError("Only an OTIO Timeline tracks Stack is supported")
        if stack.get("effects") or stack.get("markers") or stack.get("source_range"):
            diagnostic(issues, "OTIO_STACK_PROCESSING", "tracks", "Stack effects, markers and trims are unsupported")
        for item in stack.get("children", []):
            if item.get("OTIO_SCHEMA") != "Track.1":
                diagnostic(issues, "OTIO_STRUCTURE_UNSUPPORTED", "tracks", "Only flat OTIO tracks are supported"); continue
            kind = {"Video": "video", "Audio": "audio"}.get(item.get("kind"))
            if kind is None: diagnostic(issues, "OTIO_TRACK_KIND", item.get("name", ""), "Unknown track kind"); continue
            track, position = Track(new_id("track"), kind), Fraction(0)
            state = item.get("metadata", {}).get("cutvoke", {})
            track.muted = bool(state.get("muted", False)) or (kind == "audio" and not item.get("enabled", True))
            track.visible = bool(state.get("visible", True)) and item.get("enabled", True)
            sequence.tracks.append(track)
            if item.get("effects") or item.get("markers") or item.get("source_range"):
                diagnostic(issues, "OTIO_TRACK_PROCESSING", track.id, "Track effects, markers and track-level trims are unsupported")
            for child in item.get("children", []):
                schema = child.get("OTIO_SCHEMA")
                if schema not in ("Clip.1", "Clip.2", "Gap.1"):
                    diagnostic(issues, "OTIO_ITEM_UNSUPPORTED", child.get("name", ""), "Transitions and nested stacks are unsupported"); continue
                span = child.get("source_range")
                if not span: raise ValueError("Every clip/gap requires an explicit source_range")
                duration = _duration(span["duration"])
                if schema != "Gap.1":
                    reference = child.get("media_reference") or child.get("media_references", {}).get(child.get("active_media_reference_key", "DEFAULT_MEDIA"), {})
                    if reference.get("OTIO_SCHEMA") != "ExternalReference.1":
                        diagnostic(issues, "OTIO_MEDIA_REFERENCE", child.get("name", ""), "Only local ExternalReference media is supported")
                    else:
                        clip(track, reference["target_url"], position, position+duration, _duration(span["start_time"]), child.get("name", ""))
                        track.clips[-1].hidden = not child.get("enabled", True)
                    if child.get("effects") or child.get("markers"):
                        diagnostic(issues, "OTIO_CLIP_PROCESSING", child.get("name", ""), "Clip effects and markers are unsupported")
                position += duration
    elif format == "edl":
        if re.search(r'^FCM:\s*DROP', content, re.M): raise ValueError("Drop-frame EDL is unsupported")
        rate_match = re.search(r'^\* FPS:\s*(\d+)', content, re.M)
        rate = int(rate_match[1]) if rate_match else 30
        sequence.fps = Rational.of(rate)
        if not rate_match: diagnostic(issues, "EDL_RATE_ASSUMED", "fps", "No FPS comment; explicitly assuming 30 fps", "warning")
        track = Track(new_id("track"), "video"); sequence.tracks.append(track)
        def tc(value):
            h,m,s,f = map(int, value.split(":"))
            if m >= 60 or s >= 60 or f >= rate: raise ValueError("Invalid EDL timecode")
            return Fraction((h*3600+m*60+s)*rate+f, rate)
        lines = content.splitlines()
        for index, line in enumerate(lines):
            if not re.match(r'^\d{3}\s', line):
                if line.strip() and not line.startswith(("*", "TITLE:", "FCM:")):
                    diagnostic(issues, "EDL_CONTROL_UNSUPPORTED", str(index+1), "Unsupported EDL control: " + line)
                continue
            match = re.fullmatch(r'\d{3}\s+\S+\s+V\s+C\s+(\d+:\d+:\d+:\d+)\s+(\d+:\d+:\d+:\d+)\s+(\d+:\d+:\d+:\d+)\s+(\d+:\d+:\d+:\d+)\s*', line)
            if not match: diagnostic(issues, "EDL_EVENT_UNSUPPORTED", str(index+1), "Only video C cuts are supported"); continue
            source_in, source_out, record_in, record_out = map(tc, match.groups())
            if source_out-source_in != record_out-record_in: raise ValueError("EDL speed changes are unsupported")
            following = []
            for comment in lines[index+1:]:
                if re.match(r'^\d{3}\s', comment): break
                following.append(comment)
            source = next((value.removeprefix("* SOURCE FILE: ") for value in following if value.startswith("* SOURCE FILE: ")), None)
            if not source: raise ValueError("EDL needs a SOURCE FILE comment with the local source URI; relink before import")
            clip(track, source, record_in, record_out, source_in)
    elif format == "fcpxml":
        if "<!DOCTYPE" in content.upper() or "<!ENTITY" in content.upper(): raise ValueError("XML DTD/entity declarations are not accepted")
        root = ET.fromstring(content)
        if root.tag != "fcpxml": raise ValueError("Expected an FCPXML document")
        resources = {item.get("id"): item for item in root.findall("resources/*")}
        source_sequence = root.find(".//project/sequence")
        if source_sequence is None: raise ValueError("FCPXML must contain one project sequence")
        if len(root.findall(".//project/sequence")) != 1:
            diagnostic(issues, "FCPXML_EXTRA_PROJECTS", "projects", "Only the first project is imported")
        fmt = resources.get(source_sequence.get("format"))
        if fmt is not None:
            sequence.width, sequence.height = int(fmt.get("width", "1920")), int(fmt.get("height", "1080"))
            sequence.fps = _rat(1 / _fcptime(fmt.get("frameDuration", "1/30s")))
        track = Track(new_id("track"), "video"); sequence.tracks.append(track)
        spine = source_sequence.find("spine")
        if spine is None: raise ValueError("FCPXML sequence has no spine")
        for item in spine:
            if item.tag == "gap":
                if len(item): diagnostic(issues, "FCPXML_CONNECTED_ITEM", "gap", "Connected items on a gap are unsupported")
                continue
            if item.tag != "asset-clip" or item.get("lane", "0") != "0" or len(item):
                diagnostic(issues, "FCPXML_ITEM_UNSUPPORTED", item.get("name", item.tag), "Only flat primary-storyline asset-clips are supported"); continue
            asset = resources.get(item.get("ref"))
            media = asset.find("media-rep") if asset is not None else None
            url = media.get("src") if media is not None else (asset.get("src") if asset is not None else None)
            if not url: raise ValueError("FCPXML asset has no original-media URL")
            start, duration = _fcptime(item.get("offset", "0s")), _fcptime(item.get("duration", "0s"))
            clip(track, url, start, start+duration, _fcptime(item.get("start", "0s"))-_fcptime(asset.get("start", "0s")), item.get("name", ""))
            track.clips[-1].hidden = item.get("enabled", "1") != "1"
            if any(key not in {"name","ref","offset","start","duration","format","enabled"} for key in item.attrib):
                diagnostic(issues, "FCPXML_ATTRIBUTES_UNMAPPED", item.get("name", ""), "Additional clip attributes/roles are not mapped")
    else:
        raise ValueError("Supported import formats: otio, edl, fcpxml. AAF is not implemented")
    if strict and any(item["severity"] == "error" for item in issues):
        raise ValueError("Unsupported interchange items: " + json.dumps(issues, ensure_ascii=False))
    if not sequence.tracks or not any(track.clips for track in sequence.tracks): raise ValueError("No supported media clips were imported")
    if not 2 <= sequence.width <= 8192 or not 2 <= sequence.height <= 8192 or not 1 <= _seconds(sequence.fps) <= 240:
        raise ValueError("Invalid interchange canvas or frame rate")
    if sequence.reframe not in ("stretch", "contain", "cover"):
        raise ValueError("Invalid interchange reframe mode")
    return sequence, issues
