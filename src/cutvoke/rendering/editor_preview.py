"""Opt-in browser title compositing; never alter saved projects or exports."""

import copy


def browser_titles(project):
    """Keep title intervals (including title-only tails), omit their baked pixels.

    Only the active sequence is composited by the editor. Nested sequences keep
    their own text. The marker survives worker snapshots and lets cancellation
    compare the same base-video identity against the current saved project.
    """
    preview = copy.deepcopy(project)
    if any(c.compositing or c.role == "adjustment" for t in preview.sequence.tracks for c in t.clips):
        preview._browser_titles = True
        return preview
    for track in preview.sequence.tracks:
        if track.kind != "text":
            continue
        for clip in track.clips:
            clip.effects = [{"effectId": "cutvoke.text", "params": {"content": ""}}]
            clip.keyframes = {}
    preview._browser_titles = True
    return preview
