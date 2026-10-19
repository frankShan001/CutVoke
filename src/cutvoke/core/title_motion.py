"""Seek-safe title choreography, compiled to the same libass lane for every renderer.

Titles stay editable text. No raster overlays or generated media are stored. The
output frame clock is authoritative, including when rendering a later window.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction
from functools import lru_cache
import math
import unicodedata

from .keyframes import evaluate
from .model import Caption
from .rational import Rational


ENTRANCES = ("impact", "slide-left", "slide-up", "flip", "stagger", "blur-in")
EXITS = ("zoom-through", "slide-right", "scatter")
LOOPS = ("glitch", "wave", "float")


def needs_title_motion(params: dict, keyframes: dict) -> bool:
    return (any(keyframes.values()) or params.get("animInStyle") in ENTRANCES
            or params.get("animOutStyle") in EXITS
            or params.get("animLoopStyle") in LOOPS
            or float(params.get("animGlow", 0)) > 0
            or float(params.get("outerStrokeWidth", 0)) > 0
            or float(params.get("blockShadowX", 0)) != 0
            or float(params.get("blockShadowY", 0)) != 0)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _back(progress: float) -> float:
    t = progress - 1.0
    return 1.0 + 2.70158 * t ** 3 + 1.70158 * t ** 2


def _units(text: str) -> list[str]:
    """Keep combining marks, variation selectors and ZWJ clusters together."""
    units: list[str] = []
    for char in text:
        if units and (unicodedata.combining(char) or char in "\ufe0e\ufe0f\u200d"
                      or units[-1].endswith("\u200d")):
            units[-1] += char
        else:
            units.append(char)
    return units


@lru_cache(maxsize=32)
def _advances(font_path: str, text: str) -> tuple[float, ...]:
    from fontTools.ttLib import TTFont
    with TTFont(font_path, fontNumber=0, lazy=True) as font:
        cmap = font.getBestCmap() or {}
        metrics = font["hmtx"].metrics
        em = font["head"].unitsPerEm
        return tuple(sum(metrics.get(cmap.get(ord(char), ".notdef"), (em, 0))[0]
                         for char in unit if not unicodedata.combining(char)
                         and char not in "\ufe0e\ufe0f\u200d") / em
                     for unit in _units(text))


@dataclass(frozen=True)
class _Glyph:
    text: str
    x: float
    y: float
    scale: float
    rotation: float
    opacity: float = 1.0
    flip: float = 0.0
    blur: float = 0.0
    spacing: float = 0.0


def _at(cap: Caption, spec: dict, local: Fraction, width: int, height: int,
        font_path: str | None) -> tuple[list[_Glyph], bool]:
    params, keyframes = spec["params"], spec.get("keyframes", {})
    time = Rational.of(local.numerator, local.denominator)
    def lane(name: str, default: float) -> float:
        keys = keyframes.get(name, [])
        return evaluate(keys, time) if keys else default

    duration = float(cap.duration.to_fraction())
    now = float(local)
    enter = min(cap.animIn / 1000, duration) if cap.animInStyle != "none" else 0
    leave = (min(cap.animOut / 1000, max(0, duration - enter))
             if cap.animOutStyle != "none" else 0)
    strength = float(params.get("animStrength", 1))
    x, y = lane("x", cap.x * width), lane("y", cap.y * height)
    scale, rotation = lane("scale", cap.scale), lane("rotation", cap.rotation)
    opacity = lane("opacity", 1)
    base = _Glyph(cap.text, x, y, scale, rotation, opacity)
    split = (cap.animInStyle == "stagger" or cap.animOutStyle == "scatter"
             or cap.animLoopStyle == "wave")
    rows = cap.text.replace("\r\n", "\n").split("\n")
    glyphs: list[_Glyph] = []
    em = max(8, round(cap.fontSize * height / 1080)) * scale
    line_step = em * cap.lineSpacing * 1.25
    for row_index, row in enumerate(rows):
        row_y = y + (row_index - (len(rows) - 1) / 2) * line_step
        if not split:
            glyphs.append(replace(base, text=row, y=row_y))
            continue
        units = _units(row)
        advances = (_advances(font_path, row) if font_path
                    else tuple(1 if unicodedata.east_asian_width(u[0]) in "WF" else .6
                               for u in units))
        lengths = [advance * em for advance in advances]
        total = sum(lengths)
        cursor = x - (total / 2 if cap.align == "center" else total if cap.align == "right" else 0)
        for unit, length in zip(units, lengths):
            glyphs.append(replace(base, text=unit, x=cursor + length / 2, y=row_y))
            cursor += length

    rendered: list[_Glyph] = []
    count = len(glyphs)
    stagger = min(float(params.get("animStaggerMs", 45)) / 1000,
                  enter * .65 / max(1, count - 1))
    individual = max(.001, enter - stagger * max(0, count - 1))
    for index, glyph in enumerate(glyphs):
        g = glyph
        if enter and now < enter:
            p = _clamp(now / enter)
            if cap.animInStyle == "impact":
                factor = ((1 + 1.8 * strength) + (.86 - (1 + 1.8 * strength))
                          * (1 - (1 - p / .65) ** 3) if p < .65
                          else .86 + .14 * (1 - (1 - (p - .65) / .35) ** 2))
                g = replace(g, scale=g.scale * factor,
                            rotation=g.rotation - 10 * strength * (1 - p) ** 2,
                            opacity=g.opacity * _clamp(p * 8))
            elif cap.animInStyle in {"slide-left", "slide-up"}:
                offset = (1 - _back(p)) * strength
                g = replace(g, x=g.x - width * .45 * offset if cap.animInStyle == "slide-left" else g.x,
                            y=g.y + height * .4 * offset if cap.animInStyle == "slide-up" else g.y,
                            opacity=g.opacity * _clamp(p * 6))
            elif cap.animInStyle == "flip":
                g = replace(g, flip=85 * (1 - p) ** 2,
                            scale=g.scale * (.65 + .35 * _back(p)),
                            opacity=g.opacity * _clamp(p * 5))
            elif cap.animInStyle == "stagger":
                q = _clamp((now - index * stagger) / individual)
                g = replace(g, y=g.y + height * .3 * strength * (1 - q) ** 2,
                            scale=g.scale * max(.05, _back(q)),
                            rotation=g.rotation + (-1 if index % 2 else 1) * 24 * strength * (1 - q) ** 2,
                            opacity=g.opacity * _clamp(q * 5))
            elif cap.animInStyle == "blur-in":
                g = replace(g, blur=18 * strength * (1 - p) ** 2,
                            scale=g.scale * (1 + .35 * (1 - p)), opacity=g.opacity * p)
            elif cap.animInStyle == "scale":
                g = replace(g, scale=g.scale * (.6 + .4 * p), opacity=g.opacity * p)
            elif cap.animInStyle == "fade":
                g = replace(g, opacity=g.opacity * p)
            elif cap.animInStyle == "typewriter":
                # A single shaped prefix preserves native kerning for this style.
                visible = max(1, math.ceil(len(_units(g.text)) * p))
                g = replace(g, text="".join(_units(g.text)[:visible]))

        cycle = max(.3, cap.animLoopMs / 1000)
        if cap.animLoopStyle == "wave":
            phase = now / cycle * math.tau + index * .65
            g = replace(g, y=g.y + em * .14 * strength * math.sin(phase),
                        rotation=g.rotation + 7 * strength * math.sin(phase))
        elif cap.animLoopStyle == "float":
            # Move the shaped word together: preserve kerning and keep the
            # lettering's face, rim and offset shadow on one motion clock.
            phase = now / cycle * math.tau
            g = replace(g, y=g.y + em * .08 * strength * math.sin(phase),
                        rotation=g.rotation + 1.5 * strength * math.sin(phase))
        elif cap.animLoopStyle == "pulse":
            factor = 1 + .22 * math.sin(max(0, now - enter) / cycle * math.pi) ** 2
            g = replace(g, scale=g.scale * factor)
        elif cap.animLoopStyle == "blink":
            g = replace(g, opacity=g.opacity * (.27 + .73 * math.cos(now / cycle * math.pi) ** 2))

        if leave and now >= duration - leave:
            q = _clamp((now - (duration - leave)) / leave)
            if cap.animOutStyle == "zoom-through":
                g = replace(g, scale=g.scale * (1 + 5 * strength * q ** 3),
                            opacity=g.opacity * (1 - _clamp((q - .5) * 2)))
            elif cap.animOutStyle == "slide-right":
                g = replace(g, x=g.x + width * .65 * strength * q ** 2,
                            opacity=g.opacity * (1 - q))
            elif cap.animOutStyle == "scatter":
                direction = -1 if index % 2 else 1
                g = replace(g, x=g.x + direction * width * .35 * strength * q ** 2,
                            y=g.y + height * (.15 + (index % 3) * .12) * q ** 2,
                            rotation=g.rotation + direction * 95 * q,
                            opacity=g.opacity * (1 - q))
            elif cap.animOutStyle == "scale":
                g = replace(g, scale=g.scale * (1 - .4 * q), opacity=g.opacity * (1 - q))
            elif cap.animOutStyle == "fade":
                g = replace(g, opacity=g.opacity * (1 - q))
        rendered.append(g)
    glitch = cap.animLoopStyle == "glitch" and (now % cycle) < min(.16, cycle * .22)
    if glitch:
        dx = math.sin(now * 157) * 12 * strength * height / 1080
        rendered = [replace(g, x=g.x + dx, rotation=g.rotation + math.sin(now * 93) * strength)
                    for g in rendered]
    return rendered, glitch


def _timestamp(time: Fraction) -> str:
    # Floor BOTH sides of a shared frame boundary. Rounding a cue's in point up
    # makes libass miss that frame at 24/30/60 fps; padding creates ghost letters.
    cs = max(0, time.numerator * 100 // time.denominator)
    seconds, centiseconds = divmod(cs, 100)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def title_motion_events(cap: Caption, spec: dict, style: str, width: int, height: int,
                        *, font_path: str | None, escape, color) -> list[str]:
    fps = spec["fps"].to_fraction()
    begin, end = cap.start.to_fraction(), cap.end.to_fraction()
    window = spec.get("window")
    first_time = max(begin, Fraction(str(window[0]))) if window else begin
    last_time = min(end, Fraction(str(window[1]))) if window else end
    # Render windows arrive as floats; 14/24 may be represented just above
    # the exact tick. Do not skip that first frame when seeking a still.
    tolerance = Fraction(1, 1_000_000)
    first = math.ceil(first_time * fps - tolerance)
    last = math.ceil(last_time * fps - tolerance)
    glow = float(spec["params"].get("animGlow", 0)) * height / 1080
    glow_color = color(str(spec["params"].get("animColor", "#FF39D4")))
    strength = float(spec["params"].get("animStrength", 1))
    params = spec["params"]
    unit = height / 1080
    outer = float(params.get("outerStrokeWidth", 0)) * unit
    border = cap.strokeWidth * unit / 2
    rim_color = color(str(params.get("outerStrokeColor", "#4B9FAD")))
    depth_color = color(str(params.get("blockShadowColor", "#2B7183")))
    depth_x = float(params.get("blockShadowX", 0)) * unit
    depth_y = float(params.get("blockShadowY", 0)) * unit
    events: list[str] = []
    held: tuple | None = None
    held_start = Fraction(0)
    held_end = Fraction(0)

    def flush() -> None:
        if held is None:
            return
        start, finish = _timestamp(held_start), _timestamp(held_end)
        if start == finish:
            return
        for layer, text in held:
            events.append(f"Dialogue: {layer},{start},{finish},{style},,0,0,0,,{text}")

    for frame in range(first, last):
        at = Fraction(frame, 1) / fps
        glyphs, glitch = _at(cap, spec, at - begin, width, height, font_path)
        pieces = []
        if cap.background and cap.panelWidth > 0 and cap.panelHeight > 0:
            # One panel follows the group motion; letter staggering/waves must
            # never duplicate the rectangle for every glyph.
            panel_cap = replace(cap, text="■", animLoopStyle=(
                "none" if cap.animLoopStyle == "wave" else cap.animLoopStyle),
                animInStyle="slide-up" if cap.animInStyle == "stagger" else cap.animInStyle,
                animOutStyle="fade" if cap.animOutStyle == "scatter" else cap.animOutStyle)
            panel = _at(panel_cap, spec, at - begin, width, height, font_path)[0][0]
            w, h = cap.panelWidth * width, cap.panelHeight * height
            px = panel.x + (w * panel.scale / 2 if cap.align == "left" else
                            -w * panel.scale / 2 if cap.align == "right" else 0)
            background = color(cap.background)
            alpha = round(255 - (255 - int(background[2:4], 16)) * _clamp(panel.opacity))
            drawing = (f"{{\\an5\\pos({px:.2f},{panel.y:.2f})\\p1"
                       f"\\fscx{panel.scale * 100:.2f}\\fscy{panel.scale * 100:.2f}"
                       f"\\frz{panel.rotation:.2f}\\frx{panel.flip:.2f}"
                       f"\\bord0\\shad0\\1c&H{background[4:]}&\\1a&H{alpha:02X}&}}"
                       f"m 0 0 l {w:.2f} 0 {w:.2f} {h:.2f} 0 {h:.2f}")
            pieces.append((0, drawing))
        # Per-glyph events use center anchors after font-metric layout. Whole
        # lines preserve left/right horizontal alignment with a centered Y anchor.
        split = cap.animInStyle == "stagger" or cap.animOutStyle == "scatter" or cap.animLoopStyle == "wave"
        anchor = 5 if split else {"left": 4, "center": 5, "right": 6}.get(cap.align, 5)
        for glyph in glyphs:
            opacity = _clamp(glyph.opacity)
            if not glyph.text.strip() or opacity <= .001:
                continue
            alpha = round((1 - opacity) * 255)
            geometry = (f"\\an{anchor}\\pos({glyph.x:.2f},{glyph.y:.2f})"
                        f"\\fscx{glyph.scale * 100:.2f}\\fscy{glyph.scale * 100:.2f}"
                        f"\\frz{glyph.rotation:.2f}\\frx{glyph.flip:.2f}"
                        f"\\alpha&H{alpha:02X}&\\blur{glyph.blur:.2f}")
            text = escape(glyph.text)
            if depth_x or depth_y:
                # ASS positive Z rotation is counterclockwise. Rotate the
                # word-local offset, then apply the same animated scale.
                angle = math.radians(glyph.rotation)
                dx = (depth_x * math.cos(angle) + depth_y * math.sin(angle)) * glyph.scale
                dy = (-depth_x * math.sin(angle) + depth_y * math.cos(angle)) * glyph.scale
                shifted = geometry.replace(f"\\pos({glyph.x:.2f},{glyph.y:.2f})",
                    f"\\pos({glyph.x + dx:.2f},{glyph.y + dy:.2f})")
                pieces.append((0, f"{{{shifted}\\bord{border + outer:.2f}"
                               f"\\1c{depth_color}&\\3c{depth_color}&\\shad0}}{text}"))
            if outer:
                pieces.append((1, f"{{{geometry}\\bord{border + outer:.2f}"
                               f"\\1c{rim_color}&\\3c{rim_color}&\\shad0}}{text}"))
            if glow:
                halo = (f"{{{geometry}\\bord{glow:.2f}\\blur{max(1, glow * .6):.2f}"
                        f"\\1a&HFF&\\3c{glow_color}&\\shad0}}{text}")
                pieces.append((1, halo))
            if glitch:
                for direction, channel in ((-1, "#32F5FF"), (1, "#FF39D4")):
                    shifted = geometry.replace(f"\\pos({glyph.x:.2f},{glyph.y:.2f})",
                        f"\\pos({glyph.x + direction * 16 * strength * height / 1080:.2f},{glyph.y:.2f})")
                    pieces.append((2, f"{{{shifted}\\1c{color(channel)}&\\bord0\\shad0}}{text}"))
            pieces.append((3, f"{{{geometry}}}{text}"))
        state = tuple(pieces)
        boundary = Fraction(frame + 1, 1) / fps
        if state == held:
            held_end = boundary
        else:
            flush()
            held, held_start, held_end = state, at, boundary
    flush()
    return events
