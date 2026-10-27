"""FFmpeg graph operations for effect mixing, adjustment layers and mattes."""
from __future__ import annotations

import copy
import math

from cutvoke.domain.compositing import effect_compositing, region_expression, validate_sequence


def mix(base, changed, settings, parts, counter, *, gate="1"):
    settings = effect_compositing(settings)
    stamp = counter[0]
    counter[0] += 1
    root = f"mixfx{stamp}"
    opacity = settings.get("opacity", 1)
    weight = f"({opacity:.9f})*({region_expression(settings.get('region'))})*({gate})"
    mode = settings.get("blendMode", "normal")
    # Straight colors must be interpolated in premultiplied space when an
    # effect changes alpha (mask/keying); otherwise invisible RGB leaves halos.
    parts.append(f"[{base}]format=gbrap,split=2[{root}base][{root}ref]")
    parts.append(f"[{changed}][{root}ref]scale2ref=w=iw:h=ih[{root}size][{root}unused]")
    parts.append(f"[{root}unused]nullsink")
    if mode != "normal":
        formula = {"multiply": "A*B/255", "screen": "255-(255-A)*(255-B)/255",
                   "add": "min(255,A+B)", "difference": "abs(A-B)"}[mode]
        parts.append(f"[{root}base]split=2[{root}orig][{root}blend]")
        parts.append(f"[{root}blend][{root}size]blend=c0_expr='{formula}':c1_expr='{formula}':c2_expr='{formula}':c3_expr='B':shortest=1[{root}mode]")
        left, right = root + "orig", root + "mode"
    else:
        left, right = root + "base", root + "size"
    parts.append(f"[{left}]format=gbrap,premultiply=inplace=1[{root}a]")
    parts.append(f"[{right}]format=gbrap,premultiply=inplace=1[{root}b]")
    parts.append(f"[{root}a][{root}b]blend=all_expr='A*(1-({weight}))+B*({weight})':shortest=1,unpremultiply=inplace=1[{root}out]")
    return root + "out"


def adjustment(renderer, clip, current, parts, counter, origin):
    start = float(clip.timeline_start.to_fraction()) - origin
    end = float(clip.timeline_end.to_fraction()) - origin
    stamp = counter[0]
    counter[0] += 1
    root = f"adjust{stamp}"
    parts.append(f"[{current}]split=2[{root}base][{root}source]")
    parts.append(f"[{root}source]setpts=PTS-{start:.9f}/TB[{root}local]")
    enabled = copy.copy(clip)
    enabled.effects = [effect for effect in clip.effects if effect.get('enabled', True)]
    color = enabled.color_grade()
    grade = renderer._animated_color(enabled, color) or (
        'eq=' + ':'.join(f'{key}={color[key]:.9f}' for key in ('brightness', 'contrast', 'saturation')))
    parts.append(f"[{root}local]{grade}[{root}graded]")
    changed = renderer._apply_fx(enabled, root + "graded", parts, counter)
    parts.append(f"[{changed}]setpts=PTS+{start:.9f}/TB[{root}global]")
    from cutvoke.domain.keyframes import expression
    opacity = expression(clip.keyframes.get('opacity', []), f'(T-{start:.9f})', clip.transform()['opacity'])
    return mix(root + "base", root + "global", {}, parts, counter,
               gate=f"gte(T,{start:.9f})*lt(T,{end:.9f})*({opacity})")


def matte(renderer, clip, picture, sequence, get_input, parts, counter, fps, *, stack=()):
    setting = clip.compositing.get("matte")
    if not setting:
        return picture
    if clip.id in stack:
        raise ValueError("track matte dependency cycle")
    source = next((candidate for track in sequence.tracks for candidate in track.clips
                   if candidate.id == setting["clipId"]), None)
    if source is None:
        raise ValueError(f"missing matte source {setting['clipId']}")
    window = getattr(renderer._render_ctx, 'window', None)
    start = max(float(source.timeline_start.to_fraction()), float(clip.timeline_start.to_fraction()), window[0] if window else 0)
    stop = min(float(source.timeline_end.to_fraction()), float(clip.timeline_end.to_fraction()), window[1] if window else math.inf)
    if stop <= start:
        if setting.get('invert'): return picture
        label = f'emptymatte{counter[0]}';counter[0] += 1
        parts.append(f'[{picture}]format=rgba,colorchannelmixer=aa=0[{label}]')
        return label
    stamp = counter[0]
    counter[0] += 1
    root = f"matte{stamp}"
    before = getattr(renderer._render_ctx, "alpha", False)
    renderer._render_ctx.alpha = True
    try:
        source_picture = renderer._build_clip_canvas(source, sequence, get_input, parts, counter, fps)
        source_picture = matte(renderer, source, source_picture, sequence, get_input, parts, counter, fps, stack=(*stack, clip.id))
    finally:
        renderer._render_ctx.alpha = before
    shift = float((source.timeline_start - clip.timeline_start).to_fraction())
    duration = float(clip.duration.to_fraction())
    parts.append(f"[{source_picture}]setpts=PTS-STARTPTS+{shift:.9f}/TB[{root}shift]")
    parts.append(f"color=black@0:s={sequence.width}x{sequence.height}:r={fps}:d={duration:.9f},format=yuva420p[{root}bed]")
    parts.append(f"[{root}bed][{root}shift]overlay=eof_action=pass:shortest=0:format=auto,format=gbrap[{root}source]")
    if setting.get("channel", "alpha") == "alpha":
        parts.append(f"[{root}source]alphaextract[{root}mask]")
    else:
        luma = "(0.2126*r(X,Y)+0.7152*g(X,Y)+0.0722*b(X,Y))*alpha(X,Y)/255"
        parts.append(f"[{root}source]geq=r='{luma}':g='{luma}':b='{luma}':a=255,format=gray[{root}mask]")
    mask = root + "mask"
    if setting.get("invert"):
        parts.append(f"[{mask}]negate[{root}inverted]")
        mask = root + "inverted"
    parts.append(f"[{picture}]format=gbrap,split=2[{root}rgb][{root}alpha]")
    parts.append(f"[{root}alpha]alphaextract[{root}original]")
    parts.append(f"[{root}original][{mask}]blend=all_expr='A*B/255':shortest=1[{root}combined]")
    parts.append(f"[{root}rgb][{root}combined]alphamerge[{root}out]")
    return root + "out"
