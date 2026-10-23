"""Bounded, inspectable waveform alignment. Ambiguous material is rejected."""
from __future__ import annotations
import subprocess
import numpy as np


def waveform(clip, ffmpeg="ffmpeg", seconds=60):
    if clip.speed.to_fraction() != 1 or clip.speed_curve or clip.freeze_at is not None or clip.nested:
        raise ValueError("Waveform sync requires ordinary 1x camera clips")
    duration = min(seconds, float(clip.duration.to_fraction()))
    result = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-ss",
        str(float(clip.source_start.to_fraction())), "-i", clip.asset_ref.source_path,
        "-t", str(duration), "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "pipe:1"],
        capture_output=True, timeout=90)
    if result.returncode: raise ValueError("Cannot read camera audio: " + result.stderr.decode(errors="replace")[-500:])
    samples = np.frombuffer(result.stdout, dtype="<f4")
    count = len(samples) // 80
    if count < 200 or float(np.sqrt(np.mean(samples * samples))) < 1e-4:
        raise ValueError("At least two seconds of non-silent camera audio are required")
    envelope = np.sqrt(np.mean(samples[:count * 80].reshape(count, 80) ** 2, axis=1))
    return envelope.astype(np.float64)


def correlate(reference, target, maximum=10):
    a, b = reference - reference.mean(), target - target.mean()
    scores = []
    for lag in range(-round(maximum * 100), round(maximum * 100) + 1):
        start_a, start_b = max(0, -lag), max(0, lag)
        count = min(len(a) - start_a, len(b) - start_b)
        if count < 200: continue
        x, y = a[start_a:start_a + count], b[start_b:start_b + count]
        norm = np.linalg.norm(x) * np.linalg.norm(y)
        if norm > 1e-10: scores.append((float(np.dot(x, y) / norm), lag))
    if not scores: raise ValueError("Camera audio has insufficient waveform variation")
    confidence, lag = max(scores)
    alternate = max((value for value, offset in scores if abs(offset - lag) >= 15), default=0)
    margin = confidence - alternate
    if confidence < 0.65 or margin < 0.05:
        raise ValueError(f"Ambiguous waveform sync (confidence={confidence:.3f}, margin={margin:.3f}); align manually")
    return {"method": "audio-envelope-normalized-xcorr", "lagSeconds": lag / 100,
            "confidence": confidence, "ambiguityMargin": margin, "resolutionSeconds": 0.01,
            "windowSeconds": min(len(a), len(b)) / 100}


def synchronize(sequence, payload):
    from cutvoke.domain.rational import Rational
    reference = next((track for track in sequence.tracks if track.id == payload.get("referenceTrackId")), None)
    target = next((track for track in sequence.tracks if track.id == payload.get("targetTrackId")), None)
    if not reference or not target or reference is target:
        raise ValueError("Choose two different camera tracks")
    if len(reference.clips) != 1 or len(target.clips) != 1:
        raise ValueError("Waveform sync currently accepts one continuous source clip per camera")
    first, second = reference.clips[0], target.clips[0]
    if second.keyframes or second.effects:
        raise ValueError("Synchronize before adding effects or keyframes to the target camera")
    maximum = float(payload.get("maxOffsetSeconds", 10))
    if not 0 < maximum <= 30: raise ValueError("Maximum sync offset must be from 0 to 30 seconds")
    report = correlate(waveform(first), waveform(second), maximum)
    shift = float(first.timeline_start.to_fraction() - second.timeline_start.to_fraction()) - report["lagSeconds"]
    delta = Rational.from_float(shift)
    start, end = second.timeline_start + delta, second.timeline_end + delta
    if end <= Rational.of(0): raise ValueError("Synchronized camera would be entirely before timeline zero")
    clipped = max(0.0, -float(start.to_fraction()))
    if clipped:
        second.source_start += Rational.from_float(clipped)
        start = Rational.of(0)
    second.timeline_start, second.timeline_end = start, end
    report.update(referenceTrackId=reference.id, targetTrackId=target.id,
                  appliedDeltaSeconds=shift, clippedPrefixSeconds=clipped)
    sequence.multicam = dict(sequence.multicam or {}, sync="audio-waveform", syncReport=report)
    return report
