# Editable title motion

`cutvoke.text` supports the same title animation parameters through the Web
inspector, MCP, HTTP commands and CLI. Restart an existing backend or reconnect
an existing MCP process after upgrading its source so its effect registry reloads.

## Editing in the preview / 在预览中编辑

Click text in the preview or select its timeline clip to edit the same element.
The preview shows a selection outline. If the playhead is outside the selected
clip, clicking the clip seeks to the clicked position, snapped to a project frame.

Title content and valid style changes appear immediately. After 350 ms without
input, the editor saves the changed fields as an undoable edit. Chinese IME
composition previews locally and saves after composition ends. Project switching,
undo/redo and export wait for pending saves; failed saves retain the draft for retry.

点击预览中的文字或时间轴上的文字片段，都选中同一个元素，画面显示选中边框。
播放头不在该片段内时，点击片段会跳到鼠标点击的位置，并对齐到工程帧。
文字及有效样式修改实时显示，停止输入约 350 毫秒后自动保存，可撤销。
中文输入法组词期间只更新画面，组词完成后保存。保存失败时保留草稿，可重试。

The editor composites active-sequence titles as native vector text over its base
video. `editorTitles=1` opts frame/window requests into a title-free base;
`editorTitles: true` does the same for preview-preparation jobs. Unflagged API
requests and exports continue to render complete titles on the server. Title-only
intervals retain their original duration. Default video previews use up to 1280
pixels on the long edge, while settled seek posters use up to 1920 pixels.

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
All titles select explicit static font weights shared with browser text. Advanced
titles use a centered vertical anchor; classic titles retain their previous layout
and real-height sizing until opting into these features.

Rendering samples one absolute output frame clock for full exports, later preview
windows and stills. Letters, glow and glitch ghosts stay native editable text;
no per-frame title clips or image sequences are stored. Static holds coalesce
into single ASS events. Stagger/wave/scatter use font advances and preserve
combining marks. Unflagged server previews use the same libass pipeline as exports. The editor
uses matching frame-clock geometry and the same packaged fonts in its live text layer.

Dense effects use a file-backed filter graph in all render entry points to avoid
the Windows command-line limit. Static image stacks containing only shape,
glow, chromatic shift and chromakey effects are evaluated once per request,
then animated normally. The optimization works on a temporary project copy,
retains the original effects in the saved project, excludes animated PNGs,
time-ranged and temporal effects, and cleans up its temporary media.

Regression coverage: `test_title_motion.py`, `test_static_graphic_stacks.py`,
the existing title animation/typography, alpha mask, font, motion and bounded
render tests.
