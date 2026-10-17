# Editable title motion

`cutvoke.text` supports the same title animation parameters through the Web
inspector, MCP, HTTP commands and CLI. Restart an existing backend or reconnect
an existing MCP process after upgrading its source so its effect registry reloads.

| Lane | Added styles |
| --- | --- |
| Entrance (`animInStyle`) | `impact`, `slide-left`, `slide-up`, `flip`, `stagger`, `blur-in` |
| Exit (`animOutStyle`) | `zoom-through`, `slide-right`, `scatter` |
| Loop (`animLoopStyle`) | `glitch`, `wave` |

The existing fade, scale, typewriter, pulse and blink styles remain available.
Entrance and exit durations use `animIn` and `animOut` in milliseconds. The Web
inspector supplies a nonzero duration when selecting an animation for a title
whose duration was zero. Its three motion presets fill these values together.

Additional parameters:

| Parameter | Default | Range / meaning |
| --- | --- | --- |
| `animStrength` | 1 | 0.1–3, motion amplitude |
| `animStaggerMs` | 45 | 0–250, maximum delay between letters; fitted to entrance duration |
| `animGlow` | 0 | 0–24, colored outline glow, measured at 1080p |
| `animColor` | `#FF39D4` | Glow color in `#RRGGBB` form |
| `animLoopMs` | 1000 | 300–3000, loop duration |

Example `clip.insert` payload:

```json
{
  "trackId": "hero-type",
  "createTrackKind": "text",
  "clipId": "hero-word",
  "timelineStart": {"num": "0", "den": "1"},
  "timelineEnd": {"num": "2", "den": "1"},
  "text": {
    "content": "CUTVOKE", "fontSize": 180, "bold": true,
    "x": 0.5, "y": 0.5, "color": "#FFFFFF",
    "animInStyle": "impact", "animIn": 350,
    "animOutStyle": "zoom-through", "animOut": 300,
    "animLoopStyle": "glitch", "animLoopMs": 600,
    "animStrength": 1.4, "animGlow": 12, "animColor": "#FF39D4"
  }
}
```

Text clips now also render their existing `clip.keyframe` lanes: `x` and `y`
are absolute canvas anchors in pixels; `scale`, `rotation` and `opacity` use
the normal clip keyframe units. Clip-local time and interpolation are shared
with video keyframes. Built-in choreography composes with these sampled lanes.
Advanced titles use a centered vertical anchor and explicit static font weights;
classic titles retain their previous layout until opting into these features.

Rendering samples one absolute output frame clock for full exports, later preview
windows and stills. Letters, glow and glitch ghosts stay native editable text;
no per-frame title clips or image sequences are stored. Static holds coalesce
into single ASS events. Stagger/wave/scatter use font advances and preserve
combining marks. The font shapes and positions in previews are rendered by
the same libass pipeline as exports.

Dense effects use a file-backed filter graph in all render entry points to avoid
the Windows command-line limit. Static image stacks containing only shape,
glow, chromatic shift and chromakey effects are evaluated once per request,
then animated normally. The optimization works on a temporary project copy,
retains the original effects in the saved project, excludes animated PNGs,
time-ranged and temporal effects, and cleans up its temporary media.

Regression coverage: `test_title_motion.py`, `test_static_graphic_stacks.py`,
the existing title animation/typography, alpha mask, font, motion and bounded
render tests.
