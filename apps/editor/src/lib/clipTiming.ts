import type { Clip, Rational } from "../types/api";
import { rationalToSecs } from "./rational";
import { curveElapsed, curvePoints } from "./speedCurve";

export type TimingField = "start" | "end" | "duration" | "sourceIn" | "sourceOut";

/** Source coordinates follow playback direction, including reverse and speed ramps. */
export function clipSourceRange(clip: Clip): { start: number; end: number } {
  if (clip.freezeAt) {
    const held = rationalToSecs(clip.freezeAt);
    return { start: held, end: held };
  }
  const start = rationalToSecs(clip.sourceStart);
  const duration = rationalToSecs(clip.timelineEnd) - rationalToSecs(clip.timelineStart);
  const speed = rationalToSecs(clip.speed) || 1;
  const consumed = clip.speedCurve ? rationalToSecs(clip.speedCurve.sourceDuration) : duration * Math.abs(speed);
  return speed < 0 && !clip.speedCurve ? { start: start + consumed, end: start } : { start, end: start + consumed };
}

/** Seconds, a frame count (45f), or non-drop HH:MM:SS:FF. */
export function parseTimingInput(value: string, fps: Rational): number {
  const text = value.trim();
  const rate = rationalToSecs(fps);
  if (!(rate > 0)) throw new Error("Invalid frame rate");
  if (/^\d+f$/i.test(text)) return Number(text.slice(0, -1)) / rate;
  const code = /^(\d+):([0-5]\d):([0-5]\d):(\d{2,3})$/.exec(text);
  if (code) {
    const nominal = Math.round(rate), frame = Number(code[4]);
    if (frame >= nominal) throw new Error("Invalid timecode frame");
    return ((Number(code[1]) * 3600 + Number(code[2]) * 60 + Number(code[3])) * nominal + frame) / rate;
  }
  if (!/^\d+(?:\.\d+)?$/.test(text) || !Number.isFinite(Number(text))) throw new Error("Invalid time");
  return Number(text);
}

export function frameTime(seconds: number, fps: Rational): Rational {
  const frames = Math.round(seconds * rationalToSecs(fps));
  if (!Number.isSafeInteger(frames)) throw new Error("Time exceeds supported frame range");
  return { num: (BigInt(frames) * BigInt(fps.den)).toString(), den: fps.num };
}

const sourceRat = (seconds: number): Rational => ({ num: String(Math.round(seconds * 1_000_000)), den: "1000000" });

/** Numeric trims share the handle semantics and commit one atomic clip.trim. */
export function timingPayload(clip: Clip, field: TimingField, value: number, fps: Rational): Record<string, unknown> {
  const start = rationalToSecs(clip.timelineStart), end = rationalToSecs(clip.timelineEnd);
  const source = rationalToSecs(clip.sourceStart), speed = rationalToSecs(clip.speed) || 1;
  let newStart = start, newEnd = end;
  if (field === "start") newStart = value;
  if (field === "end") newEnd = value;
  if (field === "duration") newEnd = start + value;
  if (field === "sourceIn" || field === "sourceOut") {
    if (clip.freezeAt) throw new Error("Frozen source");
    const offset = value - clipSourceRange(clip).start;
    const points = curvePoints(clip.speedCurve);
    let elapsed: number;
    if (points && clip.speedCurve) {
      const total = rationalToSecs(clip.speedCurve.sourceDuration);
      if (offset < -1e-7 || offset > total + 1e-7) throw new Error("Curve trim outside source");
      elapsed = curveElapsed(offset, total, points);
    } else elapsed = offset / speed;
    if (field === "sourceIn") newStart = start + elapsed;
    else newEnd = start + elapsed;
  }
  const startRat = frameTime(newStart, fps), endRat = frameTime(newEnd, fps);
  newStart = rationalToSecs(startRat); newEnd = rationalToSecs(endRat);
  if (newStart < 0 || newEnd <= newStart) throw new Error("Invalid clip range");
  const payload: Record<string, unknown> = { clipId: clip.id };
  if (Math.abs(newStart - start) > 1e-9) {
    payload.timelineStart = startRat;
  }
  if (Math.abs(newEnd - end) > 1e-9) payload.timelineEnd = endRat;
  if (!clip.speedCurve && !clip.freezeAt) {
    // Reverse clips store the LOWER bound of the consumed source range.
    const nextSource = speed < 0 ? source + (end - newEnd) * -speed : source + (newStart - start) * speed;
    if (Math.abs(nextSource - source) > 1e-9) payload.sourceStart = sourceRat(nextSource);
  }
  return payload;
}
