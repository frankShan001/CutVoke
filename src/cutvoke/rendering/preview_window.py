"""Seek-safe bounded video evaluation, shared by posters and playback.

Keep original effect/keyframe clocks while decoding/evaluating only a bounded
interval. Stateful or unsupported treatments retain the authoritative renderer.
"""
from __future__ import annotations
import copy
import math
from functools import lru_cache
from cutvoke.domain.rational import Rational

# These filters are stateless, except for the explicitly bounded trail history.
SAFE = frozenset({"gblur", "grayscale", "colorbalance", "glow", "mask", "chromatic",
    "trail", "colorize", "lens", "shatter", "vibrance", "lightWeave", "opticalField",
    "invert", "sepia", "vflip", "hflip", "sharpen", "curves", "lut3d", "vignette"})


@lru_cache(maxsize=256)
def _static_picture(path, size, modified):
    from PIL import Image
    try:
        with Image.open(path) as image:
            return not getattr(image, 'is_animated', False)
    except (OSError, ValueError):
        return False


def bounded(project, window, renderer):
    if window is None:
        return project, {}, None
    start, end = window
    history = 0
    retained = set()
    retained_starts = []
    pictures = set()
    for track in project.sequence.tracks:
        if track.kind != "video" or not track.visible:
            continue
        for clip in track.clips:
            if clip.hidden:
                continue
            a, b = float(clip.timeline_start.to_fraction()), float(clip.timeline_end.to_fraction())
            # Transitions can consume the predecessor beyond its selected range.
            if any(e.get("effectId", "").startswith("cutvoke.transition.") and e.get("enabled", True)
                   for e in clip.effects) and a < end and b > start - 8:
                return project, {}, None
            if not a < end or not b > start:
                continue
            if (clip.nested is not None or clip.freeze_at is not None or
                    clip.frame_interpolation != "none"):
                return project, {}, None
            from cutvoke.rendering.render import _find_animations
            if _find_animations(clip):
                return project, {}, None
            for effect in renderer._enabled_fx(clip):
                key, params = renderer._fx_key_params(effect)
                if key not in SAFE:
                    return project, {}, None
                if key == "trail":
                    history = max(history, max(2, min(20, int(float(params.get("frames", 4))))) - 1)
            if clip.speed_curve is not None or clip.speed.to_fraction() != 1:
                # Retain this clip's authoritative timing, but do not force all
                # independent light/matte lanes to regenerate their history.
                retained.add(clip.id)
                retained_starts.append(a)
                continue
            # Video effect clocks require a regular source frame lattice.
            from cutvoke.rendering.render import _is_image_src
            if _is_image_src(clip.asset_ref.source_path):
                import os
                path = clip.asset_ref.source_path
                try:
                    stat = os.stat(path)
                except OSError:
                    # Keep the renderer's actionable missing-source error.
                    return project, {}, None
                if not _static_picture(path, stat.st_size, stat.st_mtime_ns):
                    return project, {}, None
                pictures.add(clip.id)
            else:
                info = renderer.probe_media(clip.asset_ref.source_path)
                fps = str(project.sequence.fps.to_fraction())
                if info.get("avg_frame_rate") != fps or info.get("r_frame_rate") != fps:
                    return project, {}, None
                path=clip.asset_ref.source_path
                if not renderer._regular_timestamps(path,
                        float(clip.source_start.to_fraction()),float(clip.duration.to_fraction()),
                        project.sequence.fps.to_fraction()):
                    return project, {}, None
    fps = project.sequence.fps.to_fraction()
    origin_frame = max(0, math.floor(start * float(fps) + 1e-7) - history)
    trim_origin = origin_frame / float(fps)
    origin = min([trim_origin, *retained_starts])
    snapshot = copy.deepcopy(project)
    clocks = {}
    for track in snapshot.sequence.tracks:
        if track.kind != "video" or not track.visible:
            continue
        for clip in track.clips:
            a, b = float(clip.timeline_start.to_fraction()), float(clip.timeline_end.to_fraction())
            if clip.hidden or clip.id in retained or not a < end or not b > trim_origin:
                continue
            offset = max(0., trim_origin - a)
            clocks[clip.id] = (offset, clip.duration)
            # A static picture has no source clock to advance. Advancing it
            # makes image2 repeatedly decode identical PNGs before trim starts.
            clip.source_start = (Rational.of(0) if clip.id in pictures else
                                 clip.source_start + Rational.from_float(offset))
            clip.timeline_start = Rational.from_float(max(a, trim_origin))
            clip.timeline_end = Rational.from_float(min(b, end))
    return snapshot, clocks, origin
