"""Program camera decisions on an absolute timeline; other layers remain visible."""
from __future__ import annotations
from cutvoke.domain.rational import Rational


def camera_at(group, seconds):
    if not isinstance(group, dict): return None
    active = group.get("activeTrackId")
    for cut in sorted(group.get("cuts", []), key=lambda value: float(Rational.from_json(**value["time"]).to_fraction())):
        if float(Rational.from_json(**cut["time"]).to_fraction()) <= seconds + 1e-9:
            active = cut["trackId"]
    return active


def visibility_expression(group, track_id, origin=0.0):
    members = group.get("trackIds", [group.get("activeTrackId")])
    if track_id not in members: return None
    cuts = sorted(group.get("cuts", []), key=lambda value: float(Rational.from_json(**value["time"]).to_fraction()))
    spans, active, start = [], group.get("activeTrackId"), 0.0
    for cut in cuts:
        end = float(Rational.from_json(**cut["time"]).to_fraction())
        if active == track_id and end > start:
            spans.append(f'gte(t,{start-origin:.9f})*lt(t,{end-origin:.9f})')
        active, start = cut["trackId"], end
    if active == track_id: spans.append(f'gte(t,{start-origin:.9f})')
    return '+'.join(spans) or '0'
