"""Build (never execute) one atomic edit.batch for selected video boundaries."""

def selected_transition_batch(project, clip_ids, effect_id='cutvoke.transition.crossfade', duration=0.25):
    """Only adjacent selected clips on the same video track; preserve existing transitions."""
    import math
    if not isinstance(effect_id, str) or not effect_id.startswith('cutvoke.transition.'):
        raise ValueError('Choose a transition effect ID; clearing is not this recipe')
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('Duration must be finite and positive')
    if not clip_ids or len(set(clip_ids)) != len(clip_ids):
        raise ValueError('Select distinct clip IDs')
    selected = set(clip_ids)
    known = {c.id for t in project.sequence.tracks for c in t.clips}
    if not selected <= known:
        raise ValueError('Selection is stale; refresh the project')
    commands = []
    for track in project.sequence.tracks:
        if track.kind != 'video':
            continue
        clips = sorted(track.clips, key=lambda c: c.timeline_start)
        for left, right in zip(clips, clips[1:]):
            if not {left.id, right.id} <= selected or left.timeline_end != right.timeline_start:
                continue
            if track.locked:
                raise ValueError('Selected boundary is on a locked track')
            if any(str(e.get('effectId', '')).startswith('cutvoke.transition.') for e in right.effects):
                continue
            commands.append({'type': 'effect.setTransition', 'payload': {
                'clipId': right.id, 'effectId': effect_id, 'params': {'duration': duration}}})
    if not commands:
        raise ValueError('No eligible selected boundaries')
    if len(commands) > 32:
        raise ValueError('More than 32 boundaries: narrow the selection; no automatic partial batches')
    return {'commands': commands}
